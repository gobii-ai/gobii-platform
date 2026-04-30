import uuid
from collections.abc import Iterable
from email.utils import parseaddr

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone

from api.models import (
    AgentCollaborator,
    CommsChannel,
    OrganizationMembership,
    OutboundMessageAttempt,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    PersistentAgentMessageRead,
    UserPhoneNumber,
    parse_web_user_address,
)

HIDE_IN_CHAT_PAYLOAD_KEY = "hide_in_chat"

READ_SOURCE_CHAT_OPEN = "chat_open"
READ_SOURCE_EMAIL_CLICK = "email_click"
READ_SOURCE_EMAIL_OPEN = "email_open"
READ_SOURCE_INBOUND_REPLY = "inbound_reply"


def visible_agent_message_filter() -> Q:
    hidden_key = f"raw_payload__{HIDE_IN_CHAT_PAYLOAD_KEY}"
    return Q(**{hidden_key: False}) | Q(**{f"{hidden_key}__isnull": True})


def peer_dm_message_filter() -> Q:
    return Q(peer_agent_id__isnull=False) | Q(conversation__is_peer_dm=True)


def is_peer_dm_message(message: PersistentAgentMessage | None) -> bool:
    if message is None:
        return False
    if getattr(message, "peer_agent_id", None):
        return True
    conversation = getattr(message, "conversation", None)
    return bool(conversation and getattr(conversation, "is_peer_dm", False))


def latest_visible_outbound_message_queryset():
    return (
        PersistentAgentMessage.objects
        .filter(is_outbound=True)
        .filter(visible_agent_message_filter())
        .exclude(peer_dm_message_filter())
        .order_by("-timestamp", "-seq")
    )


def build_latest_agent_message_read_state(
    agent_ids: Iterable[object],
    user: object,
) -> dict[str, dict[str, object]]:
    normalized_ids = [str(agent_id) for agent_id in agent_ids if agent_id]
    user_id = _user_id(user)
    if not normalized_ids:
        return {}

    latest = latest_visible_outbound_message_queryset().filter(owner_agent_id=OuterRef("pk"))
    rows = list(
        PersistentAgent.objects
        .filter(id__in=normalized_ids)
        .annotate(
            latest_agent_message_id=Subquery(latest.values("id")[:1]),
            latest_agent_message_at=Subquery(latest.values("timestamp")[:1]),
        )
        .values(
            "id",
            "latest_agent_message_id",
            "latest_agent_message_at",
        )
    )

    message_ids = [row["latest_agent_message_id"] for row in rows if row["latest_agent_message_id"]]
    reads_by_message_id: dict[str, object] = {}
    if user_id and message_ids:
        reads_by_message_id = {
            str(row["message_id"]): row["read_at"]
            for row in (
                PersistentAgentMessageRead.objects
                .filter(user_id=user_id, message_id__in=message_ids)
                .values("message_id", "read_at")
            )
        }

    result: dict[str, dict[str, object]] = {}
    for row in rows:
        message_id = row["latest_agent_message_id"]
        read_at = reads_by_message_id.get(str(message_id)) if message_id else None
        result[str(row["id"])] = {
            "latest_agent_message_id": str(message_id) if message_id else None,
            "latest_agent_message_at": row["latest_agent_message_at"],
            "latest_agent_message_read_at": read_at,
            "has_unread_agent_message": bool(message_id and read_at is None),
        }
    return result


def serialize_latest_agent_message_read_state(state: dict[str, object] | None) -> dict[str, object]:
    state = state or {}
    return {
        "has_unread_agent_message": bool(state.get("has_unread_agent_message")),
        "latest_agent_message_id": state.get("latest_agent_message_id"),
        "latest_agent_message_at": _isoformat_or_none(state.get("latest_agent_message_at")),
        "latest_agent_message_read_at": _isoformat_or_none(state.get("latest_agent_message_read_at")),
    }


def serialize_agent_message_read_state(agent: PersistentAgent, user: object) -> dict[str, object]:
    states = build_latest_agent_message_read_state([agent.id], user)
    return serialize_latest_agent_message_read_state(states.get(str(agent.id)))


def build_agent_message_read_state_for_users(
    agent: PersistentAgent,
    user_ids: Iterable[object],
) -> dict[int, dict[str, object]]:
    normalized_user_ids = sorted({user_id for user_id in (_user_id(user_id) for user_id in user_ids) if user_id})
    if not normalized_user_ids:
        return {}

    latest_message = (
        latest_visible_outbound_message_queryset()
        .filter(owner_agent=agent)
        .values("id", "timestamp")
        .first()
    )
    read_at_by_user_id: dict[int, object] = {}
    if latest_message is not None:
        read_at_by_user_id = {
            int(row["user_id"]): row["read_at"]
            for row in (
                PersistentAgentMessageRead.objects
                .filter(message_id=latest_message["id"], user_id__in=normalized_user_ids)
                .values("user_id", "read_at")
            )
        }

    result: dict[int, dict[str, object]] = {}
    for user_id in normalized_user_ids:
        read_at = read_at_by_user_id.get(user_id)
        message_id = latest_message["id"] if latest_message else None
        result[user_id] = {
            "latest_agent_message_id": str(message_id) if message_id else None,
            "latest_agent_message_at": latest_message["timestamp"] if latest_message else None,
            "latest_agent_message_read_at": read_at,
            "has_unread_agent_message": bool(message_id and read_at is None),
        }
    return result


def mark_message_read(message: PersistentAgentMessage | None, user: object, source: str) -> bool:
    user_id = _user_id(user)
    if message is None or not message.is_outbound or is_peer_dm_message(message) or not user_id:
        return False

    read, created = PersistentAgentMessageRead.objects.get_or_create(
        message=message,
        user_id=user_id,
        defaults={
            "read_at": timezone.now(),
            "read_source": source,
        },
    )
    if not created:
        return False

    _broadcast_agent_read_state_after_commit(message.owner_agent_id, read.user_id)
    return True


def mark_latest_visible_outbound_message_read(
    agent: PersistentAgent,
    user: object,
    source: str,
) -> PersistentAgentMessage | None:
    message = latest_visible_outbound_message_queryset().filter(owner_agent=agent).first()
    if message is None:
        return None
    mark_message_read(message, user, source)
    return message


def mark_latest_visible_outbound_message_read_before(
    *,
    agent_id: object,
    before,
    user: object,
    conversation_id: object = None,
    recipient_endpoint_id: object = None,
    source: str = READ_SOURCE_INBOUND_REPLY,
) -> PersistentAgentMessage | None:
    user_id = _user_id(user)
    if not agent_id or before is None or not user_id:
        return None
    thread_filter = Q()
    if conversation_id:
        thread_filter |= Q(conversation_id=conversation_id)
    if recipient_endpoint_id:
        thread_filter |= Q(to_endpoint_id=recipient_endpoint_id)
    if not thread_filter:
        return None

    message = (
        latest_visible_outbound_message_queryset()
        .filter(owner_agent_id=agent_id, timestamp__lt=before)
        .filter(thread_filter)
        .first()
    )
    if message is None:
        return None
    mark_message_read(message, user_id, source)
    return message


def resolve_inbound_read_user(agent: PersistentAgent | None, channel: CommsChannel | str, sender: str | None):
    if agent is None:
        return None

    channel_val = channel.value if isinstance(channel, CommsChannel) else str(channel)
    if channel_val == CommsChannel.WEB:
        sender_user_id, sender_agent_id = parse_web_user_address(sender or "")
        if sender_agent_id == str(agent.id) and agent._is_internal_responder_user_id(sender_user_id):
            return get_user_model().objects.filter(id=sender_user_id).first()
        return None

    if channel_val == CommsChannel.EMAIL:
        return _resolve_unique_user_for_email(agent, sender)

    if channel_val == CommsChannel.SMS:
        normalized_phone = PersistentAgentCommsEndpoint.normalize_address(
            channel_val,
            sender,
        )
        return _resolve_unique_user_for_phone(agent, normalized_phone)

    return None


def mark_postmark_event_message_read(payload: dict, source: str) -> PersistentAgentMessage | None:
    message = resolve_postmark_event_message(payload)
    if message is None:
        return None
    user = resolve_postmark_event_user(message, payload)
    if user is None:
        return message
    mark_message_read(message, user, source)
    return message


def resolve_postmark_event_message(payload: dict) -> PersistentAgentMessage | None:
    metadata = payload.get("Metadata") or payload.get("metadata")
    if isinstance(metadata, dict):
        message = _message_from_uuid(metadata.get("message_id"))
        if message is not None:
            return message

    message = _message_from_uuid(payload.get("message_id"))
    if message is not None:
        return message

    provider_message_id = payload.get("MessageID") or payload.get("MessageId")
    if not provider_message_id:
        return None

    attempt = (
        OutboundMessageAttempt.objects
        .select_related("message__owner_agent", "message__to_endpoint")
        .filter(provider_message_id=str(provider_message_id))
        .order_by("-queued_at")
        .first()
    )
    if attempt and attempt.message and attempt.message.is_outbound:
        return attempt.message
    return None


def resolve_postmark_event_user(message: PersistentAgentMessage, payload: dict):
    agent = message.owner_agent
    if agent is None:
        return None
    recipient = _postmark_recipient_address(payload)
    if not recipient and message.to_endpoint_id and message.to_endpoint.channel == CommsChannel.EMAIL:
        recipient = message.to_endpoint.address
    return _resolve_unique_user_for_email(agent, recipient)


def _message_from_uuid(raw_id: object) -> PersistentAgentMessage | None:
    if not raw_id:
        return None
    try:
        message_id = uuid.UUID(str(raw_id))
    except (TypeError, ValueError, AttributeError):
        return None
    return (
        PersistentAgentMessage.objects
        .select_related("owner_agent", "to_endpoint")
        .filter(id=message_id, is_outbound=True)
        .first()
    )


def _postmark_recipient_address(payload: dict) -> str | None:
    metadata = payload.get("Metadata") or payload.get("metadata")
    if isinstance(metadata, dict):
        for key in ("recipient", "recipient_email", "email", "to"):
            value = metadata.get(key)
            if value:
                return str(value)
    for key in ("Recipient", "Email", "OriginalRecipient"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _candidate_reader_user_ids(agent: PersistentAgent) -> set[int]:
    user_ids: set[int] = set()
    if agent.user_id:
        user_ids.add(agent.user_id)
    if agent.organization_id:
        user_ids.update(
            OrganizationMembership.objects.filter(
                org_id=agent.organization_id,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).values_list("user_id", flat=True)
        )
    user_ids.update(
        AgentCollaborator.objects.filter(agent_id=agent.id).values_list("user_id", flat=True)
    )
    return user_ids


def _resolve_unique_user_for_email(agent: PersistentAgent, address: str | None):
    email = (parseaddr(address or "")[1] or address or "").strip().lower()
    if not email:
        return None
    candidate_user_ids = _candidate_reader_user_ids(agent)
    if not candidate_user_ids:
        return None

    from allauth.account.models import EmailAddress

    user_ids = set(
        EmailAddress.objects.filter(
            user_id__in=candidate_user_ids,
            email__iexact=email,
            verified=True,
        ).values_list("user_id", flat=True)
    )
    return _unique_user_from_ids(user_ids)


def _resolve_unique_user_for_phone(agent: PersistentAgent, phone: str | None):
    normalized_phone = (phone or "").strip()
    if not normalized_phone:
        return None
    candidate_user_ids = _candidate_reader_user_ids(agent)
    if not candidate_user_ids:
        return None
    user_ids = set(
        UserPhoneNumber.objects.filter(
            user_id__in=candidate_user_ids,
            phone_number__iexact=normalized_phone,
            is_verified=True,
        ).values_list("user_id", flat=True)
    )
    return _unique_user_from_ids(user_ids)


def _unique_user_from_ids(user_ids: set[int]):
    if len(user_ids) != 1:
        return None
    return get_user_model().objects.filter(id=next(iter(user_ids))).first()


def _broadcast_agent_read_state_after_commit(agent_id: object, user_id: object) -> None:
    if not agent_id or not user_id:
        return

    def _broadcast():
        from console.agent_chat.signals import emit_agent_profile_update

        agent = PersistentAgent.objects.filter(id=agent_id).first()
        if agent is not None:
            emit_agent_profile_update(agent, user_ids={int(user_id)})

    transaction.on_commit(_broadcast)


def _user_id(user: object) -> int | None:
    if user is None:
        return None
    raw_id = getattr(user, "id", user)
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return None


def _isoformat_or_none(value: object) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None
