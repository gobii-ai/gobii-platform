"""
Generic tool enable/disable management for persistent agents.

MCP currently provides the only dynamic tool source, but these helpers live outside
the MCP manager so additional providers can plug into the same persistence logic later.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from django.db.models import F

from ...models import PersistentAgent, PersistentAgentEnabledTool
from .mcp_manager import MCPToolManager, get_mcp_manager, execute_mcp_tool
from .sqlite_batch import get_sqlite_batch_tool, execute_sqlite_batch
from .http_request import get_http_request_tool, execute_http_request

logger = logging.getLogger(__name__)

# Global cap on concurrently enabled tools per agent.
MAX_ENABLED_TOOLS = 40

SQLITE_TOOL_NAME = "sqlite_batch"
HTTP_REQUEST_TOOL_NAME = "http_request"

BUILTIN_TOOL_REGISTRY = {
    SQLITE_TOOL_NAME: {
        "definition": get_sqlite_batch_tool,
        "executor": execute_sqlite_batch,
    },
    HTTP_REQUEST_TOOL_NAME: {
        "definition": get_http_request_tool,
        "executor": execute_http_request,
    },
}


@dataclass
class ToolCatalogEntry:
    """Metadata describing an enableable tool."""

    provider: str
    full_name: str
    description: str
    parameters: Dict[str, Any]
    tool_server: str = ""
    tool_name: str = ""
    server_config_id: Optional[str] = None


def _get_manager() -> MCPToolManager:
    """Ensure the global MCP manager is ready before use."""
    manager = get_mcp_manager()
    if not manager._initialized:
        manager.initialize()
    return manager


def _build_available_tool_index(agent: PersistentAgent) -> Dict[str, ToolCatalogEntry]:
    """Build an index of enableable tools across all providers."""
    manager = _get_manager()
    catalog: Dict[str, ToolCatalogEntry] = {}

    for info in manager.get_tools_for_agent(agent):
        catalog[info.full_name] = ToolCatalogEntry(
            provider="mcp",
            full_name=info.full_name,
            description=info.description,
            parameters=info.parameters,
            tool_server=info.server_name,
            tool_name=info.tool_name,
            server_config_id=info.config_id,
        )

    for name, info in BUILTIN_TOOL_REGISTRY.items():
        try:
            tool_def = info["definition"]()
        except Exception:
            logger.exception("Failed to build builtin tool definition for %s", name)
            continue
        function_block = tool_def.get("function") if isinstance(tool_def, dict) else {}
        catalog[name] = ToolCatalogEntry(
            provider="builtin",
            full_name=name,
            description=function_block.get("description", ""),
            parameters=function_block.get("parameters", {}),
            tool_server="builtin",
            tool_name=name,
            server_config_id=None,
        )

    return catalog


def _evict_surplus_tools(agent: PersistentAgent, exclude: Optional[Sequence[str]] = None) -> List[str]:
    """Enforce the enabled tool cap by evicting the least recently used entries."""
    total = PersistentAgentEnabledTool.objects.filter(agent=agent).count()
    if total <= MAX_ENABLED_TOOLS:
        return []

    overflow = total - MAX_ENABLED_TOOLS
    queryset = PersistentAgentEnabledTool.objects.filter(agent=agent)
    if exclude:
        queryset = queryset.exclude(tool_full_name__in=list(exclude))

    oldest = list(
        queryset.order_by(
            F("last_used_at").asc(nulls_first=True),
            "enabled_at",
            "tool_full_name",
        )[:overflow]
    )
    if not oldest:
        return []

    evicted_ids = [row.id for row in oldest]
    evicted_names = [row.tool_full_name for row in oldest]
    PersistentAgentEnabledTool.objects.filter(id__in=evicted_ids).delete()
    logger.info(
        "Evicted %d tool(s) for agent %s due to %d-tool cap: %s",
        len(evicted_names),
        agent.id,
        MAX_ENABLED_TOOLS,
        ", ".join(evicted_names),
    )
    return evicted_names


def _apply_tool_metadata(row: PersistentAgentEnabledTool, entry: Optional[ToolCatalogEntry]) -> List[str]:
    """Populate cached metadata fields on the persistence row."""
    if not entry:
        return []

    updates: List[str] = []
    if entry.tool_server and row.tool_server != entry.tool_server:
        row.tool_server = entry.tool_server
        updates.append("tool_server")
    if entry.tool_name and row.tool_name != entry.tool_name:
        row.tool_name = entry.tool_name
        updates.append("tool_name")
    if entry.server_config_id is not None:
        try:
            server_uuid = uuid.UUID(str(entry.server_config_id))
        except (ValueError, TypeError):
            logger.debug(
                "Skipping server_config assignment for tool %s due to invalid id %s",
                entry.full_name,
                entry.server_config_id,
            )
        else:
            if row.server_config_id != server_uuid:
                row.server_config_id = server_uuid
                updates.append("server_config")
    return updates


def enable_tools(agent: PersistentAgent, tool_names: Iterable[str]) -> Dict[str, Any]:
    """Enable multiple tools for an agent, respecting the global cap."""
    catalog = _build_available_tool_index(agent)
    manager = _get_manager()

    requested: List[str] = []
    seen: Set[str] = set()
    for name in tool_names or []:
        if isinstance(name, str) and name not in seen:
            requested.append(name)
            seen.add(name)

    enabled: List[str] = []
    already_enabled: List[str] = []
    evicted: List[str] = []
    invalid: List[str] = []

    for name in requested:
        entry = catalog.get(name)
        if not entry:
            invalid.append(name)
            continue

        if entry.provider == "mcp" and manager.is_tool_blacklisted(name):
            invalid.append(name)
            continue

        try:
            row, created = PersistentAgentEnabledTool.objects.get_or_create(
                agent=agent,
                tool_full_name=name,
            )
        except Exception:
            logger.exception("Failed enabling tool %s", name)
            invalid.append(name)
            continue

        if created:
            metadata_updates = _apply_tool_metadata(row, entry)
            if metadata_updates:
                row.save(update_fields=metadata_updates)
            enabled.append(name)
        else:
            metadata_updates = _apply_tool_metadata(row, entry)
            if metadata_updates:
                row.save(update_fields=metadata_updates)
            already_enabled.append(name)

    if enabled or already_enabled:
        evicted = _evict_surplus_tools(agent)

    parts: List[str] = []
    if enabled:
        parts.append(f"Enabled: {', '.join(enabled)}")
    if already_enabled:
        parts.append(f"Already enabled: {', '.join(already_enabled)}")
    if evicted:
        parts.append(f"Evicted (LRU): {', '.join(evicted)}")
    if invalid:
        parts.append(f"Invalid: {', '.join(invalid)}")

    return {
        "status": "success",
        "message": "; ".join(parts),
        "enabled": enabled,
        "already_enabled": already_enabled,
        "evicted": evicted,
        "invalid": invalid,
    }


def enable_mcp_tool(agent: PersistentAgent, tool_name: str) -> Dict[str, Any]:
    """Enable a single MCP tool for the agent (with LRU eviction if needed)."""
    catalog = _build_available_tool_index(agent)
    manager = _get_manager()

    if manager.is_tool_blacklisted(tool_name):
        return {
            "status": "error",
            "message": f"Tool '{tool_name}' is blacklisted and cannot be enabled",
        }

    entry = catalog.get(tool_name)
    if not entry or entry.provider != "mcp":
        return {
            "status": "error",
            "message": f"Tool '{tool_name}' does not exist",
        }

    try:
        row = PersistentAgentEnabledTool.objects.filter(
            agent=agent,
            tool_full_name=tool_name,
        ).first()
    except Exception:
        logger.exception("Error checking existing enabled tool %s", tool_name)
        row = None

    if row:
        row.last_used_at = datetime.now(UTC)
        row.usage_count = (row.usage_count or 0) + 1
        updates = ["last_used_at", "usage_count"]
        updates.extend(_apply_tool_metadata(row, entry))
        row.save(update_fields=list(dict.fromkeys(updates)))
        return {
            "status": "success",
            "message": f"Tool '{tool_name}' is already enabled",
            "enabled": tool_name,
            "disabled": None,
        }

    try:
        row = PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name=tool_name,
        )
    except Exception as exc:
        logger.error("Failed to create enabled tool %s: %s", tool_name, exc)
        return {"status": "error", "message": str(exc)}

    metadata_updates = _apply_tool_metadata(row, entry)
    if metadata_updates:
        row.save(update_fields=list(dict.fromkeys(metadata_updates)))

    evicted = _evict_surplus_tools(agent, exclude=[tool_name])
    disabled_tool = evicted[0] if evicted else None

    message = f"Successfully enabled tool '{tool_name}'"
    if disabled_tool:
        message += f" (disabled '{disabled_tool}' due to {MAX_ENABLED_TOOLS} tool limit)"

    return {
        "status": "success",
        "message": message,
        "enabled": tool_name,
        "disabled": disabled_tool,
    }


def ensure_default_tools_enabled(agent: PersistentAgent) -> None:
    """Ensure the default MCP tool set is enabled for new agents."""
    manager = _get_manager()

    enabled_tools = set(
        PersistentAgentEnabledTool.objects.filter(agent=agent).values_list("tool_full_name", flat=True)
    )
    default_tools = set(MCPToolManager.DEFAULT_ENABLED_TOOLS)
    missing = default_tools - enabled_tools
    if not missing:
        return

    available = {tool.full_name for tool in manager.get_tools_for_agent(agent)}

    for tool_name in missing:
        if manager.is_tool_blacklisted(tool_name):
            logger.warning("Default tool '%s' is blacklisted, skipping", tool_name)
            continue
        if tool_name not in available:
            logger.warning("Default tool '%s' not found in available tools", tool_name)
            continue
        enable_mcp_tool(agent, tool_name)
        logger.info("Enabled default tool '%s' for agent %s", tool_name, agent.id)


def get_enabled_tool_definitions(agent: PersistentAgent) -> List[Dict[str, Any]]:
    """Return tool definitions for all enabled tools (MCP + built-ins)."""
    manager = _get_manager()
    definitions = manager.get_enabled_tools_definitions(agent)

    enabled_builtin_rows = PersistentAgentEnabledTool.objects.filter(
        agent=agent,
        tool_full_name__in=list(BUILTIN_TOOL_REGISTRY.keys()),
    )
    existing_names = {
        entry.get("function", {}).get("name")
        for entry in definitions
        if isinstance(entry, dict)
    }

    for row in enabled_builtin_rows:
        registry_entry = BUILTIN_TOOL_REGISTRY.get(row.tool_full_name)
        if not registry_entry:
            continue
        try:
            tool_def = registry_entry["definition"]()
        except Exception:
            logger.exception("Failed to build enabled builtin tool definition for %s", row.tool_full_name)
            continue
        tool_name = (
            tool_def.get("function", {}).get("name")
            if isinstance(tool_def, dict)
            else None
        )
        if tool_name and tool_name not in existing_names:
            definitions.append(tool_def)
            existing_names.add(tool_name)

    return definitions


def resolve_tool_entry(agent: PersistentAgent, tool_name: str) -> Optional[ToolCatalogEntry]:
    """Return catalog entry for the given tool name if available."""
    catalog = _build_available_tool_index(agent)
    return catalog.get(tool_name)


def execute_enabled_tool(agent: PersistentAgent, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an enabled tool, routing to the appropriate provider."""
    entry = resolve_tool_entry(agent, tool_name)
    if not entry:
        return {"status": "error", "message": f"Tool '{tool_name}' is not available"}

    if entry.provider == "mcp":
        if not PersistentAgentEnabledTool.objects.filter(agent=agent, tool_full_name=tool_name).exists():
            return {"status": "error", "message": f"Tool '{tool_name}' is not enabled for this agent"}
        return execute_mcp_tool(agent, tool_name, params)

    if entry.provider == "builtin":
        if not PersistentAgentEnabledTool.objects.filter(agent=agent, tool_full_name=tool_name).exists():
            return {"status": "error", "message": f"Tool '{tool_name}' is not enabled for this agent"}
        registry_entry = BUILTIN_TOOL_REGISTRY.get(tool_name)
        executor = registry_entry.get("executor") if registry_entry else None
        if executor:
            return executor(agent, params)

    return {"status": "error", "message": f"Tool '{tool_name}' has no execution handler"}
