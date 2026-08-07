"""Feature gating for the native ContactOut pilot."""

import logging
import re
from typing import Any, Iterable

from django.db import DatabaseError
from waffle import get_waffle_flag_model

from api.agent.eval_agents import is_eval_agent
from api.models import PersistentAgentEnabledTool
from api.services.pipedream_apps import pipedream_app_slug_for_tool_call
from constants.feature_flags import CONTACTOUT_PILOT


logger = logging.getLogger(__name__)

_CONTACTOUT_TOOL_NAME = "contactout"
CONTACTOUT_MCP_BLOCKED = "contactout_mcp_blocked"


def _identifier_targets_contactout(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().casefold()
    if not normalized:
        return False
    if re.search(r"(?:^|[^a-z0-9])contact[\s_-]*out(?:$|[^a-z0-9])", normalized):
        return True
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    return compact in {"contactout", "mcpcontactout"}


def is_contactout_mcp_tool(tool: Any, params: Any = None) -> bool:
    """Return whether MCP metadata or a concrete Pipedream call targets ContactOut."""
    provider = str(getattr(tool, "provider", "") or "").strip().casefold()
    if provider and provider != "mcp":
        return False

    mcp_info = getattr(tool, "mcp_info", None)
    metadata = mcp_info or tool
    tool_name = getattr(tool, "tool_name", "") or getattr(metadata, "tool_name", "")
    identifiers = (
        getattr(tool, "full_name", ""),
        tool_name,
        getattr(tool, "tool_server", ""),
        getattr(metadata, "full_name", ""),
        getattr(metadata, "server_name", ""),
        getattr(metadata, "app_slug", ""),
        pipedream_app_slug_for_tool_call(tool_name, params),
    )
    return any(_identifier_targets_contactout(value) for value in identifiers)


def contactout_mcp_blocked_for_agent(agent, tool: Any, params: Any = None) -> bool:
    return contactout_enabled_for_agent(agent) and is_contactout_mcp_tool(tool, params)


def filter_contactout_mcp_tools_for_agent(agent, tools: Iterable[Any]) -> list[Any]:
    tool_list = list(tools)
    if not contactout_enabled_for_agent(agent):
        return tool_list
    return [tool for tool in tool_list if not is_contactout_mcp_tool(tool)]


def contactout_mcp_blocked_error(agent, entry: Any, params: Any) -> dict[str, Any] | None:
    if not contactout_mcp_blocked_for_agent(agent, entry, params):
        return None
    return {
        "status": "error",
        "error_code": CONTACTOUT_MCP_BLOCKED,
        "message": (
            "ContactOut through MCP is disabled for this agent because the native ContactOut API pilot is enabled. "
            "Use the native `contactout` tool and do not retry the MCP tool."
        ),
        "provider": "mcp",
        "integration": "contactout",
        "replacement": _CONTACTOUT_TOOL_NAME,
        "retryable": False,
    }


def contactout_enabled_for_agent(agent) -> bool:
    """Return whether the user pilot flag or agent-scoped eval override is active."""
    if agent is None or not getattr(agent, "user_id", None):
        return False

    if is_eval_agent(agent):
        try:
            return PersistentAgentEnabledTool.objects.filter(
                agent=agent,
                tool_full_name=_CONTACTOUT_TOOL_NAME,
                tool_server="builtin",
            ).exists()
        except (AttributeError, DatabaseError, TypeError, ValueError):
            logger.warning(
                "Unable to evaluate ContactOut override for eval agent %s",
                getattr(agent, "id", None),
            )
            return False

    Flag = get_waffle_flag_model()
    try:
        flag = Flag.objects.get(name=CONTACTOUT_PILOT)
    except Flag.DoesNotExist:
        return False
    except (AttributeError, DatabaseError, TypeError, ValueError):
        logger.warning(
            "Unable to load ContactOut pilot flag for agent %s",
            getattr(agent, "id", None),
        )
        return False

    try:
        return bool(flag.is_active_for_user(agent.user))
    except (AttributeError, DatabaseError, TypeError, ValueError):
        logger.warning(
            "Unable to evaluate ContactOut pilot flag for user %s",
            getattr(agent, "user_id", None),
        )
        return False
