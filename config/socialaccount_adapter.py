"""Custom django-allauth social account adapter hooks."""

import logging

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import HttpResponseRedirect
from django.urls import reverse


logger = logging.getLogger(__name__)


class GobiiSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Tighten the social login flow for existing email/password users."""

    def pre_social_login(self, request, sociallogin) -> None:  # type: ignore[override]
        """Stop Google (or other) logins from hijacking password accounts."""

        # Allow normal processing when the social account already exists or the
        # user is connecting a provider while authenticated.
        if request.user.is_authenticated or sociallogin.account.pk:
            return

        email = (sociallogin.user and sociallogin.user.email or "").strip()
        if not email:
            return

        UserModel = get_user_model()
        try:
            existing_user = UserModel.objects.get(email__iexact=email)
        except UserModel.DoesNotExist:
            return

        provider_id = (sociallogin.account and sociallogin.account.provider) or "this"
        provider_name = provider_id.replace("_", " ").title()

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
            (
                f"We already have an account for {email}. Please sign in with your email and password."
            ),
        )

        raise ImmediateHttpResponse(HttpResponseRedirect(reverse("account_login")))

