"""Agent tool for native Gobii Discord bot channel subscriptions."""

import logging
from typing import Any, Dict

import requests
from django.core.exceptions import ObjectDoesNotExist, ValidationError

from api.models import PersistentAgent
from api.services.discord_bot import (
    DiscordBotIntegrationError,
    disable_subscription,
    discover_channels,
    discord_setup_required_response,
    ensure_subscription,
    latest_selected_guild,
    list_claimed_guilds,
    list_subscriptions,
    serialize_guild,
)

logger = logging.getLogger(__name__)


def get_discord_channel_subscriptions_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "discord_channel_subscriptions",
            "description": (
                "Manage native Gobii Discord bot channel subscriptions for this agent. "
                "For Discord setup requests, call list_guilds or discover_channels immediately; "
                "if setup is required, this tool returns the single connect_url to send to the user. "
                "Use this before asking for raw Discord IDs: list claimed guilds, discover visible channels, "
                "subscribe the selected channel, inspect subscriptions, or disable one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list_guilds", "discover_channels", "ensure", "list", "disable"],
                        "description": "Operation to perform.",
                    },
                    "guild_id": {
                        "type": "string",
                        "description": "Discord guild ID from list_guilds/discover_channels.",
                    },
                    "channel_id": {
                        "type": "string",
                        "description": "Discord channel ID from discover_channels.",
                    },
                    "channel_name": {
                        "type": "string",
                        "description": "Optional human-readable channel name when ensuring a subscription.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional channel search text for discovery.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "description": "Maximum channels to return during discovery.",
                    },
                    "subscription_id": {
                        "type": "string",
                        "description": "Subscription ID to disable.",
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


def execute_discord_channel_subscriptions(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    action = str(params.get("action") or "").strip().lower()
    try:
        if action == "list_guilds":
            guilds = list_claimed_guilds(agent)
            if not guilds:
                setup_required = discord_setup_required_response(agent)
                setup_required.pop("channels", None)
                setup_required["guilds"] = []
                return _result_with_sleep(setup_required, params)
            result = {"status": "success", "guilds": guilds}
            selected_guild = latest_selected_guild(agent)
            if selected_guild:
                result["selected_guild"] = serialize_guild(selected_guild)
                result["message"] = (
                    "Use selected_guild from the most recent Discord setup. "
                    "Do not ask the user to choose a server again; discover channels for this guild next."
                )
            return _result_with_sleep(result, params)

        if action == "discover_channels":
            result = discover_channels(
                agent,
                guild_id=str(params.get("guild_id") or "").strip(),
                query=str(params.get("query") or "").strip(),
                limit=int(params.get("limit") or 100),
            )
            return _result_with_sleep(result, params)

        if action == "ensure":
            guild_id = str(params.get("guild_id") or "").strip()
            channel_id = str(params.get("channel_id") or "").strip()
            if not guild_id or not channel_id:
                return {"status": "error", "message": "guild_id and channel_id are required for ensure."}
            result = ensure_subscription(
                agent,
                guild_id=guild_id,
                channel_id=channel_id,
                channel_name=str(params.get("channel_name") or "").strip(),
            )
            return _result_with_sleep({"status": "success", **result}, params)

        if action == "list":
            return _result_with_sleep({"status": "success", "subscriptions": list_subscriptions(agent)}, params)

        if action == "disable":
            subscription_id = str(params.get("subscription_id") or "").strip()
            if not subscription_id:
                return {"status": "error", "message": "subscription_id is required for disable."}
            return _result_with_sleep(
                {"status": "success", "subscription": disable_subscription(agent, subscription_id)},
                params,
            )

        return {
            "status": "error",
            "message": "Unsupported action. Use list_guilds, discover_channels, ensure, list, or disable.",
        }
    except ObjectDoesNotExist:
        return {"status": "error", "message": "Discord guild, channel, or subscription not found for this agent."}
    except (DiscordBotIntegrationError, ValidationError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    except requests.RequestException as exc:
        logger.warning("Native Discord subscription request failed for agent %s: %s", agent.id, exc)
        return {"status": "error", "message": f"Discord request failed: {exc}"}
