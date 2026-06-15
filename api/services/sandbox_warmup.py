import logging
from typing import Optional

from django.db import DatabaseError
from django.db.models import Q

from api.agent.core.prompt_context import tool_call_history_limit
from api.agent.tools.custom_tools import CUSTOM_TOOL_PREFIX
from api.agent.tools.tool_manager import BUILTIN_TOOL_REGISTRY
from api.models import (
    MCPServerConfig,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentToolCall,
)

logger = logging.getLogger(__name__)

SANDBOX_WARM_REASON_PREFIX = "recent_sandbox_tool_history"


def sandbox_warm_reason(tool_type: str, tool_name: str) -> str:
    safe_tool_name = str(tool_name or "").strip()[:96]
    return f"{SANDBOX_WARM_REASON_PREFIX}:{tool_type}:{safe_tool_name}"


def recent_sandbox_tool_history_warm_reason(agent: PersistentAgent) -> Optional[str]:
    try:
        limit = max(0, int(tool_call_history_limit(agent)))
    except (TypeError, ValueError, DatabaseError) as exc:
        logger.warning(
            "Failed to resolve tool history limit for sandbox warm-up agent=%s: %s",
            agent.id,
            exc,
        )
        return None
    if limit <= 0:
        return None

    try:
        raw_tool_names = list(
            PersistentAgentToolCall.objects.filter(step__agent=agent)
            .order_by("-step__created_at")
            .values_list("tool_name", flat=True)[:limit]
        )
    except DatabaseError as exc:
        logger.warning(
            "Failed to load recent tool history for sandbox warm-up agent=%s: %s",
            agent.id,
            exc,
        )
        return None

    recent_tool_names = [
        tool_name.strip()
        for tool_name in raw_tool_names
        if isinstance(tool_name, str) and tool_name.strip()
    ]
    if not recent_tool_names:
        return None

    sandbox_builtin_names = {
        name
        for name, entry in BUILTIN_TOOL_REGISTRY.items()
        if isinstance(entry, dict) and (entry.get("sandboxed") or entry.get("sandbox_only"))
    }
    for tool_name in recent_tool_names:
        if tool_name in sandbox_builtin_names:
            return sandbox_warm_reason("builtin", tool_name)
        if tool_name.startswith(CUSTOM_TOOL_PREFIX):
            return sandbox_warm_reason("custom", tool_name)

    unique_tool_names = list(dict.fromkeys(recent_tool_names))
    try:
        sandbox_mcp_tool_names = set(
            PersistentAgentEnabledTool.objects.filter(
                agent=agent,
                tool_full_name__in=unique_tool_names,
                server_config__isnull=False,
                server_config__is_active=True,
                server_config__command__gt="",
            )
            .filter(Q(server_config__url="") | Q(server_config__url__isnull=True))
            .exclude(server_config__scope=MCPServerConfig.Scope.PLATFORM)
            .values_list("tool_full_name", flat=True)
        )
    except DatabaseError as exc:
        logger.warning(
            "Failed to load enabled MCP tools for sandbox warm-up agent=%s: %s",
            agent.id,
            exc,
        )
        return None

    for tool_name in recent_tool_names:
        if tool_name in sandbox_mcp_tool_names:
            return sandbox_warm_reason("mcp_stdio", tool_name)
    return None
