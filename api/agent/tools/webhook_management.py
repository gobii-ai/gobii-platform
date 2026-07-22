"""Agent tools for managing native inbound and outbound webhooks."""

from typing import Any

from api.models import PersistentAgent
from api.services.agent_webhooks import AgentWebhookError, AgentWebhookService


MANAGE_INBOUND_WEBHOOKS_TOOL_NAME = "manage_inbound_webhooks"
MANAGE_OUTBOUND_WEBHOOKS_TOOL_NAME = "manage_outbound_webhooks"

_COMMON_PROPERTIES = {
    "webhook_id": {"type": "string"},
    "name": {
        "type": "string",
        "description": "Webhook name for create, or an optional replacement name for update.",
    },
    "will_continue_work": {
        "type": "boolean",
        "description": "REQUIRED. true = you'll take another action, false = you're done.",
    },
}


def _tool_definition(
    *,
    name: str,
    description: str,
    direction: str,
    actions: list[str],
    extra_property: tuple[str, dict[str, object]],
) -> dict[str, Any]:
    properties = {
        "action": {
            "type": "string",
            "enum": actions,
            "description": "Webhook management operation.",
        },
        **{key: value.copy() for key, value in _COMMON_PROPERTIES.items()},
        extra_property[0]: extra_property[1],
    }
    properties["webhook_id"]["description"] = (
        f"Agent-scoped {direction} webhook ID for operations on an existing webhook."
    )
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": ["action", "will_continue_work"],
            },
        },
    }


def get_manage_inbound_webhooks_tool() -> dict[str, Any]:
    return _tool_definition(
        name=MANAGE_INBOUND_WEBHOOKS_TOOL_NAME,
        direction="inbound",
        actions=["list", "get", "create", "update", "rotate_secret", "delete"],
        description=(
            "List, inspect, create, update, rotate, or delete native Gobii inbound webhooks that let external "
            "services wake this agent. List results omit secret endpoint URLs; use get only when the current "
            "endpoint is needed. Only mutate webhook configuration when the user clearly requested that exact change."
        ),
        extra_property=(
            "is_active",
            {
                "type": "boolean",
                "description": "Whether the inbound webhook accepts events; optional for create or update.",
            },
        ),
    )


def get_manage_outbound_webhooks_tool() -> dict[str, Any]:
    return _tool_definition(
        name=MANAGE_OUTBOUND_WEBHOOKS_TOOL_NAME,
        direction="outbound",
        actions=["list", "get", "create", "update", "delete"],
        description=(
            "List, inspect, create, update, or delete outbound webhook destinations used by send_webhook_event. "
            "List results omit exact destination URLs; use get when inspection is necessary. Only mutate webhook "
            "configuration when the user clearly requested that exact change."
        ),
        extra_property=(
            "url",
            {
                "type": "string",
                "description": "Destination URL for create, or an optional replacement URL for update.",
            },
        ),
    )


def _finish(result: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    if params.get("will_continue_work") is False:
        result["auto_sleep_ok"] = True
    return result


def _execute(agent: PersistentAgent, params: dict[str, Any], *, inbound: bool) -> dict[str, Any]:
    service = AgentWebhookService(agent, actor_user_id=agent.user_id)
    try:
        if inbound:
            result = service.manage_inbound(
                params.get("action"),
                webhook_id=params.get("webhook_id"),
                name=params.get("name") if "name" in params else None,
                is_active=params.get("is_active") if "is_active" in params else None,
            )
        else:
            result = service.manage_outbound(
                params.get("action"),
                webhook_id=params.get("webhook_id"),
                name=params.get("name") if "name" in params else None,
                url=params.get("url") if "url" in params else None,
            )
        return _finish({"status": "success", **result}, params)
    except AgentWebhookError as exc:
        return _finish({"status": "error", "message": str(exc)}, params)


def execute_manage_inbound_webhooks(agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    return _execute(agent, params, inbound=True)


def execute_manage_outbound_webhooks(agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    return _execute(agent, params, inbound=False)
