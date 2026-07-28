"""MCP timeline reply tool for persistent agents."""

from typing import Any, Dict

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from api.agent.comms.outbound_content_policy import markdown_only_error
from api.agent.comms.routing import get_recent_mcp_inbound_message
from api.agent.core.link_references import handle_link_reference_errors
from api.agent.files.attachment_helpers import (
    AttachmentResolutionError,
    create_message_attachments,
    resolve_filespace_attachments,
)
from api.agent.files.filespace_service import broadcast_message_attachment_update
from api.models import DeliveryStatus, PersistentAgent, PersistentAgentMessage
from util.text_sanitizer import normalize_llm_output

from .agent_variables import substitute_variables_with_filespace
from .attachment_guidance import SEND_TOOL_ATTACHMENTS_DESCRIPTION
from .outbound_duplicate_guard import detect_recent_duplicate_message
from .web_chat_sender import _ensure_agent_web_endpoint, _looks_like_placeholder_body, _looks_like_tool_call_markup


def _should_continue_work(params: Dict[str, Any]) -> bool:
    raw = params.get("will_continue_work")
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes"}
    return bool(raw)


def get_send_mcp_message_tool() -> Dict[str, Any]:
    """Return the MCP reply tool schema exposed while the agent has recent MCP activity."""

    return {
        "type": "function",
        "function": {
            "name": "send_mcp_message",
            "description": (
                "Append an MCP message to the agent's most recent MCP conversation. "
                "Use this for MCP questions, blockers, progress, and final results; ordinary tool results are not "
                "replies. It does not contact the owner or another human. Human-channel tools remain available as "
                "separate actions and require explicit authorization. If the MCP instruction forbids contact, make "
                "zero human-channel calls, including chat, email, SMS, Discord, or peer-agent sends. Never claim "
                "that no human contact occurred if this run made such a call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "body": {
                        "type": "string",
                        "description": (
                            "The MCP-facing message. Include the useful result directly; "
                            "tool results alone are not replies. "
                            "Markdown only; raw HTML is rejected."
                        ),
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": SEND_TOOL_ATTACHMENTS_DESCRIPTION,
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": (
                            "REQUIRED. true only if work for this MCP request remains after this message; "
                            "false when the response completes the request."
                        ),
                    },
                },
                "required": ["body", "will_continue_work"],
            },
        },
    }


@handle_link_reference_errors
def execute_send_mcp_message(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a reply to the most recent MCP-origin message without human delivery."""

    inbound = get_recent_mcp_inbound_message(agent)
    if inbound is None:
        return {
            "status": "error",
            "message": "No recent MCP conversation is available for this agent.",
            "retryable": False,
        }

    raw_body = params.get("body", "")
    body = normalize_llm_output((raw_body or "").strip())
    body = substitute_variables_with_filespace(body, agent)
    if not body:
        return {"status": "error", "message": "Message body is required.", "retryable": False}
    if content_error := markdown_only_error(body, surface="MCP"):
        return content_error
    if _looks_like_placeholder_body(body):
        return {
            "status": "error",
            "message": "Message body must contain actual MCP-facing content, not a schema placeholder.",
            "retryable": False,
        }
    if _looks_like_tool_call_markup(body):
        return {
            "status": "error",
            "message": "Message body must contain actual MCP-facing content, not raw tool-call markup.",
            "retryable": False,
        }

    max_len = settings.WEB_CHAT_MESSAGE_MAX_LENGTH
    if len(body) > max_len:
        return {
            "status": "error",
            "message": f"MCP message exceeds maximum length of {max_len} characters.",
            "retryable": False,
        }

    try:
        resolved_attachments = resolve_filespace_attachments(agent, params.get("attachments"))
    except AttachmentResolutionError as exc:
        return {"status": "error", "message": str(exc)}

    duplicate = detect_recent_duplicate_message(
        agent,
        channel=inbound.conversation.channel,
        body=body,
        conversation_id=inbound.conversation_id,
        source_kind="mcp",
    )
    if duplicate:
        return duplicate.to_error_response()

    agent_endpoint = _ensure_agent_web_endpoint(agent)
    now = timezone.now()
    with transaction.atomic():
        message = PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=agent_endpoint,
            to_endpoint=inbound.from_endpoint,
            conversation=inbound.conversation,
            parent=inbound,
            is_outbound=True,
            body=body,
            raw_payload={
                "source": "mcp_reply_tool",
                "source_kind": "mcp",
                "source_label": "Gobii MCP",
                "in_reply_to_message_id": str(inbound.id),
            },
            latest_status=DeliveryStatus.DELIVERED,
            latest_sent_at=now,
            latest_delivered_at=now,
        )
        if resolved_attachments:
            create_message_attachments(message, resolved_attachments)

    if resolved_attachments:
        broadcast_message_attachment_update(str(message.id))

    return {
        "status": "ok",
        "message": "MCP timeline reply recorded.",
        "message_id": str(message.id),
        "in_reply_to_message_id": str(inbound.id),
        "auto_sleep_ok": not _should_continue_work(params),
    }
