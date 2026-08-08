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
    resolve_active_subscription,
)

logger = logging.getLogger(__name__)


def _agent_guild(guild: dict[str, Any]) -> dict[str, str]:
    return {
        "guild_id": str(guild.get("guild_id") or ""),
        "guild_name": str(guild.get("name") or guild.get("guild_name") or ""),
    }


def _agent_channel(channel: dict[str, Any]) -> dict[str, str]:
    return {
        "guild_id": str(channel.get("guild_id") or ""),
        "guild_name": str(channel.get("guild_name") or ""),
        "channel_name": str(channel.get("channel_name") or ""),
        "label": str(channel.get("label") or ""),
    }


def _agent_subscription(subscription: dict[str, Any]) -> dict[str, str]:
    return {
        "guild_id": str(subscription.get("guild_id") or ""),
        "guild_name": str(subscription.get("guild_name") or ""),
        "channel_name": str(subscription.get("channel_name") or ""),
        "status": str(subscription.get("status") or ""),
        "last_message_at": str(subscription.get("last_message_at") or ""),
    }


def get_discord_channel_subscriptions_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "discord_channel_subscriptions",
            "description": (
                "Manage native Gobii Discord bot channel subscriptions for this agent. "
                "For Discord setup requests, call list_guilds or discover_channels immediately; "
                "if setup is required, this tool returns the single connect_url to send to the user. "
                "Use this to list connected servers, discover visible channels by name, "
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
                        "description": "Discord server ID from list_guilds/discover_channels.",
                    },
                    "channel_name": {
                        "type": "string",
                        "description": (
                            "Exact human-readable channel name for ensure or disable. guild_id is also required; "
                            "a leading # and letter case are ignored."
                        ),
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
            result = {"status": "success", "guilds": [_agent_guild(guild) for guild in guilds]}
            selected_guild = latest_selected_guild(agent)
            if selected_guild:
                result["selected_guild"] = {
                    "guild_id": selected_guild.guild_id,
                    "guild_name": selected_guild.name,
                }
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
            if isinstance(result.get("channels"), list):
                result["channels"] = [_agent_channel(channel) for channel in result["channels"]]
            if isinstance(result.get("selected_guild"), dict):
                result["selected_guild"] = _agent_guild(result["selected_guild"])
            return _result_with_sleep(result, params)

        if action == "ensure":
            guild_id = str(params.get("guild_id") or "").strip()
            channel_name = str(params.get("channel_name") or "").strip()
            if not guild_id or not channel_name:
                return {"status": "error", "message": "guild_id and channel_name are required for ensure."}
            result = ensure_subscription(
                agent,
                guild_id=guild_id,
                channel_name=channel_name,
            )
            result["subscription"] = _agent_subscription(result["subscription"])
            return _result_with_sleep({"status": "success", **result}, params)

        if action == "list":
            return _result_with_sleep(
                {
                    "status": "success",
                    "subscriptions": [_agent_subscription(subscription) for subscription in list_subscriptions(agent)],
                },
                params,
            )

        if action == "disable":
            guild_id = str(params.get("guild_id") or "").strip()
            channel_name = str(params.get("channel_name") or "").strip()
            if not guild_id or not channel_name:
                return {"status": "error", "message": "guild_id and channel_name are required for disable."}
            subscription = resolve_active_subscription(
                agent,
                guild_id=guild_id,
                channel_name=channel_name,
            )
            return _result_with_sleep(
                {
                    "status": "success",
                    "subscription": _agent_subscription(disable_subscription(agent, str(subscription.id))),
                },
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
