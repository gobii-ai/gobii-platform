"""Agent tool for sending Discord messages through the native Gobii bot."""

import logging
from typing import Any, Dict

import requests
from django.core.exceptions import ObjectDoesNotExist, ValidationError

from api.agent.files.attachment_helpers import AttachmentResolutionError, resolve_filespace_attachments
from api.agent.tools.attachment_guidance import SEND_TOOL_ATTACHMENTS_DESCRIPTION
from api.models import PersistentAgent
from api.services.discord_bot import DiscordBotIntegrationError, send_channel_message

logger = logging.getLogger(__name__)


def get_send_discord_message_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "send_discord_message",
            "description": (
                "Send a message to a Discord channel subscribed through the native Gobii Discord bot. "
                "The backend sends via a channel webhook using this agent's name and avatar."
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
                        "description": "Message body to send. Optional when attachments are provided. For reports, use Markdown sections, bullets/tables, status labels, tasteful emoji labels; "
                                       "preserve url/link/listing_url/detail_url item fields as clickable row labels or a Link column; source/feed URLs do not substitute for item links. "
                                       "Do not pass placeholders or tool-call/XML syntax; it is sent literally.",
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


def execute_send_discord_message(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    channel_id = str(params.get("channel_id") or "").strip()
    body = str(params.get("message") or "").strip()
    attachment_paths = params.get("attachments")
    if not channel_id:
        return {"status": "error", "message": "channel_id is required."}
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
