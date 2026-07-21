from smtplib import SMTPException

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.mail import BadHeaderError, send_mail
from django.db import transaction
from django.utils import timezone

from api.models import (
    OrganizationMembership,
    OutboundEmailReview,
    OutboundEmailReviewNotificationState,
    UserPreference,
)


MANAGER_ROLES = {
    OrganizationMembership.OrgRole.OWNER,
    OrganizationMembership.OrgRole.ADMIN,
    OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
}


def _workspace_filter(review: OutboundEmailReview) -> dict[str, object]:
    if review.agent.organization_id:
        return {"agent__organization_id": review.agent.organization_id}
    return {"agent__user_id": review.agent.user_id, "agent__organization__isnull": True}


def _notification_state(review: OutboundEmailReview):
    if review.agent.organization_id:
        return OutboundEmailReviewNotificationState.objects.select_for_update().get_or_create(
            organization_id=review.agent.organization_id
        )[0]
    return OutboundEmailReviewNotificationState.objects.select_for_update().get_or_create(
        user_id=review.agent.user_id
    )[0]


def _reviewer_user_ids(review: OutboundEmailReview) -> list[int]:
    if review.agent.organization_id:
        return list(
            OrganizationMembership.objects.filter(
                org_id=review.agent.organization_id,
                status=OrganizationMembership.OrgStatus.ACTIVE,
                role__in=MANAGER_ROLES,
            ).values_list("user_id", flat=True)
        )
    return [review.agent.user_id]


def _reviewer_addresses(review: OutboundEmailReview) -> list[str]:
    user_model = get_user_model()
    enabled_user_ids = [
        user.id
        for user in user_model.objects.filter(id__in=_reviewer_user_ids(review))
        if UserPreference.resolve_known_preferences(
            user
        )[UserPreference.KEY_OUTBOX_EMAIL_NOTIFICATIONS_ENABLED]
    ]
    addresses = list(
        EmailAddress.objects.filter(user_id__in=enabled_user_ids, verified=True)
        .order_by("user_id", "-primary")
        .values_list("user_id", "email")
    )
    seen_user_ids: set[int] = set()
    selected_addresses: list[str] = []
    for user_id, address in addresses:
        if user_id in seen_user_ids:
            continue
        seen_user_ids.add(user_id)
        selected_addresses.append(address)
    return selected_addresses


def _send_notification(review: OutboundEmailReview, *, pending_count: int, digest: bool) -> None:
    recipients = _reviewer_addresses(review)
    if not recipients:
        return
    subject = (
        f"{pending_count} emails need review in Gobii"
        if digest or pending_count != 1
        else "An email needs review in Gobii"
    )
    message = (
        f"Your workspace has {pending_count} email{'s' if pending_count != 1 else ''} waiting in Review Before Send.\n\n"
        "Review them at /app/outbox. No recipient has received a pending email."
    )
    try:
        send_mail(subject, message, None, recipients, fail_silently=False)
    except (SMTPException, BadHeaderError, OSError):
        return


def sync_outbox_notification_cycle(review: OutboundEmailReview, *, allow_initial: bool = False) -> None:
    now = timezone.now()
    with transaction.atomic():
        state = _notification_state(review)
        pending_count = OutboundEmailReview.objects.filter(
            **_workspace_filter(review),
            status=OutboundEmailReview.Status.PENDING,
            expires_at__gt=now,
        ).count()
        if pending_count == 0:
            if state.pending_cycle_started_at is not None:
                state.pending_cycle_started_at = None
                state.initial_notification_sent_at = None
                state.last_digest_sent_at = None
                state.save(
                    update_fields=[
                        "pending_cycle_started_at",
                        "initial_notification_sent_at",
                        "last_digest_sent_at",
                        "updated_at",
                    ]
                )
            return
        if not allow_initial or state.pending_cycle_started_at is not None:
            return
        state.pending_cycle_started_at = now
        state.initial_notification_sent_at = now
        state.save(update_fields=["pending_cycle_started_at", "initial_notification_sent_at", "updated_at"])
    _send_notification(review, pending_count=pending_count, digest=False)


def send_due_outbox_digests() -> int:
    today = timezone.localdate()
    sent = 0
    for state in OutboundEmailReviewNotificationState.objects.exclude(pending_cycle_started_at=None):
        if state.last_digest_sent_at and timezone.localdate(state.last_digest_sent_at) >= today:
            continue
        review_filter = (
            {"agent__organization_id": state.organization_id}
            if state.organization_id
            else {"agent__user_id": state.user_id, "agent__organization__isnull": True}
        )
        review = OutboundEmailReview.objects.select_related("agent").filter(
            **review_filter,
            status=OutboundEmailReview.Status.PENDING,
            expires_at__gt=timezone.now(),
        ).first()
        if review is None:
            continue
        pending_count = OutboundEmailReview.objects.filter(
            **review_filter,
            status=OutboundEmailReview.Status.PENDING,
            expires_at__gt=timezone.now(),
        ).count()
        _send_notification(review, pending_count=pending_count, digest=True)
        state.last_digest_sent_at = timezone.now()
        state.save(update_fields=["last_digest_sent_at", "updated_at"])
        sent += 1
    return sent
