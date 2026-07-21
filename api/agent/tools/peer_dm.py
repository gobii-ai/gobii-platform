"""Peer agent direct messaging tool definition and execution."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict
from uuid import UUID

from ..files.attachment_helpers import AttachmentResolutionError, resolve_filespace_attachments
from .attachment_guidance import SEND_TOOL_ATTACHMENTS_DESCRIPTION
from ..peer_comm import PeerMessagingDuplicateError, PeerMessagingError, PeerMessagingService
from ...models import PersistentAgent, PersistentAgentMessage
from .agent_variables import substitute_variables_with_filespace
from api.agent.core.link_references import handle_link_reference_errors

logger = logging.getLogger(__name__)


RETRYABLE_PEER_MESSAGE_STATUSES = {"debounced", "throttled"}

_NO_ACTION_PEER_UPDATE_PHRASES = (
    "i'll own it from here",
    "i will own it from here",
    "no action needed",
    "no change",
    "no follow-up needed",
    "no new signal",
    "nothing new",
)
_ACKNOWLEDGMENT_PREFIX_RE = re.compile(
    r"^(?:thanks\b|thank you\b|noted\b|received\b|got it\b|acknowledged\b|understood\b|"
    r"sounds good\b|all yours\b|no (?:\w+ )?action (?:is )?needed\b)",
)
_SUBSTANTIVE_REPLY_RE = re.compile(
    r"\b(?:attached|completed|created|fixed|found|logged|merged|recorded|sent|updated|"
    r"(?:i'll|will) (?:attach|complete|create|fix|log|merge|record|send|update))\b",
)
_SUBSTANTIVE_REPLY_PATTERN_RE = re.compile(
    r"\?|https?://|\b(?:actually|but|correction|error|however|mismatch|missing|please)\b|"
    r"\bneed you\b",
)


def _normalize_peer_message(body: str) -> str:
    return " ".join(str(body or "").replace("’", "'").lower().split())


def _is_no_action_peer_update(body: str) -> bool:
    normalized = _normalize_peer_message(body)
    return any(phrase in normalized for phrase in _NO_ACTION_PEER_UPDATE_PHRASES)


def _is_acknowledgment_only_peer_reply(body: str) -> bool:
    normalized = _normalize_peer_message(body)
    if not _ACKNOWLEDGMENT_PREFIX_RE.match(normalized):
        return False
    if _SUBSTANTIVE_REPLY_RE.search(normalized):
        return False
    return not _SUBSTANTIVE_REPLY_PATTERN_RE.search(normalized)


def _should_suppress_peer_acknowledgment(inbound_body: str, outbound_body: str) -> bool:
    return _is_no_action_peer_update(inbound_body) and _is_acknowledgment_only_peer_reply(outbound_body)


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
                "Send only a necessary charter-boundary handoff or substantive peer-work update. Never relay a "
                "shared-channel request to people already there, or send thanks, receipts, 'noted', or FYI acknowledgments. "
                "For repeats, send with will_continue_work=true first and await its result; rapid same-peer "
                "messages may be debounced."
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
                        "description": "New information, question, or handoff the peer needs; never an acknowledgment-only reply. Use provided link references unchanged; leave items without one unlinked.",
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": SEND_TOOL_ATTACHMENTS_DESCRIPTION,
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. true = you'll take another action, false = you're done. Omitting this stops you for good—choose wisely.",
                    },
                },
                "required": ["peer_agent_id", "message", "will_continue_work"],
            },
        },
    }


@handle_link_reference_errors
def execute_send_agent_message(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the peer messaging tool for the active agent."""
    peer_agent_id_raw = params.get("peer_agent_id")
    message = params.get("message")
    will_continue = _should_continue_work(params)
    attachment_paths = params.get("attachments")

    if not peer_agent_id_raw or not message:
        return {
            "status": "error",
            "message": "Parameters 'peer_agent_id' and 'message' are required.",
        }

    message = substitute_variables_with_filespace(str(message), agent)

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

    try:
        resolved_attachments = resolve_filespace_attachments(agent, attachment_paths)
    except AttachmentResolutionError as exc:
        return {
            "status": "error",
            "message": str(exc),
        }

    if not resolved_attachments and _is_acknowledgment_only_peer_reply(str(message)):
        latest_inbound = (
            PersistentAgentMessage.objects.filter(
                owner_agent=agent,
                peer_agent=peer_agent,
                is_outbound=False,
                conversation__is_peer_dm=True,
            )
            .order_by("-timestamp", "-id")
            .first()
        )
        if latest_inbound is not None and _is_no_action_peer_update(latest_inbound.body or ""):
            return {
                "status": "suppressed",
                "message": "Acknowledgment-only peer reply suppressed because the latest update requires no action.",
                "auto_sleep_ok": not will_continue,
            }

    service = PeerMessagingService(agent, peer_agent)

    try:
        result = service.send_message(message, attachments=resolved_attachments)
    except PeerMessagingDuplicateError as exc:
        response = dict(exc.duplicate_response)
        return response
    except PeerMessagingError as exc:
        status = str(exc.status or "error").lower()
        response: Dict[str, Any] = {
            "status": exc.status,
            "message": str(exc),
        }
        if exc.retry_at:
            response["retry_at_iso"] = exc.retry_at.isoformat()
        if status in RETRYABLE_PEER_MESSAGE_STATUSES:
            response["retryable"] = True
        return response

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
