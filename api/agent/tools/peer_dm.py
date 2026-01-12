"""Peer agent direct messaging tool definition and execution."""
from __future__ import annotations

import logging
from typing import Any, Dict
from uuid import UUID

from ..peer_comm import (
    PeerMessagingDuplicateError,
    PeerMessagingError,
    PeerMessagingService,
)
from ...models import PersistentAgent

logger = logging.getLogger(__name__)


def _should_continue_work(params: Dict[str, Any]) -> bool:
    """Return True when the agent declares more work coming after this DM."""
    raw = params.get("will_continue_work")
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        return normalized in {"1", "true", "yes"}
    return bool(raw)


def get_send_agent_message_tool() -> Dict[str, Any]:
    """Return the tool schema exposed to the LLM."""
    return {
        "type": "function",
        "function": {
            "name": "send_agent_message",
            "description": (
                "Send a concise direct message to another agent that shares a peer link with you. "
                "Use this to coordinate tasks with partner agents. Keep messages focused and avoid loops."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "peer_agent_id": {
                        "type": "string",
                        "description": "UUID of the linked agent you want to contact.",
                    },
                    "message": {
                        "type": "string",
                        "description": "The body of the message to send. Keep it brief and actionable.",
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. Set false to STOP when: all kanban cards are done AND you've finished coordination. Set true only if you have more work after this message. Omitting this wastes credits.",
                    },
                },
                "required": ["peer_agent_id", "message"],
            },
        },
    }


def execute_send_agent_message(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the peer messaging tool for the active agent."""
    peer_agent_id_raw = params.get("peer_agent_id")
    message = params.get("message")
    will_continue = _should_continue_work(params)

    if not peer_agent_id_raw or not message:
        return {
            "status": "error",
            "message": "Parameters 'peer_agent_id' and 'message' are required.",
        }

    try:
        peer_agent_uuid = UUID(str(peer_agent_id_raw))
    except ValueError:
        return {
            "status": "error",
            "message": "peer_agent_id must be a valid UUID.",
        }

    if peer_agent_uuid == agent.id:
        return {
            "status": "error",
            "message": "Cannot send a peer message to the same agent.",
        }

    try:
        peer_agent = PersistentAgent.objects.get(id=peer_agent_uuid)
    except PersistentAgent.DoesNotExist:
        logger.info(
            "Peer DM target not found: sender=%s target=%s",
            agent.id,
            peer_agent_uuid,
        )
        return {
            "status": "error",
            "message": "Target agent not found or inaccessible.",
        }

    service = PeerMessagingService(agent, peer_agent)

    try:
        result = service.send_message(message)
    except PeerMessagingDuplicateError as exc:
        response = dict(exc.duplicate_response)
        return response
    except PeerMessagingError as exc:
        response: Dict[str, Any] = {
            "status": exc.status,
            "message": str(exc),
        }
        if exc.retry_at:
            response["retry_at_iso"] = exc.retry_at.isoformat()
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception(
            "Unexpected peer DM failure sender=%s target=%s", agent.id, peer_agent.id
        )
        return {
            "status": "error",
            "message": "Peer messaging failed unexpectedly.",
        }

    payload: Dict[str, Any] = {
        "status": result.status,
        "message": result.message,
        "remaining_credits": result.remaining_credits,
    }
    if result.window_reset_at:
        payload["window_reset_at_iso"] = result.window_reset_at.isoformat()
    if result.status.lower() == "ok":
        payload["auto_sleep_ok"] = not will_continue
    return payload
