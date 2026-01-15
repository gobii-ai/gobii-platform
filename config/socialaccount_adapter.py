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


logger = logging.getLogger(__name__)

# Session keys to preserve during social auth flow
_PRESERVE_SESSION_KEYS = (
    "agent_charter",
    PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY,
    "agent_charter_source",
)

# Cookie name for stashing charter data during OAuth
OAUTH_CHARTER_COOKIE = "gobii_oauth_charter"


class GobiiSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Tighten the social login flow for existing email/password users."""

    def pre_social_login(self, request: HttpRequest, social_login: SocialLogin) -> None:
        """Stop Google (or other) logins from hijacking password accounts.

        Also restore agent charter data from cookie if missing from session
        (can happen during OAuth flow).
        """
        # Restore agent charter data from signed cookie if missing from session
        if "agent_charter" not in request.session:
            cookie_value = request.COOKIES.get(OAUTH_CHARTER_COOKIE)
            if cookie_value:
                try:
                    stashed = signing.loads(cookie_value, max_age=3600)  # 1 hour max
                    for key in _PRESERVE_SESSION_KEYS:
                        if key in stashed and key not in request.session:
                            request.session[key] = stashed[key]
                    request.session.modified = True
                    logger.info("Restored agent charter from OAuth cookie during social login")
                except (signing.BadSignature, signing.SignatureExpired):
                    logger.debug("Invalid or expired OAuth charter cookie")

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

