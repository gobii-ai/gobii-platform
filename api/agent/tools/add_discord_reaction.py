"""Agent tool for adding reactions through the native Gobii Discord bot."""

import logging
from typing import Any, Dict

import requests
from django.core.exceptions import ObjectDoesNotExist

from api.models import PersistentAgent
from api.services.discord_bot import DiscordBotIntegrationError, add_discord_reaction

logger = logging.getLogger(__name__)


def get_add_discord_reaction_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "add_discord_reaction",
            "description": (
                "Add one emoji reaction to a message in a Discord channel subscribed by this agent. "
                "Use the discord_message_id and channel ID from Discord message context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {
                        "type": "string",
                        "description": "Subscribed Discord channel ID.",
                    },
                    "message_id": {
                        "type": "string",
                        "description": "Discord message ID to react to.",
                    },
                    "emoji": {
                        "type": "string",
                        "description": (
                            "One Unicode emoji, or a Discord custom emoji formatted as name:id, <:name:id>, or <a:name:id>."
                        ),
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. true = you'll take another action, false = you're done.",
                    },
                },
                "required": ["channel_id", "message_id", "emoji", "will_continue_work"],
            },
        },
    }


def execute_add_discord_reaction(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    channel_id = str(params.get("channel_id") or "").strip()
    message_id = str(params.get("message_id") or "").strip()
    emoji = str(params.get("emoji") or "").strip()
    try:
        normalized_emoji = add_discord_reaction(
            agent,
            channel_id=channel_id,
            message_id=message_id,
            emoji=emoji,
        )
        result: dict[str, Any] = {
            "status": "success",
            "channel_id": channel_id,
            "message_id": message_id,
            "discord_message_id": message_id,
            "emoji": normalized_emoji,
        }
        if params.get("will_continue_work") is False:
            result["auto_sleep_ok"] = True
        return result
    except ObjectDoesNotExist:
        return {"status": "error", "message": "No active native Discord subscription was found for that channel."}
    except (DiscordBotIntegrationError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    except requests.RequestException as exc:
        logger.warning("Native Discord reaction failed for agent %s: %s", agent.id, exc)
        return {"status": "error", "message": f"Discord request failed: {exc}"}
