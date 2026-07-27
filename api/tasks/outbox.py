import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from api.agent.comms.outbound_delivery import deliver_agent_email
from api.models import DeliveryStatus, OutboundEmailReview, PersistentAgentUserActionEvent
from api.services.outbound_email_review import expire_review_if_needed, track_review_event
from api.services.outbox_notifications import send_due_outbox_digests
from util.analytics import AnalyticsEvent


logger = logging.getLogger(__name__)


@shared_task(name="api.tasks.dispatch_approved_outbox_email")
def dispatch_approved_outbox_email(review_id: str) -> None:
    review = (
        OutboundEmailReview.objects.select_related("message", "agent")
        .filter(pk=review_id, status=OutboundEmailReview.Status.APPROVED)
        .first()
    )
    if review is None or review.message.latest_status != DeliveryStatus.QUEUED:
        return
    deliver_agent_email(review.message)
    review.message.refresh_from_db(fields=["latest_status", "latest_error_message"])
    if review.message.latest_status == DeliveryStatus.FAILED:
        PersistentAgentUserActionEvent.objects.create(
            agent=review.agent,
            action_type=PersistentAgentUserActionEvent.ActionType.OUTBOX_FAILED,
            metadata={
                "outboxItemId": str(review.id),
                "messageId": str(review.message_id),
                "error": review.message.latest_error_message,
            },
        )
        track_review_event(review, AnalyticsEvent.OUTBOX_EMAIL_FAILED)


@shared_task(name="api.tasks.reconcile_approved_outbox_emails")
def reconcile_approved_outbox_emails() -> int:
    review_ids = list(
        OutboundEmailReview.objects.filter(
            status=OutboundEmailReview.Status.APPROVED,
            message__latest_status=DeliveryStatus.QUEUED,
        ).values_list("id", flat=True)[:500]
    )
    for review_id in review_ids:
        dispatch_approved_outbox_email.delay(str(review_id))
    return len(review_ids)


@shared_task(name="api.tasks.expire_pending_outbox_emails")
def expire_pending_outbox_emails() -> int:
    now = timezone.now()
    review_ids = list(
        OutboundEmailReview.objects.filter(
            status=OutboundEmailReview.Status.PENDING,
            expires_at__lte=now,
        ).values_list("id", flat=True)[:500]
    )
    expired = 0
    for review_id in review_ids:
        with transaction.atomic():
            review = OutboundEmailReview.objects.select_for_update().select_related("agent").get(pk=review_id)
            if not expire_review_if_needed(review, now=now):
                continue
            expired += 1
    return expired


@shared_task(name="api.tasks.send_outbox_review_digests")
def send_outbox_review_digests() -> int:
    return send_due_outbox_digests()
