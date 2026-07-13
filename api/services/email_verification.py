"""
Email verification gating service.

Provides helpers to check whether a user has verified their email address,
used to gate external communications (email, SMS, webhooks) until verified.
"""

from allauth.account import signals
from allauth.account.forms import AddEmailForm
from allauth.account.internal.flows.manage_email import email_already_exists
from allauth.account.models import EmailAddress
from django.core.exceptions import ValidationError
from django.db import transaction


EMAIL_VERIFICATION_REDIRECT_URL_SESSION_KEY = "email_verification_redirect_url"
EMAIL_CHANGE_REDIRECT_URL = "/app/profile"


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


def has_verified_email_address(user, email: str | None) -> bool:
    normalized_email = str(email or "").strip()
    if not normalized_email:
        return False
    if user is None:
        return False
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return EmailAddress.objects.filter(
        user=user,
        email__iexact=normalized_email,
        verified=True,
    ).exists()


def get_email_address_for_verification(user) -> EmailAddress | None:
    from allauth.account.internal.flows.email_verification import get_address_for_user

    return get_address_for_user(user)


def get_user_email_address_for_verification(user) -> EmailAddress | None:
    email = str(getattr(user, "email", "") or "").strip()
    if not email:
        return None

    address = (
        EmailAddress.objects.filter(user=user, email__iexact=email)
        .order_by("-primary", "-verified")
        .first()
    )
    if address is not None:
        return address

    primary_exists = EmailAddress.objects.filter(user=user, primary=True).exists()
    return EmailAddress.objects.create(
        user=user,
        email=email,
        verified=False,
        primary=not primary_exists,
    )


def get_pending_email_change(user) -> EmailAddress | None:
    pending = EmailAddress.objects.get_new(user)
    current_email = str(user.email or "").strip().casefold()
    return pending if pending and pending.email.casefold() != current_email else None


def serialize_email_verification(user) -> dict[str, str | bool | None]:
    current_email = str(user.email or "").strip()
    pending = get_pending_email_change(user)
    return {
        "email": current_email,
        "isVerified": bool(current_email and EmailAddress.objects.filter(
            user=user, email__iexact=current_email, verified=True
        ).exists()),
        "pendingEmail": pending.email if pending else None,
    }


def validate_email_change(user, email) -> tuple[AddEmailForm, str | None]:
    form = AddEmailForm(data={"email": email}, user=user)
    if form.is_valid():
        try:
            email_already_exists(form.cleaned_data["email"], user=user, always_raise=True)
        except ValidationError as exc:
            form.add_error("email", exc)
    return form, form.cleaned_data.get("email") if not form.errors else None


def start_email_change(request, email: str) -> dict[str, str | bool | None]:
    user = request.user
    current_email_is_verified = EmailAddress.objects.filter(user=user, email__iexact=user.email, verified=True).exists()

    # Keep allauth's canonical pending-address replacement, but send inside the
    # transaction so provider failures restore the previous address.
    with transaction.atomic():
        new_address = EmailAddress.objects.add_new_email(request, user, email, send_verification=False)
        send_email_verification(request, new_address, redirect_url=EMAIL_CHANGE_REDIRECT_URL)
        if not current_email_is_verified:
            new_address.set_as_primary()

    signals.email_added.send(sender=EmailAddress, request=request, user=user, email_address=new_address)
    return serialize_email_verification(user)


def cancel_email_change(user) -> dict[str, str | bool | None]:
    pending = get_pending_email_change(user)
    if pending:
        pending.remove()
    return serialize_email_verification(user)


def get_email_verification_target(user) -> EmailAddress | None:
    return get_pending_email_change(user) or get_user_email_address_for_verification(user)


def send_email_verification(
    request,
    email_address: EmailAddress,
    *,
    redirect_url: str | None = None,
) -> bool:
    from allauth.account.internal.flows.email_verification import send_verification_email_to_address

    if redirect_url:
        request.session[EMAIL_VERIFICATION_REDIRECT_URL_SESSION_KEY] = redirect_url
        request.session.modified = True
    try:
        return send_verification_email_to_address(request, email_address)
    finally:
        if redirect_url:
            request.session.pop(EMAIL_VERIFICATION_REDIRECT_URL_SESSION_KEY, None)
            request.session.modified = True


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
