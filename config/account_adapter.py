"""Custom allauth adapter hooks."""

import logging
from typing import Iterable

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

logger = logging.getLogger(__name__)


class GobiiAccountAdapter(DefaultAccountAdapter):
    """Reject signups that use a blocked email domain."""

    def clean_email(self, email: str) -> str:
        cleaned_email = super().clean_email(email)
        domain = cleaned_email.rsplit("@", 1)[-1].lower()

        blocked_domain = self._match_blocked_domain(
            domain, settings.SIGNUP_BLOCKED_EMAIL_DOMAINS
        )
        if blocked_domain:
            logger.warning(
                "Signup rejected for blocked email domain",
                extra={"domain": blocked_domain},
            )
            raise ValidationError(
                f"We can't create accounts with email addresses from {blocked_domain}. "
                "Please use a different email."
            )

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
    def _match_blocked_domain(domain: str, blocked_domains: Iterable[str] | None) -> str | None:
        if not blocked_domains:
            return None

        for blocked in blocked_domains:
            if domain == blocked or domain.endswith(f".{blocked}"):
                return blocked

        return None

    @staticmethod
    def _get_latest_auth_method(request) -> str | None:
        # NOTE: This relies on an internal django-allauth session key and may break on upgrades.
        methods = request.session.get("account_authentication_methods", [])
        if not methods:
            return None
        latest = methods[-1]
        return latest.get("method")
