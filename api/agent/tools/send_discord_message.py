"""Agent tool for sending Discord messages through the native Gobii bot."""

import logging
from typing import Any, Dict

import requests
from django.core.exceptions import ObjectDoesNotExist, ValidationError

from api.agent.files.attachment_helpers import AttachmentResolutionError, resolve_filespace_attachments
from api.agent.comms.outbound_content_policy import markdown_only_error
from api.agent.tools.attachment_guidance import SEND_TOOL_ATTACHMENTS_DESCRIPTION
from api.agent.tools.agent_variables import substitute_variables_with_filespace
from api.agent.core.link_references import handle_link_reference_errors
from api.models import PersistentAgent
from api.services.discord_bot import DiscordBotIntegrationError, send_channel_message

logger = logging.getLogger(__name__)


def get_send_discord_message_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "send_discord_message",
            "description": (
                "Send this agent's requested, owned contribution to a subscribed Discord channel. Include others' work "
                "only when this agent's charter or request owns the aggregation, and attribute it; separate assignments "
                "are not synthesis."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {
                        "type": "string",
                        "description": "Subscribed Discord channel ID.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message body to send. Optional when attachments are provided. For reports, use Markdown sections, bullets/tables, status labels, tasteful emoji labels. "
                                       "Use Markdown only; raw HTML is rejected. Use code formatting to show HTML literally. "
                                       "Do not pass tool-call/XML syntax; it is sent literally.",
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": SEND_TOOL_ATTACHMENTS_DESCRIPTION,
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. true = you'll take another action, false = you're done.",
                    },
                },
                "required": ["channel_id", "will_continue_work"],
            },
        },
    }


@handle_link_reference_errors
def execute_send_discord_message(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    channel_id = str(params.get("channel_id") or "").strip()
    body = str(params.get("message") or "").strip()
    attachment_paths = params.get("attachments")
    if not channel_id:
        return {"status": "error", "message": "channel_id is required."}
    body = substitute_variables_with_filespace(body, agent)
    if content_error := markdown_only_error(body, surface="Discord"):
        return content_error
    try:
        resolved_attachments = resolve_filespace_attachments(agent, attachment_paths)
    except AttachmentResolutionError as exc:
        return {"status": "error", "message": str(exc)}
    if not body and not resolved_attachments:
        return {"status": "error", "message": "message is required when attachments is empty."}
    try:
        message = send_channel_message(
            agent,
            channel_id=channel_id,
            body=body,
            attachments=resolved_attachments,
        )
        result: dict[str, Any] = {
            "status": "success",
            "message_id": str(message.id),
            "discord_message_id": str((message.raw_payload or {}).get("discord_message_id") or ""),
            "channel_id": channel_id,
            "attachment_count": len(resolved_attachments),
        }
        if params.get("will_continue_work") is False:
            result["auto_sleep_ok"] = True
        return result
    except ObjectDoesNotExist:
        return {"status": "error", "message": "No active native Discord subscription was found for that channel."}
    except (DiscordBotIntegrationError, ValidationError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    except requests.RequestException as exc:
        logger.warning("Native Discord send failed for agent %s: %s", agent.id, exc)
        return {"status": "error", "message": f"Discord request failed: {exc}"}
