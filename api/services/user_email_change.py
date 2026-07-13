from smtplib import SMTPException

from allauth.account import signals
from allauth.account.forms import AddEmailForm
from allauth.account.internal.flows.manage_email import email_already_exists
from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from anymail.exceptions import AnymailError
from django.core.exceptions import ValidationError
from django.db import transaction

from api.services.email_verification import (
    get_user_email_address_for_verification,
    send_email_verification,
)


EMAIL_CHANGE_REDIRECT_URL = "/app/profile"


class EmailChangeSendError(Exception):
    pass


class EmailChangeRateLimitError(Exception):
    pass


def get_pending_email_change(user) -> EmailAddress | None:
    current_email = str(user.email or "").strip()
    pending = EmailAddress.objects.filter(user=user, verified=False)
    if current_email:
        pending = pending.exclude(email__iexact=current_email)
    return pending.order_by("-pk").first()


def serialize_email_verification(user) -> dict[str, str | bool | None]:
    current_email = str(user.email or "").strip()
    is_verified = bool(
        current_email
        and EmailAddress.objects.filter(
            user=user,
            email__iexact=current_email,
            verified=True,
        ).exists()
    )
    pending = get_pending_email_change(user)
    return {
        "email": current_email,
        "isVerified": is_verified,
        "pendingEmail": pending.email if pending else None,
    }


def validate_email_change(user, email) -> tuple[AddEmailForm, str | None]:
    form = AddEmailForm(data={"email": email}, user=user)
    if not form.is_valid():
        return form, None
    cleaned_email = form.cleaned_data["email"]
    try:
        email_already_exists(cleaned_email, user=user, always_raise=True)
    except ValidationError as exc:
        form.add_error("email", exc)
        return form, None
    return form, cleaned_email


def start_email_change(request, email: str) -> tuple[dict[str, str | bool | None], str]:
    user = request.user
    current_email = str(user.email or "").strip()
    has_verified_identity = EmailAddress.objects.filter(user=user, verified=True).exists()
    previous_pending_ids = list(
        EmailAddress.objects.filter(user=user, verified=False)
        .exclude(email__iexact=current_email)
        .values_list("pk", flat=True)
    )

    new_address = EmailAddress.objects.create(
        user=user,
        email=email,
        verified=False,
        primary=False,
    )

    try:
        sent = send_email_verification(
            request,
            new_address,
            redirect_url=EMAIL_CHANGE_REDIRECT_URL,
        )
    except ImmediateHttpResponse as exc:
        new_address.delete()
        if exc.response.status_code == 429:
            raise EmailChangeRateLimitError from exc
        raise
    except (AnymailError, OSError, SMTPException) as exc:
        new_address.delete()
        raise EmailChangeSendError from exc

    with transaction.atomic():
        EmailAddress.objects.filter(pk__in=previous_pending_ids).delete()
        if not has_verified_identity:
            EmailAddress.objects.filter(user=user, email__iexact=current_email).exclude(
                pk=new_address.pk
            ).delete()
            new_address.set_as_primary()

    signals.email_added.send(
        sender=EmailAddress,
        request=request,
        user=user,
        email_address=new_address,
    )

    message = (
        "Verification email sent. Your current email will remain active until the new address is verified."
        if sent and has_verified_identity
        else "Verification email sent."
        if sent
        else "A verification email was already sent recently. Please check your inbox or try again later."
    )
    return serialize_email_verification(user), message


def cancel_email_change(user) -> tuple[dict[str, str | bool | None], str]:
    pending = get_pending_email_change(user)
    if pending is None:
        return serialize_email_verification(user), "No email change is pending."
    pending.delete()
    return serialize_email_verification(user), "Email change canceled."


def get_email_verification_target(user) -> EmailAddress | None:
    return get_pending_email_change(user) or get_user_email_address_for_verification(user)
