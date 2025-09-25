"""Tool to let persistent agents send messages via the console web chat."""
from __future__ import annotations

from typing import Any, Dict

from django.contrib.auth import get_user_model
from django.utils import timezone

from api.models import (
    CommsChannel,
    DeliveryStatus,
    PersistentAgent,
    PersistentAgentConversation,
    PersistentAgentMessage,
    parse_web_address,
)
from api.agent.comms.message_service import _ensure_web_channel_context
from api.services.web_sessions import get_active_web_session


def get_send_web_message_tool() -> Dict[str, Any]:
    """Tool schema exposed to the LLM for web chat responses."""

    return {
        "type": "function",
        "function": {
            "name": "send_web_message",
            "description": (
                "Send a reply through the Gobii console web chat. "
                "Use this when you want to message a human chatting with you in the console. "
                "Multiple humans can share the same agent, so you MUST target the correct user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": (
                            "UUID of the console user you are messaging. "
                            "This is available on inbound messages as raw_payload.user_id."
                        ),
                    },
                    "conversation_address": {
                        "type": "string",
                        "description": (
                            "Optional fallback: the web conversation address (e.g., 'web:user:<uuid>'). "
                            "Use only if user_id is not available."
                        ),
                    },
                    "subject": {
                        "type": "string",
                        "description": "Optional subject line for the web message.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Markdown body of the message to send.",
                    },
                },
                "required": ["body"],
                "additionalProperties": False,
            },
        },
    }


def _resolve_user_from_params(agent: PersistentAgent, params: Dict[str, Any]):
    """Resolve the console user instance using the provided parameters."""

    User = get_user_model()
    user = None
    user_id = params.get("user_id")

    if user_id:
        parsed = parse_web_address(user_id) if isinstance(user_id, str) else None
        if parsed and parsed[0] == "user":
            user_id_value = parsed[1]
        else:
            user_id_value = user_id
        try:
            user = User.objects.get(id=user_id_value)
        except User.DoesNotExist as exc:  # pragma: no cover - defensive logging higher level
            raise ValueError(f"No console user found with id '{user_id}'.") from exc

    elif params.get("conversation_address"):
        address = params["conversation_address"].strip()
        conv = PersistentAgentConversation.objects.filter(
            owner_agent=agent,
            channel=CommsChannel.WEB,
            address=address,
        ).first()

        parsed = parse_web_address(address) if address else None
        parsed_user_id = parsed[1] if parsed and parsed[0] == "user" else None

        if parsed_user_id:
            try:
                user = User.objects.get(id=parsed_user_id)
            except User.DoesNotExist:  # pragma: no cover - defensive fallback
                user = None

        if user is None and conv and conv.display_name:
            # Defensive: fall back to matching by display name email/username when available
            lookup_fields = {"email": conv.display_name}
            try:
                user = User.objects.filter(**lookup_fields).first()
            except Exception:  # pragma: no cover - defensive fallback
                user = None

        if user is None:
            raise ValueError(
                "Unable to resolve the console user for conversation_address. "
                "Provide user_id instead."
            )

    else:
        raise ValueError("You must provide either user_id or conversation_address.")

    return user


def execute_send_web_message(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Send a web message to a specific console user."""

    body = (params.get("body") or "").strip()
    if not body:
        raise ValueError("Message body cannot be empty.")

    subject = (params.get("subject") or "").strip() or None

    user = _resolve_user_from_params(agent, params)

    session = get_active_web_session(agent, user)
    if session is None:
        raise ValueError(
            "The user is not currently active in the web console. "
            "Ask them to reopen the workspace or use another approved contact channel."
        )

    agent_ep, user_ep, conversation = _ensure_web_channel_context(agent, user)

    now = timezone.now()
    payload = {
        "channel": CommsChannel.WEB,
        "source": "agent",
        "user_id": str(user.id),
    }
    if subject:
        payload["subject"] = subject

    message = PersistentAgentMessage.objects.create(
        owner_agent=agent,
        from_endpoint=agent_ep,
        conversation=conversation,
        is_outbound=True,
        body=body,
        raw_payload=payload,
        latest_status=DeliveryStatus.DELIVERED,
        latest_sent_at=now,
        latest_delivered_at=now,
        latest_error_message="",
    )

    return {
        "status": "sent",
        "message_id": str(message.id),
        "conversation_id": str(conversation.id),
        "user_id": str(user.id),
        "conversation_address": conversation.address,
    }
