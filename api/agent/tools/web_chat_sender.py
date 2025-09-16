"""Web chat sender tool for persistent agents."""

from __future__ import annotations

from typing import Any, Dict

from django.conf import settings
from django.utils import timezone

from ..comms.message_service import _get_or_create_conversation, _ensure_participant
from ...models import (
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
    DeliveryStatus,
    CommsChannel,
    build_web_agent_address,
)


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
                },
                "required": ["body"],
            },
        },
    }


def execute_send_chat_message(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Persist an outbound web chat message for an agent."""

    raw_body = params.get("body", "")
    body = (raw_body or "").strip()
    if not body:
        return {"status": "error", "message": "Message body is required."}

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

    message = PersistentAgentMessage.objects.create(
        owner_agent=agent,
        from_endpoint=agent_endpoint,
        conversation=conversation,
        is_outbound=True,
        body=body,
        raw_payload={"source": "web_chat_tool"},
    )

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
    }


def _ensure_agent_web_endpoint(agent: PersistentAgent) -> PersistentAgentCommsEndpoint:
    """Ensure the agent has a dedicated web chat endpoint."""

    address = build_web_agent_address(agent.id)
    endpoint, created = PersistentAgentCommsEndpoint.objects.get_or_create(
        owner_agent=agent,
        channel=CommsChannel.WEB,
        defaults={
            "address": address,
            "is_primary": agent.preferred_contact_endpoint_id is not None
            and agent.preferred_contact_endpoint.channel == CommsChannel.WEB,
        },
    )

    if not endpoint.address:
        endpoint.address = address
        endpoint.save(update_fields=["address"])

    return endpoint


def _ensure_user_web_endpoint(address: str) -> PersistentAgentCommsEndpoint:
    """Ensure an external participant endpoint exists for the given web chat address."""

    endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=CommsChannel.WEB,
        address=address,
        defaults={"owner_agent": None},
    )
    return endpoint
