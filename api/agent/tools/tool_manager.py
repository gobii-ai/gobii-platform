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

from django.conf import settings
from django.db.models import F

from ...models import PersistentAgent, PersistentAgentEnabledTool
from ...services.sandbox_compute import (
    SandboxComputeService,
    SandboxComputeUnavailable,
    sandbox_compute_enabled_for_agent,
)
from ...services.prompt_settings import get_prompt_settings, DEFAULT_STANDARD_ENABLED_TOOL_LIMIT
from ..core.llm_config import AgentLLMTier, get_agent_llm_tier
from .mcp_manager import MCPToolManager, get_mcp_manager, execute_mcp_tool
from .sqlite_batch import get_sqlite_batch_tool, execute_sqlite_batch
from .http_request import get_http_request_tool, execute_http_request
from .read_file import get_read_file_tool, execute_read_file
from .create_file import get_create_file_tool, execute_create_file
from .create_csv import get_create_csv_tool, execute_create_csv
from .create_pdf import get_create_pdf_tool, execute_create_pdf
from .create_chart import get_create_chart_tool, execute_create_chart
from .python_exec import get_python_exec_tool
from .run_command import get_run_command_tool, execute_run_command
from .autotool_heuristics import find_matching_tools
from config.plans import PLAN_CONFIG

logger = logging.getLogger(__name__)


def _coerce_params_to_schema(params: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce parameter values to match expected types from JSON schema.

    Handles common LLM mistakes like passing "true"/"false" strings for booleans,
    or string numbers for integers/numbers.
    """
    if not schema or not isinstance(params, dict):
        return params

    properties = schema.get("properties", {})
    if not properties:
        return params

    coerced = dict(params)
    for key, value in params.items():
        if key not in properties or value is None:
            continue

        prop_schema = properties[key]
        expected_type = prop_schema.get("type")

        if expected_type == "boolean" and isinstance(value, str):
            coerced[key] = value.lower() == "true"
        elif expected_type == "integer" and isinstance(value, str):
            try:
                coerced[key] = int(value)
            except ValueError:
                pass
        elif expected_type == "number" and isinstance(value, str):
            try:
                coerced[key] = float(value)
            except ValueError:
                pass

    return coerced

SQLITE_TOOL_NAME = "sqlite_batch"
HTTP_REQUEST_TOOL_NAME = "http_request"
READ_FILE_TOOL_NAME = "read_file"
CREATE_FILE_TOOL_NAME = "create_file"
CREATE_CSV_TOOL_NAME = "create_csv"
CREATE_PDF_TOOL_NAME = "create_pdf"
CREATE_CHART_TOOL_NAME = "create_chart"
PYTHON_EXEC_TOOL_NAME = "python_exec"
RUN_COMMAND_TOOL_NAME = "run_command"
DEFAULT_BUILTIN_TOOLS = {READ_FILE_TOOL_NAME, SQLITE_TOOL_NAME, CREATE_CHART_TOOL_NAME}


def _sandbox_fallback_tools() -> Set[str]:
    tools = getattr(settings, "SANDBOX_COMPUTE_LOCAL_FALLBACK_TOOLS", [])
    if isinstance(tools, (list, tuple, set)):
        return {str(tool) for tool in tools if str(tool)}
    return set()


def is_sqlite_enabled_for_agent(agent: Optional[PersistentAgent]) -> bool:
    """
    Check if the sqlite tool should be available for this agent.

    SQLite is a core capability and is enabled for all agents.
    """
    return agent is not None


SKIP_AUTO_SUBSTITUTION_TOOL_NAMES = {
    "send_email",
    "send_sms",
    "send_chat_message",
    "read_file",
}


def should_skip_auto_substitution(tool_name: str) -> bool:
    """Check if a tool opts out of automatic variable substitution.

    Tools that skip auto-substitution handle $[var] placeholders themselves,
    typically because they need context-specific resolution (e.g., create_pdf
    converts filespace paths to data URIs instead of signed URLs).
    """
    if tool_name in SKIP_AUTO_SUBSTITUTION_TOOL_NAMES:
        return True
    entry = BUILTIN_TOOL_REGISTRY.get(tool_name)
    if entry:
        return entry.get("skip_auto_substitution", False)
    return False


BUILTIN_TOOL_REGISTRY = {
    SQLITE_TOOL_NAME: {
        "definition": get_sqlite_batch_tool,
        "executor": execute_sqlite_batch,
    },
    HTTP_REQUEST_TOOL_NAME: {
        "definition": get_http_request_tool,
        "executor": execute_http_request,
    },
    READ_FILE_TOOL_NAME: {
        "definition": get_read_file_tool,
        "executor": execute_read_file,
    },
    CREATE_FILE_TOOL_NAME: {
        "definition": get_create_file_tool,
        "executor": execute_create_file,
        "sandboxed": True,
    },
    CREATE_CSV_TOOL_NAME: {
        "definition": get_create_csv_tool,
        "executor": execute_create_csv,
        "sandboxed": True,
    },
    CREATE_PDF_TOOL_NAME: {
        "definition": get_create_pdf_tool,
        "executor": execute_create_pdf,
        "skip_auto_substitution": True,  # PDF does its own substitution (data URIs for embedded assets)
        "sandboxed": True,
    },
    CREATE_CHART_TOOL_NAME: {
        "definition": get_create_chart_tool,
        "executor": execute_create_chart,
        "sandboxed": True,
    },
    PYTHON_EXEC_TOOL_NAME: {
        "definition": get_python_exec_tool,
        "sandboxed": True,
        "sandbox_only": True,
    },
    RUN_COMMAND_TOOL_NAME: {
        "definition": get_run_command_tool,
        "executor": execute_run_command,
        "sandbox_only": True,
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


def _normalize_tool_limit(
    limit: Optional[int],
    fallback: int = DEFAULT_STANDARD_ENABLED_TOOL_LIMIT,
) -> int:
    baseline = max(int(fallback or 1), 1)
    try:
        parsed = int(limit) if limit is not None else baseline
    except (TypeError, ValueError):  # pragma: no cover - defensive fallback
        parsed = baseline
    return max(parsed, 1)


def get_enabled_tool_limit(agent: Optional[PersistentAgent]) -> int:
    """Return the configured tool cap for the agent's tier."""
    fallback = DEFAULT_STANDARD_ENABLED_TOOL_LIMIT
    if agent is None:
        return _normalize_tool_limit(None, fallback)

    try:
        settings = get_prompt_settings()
        fallback = settings.standard_enabled_tool_limit
        tier = get_agent_llm_tier(agent)
        limit_map = {
            AgentLLMTier.ULTRA_MAX: settings.ultra_max_enabled_tool_limit,
            AgentLLMTier.ULTRA: settings.ultra_enabled_tool_limit,
            AgentLLMTier.MAX: settings.max_enabled_tool_limit,
            AgentLLMTier.PREMIUM: settings.premium_enabled_tool_limit,
        }
        return _normalize_tool_limit(limit_map.get(tier, fallback), fallback)
    except Exception:  # pragma: no cover - defensive fallback
        logger.exception("Failed to resolve enabled tool limit for agent %s", getattr(agent, "id", None))
        return _normalize_tool_limit(None, fallback)


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
        if info.get("sandbox_only") and not sandbox_compute_enabled_for_agent(agent):
            continue
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


def _evict_surplus_tools(
    agent: PersistentAgent,
    exclude: Optional[Sequence[str]] = None,
    *,
    limit: Optional[int] = None,
) -> List[str]:
    """Enforce the enabled tool cap by evicting the least recently used entries."""
    cap = _normalize_tool_limit(limit if limit is not None else get_enabled_tool_limit(agent))
    total = PersistentAgentEnabledTool.objects.filter(agent=agent).count()
    if total <= cap:
        return []

    overflow = total - cap
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
        cap,
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
    """Enable multiple tools for an agent, respecting the tiered cap."""
    catalog = _build_available_tool_index(agent)
    manager = _get_manager()
    limit = get_enabled_tool_limit(agent)

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

    resolved_seen: Set[str] = set()
    for name in requested:
        entry = catalog.get(name)
        resolved_name = name
        if not entry:
            resolved_name = _normalize_mcp_tool_name(name, catalog) or name
            entry = catalog.get(resolved_name)
            if entry and resolved_name != name:
                logger.info("Normalized tool name '%s' -> '%s' during enable_tools", name, resolved_name)
        if not entry:
            invalid.append(name)
            continue
        if resolved_name in resolved_seen:
            continue
        resolved_seen.add(resolved_name)

        if entry.provider == "mcp" and manager.is_tool_blacklisted(resolved_name):
            invalid.append(name)
            continue

        try:
            row, created = PersistentAgentEnabledTool.objects.get_or_create(
                agent=agent,
                tool_full_name=resolved_name,
            )
        except Exception:
            logger.exception("Failed enabling tool %s", resolved_name)
            invalid.append(name)
            continue

        if created:
            metadata_updates = _apply_tool_metadata(row, entry)
            if metadata_updates:
                row.save(update_fields=metadata_updates)
            enabled.append(resolved_name)
        else:
            metadata_updates = _apply_tool_metadata(row, entry)
            if metadata_updates:
                row.save(update_fields=metadata_updates)
            already_enabled.append(resolved_name)

    if enabled or already_enabled:
        evicted = _evict_surplus_tools(agent, limit=limit)

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


def _auto_enable_tool_for_execution(agent: PersistentAgent, entry: ToolCatalogEntry) -> Dict[str, Any]:
    """Enable a tool just in time without recording usage (execution will handle usage)."""
    tool_name = entry.full_name
    if entry.provider == "mcp":
        manager = _get_manager()
        if manager.is_tool_blacklisted(tool_name):
            return {
                "status": "error",
                "message": f"Tool '{tool_name}' is blacklisted and cannot be enabled",
            }

    try:
        row, created = PersistentAgentEnabledTool.objects.get_or_create(
            agent=agent,
            tool_full_name=tool_name,
        )
    except Exception:
        logger.exception("Failed to auto-enable tool %s for agent %s", tool_name, getattr(agent, "id", None))
        return {"status": "error", "message": f"Failed to enable tool '{tool_name}'"}

    metadata_updates = _apply_tool_metadata(row, entry)
    if metadata_updates:
        row.save(update_fields=metadata_updates)

    evicted = _evict_surplus_tools(agent, exclude=[tool_name], limit=get_enabled_tool_limit(agent))
    if created:
        logger.info("Auto-enabled tool '%s' for agent %s", tool_name, agent.id)
    if evicted:
        logger.info(
            "Auto-enabled tool '%s' evicted %d tool(s) for agent %s: %s",
            tool_name,
            len(evicted),
            agent.id,
            ", ".join(evicted),
        )

    return {
        "status": "success",
        "enabled": tool_name,
        "already_enabled": not created,
        "evicted": evicted,
    }


def enable_mcp_tool(agent: PersistentAgent, tool_name: str) -> Dict[str, Any]:
    """Enable a single MCP tool for the agent (with LRU eviction if needed)."""
    catalog = _build_available_tool_index(agent)
    manager = _get_manager()
    limit = get_enabled_tool_limit(agent)

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

    evicted = _evict_surplus_tools(agent, exclude=[tool_name], limit=limit)
    disabled_tool = evicted[0] if evicted else None

    message = f"Successfully enabled tool '{tool_name}'"
    if disabled_tool:
        message += f" (disabled '{disabled_tool}' due to {limit} tool limit)"

    return {
        "status": "success",
        "message": message,
        "enabled": tool_name,
        "disabled": disabled_tool,
    }


def mark_tool_enabled_without_discovery(agent: PersistentAgent, tool_name: str) -> Dict[str, Any]:
    """
    Trust a tool name and ensure it is marked enabled without refreshing the MCP catalog.

    This bypasses MCP server discovery and only touches the persistence row + LRU eviction.
    """
    if not tool_name:
        return {"status": "error", "message": "Tool name is required"}

    now = datetime.now(UTC)
    try:
        row = PersistentAgentEnabledTool.objects.filter(
            agent=agent,
            tool_full_name=tool_name,
        ).first()
    except Exception as exc:
        logger.error("Failed to look up enabled tool %s: %s", tool_name, exc)
        return {"status": "error", "message": str(exc)}

    if row:
        row.last_used_at = now
        row.usage_count = (row.usage_count or 0) + 1
        row.save(update_fields=["last_used_at", "usage_count"])
        return {
            "status": "success",
            "message": f"Tool '{tool_name}' is already enabled (metadata untouched)",
            "enabled": tool_name,
            "disabled": None,
        }

    try:
        row = PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name=tool_name,
            last_used_at=now,
            usage_count=1,
        )
    except Exception as exc:
        logger.error("Failed to mark tool %s enabled without discovery: %s", tool_name, exc)
        return {"status": "error", "message": str(exc)}

    evicted = _evict_surplus_tools(agent, exclude=[tool_name])
    disabled_tool = evicted[0] if evicted else None

    message = f"Marked tool '{tool_name}' enabled without discovery"
    if disabled_tool:
        message += f" (disabled '{disabled_tool}' due to tool limit)"

    return {
        "status": "success",
        "message": message,
        "enabled": tool_name,
        "disabled": disabled_tool,
    }


def ensure_default_tools_enabled(
    agent: PersistentAgent,
    *,
    allowed_server_names: Optional[Iterable[str]] = None,
) -> None:
    """Ensure the default tool set is enabled for new agents."""
    manager = _get_manager()

    enabled_tools = set(
        PersistentAgentEnabledTool.objects.filter(agent=agent).values_list("tool_full_name", flat=True)
    )
    default_tools = set(MCPToolManager.DEFAULT_ENABLED_TOOLS)
    missing_mcp = default_tools - enabled_tools
    missing_builtin = DEFAULT_BUILTIN_TOOLS - enabled_tools
    if not missing_mcp and not missing_builtin:
        return

    available = set()
    if missing_mcp:
        available = {
            tool.full_name
            for tool in manager.get_tools_for_agent(agent, allowed_server_names=allowed_server_names)
        }

    for tool_name in missing_mcp:
        if manager.is_tool_blacklisted(tool_name):
            logger.warning("Default tool '%s' is blacklisted, skipping", tool_name)
            continue
        if tool_name not in available:
            logger.warning("Default tool '%s' not found in available tools", tool_name)
            continue
        enable_mcp_tool(agent, tool_name)
        logger.info("Enabled default tool '%s' for agent %s", tool_name, agent.id)

    for tool_name in missing_builtin:
        if tool_name not in BUILTIN_TOOL_REGISTRY:
            logger.warning("Default builtin tool '%s' not registered, skipping", tool_name)
            continue
        mark_tool_enabled_without_discovery(agent, tool_name)
        logger.info("Enabled default builtin tool '%s' for agent %s", tool_name, agent.id)


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

    # Check sqlite eligibility once for filtering
    sqlite_eligible = is_sqlite_enabled_for_agent(agent)

    for row in enabled_builtin_rows:
        # Skip sqlite_batch if agent is not eligible (even if previously enabled)
        if row.tool_full_name == SQLITE_TOOL_NAME and not sqlite_eligible:
            continue

        registry_entry = BUILTIN_TOOL_REGISTRY.get(row.tool_full_name)
        if not registry_entry:
            continue
        if registry_entry.get("sandbox_only") and not sandbox_compute_enabled_for_agent(agent):
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


def _normalize_mcp_tool_name(tool_name: str, catalog: Dict[str, "ToolCatalogEntry"]) -> Optional[str]:
    """Try to find a matching MCP tool name using fuzzy matching.

    Handles common LLM mistakes like:
    - mcp_bright_data_... vs mcp_brightdata_...
    - Extra underscores in server names
    """
    if not tool_name.startswith("mcp_"):
        return None

    # Try normalizing by removing underscores from the server name portion
    # mcp_bright_data_tool -> mcp_brightdata_tool
    normalized = tool_name.replace("mcp_bright_data_", "mcp_brightdata_")
    if normalized in catalog:
        return normalized

    # Try finding a tool that matches when we normalize both names
    tool_name_collapsed = tool_name.replace("_", "").lower()
    for candidate in catalog:
        if candidate.replace("_", "").lower() == tool_name_collapsed:
            return candidate

    return None


def resolve_tool_entry(agent: PersistentAgent, tool_name: str) -> Optional[ToolCatalogEntry]:
    """Return catalog entry for the given tool name if available.

    Attempts fuzzy matching for MCP tools when exact match fails.
    """
    catalog = _build_available_tool_index(agent)

    # Try exact match first
    entry = catalog.get(tool_name)
    if entry:
        return entry

    # Try fuzzy matching for MCP tools
    if tool_name.startswith("mcp_"):
        normalized_name = _normalize_mcp_tool_name(tool_name, catalog)
        if normalized_name:
            logger.info(
                "Normalized MCP tool name '%s' -> '%s'",
                tool_name, normalized_name
            )
            return catalog.get(normalized_name)
    if tool_name.startswith("mcp_"):
        # Last resort: try MCP manager's resolve_tool_info which can discover tools
        manager = _get_manager()
        info = manager.resolve_tool_info(tool_name)
        if info:
            logger.info(
                "Resolved MCP tool '%s' via manager discovery (server=%s)",
                tool_name, info.server_name
            )
            return ToolCatalogEntry(
                provider="mcp",
                full_name=info.full_name,
                description=info.description,
                parameters=info.parameters,
                tool_server=info.server_name,
                tool_name=info.tool_name,
                server_config_id=info.config_id,
            )

    return None


def auto_enable_heuristic_tools(
    agent: PersistentAgent,
    text: str,
    *,
    max_auto_enable: int = 5,
) -> List[str]:
    """
    Heuristically auto-enable site-specific tools based on keyword mentions in text.

    Only enables tools if there is room in the agent's tool budget - will NOT evict
    existing tools. This is a best-effort optimization to pre-enable relevant tools
    before the LLM needs them.

    Args:
        agent: The agent to enable tools for.
        text: Text to scan for keyword mentions (typically user message).
        max_auto_enable: Maximum number of tools to auto-enable per call.

    Returns:
        List of tool names that were successfully auto-enabled.
    """
    if not text or not agent:
        return []

    # Find tools that match keywords in the text
    matched_tools = find_matching_tools(text)
    if not matched_tools:
        return []

    # Check current capacity
    cap = get_enabled_tool_limit(agent)
    current_count = PersistentAgentEnabledTool.objects.filter(agent=agent).count()
    available_slots = cap - current_count

    # If no room, don't auto-enable (never evict for heuristic matches)
    if available_slots <= 0:
        logger.debug(
            "Skipping autotool heuristics for agent %s: at capacity (%d/%d)",
            agent.id,
            current_count,
            cap,
        )
        return []

    # Filter out already-enabled tools
    already_enabled = set(
        PersistentAgentEnabledTool.objects.filter(
            agent=agent,
            tool_full_name__in=matched_tools,
        ).values_list("tool_full_name", flat=True)
    )
    to_enable = [t for t in matched_tools if t not in already_enabled]

    if not to_enable:
        return []

    # Limit to available slots and max_auto_enable cap
    to_enable = to_enable[: min(available_slots, max_auto_enable)]

    # Get the catalog to validate tools exist and get metadata
    catalog = _build_available_tool_index(agent)
    manager = _get_manager()

    enabled: List[str] = []
    for tool_name in to_enable:
        entry = catalog.get(tool_name)
        if not entry:
            logger.debug("Autotool heuristic: tool %s not in catalog, skipping", tool_name)
            continue

        if entry.provider == "mcp" and manager.is_tool_blacklisted(tool_name):
            logger.debug("Autotool heuristic: tool %s is blacklisted, skipping", tool_name)
            continue

        try:
            row, created = PersistentAgentEnabledTool.objects.get_or_create(
                agent=agent,
                tool_full_name=tool_name,
            )
            if created:
                metadata_updates = _apply_tool_metadata(row, entry)
                if metadata_updates:
                    row.save(update_fields=metadata_updates)
                enabled.append(tool_name)
                logger.info(
                    "Autotool heuristic: enabled %s for agent %s",
                    tool_name,
                    agent.id,
                )
        except Exception:
            logger.exception("Autotool heuristic: failed to enable %s", tool_name)
            continue

    return enabled


def execute_enabled_tool(agent: PersistentAgent, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an enabled tool, routing to the appropriate provider."""
    entry = resolve_tool_entry(agent, tool_name)
    if not entry:
        return {"status": "error", "message": f"Tool '{tool_name}' is not available"}

    # Use the resolved tool name (may differ from input if normalized)
    resolved_name = entry.full_name

    # Coerce params to match expected types (handles LLM passing "true" instead of true, etc.)
    params = _coerce_params_to_schema(params, entry.parameters)

    # Block sqlite execution for ineligible agents (even if previously enabled)
    if resolved_name == SQLITE_TOOL_NAME and not is_sqlite_enabled_for_agent(agent):
        message = "Database tool is not available for this deployment."
        if getattr(settings, "GOBII_PROPRIETARY_MODE", False):
            message = (
                "Database tool is not available on your current plan. "
                "Upgrade to a paid plan with max intelligence to access this feature."
            )
        return {
            "status": "error",
            "message": message,
        }

    if not PersistentAgentEnabledTool.objects.filter(agent=agent, tool_full_name=resolved_name).exists():
        auto_enable = _auto_enable_tool_for_execution(agent, entry)
        if auto_enable.get("status") != "success":
            return {
                "status": "error",
                "message": auto_enable.get("message", f"Tool '{resolved_name}' is not enabled for this agent"),
            }

    if entry.provider == "mcp":
        return execute_mcp_tool(agent, resolved_name, params)

    if entry.provider == "builtin":
        registry_entry = BUILTIN_TOOL_REGISTRY.get(resolved_name)
        executor = registry_entry.get("executor") if registry_entry else None
        if registry_entry:
            try:
                row = PersistentAgentEnabledTool.objects.filter(
                    agent=agent,
                    tool_full_name=resolved_name,
                ).first()
            except Exception:
                row = None
                logger.exception("Failed to load enabled entry for builtin tool %s", resolved_name)

            if row:
                try:
                    row.last_used_at = datetime.now(UTC)
                    row.usage_count = (row.usage_count or 0) + 1
                    update_fields = ["last_used_at", "usage_count"]
                    metadata_updates = _apply_tool_metadata(row, entry)
                    if metadata_updates:
                        update_fields.extend(metadata_updates)
                    row.save(update_fields=list(dict.fromkeys(update_fields)))
                except Exception:
                    logger.exception("Failed to record usage for builtin tool %s", resolved_name)

            if registry_entry.get("sandbox_only") and not sandbox_compute_enabled_for_agent(agent):
                return {
                    "status": "error",
                    "message": f"Tool '{resolved_name}' requires sandbox compute.",
                }

            if registry_entry.get("sandboxed") and sandbox_compute_enabled_for_agent(agent):
                try:
                    service = SandboxComputeService()
                except SandboxComputeUnavailable as exc:
                    return {"status": "error", "message": str(exc)}
                sandbox_result = service.tool_request(agent, resolved_name, params)
                if (
                    isinstance(sandbox_result, dict)
                    and sandbox_result.get("error_code") == "sandbox_unsupported_tool"
                    and resolved_name in _sandbox_fallback_tools()
                    and executor
                ):
                    return executor(agent, params)
                return sandbox_result

        if executor:
            return executor(agent, params)

    return {"status": "error", "message": f"Tool '{resolved_name}' has no execution handler"}
