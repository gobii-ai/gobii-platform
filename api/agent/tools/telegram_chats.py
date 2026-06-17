"""Agent tool for native Telegram managed bot chat bindings."""

import logging
from typing import Any, Dict

from django.core.exceptions import ObjectDoesNotExist, ValidationError

from api.models import PersistentAgent
from api.services.telegram_bot import (
    TelegramBotIntegrationError,
    active_telegram_identity,
    disable_chat_binding,
    list_chat_bindings,
    telegram_setup_required_response,
)

logger = logging.getLogger(__name__)


def get_telegram_chats_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "telegram_chats",
            "description": (
                "Inspect and manage native Telegram managed-bot chats for this agent. "
                "Use this before sending Telegram messages or when asked whether Telegram is connected. "
                "V1 supports DMs and group commands, mentions, or replies delivered to this agent bot."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "list", "disable"],
                        "description": "Operation to perform.",
                    },
                    "chat_binding_id": {
                        "type": "string",
                        "description": "Telegram chat binding ID to disable.",
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. true = you'll take another action, false = you're done.",
                    },
                },
                "required": ["action", "will_continue_work"],
            },
        },
    }


def _result_with_sleep(result: dict[str, Any], params: Dict[str, Any]) -> dict[str, Any]:
    if params.get("will_continue_work") is False:
        result["auto_sleep_ok"] = True
    return result


def execute_telegram_chats(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    action = str(params.get("action") or "").strip().lower()
    try:
        identity = active_telegram_identity(agent)
        if action == "status":
            if identity is None:
                return _result_with_sleep(telegram_setup_required_response(agent), params)
            return _result_with_sleep(
                {
                    "status": "success",
                    "bot_username": identity.username,
                    "profile_sync_status": identity.profile_sync_status,
                    "chats": list_chat_bindings(agent),
                },
                params,
            )

        if action == "list":
            if identity is None:
                return _result_with_sleep(telegram_setup_required_response(agent), params)
            return _result_with_sleep({"status": "success", "chats": list_chat_bindings(agent)}, params)

        if action == "disable":
            chat_binding_id = str(params.get("chat_binding_id") or "").strip()
            if not chat_binding_id:
                return {"status": "error", "message": "chat_binding_id is required for disable."}
            return _result_with_sleep(
                {"status": "success", "chat": disable_chat_binding(agent, chat_binding_id)},
                params,
            )

        return {"status": "error", "message": "Unsupported action. Use status, list, or disable."}
    except ObjectDoesNotExist:
        return {"status": "error", "message": "Telegram bot or chat binding was not found for this agent."}
    except (TelegramBotIntegrationError, ValidationError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
