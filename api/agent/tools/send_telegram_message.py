"""Agent tool for sending Telegram messages through an agent managed bot."""

import logging
from typing import Any, Dict

import requests
from django.core.exceptions import ObjectDoesNotExist, ValidationError

from api.agent.files.attachment_helpers import AttachmentResolutionError, resolve_filespace_attachments
from api.agent.tools.attachment_guidance import SEND_TOOL_ATTACHMENTS_DESCRIPTION
from api.models import PersistentAgent
from api.services.telegram_bot import TelegramBotIntegrationError, send_chat_message

logger = logging.getLogger(__name__)


def get_send_telegram_message_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "send_telegram_message",
            "description": (
                "Send a message to a Telegram chat that has already talked to this agent's managed Telegram bot. "
                "Use telegram_chats first when the target chat is unclear."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_binding_id": {
                        "type": "string",
                        "description": "Known Telegram chat binding ID from telegram_chats.",
                    },
                    "chat_id": {
                        "type": "string",
                        "description": "Telegram chat ID. Prefer chat_binding_id when available.",
                    },
                    "message_thread_id": {
                        "type": "string",
                        "description": "Optional Telegram forum topic/thread ID.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message body to send. Do not pass placeholders or tool-call/XML syntax; it is sent literally.",
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
                "required": ["message", "will_continue_work"],
            },
        },
    }


def execute_send_telegram_message(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    body = str(params.get("message") or "").strip()
    if not body:
        return {"status": "error", "message": "message is required."}
    try:
        resolved_attachments = resolve_filespace_attachments(agent, params.get("attachments"))
    except AttachmentResolutionError as exc:
        return {"status": "error", "message": str(exc)}
    try:
        message = send_chat_message(
            agent,
            chat_binding_id=str(params.get("chat_binding_id") or "").strip(),
            chat_id=str(params.get("chat_id") or "").strip(),
            message_thread_id=str(params.get("message_thread_id") or "").strip(),
            body=body,
            attachments=resolved_attachments,
        )
        result: dict[str, Any] = {
            "status": "success",
            "message_id": str(message.id),
            "telegram_message_id": str((message.raw_payload or {}).get("telegram_message_id") or ""),
            "attachment_count": len(resolved_attachments),
        }
        if params.get("will_continue_work") is False:
            result["auto_sleep_ok"] = True
        return result
    except ObjectDoesNotExist:
        return {"status": "error", "message": "No active Telegram chat was found for this agent."}
    except (TelegramBotIntegrationError, ValidationError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    except requests.RequestException as exc:
        logger.warning("Native Telegram send failed for agent %s: %s", agent.id, exc)
        return {"status": "error", "message": f"Telegram request failed: {exc}"}
