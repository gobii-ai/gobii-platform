"""Agent tools for managing native inbound and outbound webhooks."""

from typing import Any

from api.models import PersistentAgent
from api.services.agent_webhooks import (
    AgentWebhookError,
    create_inbound_webhook,
    create_outbound_webhook,
    delete_inbound_webhook,
    delete_outbound_webhook,
    get_inbound_webhook,
    get_outbound_webhook,
    rotate_inbound_webhook_secret,
    serialize_inbound_webhook,
    serialize_outbound_webhook,
    update_inbound_webhook,
    update_outbound_webhook,
)


MANAGE_INBOUND_WEBHOOKS_TOOL_NAME = "manage_inbound_webhooks"
MANAGE_OUTBOUND_WEBHOOKS_TOOL_NAME = "manage_outbound_webhooks"


def _finish(result: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    if params.get("will_continue_work") is False:
        result["auto_sleep_ok"] = True
    return result


def _error(message: str, params: dict[str, Any]) -> dict[str, Any]:
    return _finish({"status": "error", "message": message}, params)


def get_manage_inbound_webhooks_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": MANAGE_INBOUND_WEBHOOKS_TOOL_NAME,
            "description": (
                "List, inspect, create, update, rotate, or delete native Gobii inbound webhooks that let external "
                "services wake this agent. List results omit secret endpoint URLs; use get only when the current "
                "endpoint is needed. Only mutate webhook configuration when the user clearly requested that exact change."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get", "create", "update", "rotate_secret", "delete"],
                        "description": "Webhook management operation.",
                    },
                    "webhook_id": {
                        "type": "string",
                        "description": "Agent-scoped inbound webhook ID for get, update, rotate_secret, or delete.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Webhook name for create, or an optional replacement name for update.",
                    },
                    "is_active": {
                        "type": "boolean",
                        "description": "Whether the inbound webhook accepts events; optional for create or update.",
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


def execute_manage_inbound_webhooks(agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    action = str(params.get("action") or "").strip().lower()
    webhook_id = params.get("webhook_id")
    try:
        if action == "list":
            webhooks = [
                serialize_inbound_webhook(webhook)
                for webhook in agent.inbound_webhooks.order_by("name")
            ]
            return _finish({"status": "success", "webhooks": webhooks}, params)
        if action == "get":
            webhook = get_inbound_webhook(agent, webhook_id)
            return _finish(
                {"status": "success", "webhook": serialize_inbound_webhook(webhook, include_url=True)},
                params,
            )
        if action == "create":
            webhook = create_inbound_webhook(
                agent,
                name=str(params.get("name") or ""),
                is_active=params.get("is_active", True),
                actor_user_id=agent.user_id,
            )
            return _finish(
                {"status": "success", "webhook": serialize_inbound_webhook(webhook, include_url=True)},
                params,
            )
        if action == "update":
            webhook = update_inbound_webhook(
                agent,
                webhook_id,
                name=params.get("name") if "name" in params else None,
                is_active=params.get("is_active") if "is_active" in params else None,
                actor_user_id=agent.user_id,
            )
            return _finish(
                {"status": "success", "webhook": serialize_inbound_webhook(webhook)},
                params,
            )
        if action == "rotate_secret":
            webhook = rotate_inbound_webhook_secret(
                agent,
                webhook_id,
                actor_user_id=agent.user_id,
            )
            return _finish(
                {
                    "status": "success",
                    "message": "Inbound webhook secret rotated; the previous endpoint URL no longer works.",
                    "webhook": serialize_inbound_webhook(webhook, include_url=True),
                },
                params,
            )
        if action == "delete":
            webhook = delete_inbound_webhook(
                agent,
                webhook_id,
                actor_user_id=agent.user_id,
            )
            return _finish({"status": "success", "deleted_webhook": webhook}, params)
        return _error(
            "Unsupported action. Use list, get, create, update, rotate_secret, or delete.",
            params,
        )
    except AgentWebhookError as exc:
        return _error(str(exc), params)


def get_manage_outbound_webhooks_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": MANAGE_OUTBOUND_WEBHOOKS_TOOL_NAME,
            "description": (
                "List, inspect, create, update, or delete outbound webhook destinations used by send_webhook_event. "
                "List results omit exact destination URLs; use get when inspection is necessary. Only mutate webhook "
                "configuration when the user clearly requested that exact change."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get", "create", "update", "delete"],
                        "description": "Webhook management operation.",
                    },
                    "webhook_id": {
                        "type": "string",
                        "description": "Agent-scoped outbound webhook ID for get, update, or delete.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Webhook name for create, or an optional replacement name for update.",
                    },
                    "url": {
                        "type": "string",
                        "description": "Destination URL for create, or an optional replacement URL for update.",
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


def execute_manage_outbound_webhooks(agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    action = str(params.get("action") or "").strip().lower()
    webhook_id = params.get("webhook_id")
    try:
        if action == "list":
            webhooks = [
                serialize_outbound_webhook(webhook)
                for webhook in agent.webhooks.order_by("name")
            ]
            return _finish({"status": "success", "webhooks": webhooks}, params)
        if action == "get":
            webhook = get_outbound_webhook(agent, webhook_id)
            return _finish(
                {"status": "success", "webhook": serialize_outbound_webhook(webhook, include_url=True)},
                params,
            )
        if action == "create":
            webhook = create_outbound_webhook(
                agent,
                name=str(params.get("name") or ""),
                url=str(params.get("url") or ""),
                actor_user_id=agent.user_id,
            )
            return _finish(
                {"status": "success", "webhook": serialize_outbound_webhook(webhook, include_url=True)},
                params,
            )
        if action == "update":
            webhook = update_outbound_webhook(
                agent,
                webhook_id,
                name=params.get("name") if "name" in params else None,
                url=params.get("url") if "url" in params else None,
                actor_user_id=agent.user_id,
            )
            return _finish(
                {"status": "success", "webhook": serialize_outbound_webhook(webhook, include_url=True)},
                params,
            )
        if action == "delete":
            webhook = delete_outbound_webhook(
                agent,
                webhook_id,
                actor_user_id=agent.user_id,
            )
            return _finish({"status": "success", "deleted_webhook": webhook}, params)
        return _error("Unsupported action. Use list, get, create, update, or delete.", params)
    except AgentWebhookError as exc:
        return _error(str(exc), params)
