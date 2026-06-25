"""Agent tool for native Slack channel subscriptions."""

import logging
from typing import Any, Dict

import requests
from django.core.exceptions import ObjectDoesNotExist, ValidationError

from api.models import PersistentAgent
from api.services.slack_bot import (
    SlackIntegrationError,
    disable_subscription,
    discover_channels,
    ensure_subscription,
    list_subscriptions,
    slack_setup_required_response,
)

logger = logging.getLogger(__name__)


def get_slack_channel_subscriptions_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "slack_channel_subscriptions",
            "description": (
                "Manage native Slack channel subscriptions for this agent. "
                "For setup requests, call discover_channels immediately; if setup is required, "
                "this tool returns the single setup_url to send to the user. Discover channels before "
                "asking for raw Slack channel IDs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["discover_channels", "ensure", "list", "disable"],
                        "description": "Operation to perform.",
                    },
                    "workspace_id": {
                        "type": "string",
                        "description": "Gobii Slack workspace claim ID returned by discover_channels.",
                    },
                    "channel_id": {
                        "type": "string",
                        "description": "Slack channel ID returned by discover_channels.",
                    },
                    "channel_name": {
                        "type": "string",
                        "description": "Optional human-readable channel name when ensuring a subscription.",
                    },
                    "channel_type": {
                        "type": "string",
                        "description": "Optional Slack channel type, such as public_channel or private_channel.",
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


def execute_slack_channel_subscriptions(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    action = str(params.get("action") or "").strip().lower()
    try:
        if action == "discover_channels":
            result = discover_channels(
                agent,
                query=str(params.get("query") or "").strip(),
                limit=int(params.get("limit") or 100),
            )
            return _result_with_sleep(result, params)

        if action == "ensure":
            workspace_id = str(params.get("workspace_id") or "").strip()
            channel_id = str(params.get("channel_id") or "").strip()
            if not workspace_id or not channel_id:
                return {"status": "error", "message": "workspace_id and channel_id are required for ensure."}
            result = ensure_subscription(
                agent,
                workspace_id=workspace_id,
                channel_id=channel_id,
                channel_name=str(params.get("channel_name") or "").strip(),
                channel_type=str(params.get("channel_type") or "").strip(),
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
            "message": "Unsupported action. Use discover_channels, ensure, list, or disable.",
        }
    except ObjectDoesNotExist:
        return {"status": "error", "message": "Slack workspace, channel, or subscription not found for this agent."}
    except (SlackIntegrationError, ValidationError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    except requests.RequestException as exc:
        logger.warning("Native Slack subscription request failed for agent %s: %s", agent.id, exc)
        return {"status": "error", "message": f"Slack request failed: {exc}"}
