"""Agent tool for managing Pipedream Connect trigger subscriptions."""

import logging
from typing import Any, Dict

import requests
from django.core.exceptions import ValidationError

from api.models import PersistentAgent, PersistentAgentPipedreamTriggerSubscription
from api.services.pipedream_trigger_subscriptions import (
    DISCORD_APP_SLUG,
    DISCORD_MESSAGE_EVENT_TYPE,
    PipedreamTriggerSubscriptionError,
    disable_subscription,
    discover_targets,
    ensure_subscriptions,
    list_subscriptions,
    serialize_subscription,
)

logger = logging.getLogger(__name__)


def get_pipedream_trigger_subscriptions_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "pipedream_trigger_subscriptions",
            "description": (
                "Provision and inspect inbound Pipedream Connect app triggers for this agent. "
                "Use this for receiving messages from connected apps and discovering subscribable targets. "
                "V1 supports Discord and Slack new-message subscriptions for selected channel IDs only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "discover_targets", "ensure", "disable"],
                        "description": "Operation to perform.",
                    },
                    "app_slug": {
                        "type": "string",
                        "description": "Connected app slug. V1 supports discord and slack.",
                    },
                    "event_type": {
                        "type": "string",
                        "description": "Event type. V1 supports message.created.",
                    },
                    "channel_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Connected-app channel IDs to subscribe to when action is ensure.",
                    },
                    "channel_names": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Optional map of channel ID to human-readable channel name.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional search text when discovering remote targets like Discord or Slack channels.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "description": "Maximum number of targets to return when action is discover_targets.",
                    },
                    "subscription_id": {
                        "type": "string",
                        "description": "Subscription ID to disable when action is disable.",
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
    will_continue_work = params.get("will_continue_work")
    if will_continue_work is False:
        result["auto_sleep_ok"] = True
    return result


def execute_pipedream_trigger_subscriptions(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    action = str(params.get("action") or "").strip().lower()
    try:
        if action == "list":
            return _result_with_sleep(
                {
                    "status": "success",
                    "subscriptions": list_subscriptions(agent),
                },
                params,
            )

        if action == "ensure":
            app_slug = str(params.get("app_slug") or DISCORD_APP_SLUG).strip().lower()
            event_type = str(params.get("event_type") or DISCORD_MESSAGE_EVENT_TYPE).strip().lower()
            channel_ids = params.get("channel_ids") or []
            channel_names = params.get("channel_names") or {}
            if not isinstance(channel_ids, list):
                return {"status": "error", "message": "channel_ids must be a list of connected-app channel IDs."}
            if not isinstance(channel_names, dict):
                channel_names = {}
            results = ensure_subscriptions(
                agent,
                app_slug=app_slug,
                event_type=event_type,
                channel_ids=channel_ids,
                channel_names=channel_names,
            )
            if len(results) == 1 and results[0].action_required:
                return _result_with_sleep(
                    {
                        "status": "action_required",
                        "message": results[0].message,
                        "connect_url": results[0].connect_url,
                    },
                    params,
                )
            return _result_with_sleep(
                {
                    "status": "success",
                    "subscriptions": [
                        serialize_subscription(result.subscription)
                        for result in results
                        if result.subscription is not None
                    ],
                    "created_count": sum(1 for result in results if result.created),
                    "reused_count": sum(1 for result in results if result.reused),
                },
                params,
            )

        if action == "discover_targets":
            app_slug = str(params.get("app_slug") or DISCORD_APP_SLUG).strip().lower()
            event_type = str(params.get("event_type") or DISCORD_MESSAGE_EVENT_TYPE).strip().lower()
            result = discover_targets(
                agent,
                app_slug=app_slug,
                event_type=event_type,
                query=str(params.get("query") or "").strip(),
                limit=int(params.get("limit") or 100),
            )
            if result.action_required:
                return _result_with_sleep(
                    {
                        "status": "action_required",
                        "message": result.message,
                        "connect_url": result.connect_url,
                        "targets": [],
                    },
                    params,
                )
            return _result_with_sleep(
                {
                    "status": "success",
                    "message": result.message,
                    "target_type": result.target_type,
                    "targets": [
                        {
                            "label": target.label,
                            "value": target.value,
                        }
                        for target in result.targets
                    ],
                },
                params,
            )

        if action == "disable":
            subscription_id = str(params.get("subscription_id") or "").strip()
            if not subscription_id:
                return {"status": "error", "message": "subscription_id is required for disable."}
            return _result_with_sleep(
                {
                    "status": "success",
                    "subscription": disable_subscription(agent, subscription_id),
                },
                params,
            )

        return {"status": "error", "message": "Unsupported action. Use list, discover_targets, ensure, or disable."}
    except PersistentAgentPipedreamTriggerSubscription.DoesNotExist:
        return {"status": "error", "message": "Subscription not found for this agent."}
    except (PipedreamTriggerSubscriptionError, ValidationError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    except requests.RequestException as exc:
        logger.warning("Pipedream trigger subscription request failed for agent %s: %s", agent.id, exc)
        return {"status": "error", "message": f"Pipedream request failed: {exc}"}
