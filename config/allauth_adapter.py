"""Custom allauth adapter hooks."""

import logging
from collections.abc import Iterable
from functools import lru_cache

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect
from django.urls import reverse

from api.services.system_settings import (
    get_account_allow_password_login,
    get_account_allow_password_signup,
    get_account_allow_social_login,
    get_account_allow_social_signup,
)
from util.onboarding import set_trial_onboarding_requires_plan_selection

try:
    from MailChecker import MailChecker as _MailChecker
except ImportError:  # pragma: no cover - dependency is expected in production
    try:
        from mailchecker import MailChecker as _MailChecker  # type: ignore[attr-defined]
    except ImportError:
        _MailChecker = None

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_mailchecker() -> object | None:
    if _MailChecker is None:
        return None
    return _MailChecker()


def is_disposable_domain(domain: str) -> bool:
    checker = _get_mailchecker()
    if checker is None:
        return False
    checker_fn = getattr(checker, "is_blacklisted", None)
    if not callable(checker_fn):
        return False
    return bool(checker_fn(f"u@{domain}"))


class GobiiAccountAdapter(DefaultAccountAdapter):
    """Signup and login policy hooks for django-allauth."""

    GENERIC_EMAIL_BLOCK_ERROR = "We are unable to create an account with this email address. Please use a different one."

    def clean_email(self, email: str) -> str:
        cleaned_email = super().clean_email(email)
        domain = self._extract_domain(cleaned_email)

        if self._matches_domain_rule(domain, settings.GOBII_EMAIL_DOMAIN_ALLOWLIST):
            return cleaned_email

        if self._matches_domain_rule(domain, settings.GOBII_EMAIL_DOMAIN_BLOCKLIST):
            self._log_email_block(reason="blocklist", domain=domain, email=cleaned_email)
            raise ValidationError(self.GENERIC_EMAIL_BLOCK_ERROR)

        if settings.GOBII_EMAIL_BLOCK_DISPOSABLE and is_disposable_domain(domain):
            self._log_email_block(reason="disposable", domain=domain, email=cleaned_email)
            raise ValidationError(self.GENERIC_EMAIL_BLOCK_ERROR)

        return cleaned_email

    def is_open_for_signup(self, request) -> bool:
        allow_password = get_account_allow_password_signup()
        allow_social = get_account_allow_social_signup()
        if request and getattr(request, "method", "").upper() == "POST":
            return allow_password
        return allow_password or allow_social

    def pre_login(
        self,
        request,
        user,
        *,
        email_verification,
        signal_kwargs,
        email,
        signup,
        redirect_url,
    ):
        response = super().pre_login(
            request,
            user,
            email_verification=email_verification,
            signal_kwargs=signal_kwargs,
            email=email,
            signup=signup,
            redirect_url=redirect_url,
        )
        if response:
            return response

        if signup:
            set_trial_onboarding_requires_plan_selection(
                request,
                required=True,
            )

        if signup:
            return None

        method = self._get_latest_auth_method(request)
        if method in {"password", "code"} and not get_account_allow_password_login():
            messages.error(request, "Password login is currently disabled.")
            raise ImmediateHttpResponse(HttpResponseRedirect(reverse("account_login")))
        if method == "socialaccount" and not get_account_allow_social_login():
            messages.error(request, "Social login is currently disabled.")
            raise ImmediateHttpResponse(HttpResponseRedirect(reverse("account_login")))
        return None

    @staticmethod
    def _extract_domain(email: str) -> str:
        return email.rsplit("@", 1)[-1].strip().lower()

    @classmethod
    def _matches_domain_rule(cls, domain: str, rules: Iterable[str]) -> bool:
        for raw_rule in rules:
            rule = raw_rule.strip().lower()
            if not rule:
                continue
            if domain == rule or domain.endswith(f".{rule}"):
                return True
        return False

    @classmethod
    def _log_email_block(cls, *, reason: str, domain: str, email: str) -> None:
        logger.warning(
            "Signup rejected for email domain policy",
            extra={
                "reason": reason,
                "domain": domain,
                "email": cls._redact_email(email),
            },
        )

    @staticmethod
    def _redact_email(email: str) -> str:
        local_part, _, domain = email.partition("@")
        if not domain:
            return "***"
        local_prefix = local_part[:1] if local_part else "u"
        return f"{local_prefix}***@{domain.lower()}"

    @staticmethod
    def _get_latest_auth_method(request) -> str | None:
        # NOTE: This relies on an internal django-allauth session key and may break on upgrades.
        methods = request.session.get("account_authentication_methods", [])
        if not methods:
            return None
        latest = methods[-1]
        return latest.get("method")
