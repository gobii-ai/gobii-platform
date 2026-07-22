"""Agent tools for managing native inbound and outbound webhooks."""

from typing import Any

from api.models import PersistentAgent
from api.services.agent_webhooks import AgentWebhookError, AgentWebhookService


MANAGE_INBOUND_WEBHOOKS_TOOL_NAME = "manage_inbound_webhooks"
MANAGE_OUTBOUND_WEBHOOKS_TOOL_NAME = "manage_outbound_webhooks"


def _tool_definition(direction: str) -> dict[str, Any]:
    inbound = direction == "inbound"
    actions = ["list", "get", "create", "update", "delete"]
    if inbound:
        actions.insert(4, "rotate_secret")
    extra_name = "is_active" if inbound else "url"
    extra = ({"type": "boolean", "description": "Whether the inbound webhook accepts events."} if inbound
             else {"type": "string", "description": "Outbound destination URL."})
    return {
        "type": "function",
        "function": {
            "name": f"manage_{direction}_webhooks",
            "description": (
                f"List, inspect, create, update, {'rotate, ' if inbound else ''}or delete {direction} webhooks. "
                "Lists omit secret or destination URLs; use get only when the exact URL is needed. "
                "Only mutate configuration when the user clearly requested that exact change."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": actions},
                    "webhook_id": {"type": "string", "description": f"Agent-scoped {direction} webhook ID."},
                    "name": {"type": "string", "description": "Webhook name."},
                    extra_name: extra,
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. true = you'll take another action, false = you're done.",
                    },
                },
                "required": ["action", "will_continue_work"],
            },
        },
    }


def get_manage_inbound_webhooks_tool() -> dict[str, Any]:
    return _tool_definition("inbound")


def get_manage_outbound_webhooks_tool() -> dict[str, Any]:
    return _tool_definition("outbound")


def _execute(agent: PersistentAgent, params: dict[str, Any], direction: str) -> dict[str, Any]:
    try:
        result = AgentWebhookService(agent, actor_user_id=agent.user_id).manage(
            direction,
            params.get("action"),
            webhook_id=params.get("webhook_id"),
            name=params.get("name") if "name" in params else None,
            is_active=params.get("is_active") if "is_active" in params else None,
            url=params.get("url") if "url" in params else None,
        )
        result = {"status": "success", **result}
    except AgentWebhookError as exc:
        result = {"status": "error", "message": str(exc)}
    if params.get("will_continue_work") is False:
        result["auto_sleep_ok"] = True
    return result


def execute_manage_inbound_webhooks(agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    return _execute(agent, params, "inbound")


def execute_manage_outbound_webhooks(agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    return _execute(agent, params, "outbound")
