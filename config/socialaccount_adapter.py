"""Custom django-allauth social account adapter hooks."""

import logging

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialLogin
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core import signing
from django.http import HttpResponseRedirect, HttpRequest
from django.urls import reverse

from agents.services import PretrainedWorkerTemplateService
from api.services.system_settings import get_account_allow_social_signup
from util.onboarding import (
    TRIAL_ONBOARDING_PENDING_SESSION_KEY,
    TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY,
    TRIAL_ONBOARDING_TARGET_SESSION_KEY,
)


logger = logging.getLogger(__name__)

# Session keys to preserve during social auth flow
OAUTH_CHARTER_SESSION_KEYS = (
    "agent_charter",
    "agent_charter_override",
    PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY,
    "agent_charter_source",
    TRIAL_ONBOARDING_PENDING_SESSION_KEY,
    TRIAL_ONBOARDING_TARGET_SESSION_KEY,
    TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY,
)

OAUTH_ATTRIBUTION_SESSION_KEYS = (
    "utm_first_touch",
    "utm_last_touch",
    "click_ids_first",
    "click_ids_last",
    "fbclid_first",
    "fbclid_last",
    "utm_querystring",
)

# Cookie name for stashing charter data during OAuth
OAUTH_CHARTER_COOKIE = "gobii_oauth_charter"
OAUTH_ATTRIBUTION_COOKIE = "gobii_oauth_attribution"


def _restore_session_keys_from_cookie(
    request: HttpRequest,
    *,
    cookie_name: str,
    keys: tuple[str, ...],
    overwrite_existing: bool = False,
) -> bool:
    cookie_value = request.COOKIES.get(cookie_name)
    if not cookie_value:
        return False

    try:
        stashed = signing.loads(cookie_value, max_age=3600)  # 1 hour max
    except (signing.BadSignature, signing.SignatureExpired):
        logger.debug("Invalid or expired OAuth cookie: %s", cookie_name)
        return False

    restored_any = False
    for key in keys:
        if key not in stashed:
            continue
        if not overwrite_existing and key in request.session:
            continue
        request.session[key] = stashed[key]
        restored_any = True

    if restored_any:
        request.session.modified = True

    return restored_any


def restore_oauth_session_state(
    request: HttpRequest,
    *,
    overwrite_existing: bool = False,
) -> bool:
    """Restore charter and attribution session keys from OAuth fallback cookies."""
    charter_restored = _restore_session_keys_from_cookie(
        request,
        cookie_name=OAUTH_CHARTER_COOKIE,
        keys=OAUTH_CHARTER_SESSION_KEYS,
        overwrite_existing=overwrite_existing,
    )
    attribution_restored = _restore_session_keys_from_cookie(
        request,
        cookie_name=OAUTH_ATTRIBUTION_COOKIE,
        keys=OAUTH_ATTRIBUTION_SESSION_KEYS,
        overwrite_existing=overwrite_existing,
    )
    return charter_restored or attribution_restored


class GobiiSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Tighten the social login flow for existing email/password users."""

    def is_open_for_signup(self, request: HttpRequest, sociallogin: SocialLogin) -> bool:
        return get_account_allow_social_signup()

    def pre_social_login(self, request: HttpRequest, social_login: SocialLogin) -> None:
        """Stop Google (or other) logins from hijacking password accounts.

        Also restore stashed OAuth session state (charter + attribution)
        when available.
        """
        if restore_oauth_session_state(request):
            logger.info("Restored OAuth session state during social login")

        # Allow normal processing when the social account already exists or the
        # user is connecting a provider while authenticated.
        if request.user.is_authenticated or social_login.account.pk:
            return

        email = (getattr(social_login.user, "email", None) or "").strip()
        if not email:
            return

        UserModel = get_user_model()
        try:
            existing_user = UserModel.objects.get(email__iexact=email)
        except UserModel.DoesNotExist:
            return

        provider_id = social_login.account.provider

        logger.info(
            "Social login blocked because email already exists",
            extra={
                "provider": provider_id,
                "email": email,
                "existing_user_id": existing_user.pk,
            },
        )

        messages.error(
            request,
            f"We already have an account for {email}. Please sign in with your email and password.",
        )

        raise ImmediateHttpResponse(HttpResponseRedirect(reverse("account_login")))
