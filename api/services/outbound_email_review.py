import hashlib
import json
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from api.agent.comms.message_service import _ensure_participant, _get_or_create_conversation
from api.agent.comms.email_threading import get_message_contact_address
from api.models import (
    AgentEmailAccount,
    AgentFsNode,
    CommsAllowlistEntry,
    CommsChannel,
    DeliveryStatus,
    OutboundEmailReview,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
    PersistentAgentMessageAttachment,
    PersistentAgentUserActionEvent,
    get_agent_contact_counts,
)
from api.services.outbound_email_policy import classify_email_recipients, normalize_email_addresses
from util.subscription_helper import get_user_max_contacts_per_agent
from api.services.email_verification import EmailVerificationError, require_verified_email
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource


OUTBOX_EXPIRY_DAYS = 7
OUTBOX_STALE_AFTER_HOURS = 24
OUTBOX_APPROVAL_INVALID_ERROR_CODE = "outbox_approval_invalid"
OUTBOX_ATTACHMENT_INVALID_ERROR_CODE = "outbox_attachment_invalid"
OUTBOX_CONTACT_REVOKED_ERROR_CODE = "outbox_contact_revoked"
NON_RETRYABLE_OUTBOX_ERROR_CODES = frozenset(
    {
        OUTBOX_APPROVAL_INVALID_ERROR_CODE,
        OUTBOX_ATTACHMENT_INVALID_ERROR_CODE,
    }
)


class OutboundEmailReviewError(Exception):
    pass


class StaleOutboxVersionError(OutboundEmailReviewError):
    pass


def _reviews_for_update():
    # PostgreSQL cannot apply FOR UPDATE to nullable select_related joins used by approval.
    return OutboundEmailReview.objects.select_for_update(of=("self",))


def track_review_event(
    review: OutboundEmailReview,
    event: AnalyticsEvent,
    *,
    actor=None,
    properties: dict[str, object] | None = None,
) -> None:
    event_properties = {
        "outbox_item_id": str(review.id),
        "message_id": str(review.message_id),
        "agent_id": str(review.agent_id),
        "organization_id": str(review.agent.organization_id) if review.agent.organization_id else None,
        **(properties or {}),
    }
    user_id = actor.pk if actor is not None else review.agent.user_id
    transaction.on_commit(
        lambda: Analytics.track_event(
            user_id=user_id,
            event=event,
            source=AnalyticsSource.CONSOLE if actor is not None else AnalyticsSource.AGENT,
            properties=event_properties,
        )
    )


def track_outbox_bypass_denied(message: PersistentAgentMessage, *, reason: str) -> None:
    agent = message.owner_agent
    if agent is None:
        return
    transaction.on_commit(
        lambda: Analytics.track_event(
            user_id=agent.user_id,
            event=AnalyticsEvent.OUTBOX_DELIVERY_BYPASS_DENIED,
            source=AnalyticsSource.AGENT,
            properties={
                "message_id": str(message.id),
                "agent_id": str(agent.id),
                "organization_id": str(agent.organization_id) if agent.organization_id else None,
                "reason": reason,
            },
        )
    )


def get_message_email_recipients(message: PersistentAgentMessage) -> tuple[str, ...]:
    primary = get_message_contact_address(message)
    cc_addresses = message.cc_endpoints.values_list("address", flat=True)
    return normalize_email_addresses([primary, *cc_addresses])


def snapshot_message_attachments(message: PersistentAgentMessage) -> None:
    for attachment in message.attachments.select_related("filespace_node"):
        if attachment.file and attachment.file.name and attachment.content_sha256:
            continue
        node = attachment.filespace_node
        source = node.content if node and node.content and node.content.name else None
        if source is None:
            raise OutboundEmailReviewError(f"Attachment '{attachment.filename}' is no longer available.")
        try:
            with source.storage.open(source.name, "rb") as source_file:
                content = source_file.read()
        except OSError as exc:
            raise OutboundEmailReviewError(
                f"Attachment '{attachment.filename}' could not be copied into the Outbox."
            ) from exc

        content_hash = hashlib.sha256(content).hexdigest()
        attachment.file.save(attachment.filename, ContentFile(content), save=False)
        attachment.file_size = len(content)
        attachment.content_sha256 = content_hash
        attachment.save(update_fields=["file", "file_size", "content_sha256"])


def load_verified_snapshot_attachments(
    message: PersistentAgentMessage,
) -> tuple[tuple[PersistentAgentMessageAttachment, bytes], ...]:
    verified: list[tuple[PersistentAgentMessageAttachment, bytes]] = []
    for attachment in message.attachments.select_related("filespace_node").order_by("id"):
        if not attachment.file or not attachment.file.name or not attachment.content_sha256:
            raise OutboundEmailReviewError(f"Attachment '{attachment.filename}' is no longer available.")
        try:
            with attachment.file.storage.open(attachment.file.name, "rb") as stored_file:
                content = stored_file.read()
        except OSError as exc:
            raise OutboundEmailReviewError(f"Attachment '{attachment.filename}' is no longer available.") from exc
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != attachment.content_sha256:
            raise OutboundEmailReviewError(f"Attachment '{attachment.filename}' changed after it was queued.")
        verified.append((attachment, content))
    return tuple(verified)


def verify_snapshot_attachments(message: PersistentAgentMessage) -> None:
    load_verified_snapshot_attachments(message)


def _attachment_manifest(message: PersistentAgentMessage) -> list[dict[str, object]]:
    return [
        {
            "filename": attachment.filename,
            "content_type": attachment.content_type,
            "file_size": attachment.file_size,
            "sha256": attachment.content_sha256,
        }
        for attachment in message.attachments.order_by("id")
    ]


def compute_message_content_hash(message: PersistentAgentMessage) -> str:
    raw_payload = message.raw_payload if isinstance(message.raw_payload, dict) else {}
    canonical = {
        "sender": (message.from_endpoint.address or "").strip().lower(),
        "to": get_message_contact_address(message),
        "cc": sorted(
            address.lower()
            for address in message.cc_endpoints.values_list("address", flat=True)
        ),
        "subject": str(raw_payload.get("subject") or ""),
        "body": message.body or "",
        "attachments": _attachment_manifest(message),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def queue_message_for_review(message: PersistentAgentMessage) -> OutboundEmailReview:
    if not message.is_outbound or message.from_endpoint.channel != CommsChannel.EMAIL:
        raise OutboundEmailReviewError("Only outbound email can be placed in the Outbox.")
    snapshot_message_attachments(message)
    content_hash = compute_message_content_hash(message)
    message.latest_status = DeliveryStatus.PENDING_APPROVAL
    message.latest_error_code = ""
    message.latest_error_message = ""
    message.save(update_fields=["latest_status", "latest_error_code", "latest_error_message"])
    review = OutboundEmailReview.objects.create(
        message=message,
        agent=message.owner_agent,
        content_hash=content_hash,
        expires_at=timezone.now() + timedelta(days=OUTBOX_EXPIRY_DAYS),
    )
    track_review_event(
        review,
        AnalyticsEvent.OUTBOX_EMAIL_QUEUED,
        properties={"recipient_count": len(get_message_email_recipients(message))},
    )
    return review


def review_is_stale(review: OutboundEmailReview, *, now=None) -> bool:
    return (now or timezone.now()) >= review.queued_at + timedelta(hours=OUTBOX_STALE_AFTER_HOURS)


def review_thread_changed(review: OutboundEmailReview) -> bool:
    message = review.message
    if not message.conversation_id:
        return False
    return PersistentAgentMessage.objects.filter(
        conversation_id=message.conversation_id,
        timestamp__gt=review.queued_at,
    ).exclude(pk=message.pk).exists()


@transaction.atomic
def expire_review_if_needed(review: OutboundEmailReview, *, now=None) -> bool:
    now = now or timezone.now()
    if review.status != OutboundEmailReview.Status.PENDING or now < review.expires_at:
        return False
    locked = _reviews_for_update().select_related("agent").get(pk=review.pk)
    if locked.status != OutboundEmailReview.Status.PENDING or now < locked.expires_at:
        review.status = locked.status
        review.decided_at = locked.decided_at
        return False
    locked.status = OutboundEmailReview.Status.EXPIRED
    locked.decided_at = now
    locked.save(update_fields=["status", "decided_at", "updated_at"])
    review.status = locked.status
    review.decided_at = locked.decided_at
    PersistentAgentUserActionEvent.objects.create(
        agent=locked.agent,
        action_type=PersistentAgentUserActionEvent.ActionType.OUTBOX_EXPIRED,
        metadata={"outboxItemId": str(locked.id), "messageId": str(locked.message_id)},
    )
    track_review_event(locked, AnalyticsEvent.OUTBOX_EMAIL_EXPIRED)
    return True


def authorize_reviewed_external_contacts(message: PersistentAgentMessage) -> None:
    agent = message.owner_agent
    decision = classify_email_recipients(agent, get_message_email_recipients(message))
    if decision.blocked_recipients:
        blocked = decision.blocked_recipients[0]
        raise OutboundEmailReviewError(
            f"Outbound email is disabled for contact '{blocked}'. Enable it in Contacts & Access first."
        )
    if not decision.unknown_external_recipients:
        return

    cap = get_user_max_contacts_per_agent(agent.user, organization=agent.organization)
    counts = get_agent_contact_counts(agent)
    if cap > 0 and counts is not None:
        available = max(cap - counts["total"], 0)
        if len(decision.unknown_external_recipients) > available:
            raise OutboundEmailReviewError(
                f"This agent has {available} of {cap} contact slots available."
            )

    for address in decision.unknown_external_recipients:
        contact, created = CommsAllowlistEntry.objects.get_or_create(
            agent=agent,
            channel=CommsChannel.EMAIL,
            address=address,
            defaults={
                "is_active": True,
                "allow_inbound": False,
                "allow_outbound": True,
                "can_configure": False,
            },
        )
        if not created and (not contact.is_active or not contact.allow_outbound):
            raise OutboundEmailReviewError(
                f"Outbound email is disabled for contact '{address}'. Enable it in Contacts & Access first."
            )


def validate_approved_external_contacts(message: PersistentAgentMessage) -> None:
    decision = classify_email_recipients(message.owner_agent, get_message_email_recipients(message))
    unavailable = decision.blocked_recipients or decision.unknown_external_recipients
    if unavailable:
        raise OutboundEmailReviewError(
            f"Outbound email is no longer authorized for contact '{unavailable[0]}'."
        )


def validate_message_recipients(message: PersistentAgentMessage) -> None:
    recipients = get_message_email_recipients(message)
    if not recipients:
        raise OutboundEmailReviewError("A valid recipient is required.")
    for address in recipients:
        try:
            validate_email(address)
        except ValidationError as exc:
            raise OutboundEmailReviewError(f"'{address}' is not a valid email address.") from exc


def replace_message_recipients(
    message: PersistentAgentMessage,
    *,
    to_address: str,
    cc_addresses,
) -> None:
    normalized_to = normalize_email_addresses([to_address])
    if not normalized_to:
        raise OutboundEmailReviewError("A valid To address is required.")
    normalized_cc = tuple(address for address in normalize_email_addresses(cc_addresses) if address != normalized_to[0])
    endpoints = {
        address: PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.EMAIL,
            address=address,
            defaults={"owner_agent": None},
        )[0]
        for address in (normalized_to[0], *normalized_cc)
    }
    old_to = get_message_contact_address(message)
    new_to = normalized_to[0]
    if old_to != new_to:
        conversation = _get_or_create_conversation(
            CommsChannel.EMAIL,
            new_to,
            owner_agent=message.owner_agent,
        )
        _ensure_participant(
            conversation,
            message.from_endpoint,
            PersistentAgentConversationParticipant.ParticipantRole.AGENT,
        )
        _ensure_participant(
            conversation,
            endpoints[new_to],
            PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL,
        )
        message.conversation = conversation
        message.to_endpoint = None
        message.parent = None
    message.cc_endpoints.set([endpoints[address] for address in normalized_cc])


def replace_message_attachments(message: PersistentAgentMessage, node_ids) -> None:
    if not isinstance(node_ids, list):
        raise OutboundEmailReviewError("attachmentNodeIds must be an array.")
    normalized_ids = list(dict.fromkeys(str(node_id) for node_id in node_ids))
    nodes = list(
        AgentFsNode.objects.alive().files().filter(
            id__in=normalized_ids,
            filespace__access__agent=message.owner_agent,
        )
    )
    if len(nodes) != len(normalized_ids):
        raise OutboundEmailReviewError("One or more attachments are unavailable to this agent.")
    nodes_by_id = {str(node.id): node for node in nodes}
    message.attachments.all().delete()
    for node_id in normalized_ids:
        node = nodes_by_id[node_id]
        PersistentAgentMessageAttachment.objects.create(
            message=message,
            file="",
            content_type=node.mime_type or "application/octet-stream",
            file_size=int(node.size_bytes or 0),
            filename=node.name,
            filespace_node=node,
        )
    snapshot_message_attachments(message)


@transaction.atomic
def update_pending_review_message(
    review: OutboundEmailReview,
    *,
    actor,
    expected_version: int,
    changes: dict[str, object],
) -> OutboundEmailReview:
    locked = _reviews_for_update().select_related("message").get(pk=review.pk)
    expire_review_if_needed(locked)
    if locked.status != OutboundEmailReview.Status.PENDING:
        raise OutboundEmailReviewError("This Outbox item can no longer be edited.")
    if locked.content_version != expected_version:
        raise StaleOutboxVersionError("stale_version")

    message = locked.message
    if "to" in changes or "cc" in changes:
        replace_message_recipients(
            message,
            to_address=str(changes.get("to", get_message_contact_address(message))),
            cc_addresses=changes.get("cc", message.cc_endpoints.values_list("address", flat=True)),
        )
    if "subject" in changes:
        payload = dict(message.raw_payload or {})
        payload["subject"] = str(changes["subject"] or "").strip()
        message.raw_payload = payload
    if "body" in changes:
        message.body = str(changes["body"] or "")
    if "attachmentNodeIds" in changes:
        replace_message_attachments(message, changes["attachmentNodeIds"])
    message.save()

    locked.content_version += 1
    locked.content_hash = compute_message_content_hash(message)
    locked.last_edited_at = timezone.now()
    locked.last_edited_by = actor
    locked.save(update_fields=["content_version", "content_hash", "last_edited_at", "last_edited_by", "updated_at"])
    _record_review_action(
        locked,
        actor=actor,
        action_type=PersistentAgentUserActionEvent.ActionType.OUTBOX_EDITED,
    )
    return locked


def _validate_agent_and_sender(message: PersistentAgentMessage) -> None:
    agent = message.owner_agent
    if not agent or agent.is_deleted or not agent.is_active:
        raise OutboundEmailReviewError("This agent is paused or unavailable.")
    try:
        require_verified_email(agent.user, action_description="approve emails")
    except EmailVerificationError as exc:
        raise OutboundEmailReviewError(str(exc)) from exc
    if message.from_endpoint.owner_agent_id != agent.id:
        raise OutboundEmailReviewError("The sender no longer belongs to this agent.")
    account = AgentEmailAccount.objects.filter(endpoint=message.from_endpoint).first()
    if account is not None and not account.is_outbound_enabled:
        raise OutboundEmailReviewError("The connected sender is no longer enabled.")


def _validate_reply_target(message: PersistentAgentMessage) -> None:
    if message.parent_id is None:
        return
    parent = message.parent
    if parent is None or parent.owner_agent_id != message.owner_agent_id:
        raise OutboundEmailReviewError("The reply target is no longer valid.")
    if get_message_contact_address(parent) != get_message_contact_address(message):
        raise OutboundEmailReviewError("The reply target no longer matches the primary recipient.")


def _record_review_action(review: OutboundEmailReview, *, actor, action_type: str) -> None:
    PersistentAgentUserActionEvent.objects.create(
        agent=review.agent,
        actor_user=actor,
        action_type=action_type,
        metadata={"outboxItemId": str(review.id), "messageId": str(review.message_id)},
    )
    analytics_events = {
        PersistentAgentUserActionEvent.ActionType.OUTBOX_EDITED: AnalyticsEvent.OUTBOX_EMAIL_EDITED,
        PersistentAgentUserActionEvent.ActionType.OUTBOX_APPROVED: AnalyticsEvent.OUTBOX_EMAIL_APPROVED,
        PersistentAgentUserActionEvent.ActionType.OUTBOX_DISCARDED: AnalyticsEvent.OUTBOX_EMAIL_DISCARDED,
        PersistentAgentUserActionEvent.ActionType.OUTBOX_RETRIED: AnalyticsEvent.OUTBOX_EMAIL_RETRIED,
    }
    event = analytics_events.get(action_type)
    if event is not None:
        properties = {}
        if event == AnalyticsEvent.OUTBOX_EMAIL_APPROVED:
            properties["approval_latency_seconds"] = max(
                int((timezone.now() - review.queued_at).total_seconds()),
                0,
            )
        track_review_event(review, event, actor=actor, properties=properties)


@transaction.atomic
def approve_review(
    review: OutboundEmailReview,
    *,
    actor,
    expected_version: int,
    changes: dict[str, object] | None = None,
    acknowledge_thread_changed: bool = False,
) -> OutboundEmailReview:
    locked = _reviews_for_update().select_related(
        "message__from_endpoint",
        "message__owner_agent__user",
        "message__owner_agent__organization",
        "message__parent",
    ).get(pk=review.pk)
    expire_review_if_needed(locked)
    if locked.status != OutboundEmailReview.Status.PENDING:
        raise OutboundEmailReviewError("This Outbox item can no longer be approved.")
    if locked.content_version != expected_version:
        raise StaleOutboxVersionError("stale_version")
    if changes:
        locked = update_pending_review_message(
            locked,
            actor=actor,
            expected_version=expected_version,
            changes=changes,
        )

    message = locked.message
    _validate_agent_and_sender(message)
    validate_message_recipients(message)
    _validate_reply_target(message)
    verify_snapshot_attachments(message)
    current_hash = compute_message_content_hash(message)
    if current_hash != locked.content_hash:
        raise OutboundEmailReviewError("The email changed outside the Outbox editor and must be reviewed again.")
    if review_thread_changed(locked) and not acknowledge_thread_changed:
        raise OutboundEmailReviewError("thread_changed")
    authorize_reviewed_external_contacts(message)

    now = timezone.now()
    locked.status = OutboundEmailReview.Status.APPROVED
    locked.approved_version = locked.content_version
    locked.approved_content_hash = locked.content_hash
    locked.decided_at = now
    locked.decided_by = actor
    locked.save(
        update_fields=[
            "status",
            "approved_version",
            "approved_content_hash",
            "decided_at",
            "decided_by",
            "updated_at",
        ]
    )
    message.latest_status = DeliveryStatus.QUEUED
    message.latest_error_code = ""
    message.latest_error_message = ""
    message.save(update_fields=["latest_status", "latest_error_code", "latest_error_message"])
    _record_review_action(
        locked,
        actor=actor,
        action_type=PersistentAgentUserActionEvent.ActionType.OUTBOX_APPROVED,
    )

    from api.tasks.outbox import dispatch_approved_outbox_email

    transaction.on_commit(lambda: dispatch_approved_outbox_email.delay(str(locked.id)))
    return locked


@transaction.atomic
def discard_review(review: OutboundEmailReview, *, actor, expected_version: int) -> OutboundEmailReview:
    locked = _reviews_for_update().get(pk=review.pk)
    expire_review_if_needed(locked)
    if locked.status != OutboundEmailReview.Status.PENDING:
        raise OutboundEmailReviewError("This Outbox item can no longer be discarded.")
    if locked.content_version != expected_version:
        raise StaleOutboxVersionError("stale_version")
    locked.status = OutboundEmailReview.Status.DISCARDED
    locked.decided_at = timezone.now()
    locked.decided_by = actor
    locked.save(update_fields=["status", "decided_at", "decided_by", "updated_at"])
    _record_review_action(
        locked,
        actor=actor,
        action_type=PersistentAgentUserActionEvent.ActionType.OUTBOX_DISCARDED,
    )
    return locked


@transaction.atomic
def retry_review(review: OutboundEmailReview, *, actor) -> OutboundEmailReview:
    locked = _reviews_for_update().select_related("message").get(pk=review.pk)
    if locked.status != OutboundEmailReview.Status.APPROVED:
        raise OutboundEmailReviewError("Only an approved Outbox item can be retried.")
    message = locked.message
    if message.latest_status != DeliveryStatus.FAILED:
        raise OutboundEmailReviewError("Only a failed delivery can be retried.")
    _validate_agent_and_sender(message)
    validate_message_recipients(message)
    validate_approved_external_contacts(message)
    verify_snapshot_attachments(message)
    if compute_message_content_hash(message) != locked.approved_content_hash:
        raise OutboundEmailReviewError("The approved email changed and cannot be retried.")
    message.latest_status = DeliveryStatus.QUEUED
    message.latest_error_code = ""
    message.latest_error_message = ""
    message.save(update_fields=["latest_status", "latest_error_code", "latest_error_message"])
    _record_review_action(
        locked,
        actor=actor,
        action_type=PersistentAgentUserActionEvent.ActionType.OUTBOX_RETRIED,
    )

    from api.tasks.outbox import dispatch_approved_outbox_email

    transaction.on_commit(lambda: dispatch_approved_outbox_email.delay(str(locked.id)))
    return locked
