"""Web chat sender tool for persistent agents."""

from __future__ import annotations

from typing import Any, Dict

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from ..comms.message_service import _get_or_create_conversation, _ensure_participant
from ..files.attachment_helpers import (
    AttachmentResolutionError,
    create_message_attachments,
    resolve_filespace_attachments,
)
from ..files.filespace_service import broadcast_message_attachment_update
from util.text_sanitizer import normalize_whitespace
from ...models import (
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
    DeliveryStatus,
    CommsChannel,
    build_web_agent_address,
    parse_web_user_address,
)
from ...services.web_sessions import get_active_web_session
from .outbound_duplicate_guard import detect_recent_duplicate_message


def _should_continue_work(params: Dict[str, Any]) -> bool:
    """Return True if the agent indicates more work right after this chat message."""
    raw = params.get("will_continue_work")
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        return normalized in {"1", "true", "yes"}
    return bool(raw)


def get_send_chat_tool() -> Dict[str, Any]:
    """Definition for the send_chat_message tool exposed to the agent."""

    return {
        "type": "function",
        "function": {
            "name": "send_chat_message",
            "description": (
                "Send a short, conversational response to the user via Gobii's in-console web chat. "
                "Use this for quick updates, follow-up questions, or sharing results in real time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "body": {
                        "type": "string",
                        "description": "Plaintext message content to deliver in chat.",
                    },
                    "to_address": {
                        "type": "string",
                        "description": (
                            "Optional web chat address for the recipient (e.g. 'web://user/123/agent/<agent_id>'). "
                            "If omitted, the agent will reply to the latest active chat participant or preferred web contact."
                        ),
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of filespace paths from the agent's default filespace to include.",
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "Set true when this is just a quick update and you'll keep working immediately.",
                    },
                },
                "required": ["body"],
            },
        },
    }


def execute_send_chat_message(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Persist an outbound web chat message for an agent."""

    raw_body = params.get("body", "")
    # Normalize whitespace: collapse excessive newlines, strip trailing spaces
    body = normalize_whitespace((raw_body or "").strip())
    if not body:
        return {"status": "error", "message": "Message body is required."}
    will_continue = _should_continue_work(params)
    attachment_paths = params.get("attachments")
    try:
        resolved_attachments = resolve_filespace_attachments(agent, attachment_paths)
    except AttachmentResolutionError as exc:
        return {"status": "error", "message": str(exc)}

    max_len = getattr(settings, "WEB_CHAT_MESSAGE_MAX_LENGTH", 4000)
    if len(body) > max_len:
        return {
            "status": "error",
            "message": f"Chat message exceeds maximum length of {max_len} characters.",
        }

    to_address = (params.get("to_address") or "").strip()

    if not to_address:
        # Prefer explicit preferred endpoint configured for web chat
        if agent.preferred_contact_endpoint and agent.preferred_contact_endpoint.channel == CommsChannel.WEB:
            to_address = agent.preferred_contact_endpoint.address
        else:
            latest_conversation = agent.owned_conversations.filter(channel=CommsChannel.WEB).order_by("-id").first()
            if latest_conversation:
                to_address = latest_conversation.address

    if not to_address:
        return {
            "status": "error",
            "message": "No eligible web chat recipient found. Provide 'to_address'.",
        }

    user_id, agent_id = parse_web_user_address(to_address)
    if agent_id != str(agent.id) or user_id is None:
        return {
            "status": "error",
            "message": "Recipient address is not valid for this agent.",
        }

    # Check if this is a normal user interaction or a test/eval interaction
    is_eval_mode = (agent.execution_environment == "eval")

    if not is_eval_mode:
        User = get_user_model()
        try:
            recipient_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            recipient_user = None

        if not recipient_user or get_active_web_session(agent, recipient_user) is None:
            return {
                "status": "error",
                "message": (
                    "No active web chat session exists for this user. Retry using the user's most recently "
                    "active non-web communication channel (e.g., email or SMS)."
                ),
            }

        if not agent.is_recipient_whitelisted(CommsChannel.WEB, to_address):
            return {
                "status": "error",
                "message": "Recipient is not authorized for web chat with this agent.",
            }

    agent_endpoint = _ensure_agent_web_endpoint(agent)
    user_endpoint = _ensure_user_web_endpoint(to_address)

    conversation = _get_or_create_conversation(CommsChannel.WEB, to_address, owner_agent=agent)
    _ensure_participant(
        conversation,
        agent_endpoint,
        PersistentAgentConversationParticipant.ParticipantRole.AGENT,
    )
    _ensure_participant(
        conversation,
        user_endpoint,
        PersistentAgentConversationParticipant.ParticipantRole.HUMAN_USER,
    )

    duplicate = detect_recent_duplicate_message(
        agent,
        channel=CommsChannel.WEB,
        body=body,
        conversation_id=conversation.id,
    )
    if duplicate:
        return duplicate.to_error_response()

    message = PersistentAgentMessage.objects.create(
        owner_agent=agent,
        from_endpoint=agent_endpoint,
        to_endpoint=user_endpoint,
        conversation=conversation,
        is_outbound=True,
        body=body,
        raw_payload={"source": "web_chat_tool"},
    )
    if resolved_attachments:
        create_message_attachments(message, resolved_attachments)
        broadcast_message_attachment_update(str(message.id))

    now = timezone.now()
    PersistentAgentMessage.objects.filter(pk=message.pk).update(
        latest_status=DeliveryStatus.DELIVERED,
        latest_sent_at=now,
        latest_delivered_at=now,
        latest_error_code="",
        latest_error_message="",
    )

    return {
        "status": "ok",
        "message": f"Web chat message sent to {to_address}",
        "message_id": str(message.id),
        "auto_sleep_ok": not will_continue,
    }


def _ensure_agent_web_endpoint(agent: PersistentAgent) -> PersistentAgentCommsEndpoint:
    """Ensure the agent has a dedicated web chat endpoint."""

    address = build_web_agent_address(agent.id)
    endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
        owner_agent=agent,
        channel=CommsChannel.WEB,
        address=address,
        defaults={
            "is_primary": bool(
                agent.preferred_contact_endpoint
                and agent.preferred_contact_endpoint.channel == CommsChannel.WEB
            ),
        },
    )

    return endpoint


def _ensure_user_web_endpoint(address: str) -> PersistentAgentCommsEndpoint:
    """Ensure an external participant endpoint exists for the given web chat address."""

    normalized = (address or "").strip()
    endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=CommsChannel.WEB,
        address=normalized,
        defaults={"owner_agent": None},
    )
    return endpoint
