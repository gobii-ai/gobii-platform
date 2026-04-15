"""
Email verification gating service.

Provides helpers to check whether a user has verified their email address,
used to gate external communications (email, SMS, webhooks) until verified.
"""

import logging

from allauth.account.models import EmailAddress
from django.conf import settings
from django.core.cache import cache
from django.db import transaction

logger = logging.getLogger(__name__)


class EmailVerificationError(Exception):
    """Raised when an action requires email verification."""

    def __init__(self, message: str | None = None):
        self.message = message or (
            "Email verification required. Please verify your email address to use this feature."
        )
        super().__init__(self.message)

    def to_tool_response(self) -> dict:
        """Return a tool-compatible error response."""
        return {
            "status": "error",
            "error_code": "EMAIL_VERIFICATION_REQUIRED",
            "message": self.message,
        }


def has_verified_email(user) -> bool:
    """
    Check if user has at least one verified email address.

    Superusers bypass this check and are always considered verified.

    Args:
        user: The user to check (can be None or anonymous)

    Returns:
        True if user has a verified email or is a superuser, False otherwise
    """
    if user is None:
        return False
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return EmailAddress.objects.filter(user=user, verified=True).exists()


def require_verified_email(user, *, action_description: str = "perform this action") -> None:
    """
    Raise EmailVerificationError if user does not have a verified email.

    Args:
        user: The user to check
        action_description: Human-readable description of the action being attempted,
                          used in the error message (e.g., "send emails")

    Raises:
        EmailVerificationError: If the user lacks a verified email
    """
    if not has_verified_email(user):
        raise EmailVerificationError(
            f"Email verification required to {action_description}. "
            "Please verify your email address in your account settings."
        )


def ensure_current_email_address_record(user) -> EmailAddress | None:
    """Ensure the user's current email has an EmailAddress row and is primary."""
    email = (getattr(user, "email", "") or "").strip()
    if not user or not getattr(user, "pk", None) or not email:
        return None

    with transaction.atomic():
        email_address = (
            EmailAddress.objects
            .select_for_update()
            .filter(user=user, email__iexact=email)
            .order_by("-primary", "-verified", "pk")
            .first()
        )
        if email_address is None:
            email_address = EmailAddress.objects.create(
                user=user,
                email=email,
                verified=False,
                primary=True,
            )
        else:
            updated_fields: list[str] = []
            if email_address.email != email:
                email_address.email = email
                updated_fields.append("email")
            if not email_address.primary:
                email_address.primary = True
                updated_fields.append("primary")
            if updated_fields:
                email_address.save(update_fields=updated_fields)

        EmailAddress.objects.filter(user=user, primary=True).exclude(pk=email_address.pk).update(primary=False)
        EmailAddress.objects.filter(user=user, email__iexact=email).exclude(pk=email_address.pk).update(primary=False)

    return email_address


def maybe_send_inbound_email_verification(request, user, *, sender_email: str) -> bool:
    """
    Send the standard email verification message when an inbound owner email is blocked.

    This is intentionally limited to exact matches on the account's current email and
    is rate-limited so repeated replies do not spam the owner.
    """
    if user is None or not getattr(user, "pk", None):
        return False

    normalized_sender = (sender_email or "").strip().lower()
    normalized_user_email = (getattr(user, "email", "") or "").strip().lower()
    if not normalized_sender or normalized_sender != normalized_user_email:
        return False
    if has_verified_email(user):
        return False

    email_address = ensure_current_email_address_record(user)
    if email_address is None:
        return False

    throttle_key = f"inbound-email-verification:{user.id}:{normalized_user_email}"
    if not cache.add(
        throttle_key,
        "1",
        timeout=settings.INBOUND_EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS,
    ):
        return False

    from allauth.account.internal.flows.email_verification import (
        send_verification_email_to_address,
    )

    try:
        send_verification_email_to_address(request, email_address)
    except Exception:
        # Provider or backend failures should not break inbound webhook handling.
        cache.delete(throttle_key)
        logger.exception(
            "Failed sending inbound-triggered verification email for user %s",
            user.id,
        )
        return False
    return True
