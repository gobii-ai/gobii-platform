"""
Generic tool enable/disable management for persistent agents.

Dynamic tools can come from MCP, built-ins, or agent-authored custom tools.
These helpers live outside the MCP manager so multiple providers can share the
same persistence logic.
"""

import fnmatch
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from django.conf import settings
from django.db import DatabaseError
from django.db.models import F

from util.text_sanitizer import decode_unicode_escapes

from api.agent.eval_agents import is_eval_agent

from ...models import PersistentAgent, PersistentAgentCustomTool, PersistentAgentEnabledTool, PersistentAgentSystemSkillState
from ...services.sandbox_compute import SandboxComputeService, SandboxComputeUnavailable, sandbox_compute_enabled_for_agent, track_sandbox_unavailable
from ...services.prompt_settings import get_prompt_settings, DEFAULT_STANDARD_ENABLED_TOOL_LIMIT
from ...services.mcp_servers import agent_accessible_server_configs
from ...services.pipedream_apps import (
    filter_deprecated_pipedream_tools_for_agent,
    get_pipedream_app_visibility_for_agent,
    is_pipedream_tool_visible_to_agent,
    pipedream_app_slug_for_tool_name,
)
from ...services.tool_blacklist import get_agent_tool_blacklist, is_tool_blacklisted_for_agent, tool_blacklist_error
from ...utils.json_schema import sanitize_tool_parameters_schema_for_llm
from ..core.llm_config import AgentLLMTier, get_agent_llm_tier
from .mcp_manager import MCPToolInfo, MCPToolManager, get_mcp_manager, execute_mcp_tool, execute_mcp_tool_isolated
from .sqlite_batch import get_sqlite_batch_tool, execute_sqlite_batch
from .http_request import get_http_request_tool, execute_http_request
from .brightdata import (
    BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME,
    BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME,
    BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME,
    execute_brightdata_linkedin_person_profile,
    execute_brightdata_scrape_as_markdown,
    execute_brightdata_search_engine,
    get_brightdata_linkedin_person_profile_tool,
    get_brightdata_scrape_as_markdown_tool,
    get_brightdata_search_engine_tool,
)
from .read_file import get_read_file_tool, execute_read_file
from .create_file import get_create_file_tool, execute_create_file
from .create_csv import get_create_csv_tool, execute_create_csv
from .create_pdf import get_create_pdf_tool, execute_create_pdf
from .create_chart import get_create_chart_tool, execute_create_chart
from .create_image import get_create_image_tool, execute_create_image, is_image_generation_available_for_agent
from .create_video import get_create_video_tool, execute_create_video, is_video_generation_available_for_agent
from .custom_tools import execute_create_custom_tool, execute_custom_tool, get_create_custom_tool_tool, is_custom_tools_available_for_agent
from .custom_tool_names import CREATE_CUSTOM_TOOL_NAME, CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY
from .eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_DEFINITIONS, EVAL_SYNTHETIC_TOOL_SERVER, get_eval_synthetic_tool_definition, get_eval_synthetic_tool_fallback_result, is_eval_synthetic_tool_name
from .python_exec import get_python_exec_tool
from .run_command import get_run_command_tool, execute_run_command
from .meta_ads import get_meta_ads_tool, execute_meta_ads
from .add_discord_reaction import get_add_discord_reaction_tool, execute_add_discord_reaction
from .discord_channel_subscriptions import get_discord_channel_subscriptions_tool, execute_discord_channel_subscriptions
from .send_discord_message import get_send_discord_message_tool, execute_send_discord_message
from api.agent.system_skills.defaults import DISCORD_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.image_generation import IMAGE_GENERATION_SYSTEM_SKILL_KEY
from .meta_gobii import execute_meta_gobii_tool, get_meta_gobii_tool_definition, is_meta_gobii_available_for_agent
from .meta_gobii_names import META_GOBII_SYSTEM_SKILL_KEY, META_GOBII_TOOL_NAMES
from .autotool_heuristics import find_matching_tools
from .sqlite_skills import get_required_skill_tool_ids
from .static_tools import get_static_tool_names, planning_mode_disallows_tool

logger = logging.getLogger(__name__)

SQLITE_TOOL_NAME = "sqlite_batch"
HTTP_REQUEST_TOOL_NAME = "http_request"
READ_FILE_TOOL_NAME = "read_file"
CREATE_FILE_TOOL_NAME = "create_file"
CREATE_CSV_TOOL_NAME = "create_csv"
CREATE_PDF_TOOL_NAME = "create_pdf"
CREATE_CHART_TOOL_NAME = "create_chart"
CREATE_IMAGE_TOOL_NAME = "create_image"
CREATE_VIDEO_TOOL_NAME = "create_video"
PYTHON_EXEC_TOOL_NAME = "python_exec"
RUN_COMMAND_TOOL_NAME = "run_command"
META_ADS_TOOL_NAME = "meta_ads"
DISCORD_CHANNEL_SUBSCRIPTIONS_TOOL_NAME = "discord_channel_subscriptions"
DISCORD_ADD_REACTION_TOOL_NAME = "add_discord_reaction"
DISCORD_SEND_MESSAGE_TOOL_NAME = "send_discord_message"
PIPEDREAM_TOOL_SERVER_NAME = "pipedream"
DEFAULT_BUILTIN_TOOLS = {
    READ_FILE_TOOL_NAME,
    SQLITE_TOOL_NAME,
    CREATE_CHART_TOOL_NAME,
    BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME,
    BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME,
}


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


def _normalize_tool_params_unicode_escapes(params: Any) -> Any:
    if isinstance(params, str):
        return decode_unicode_escapes(params)
    if isinstance(params, dict):
        return {key: _normalize_tool_params_unicode_escapes(value) for key, value in params.items()}
    if isinstance(params, list):
        return [_normalize_tool_params_unicode_escapes(item) for item in params]
    return params


def _is_pipedream_entry(entry: "ToolCatalogEntry") -> bool:
    return entry.provider == "mcp" and entry.tool_server == PIPEDREAM_TOOL_SERVER_NAME


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
    "create_image",
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
        # Keep sqlite availability centralized so search/discovery and runtime
        # execution expose the same builtins.
        "is_available": is_sqlite_enabled_for_agent,
    },
    HTTP_REQUEST_TOOL_NAME: {
        "definition": get_http_request_tool,
        "executor": execute_http_request,
        "parallel_safe": True,
    },
    BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME: {
        "definition": get_brightdata_search_engine_tool,
        "executor": execute_brightdata_search_engine,
        "parallel_safe": True,
    },
    BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME: {
        "definition": get_brightdata_scrape_as_markdown_tool,
        "executor": execute_brightdata_scrape_as_markdown,
        "parallel_safe": True,
    },
    BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME: {
        "definition": get_brightdata_linkedin_person_profile_tool,
        "executor": execute_brightdata_linkedin_person_profile,
        "parallel_safe": True,
    },
    READ_FILE_TOOL_NAME: {
        "definition": get_read_file_tool,
        "executor": execute_read_file,
        "parallel_safe": True,
    },
    CREATE_FILE_TOOL_NAME: {
        "definition": get_create_file_tool,
        "executor": execute_create_file,
        "sandboxed": False,
    },
    CREATE_CSV_TOOL_NAME: {
        "definition": get_create_csv_tool,
        "executor": execute_create_csv,
        "parallel_safe": True,
    },
    CREATE_PDF_TOOL_NAME: {
        "definition": get_create_pdf_tool,
        "executor": execute_create_pdf,
        "skip_auto_substitution": True,  # PDF does its own substitution (data URIs for embedded assets)
        "sandboxed": False,
        "parallel_safe": True,
    },
    CREATE_CHART_TOOL_NAME: {
        "definition": get_create_chart_tool,
        "executor": execute_create_chart,
        "sandboxed": False,
        "parallel_safe": True,
    },
    CREATE_IMAGE_TOOL_NAME: {
        "definition": get_create_image_tool,
        "executor": execute_create_image,
        "is_available": is_image_generation_available_for_agent,
        "system_skill_key": IMAGE_GENERATION_SYSTEM_SKILL_KEY,
    },
    CREATE_VIDEO_TOOL_NAME: {
        "definition": get_create_video_tool,
        "executor": execute_create_video,
        "is_available": is_video_generation_available_for_agent,
    },
    CREATE_CUSTOM_TOOL_NAME: {
        "definition": get_create_custom_tool_tool,
        "executor": execute_create_custom_tool,
        "sandbox_only": True,
        "search_hidden": True,
        "system_skill_key": CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY,
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
    META_ADS_TOOL_NAME: {
        "definition": get_meta_ads_tool,
        "executor": execute_meta_ads,
        "search_hidden": True,
        "system_skill_key": "meta_ads_platform",
    },
    DISCORD_CHANNEL_SUBSCRIPTIONS_TOOL_NAME: {
        "definition": get_discord_channel_subscriptions_tool,
        "executor": execute_discord_channel_subscriptions,
        "search_hidden": True,
        "system_skill_key": DISCORD_NATIVE_SYSTEM_SKILL_KEY,
    },
    DISCORD_ADD_REACTION_TOOL_NAME: {
        "definition": get_add_discord_reaction_tool,
        "executor": execute_add_discord_reaction,
        "search_hidden": True,
        "system_skill_key": DISCORD_NATIVE_SYSTEM_SKILL_KEY,
    },
    DISCORD_SEND_MESSAGE_TOOL_NAME: {
        "definition": get_send_discord_message_tool,
        "executor": execute_send_discord_message,
        "search_hidden": True,
        "system_skill_key": DISCORD_NATIVE_SYSTEM_SKILL_KEY,
    },
    **{
        tool_name: {
            "definition": lambda tool_name=tool_name: get_meta_gobii_tool_definition(tool_name),
            "executor": lambda agent, params, tool_name=tool_name: execute_meta_gobii_tool(agent, tool_name, params),
            "is_available": is_meta_gobii_available_for_agent,
            "search_hidden": True,
            "system_skill_key": META_GOBII_SYSTEM_SKILL_KEY,
        }
        for tool_name in META_GOBII_TOOL_NAMES
    },
}


def _is_builtin_tool_available(
    tool_name: str,
    agent: Optional[PersistentAgent],
    *,
    include_hidden: bool = False,
) -> bool:
    """Return whether a builtin tool should be exposed for this agent."""
    entry = BUILTIN_TOOL_REGISTRY.get(tool_name)
    if not entry:
        return False

    if entry.get("search_hidden") and not include_hidden:
        return False

    if planning_mode_disallows_tool(agent, tool_name):
        return False

    if entry.get("sandbox_only"):
        if agent is None or not sandbox_compute_enabled_for_agent(agent):
            return False

    availability_check = entry.get("is_available")
    if callable(availability_check):
        try:
            return bool(availability_check(agent))
        except Exception:
            logger.exception("Builtin availability check failed for %s", tool_name)
            return False

    return True


def _build_builtin_tool_definition(
    tool_name: str,
    registry_entry: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Build and validate a builtin tool definition."""
    try:
        tool_def = registry_entry["definition"]()
    except Exception:
        logger.exception("Failed to build builtin tool definition for %s", tool_name)
        return None

    if not isinstance(tool_def, dict):
        logger.warning("Builtin tool %s returned non-dict definition", tool_name)
        return None
    return tool_def


def _build_builtin_catalog_entry(
    tool_name: str,
    registry_entry: Dict[str, Any],
) -> Optional["ToolCatalogEntry"]:
    """Build a catalog entry for a builtin tool."""
    tool_def = _build_builtin_tool_definition(tool_name, registry_entry)
    if not tool_def:
        return None
    function_block = tool_def.get("function") if isinstance(tool_def, dict) else {}
    return ToolCatalogEntry(
        provider="builtin",
        full_name=tool_name,
        description=function_block.get("description", ""),
        parameters=function_block.get("parameters", {}),
        tool_server="builtin",
        tool_name=tool_name,
        server_config_id=None,
        system_skill_key=str(registry_entry.get("system_skill_key") or ""),
    )


def get_available_builtin_tool_entries(
    agent: Optional[PersistentAgent],
    *,
    include_hidden: bool = False,
) -> Dict[str, "ToolCatalogEntry"]:
    """Return builtin tool catalog entries available to the provided agent."""
    catalog: Dict[str, ToolCatalogEntry] = {}
    blacklisted_tools = get_agent_tool_blacklist(agent)
    for name, registry_entry in BUILTIN_TOOL_REGISTRY.items():
        if name in blacklisted_tools:
            continue
        if not _is_builtin_tool_available(name, agent, include_hidden=include_hidden):
            continue
        entry = _build_builtin_catalog_entry(name, registry_entry)
        if entry:
            catalog[name] = entry
    return catalog


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
    system_skill_key: str = ""
    mcp_info: Optional[MCPToolInfo] = None


def _custom_tool_parameters_for_llm(parameters_schema: Any) -> Dict[str, Any]:
    return sanitize_tool_parameters_schema_for_llm(parameters_schema)


def _sanitize_tool_definition_for_llm(tool_def: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(tool_def, dict):
        return tool_def

    function_block = tool_def.get("function")
    if not isinstance(function_block, dict):
        return tool_def

    sanitized = dict(tool_def)
    sanitized_function = dict(function_block)
    sanitized_function["parameters"] = sanitize_tool_parameters_schema_for_llm(
        function_block.get("parameters")
    )
    sanitized["function"] = sanitized_function
    return sanitized


def _tool_definition_name(tool_def: Dict[str, Any]) -> Optional[str]:
    function_block = tool_def.get("function") if isinstance(tool_def, dict) else None
    if not isinstance(function_block, dict):
        return None
    name = function_block.get("name")
    return name if isinstance(name, str) and name else None


def get_available_custom_tool_entries(
    agent: Optional[PersistentAgent],
) -> Dict[str, ToolCatalogEntry]:
    """Return custom tool catalog entries available to the provided agent."""
    if not is_custom_tools_available_for_agent(agent):
        return {}

    catalog: Dict[str, ToolCatalogEntry] = {}
    blacklisted_tools = get_agent_tool_blacklist(agent)
    for tool in PersistentAgentCustomTool.objects.filter(agent=agent).order_by("tool_name"):
        if tool.tool_name in blacklisted_tools:
            continue
        catalog[tool.tool_name] = ToolCatalogEntry(
            provider="custom",
            full_name=tool.tool_name,
            description=tool.description,
            parameters=_custom_tool_parameters_for_llm(tool.parameters_schema),
            tool_server="custom",
            tool_name=tool.tool_name,
            server_config_id=None,
        )
    return catalog


def _get_manager() -> MCPToolManager:
    """Return the process manager without triggering global MCP discovery."""
    return get_mcp_manager()


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


def _build_available_tool_index(
    agent: PersistentAgent,
    *,
    include_hidden_builtin: bool = False,
    include_mcp: bool = True,
) -> Dict[str, ToolCatalogEntry]:
    """Build an index of enableable tools across all providers."""
    catalog: Dict[str, ToolCatalogEntry] = {}
    blacklisted_tools = get_agent_tool_blacklist(agent)

    if include_mcp:
        manager = _get_manager()
        hide_pipedream_tools = is_eval_agent(agent)
        visible_mcp_tools = filter_deprecated_pipedream_tools_for_agent(
            agent,
            manager.get_tools_for_agent(agent),
        )
        for info in visible_mcp_tools:
            if info.full_name in blacklisted_tools:
                continue
            if hide_pipedream_tools and info.server_name == PIPEDREAM_TOOL_SERVER_NAME:
                continue
            catalog[info.full_name] = ToolCatalogEntry(
                provider="mcp",
                full_name=info.full_name,
                description=info.description,
                parameters=info.parameters,
                tool_server=info.server_name,
                tool_name=info.tool_name,
                server_config_id=info.config_id,
                mcp_info=info,
            )

    catalog.update(get_available_builtin_tool_entries(agent, include_hidden=include_hidden_builtin))
    catalog.update(get_available_custom_tool_entries(agent))
    for tool_name, metadata in EVAL_SYNTHETIC_TOOL_DEFINITIONS.items():
        if tool_name in blacklisted_tools:
            continue
        tool_def = get_eval_synthetic_tool_definition(agent, tool_name)
        if not tool_def:
            continue
        catalog[tool_name] = ToolCatalogEntry(
            provider="eval",
            full_name=tool_name,
            description=metadata["description"],
            parameters=metadata["parameters"],
            tool_server=EVAL_SYNTHETIC_TOOL_SERVER,
            tool_name=tool_name,
            server_config_id=None,
            system_skill_key=str(metadata.get("system_skill_key") or ""),
        )

    return catalog


def get_available_tool_ids(agent: PersistentAgent) -> Set[str]:
    """Return canonical tool IDs currently available to the agent."""
    return set(_build_available_tool_index(agent).keys()) | get_static_tool_names(agent)


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


def _skill_tool_entry(
    agent: PersistentAgent,
    tool_name: str,
    custom_entries: Dict[str, ToolCatalogEntry],
    mcp_configs: Sequence[Any],
    row: Optional[PersistentAgentEnabledTool] = None,
) -> Optional[ToolCatalogEntry]:
    """Resolve locally available metadata without remote catalog discovery."""
    registry_entry = BUILTIN_TOOL_REGISTRY.get(tool_name)
    if registry_entry:
        return _build_builtin_catalog_entry(tool_name, registry_entry)
    if tool_name in custom_entries:
        return custom_entries[tool_name]
    if tool_name in EVAL_SYNTHETIC_TOOL_DEFINITIONS:
        definition = get_eval_synthetic_tool_definition(agent, tool_name)
        metadata = EVAL_SYNTHETIC_TOOL_DEFINITIONS[tool_name]
        if definition:
            return ToolCatalogEntry(
                "eval", tool_name, metadata["description"], metadata["parameters"],
                EVAL_SYNTHETIC_TOOL_SERVER, tool_name,
                system_skill_key=str(metadata.get("system_skill_key") or ""),
            )
    if row and row.tool_server:
        return ToolCatalogEntry(
            provider="mcp" if row.tool_server not in {"builtin", "custom"} else row.tool_server,
            full_name=tool_name,
            description="",
            parameters={},
            tool_server=row.tool_server,
            tool_name=row.tool_name or tool_name,
            server_config_id=str(row.server_config_id) if row.server_config_id else None,
        )
    if not tool_name.startswith("mcp_"):
        return None
    matching_configs = [
        (len(prefix), config, tool_name[len(prefix):])
        for config in mcp_configs
        if tool_name.startswith(prefix := f"mcp_{config.name}_")
    ]
    if not matching_configs:
        return None
    _prefix_length, config, raw_tool_name = max(matching_configs, key=lambda item: item[0])
    return ToolCatalogEntry(
        provider="mcp",
        full_name=tool_name,
        description="",
        parameters={},
        tool_server=config.name,
        tool_name=raw_tool_name,
        server_config_id=str(config.id),
    )


def _resolve_missing_skill_tool(
    agent: PersistentAgent,
    tool_name: str,
    local_entry: Optional[ToolCatalogEntry],
) -> Optional[ToolCatalogEntry]:
    """Resolve one missing requirement using only its local or inferred provider."""
    if local_entry and local_entry.provider != "mcp":
        if local_entry.provider == "builtin" and not _is_builtin_tool_available(
            tool_name, agent, include_hidden=True
        ):
            return None
        return local_entry
    app_slug = pipedream_app_slug_for_tool_name(tool_name)
    if not local_entry and not app_slug:
        return None

    manager = _get_manager()
    if local_entry:
        discovered = manager.get_tools_for_agent(
            agent,
            allowed_config_ids={local_entry.server_config_id},
        )
    else:
        discovered = manager.get_tools_for_agent(
            agent,
            allowed_server_names={PIPEDREAM_TOOL_SERVER_NAME},
            pipedream_app_slugs={app_slug},
        )
    for info in discovered:
        if info.full_name == tool_name:
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


def _is_mcp_tool_blacklisted(tool_name: str) -> bool:
    return any(fnmatch.fnmatch(tool_name, pattern) for pattern in MCPToolManager.TOOL_BLACKLIST)


def ensure_skill_tools_enabled(agent: PersistentAgent) -> Dict[str, Any]:
    """Ensure all tools required by latest persisted skills are enabled."""
    required = sorted(get_required_skill_tool_ids(agent))
    limit = get_enabled_tool_limit(agent)
    if not required:
        return {
            "status": "success",
            "enabled": [],
            "already_enabled": [],
            "evicted": [],
            "invalid": [],
            "required": [],
            "limit": limit,
            "total_enabled": PersistentAgentEnabledTool.objects.filter(agent=agent).count(),
            "overflow_by": 0,
            "over_capacity": False,
        }

    enabled: List[str] = []
    invalid: List[str] = []
    static_tool_names = get_static_tool_names(agent)
    already_enabled = [name for name in required if name in static_tool_names]
    dynamic_required = [name for name in required if name not in static_tool_names]

    existing_rows = {
        row.tool_full_name: row
        for row in PersistentAgentEnabledTool.objects.filter(
            agent=agent,
            tool_full_name__in=dynamic_required,
        )
    }
    custom_entries = get_available_custom_tool_entries(agent)
    needs_mcp_configs = any(
        name.startswith("mcp_") and name not in BUILTIN_TOOL_REGISTRY and not getattr(existing_rows.get(name), "tool_server", "")
        for name in dynamic_required
    )
    try:
        mcp_configs = list(agent_accessible_server_configs(agent)) if needs_mcp_configs else []
    except DatabaseError:
        logger.debug("Failed to load MCP servers for skill validation", exc_info=True)
        mcp_configs = []
    tier_blacklist = get_agent_tool_blacklist(agent)
    for tool_name in dynamic_required:
        if tool_name in tier_blacklist or (
            tool_name not in BUILTIN_TOOL_REGISTRY and _is_mcp_tool_blacklisted(tool_name)
        ):
            invalid.append(tool_name)
            continue

        existing_row = existing_rows.get(tool_name)
        local_entry = _skill_tool_entry(agent, tool_name, custom_entries, mcp_configs, existing_row)
        if existing_row:
            metadata_updates = _apply_tool_metadata(existing_row, local_entry)
            if metadata_updates:
                existing_row.save(update_fields=metadata_updates)
            _ensure_system_skill_enabled_for_tool(agent, local_entry)
            already_enabled.append(tool_name)
            continue

        entry = _resolve_missing_skill_tool(agent, tool_name, local_entry)
        if not entry:
            invalid.append(tool_name)
            continue

        try:
            row, created = PersistentAgentEnabledTool.objects.get_or_create(
                agent=agent,
                tool_full_name=tool_name,
            )
        except DatabaseError:
            logger.exception("Failed to ensure skill tool '%s' for agent %s", tool_name, agent.id)
            invalid.append(tool_name)
            continue

        metadata_updates = _apply_tool_metadata(row, entry)
        if metadata_updates:
            row.save(update_fields=metadata_updates)
        _ensure_system_skill_enabled_for_tool(agent, entry)

        if created:
            enabled.append(tool_name)
        else:
            already_enabled.append(tool_name)

    evicted = _evict_surplus_tools(
        agent,
        exclude=required,
        limit=limit,
    )

    total_enabled = PersistentAgentEnabledTool.objects.filter(agent=agent).count()
    overflow_by = max(total_enabled - limit, 0)
    over_capacity = overflow_by > 0
    if over_capacity:
        logger.warning(
            "Agent %s has %d enabled tools after skill enforcement (cap=%d, overflow=%d). "
            "Required skill tools are preserved.",
            agent.id,
            total_enabled,
            limit,
            overflow_by,
        )

    return {
        "status": "warning" if over_capacity else "success",
        "enabled": enabled,
        "already_enabled": already_enabled,
        "evicted": evicted,
        "invalid": invalid,
        "required": required,
        "limit": limit,
        "total_enabled": total_enabled,
        "overflow_by": overflow_by,
        "over_capacity": over_capacity,
    }


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


def _ensure_system_skill_enabled(
    agent: PersistentAgent,
    skill_key: str,
    *,
    tool_name: str = "",
    reactivate: bool = True,
) -> Optional[str]:
    if not skill_key:
        return None

    from api.agent.system_skills.registry import get_system_skill_definition

    definition = get_system_skill_definition(skill_key)
    if definition is None:
        logger.warning("Tool %s references unknown system skill %s", tool_name or "(unknown)", skill_key)
        return None

    state, _created = PersistentAgentSystemSkillState.objects.get_or_create(
        agent=agent,
        skill_key=definition.skill_key,
        defaults={"is_enabled": True},
    )
    if reactivate and not state.is_enabled:
        state.is_enabled = True
        state.save(update_fields=["is_enabled"])
    return definition.skill_key


def _ensure_system_skill_enabled_for_tool(agent: PersistentAgent, entry: Optional[ToolCatalogEntry]) -> Optional[str]:
    if not entry:
        return None
    return _ensure_system_skill_enabled(
        agent,
        entry.system_skill_key,
        tool_name=entry.full_name,
    )


def _ensure_system_skill_enabled_for_builtin_tool_name(
    agent: PersistentAgent,
    tool_name: str,
    *,
    reactivate: bool = True,
) -> Optional[str]:
    registry_entry = BUILTIN_TOOL_REGISTRY.get(tool_name)
    if not registry_entry:
        return None
    return _ensure_system_skill_enabled(
        agent,
        str(registry_entry.get("system_skill_key") or ""),
        tool_name=tool_name,
        reactivate=reactivate,
    )


def enable_tools(
    agent: PersistentAgent,
    tool_names: Iterable[str],
    *,
    include_hidden_builtin: bool = False,
) -> Dict[str, Any]:
    """Enable multiple tools for an agent, respecting the tiered cap."""
    catalog = _build_available_tool_index(agent, include_hidden_builtin=include_hidden_builtin)
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
            _ensure_system_skill_enabled_for_tool(agent, entry)
            enabled.append(resolved_name)
        else:
            metadata_updates = _apply_tool_metadata(row, entry)
            if metadata_updates:
                row.save(update_fields=metadata_updates)
            _ensure_system_skill_enabled_for_tool(agent, entry)
            already_enabled.append(resolved_name)

    if enabled or already_enabled:
        evicted = _evict_surplus_tools(agent, exclude=list(resolved_seen), limit=limit)

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
    if is_tool_blacklisted_for_agent(agent, tool_name):
        return tool_blacklist_error(tool_name)
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
    _ensure_system_skill_enabled_for_tool(agent, entry)

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


def mark_tool_enabled_without_discovery(agent: PersistentAgent, tool_name: str) -> Dict[str, Any]:
    """
    Trust a tool name and ensure it is marked enabled without refreshing the MCP catalog.

    This bypasses MCP server discovery and only touches the persistence row + LRU eviction.
    """
    if not tool_name:
        return {"status": "error", "message": "Tool name is required"}

    if is_tool_blacklisted_for_agent(agent, tool_name):
        return tool_blacklist_error(tool_name)

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
        updates = ["last_used_at", "usage_count"]
        if tool_name in BUILTIN_TOOL_REGISTRY and row.tool_server != "builtin":
            row.tool_server = "builtin"
            row.tool_name = tool_name
            updates.extend(["tool_server", "tool_name"])
        row.save(update_fields=updates)
        _ensure_system_skill_enabled_for_builtin_tool_name(agent, tool_name)
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
            tool_server="builtin" if tool_name in BUILTIN_TOOL_REGISTRY else "",
            tool_name=tool_name if tool_name in BUILTIN_TOOL_REGISTRY else "",
            last_used_at=now,
            usage_count=1,
        )
    except Exception as exc:
        logger.error("Failed to mark tool %s enabled without discovery: %s", tool_name, exc)
        return {"status": "error", "message": str(exc)}

    evicted = _evict_surplus_tools(agent, exclude=[tool_name])
    _ensure_system_skill_enabled_for_builtin_tool_name(agent, tool_name)
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
) -> None:
    """Ensure the default tool set is enabled for new agents."""
    enabled_tools = set(
        PersistentAgentEnabledTool.objects.filter(agent=agent).values_list("tool_full_name", flat=True)
    )
    missing_builtin = DEFAULT_BUILTIN_TOOLS - enabled_tools
    if not missing_builtin:
        return

    for tool_name in missing_builtin:
        if is_tool_blacklisted_for_agent(agent, tool_name):
            logger.warning("Default builtin tool '%s' is tier-blacklisted, skipping", tool_name)
            continue
        if tool_name not in BUILTIN_TOOL_REGISTRY:
            logger.warning("Default builtin tool '%s' not registered, skipping", tool_name)
            continue
        result = mark_tool_enabled_without_discovery(agent, tool_name)
        if result.get("status") == "success":
            logger.info("Enabled default builtin tool '%s' for agent %s", tool_name, agent.id)


def get_enabled_tool_definitions(agent: PersistentAgent) -> List[Dict[str, Any]]:
    """Return tool definitions for all enabled tools (MCP, built-ins, custom)."""
    manager = _get_manager()
    blacklisted_tools = get_agent_tool_blacklist(agent)
    enabled_eval_tool_names = list(
        PersistentAgentEnabledTool.objects
        .filter(
            agent=agent,
            tool_full_name__in=list(EVAL_SYNTHETIC_TOOL_DEFINITIONS.keys()),
            tool_server=EVAL_SYNTHETIC_TOOL_SERVER,
        )
        .exclude(tool_full_name__in=list(blacklisted_tools))
        .values_list("tool_full_name", flat=True)
    )
    eval_tool_name_set = set(enabled_eval_tool_names)
    enabled_pipedream_tool_names = set(
        PersistentAgentEnabledTool.objects
        .filter(agent=agent, tool_server=PIPEDREAM_TOOL_SERVER_NAME)
        .values_list("tool_full_name", flat=True)
    )
    hidden_eval_mcp_tool_names = enabled_pipedream_tool_names if is_eval_agent(agent) else set()
    pipedream_visibility = get_pipedream_app_visibility_for_agent(agent)
    hidden_deprecated_pipedream_tool_names = {
        tool_name
        for tool_name in enabled_pipedream_tool_names
        if not pipedream_visibility.is_tool_visible(tool_name)
    }
    definitions = [
        _sanitize_tool_definition_for_llm(definition)
        for definition in manager.get_enabled_tools_definitions(agent)
        if _tool_definition_name(definition) not in blacklisted_tools
        and _tool_definition_name(definition) not in eval_tool_name_set
        and _tool_definition_name(definition) not in hidden_eval_mcp_tool_names
        and _tool_definition_name(definition) not in hidden_deprecated_pipedream_tool_names
    ]
    enabled_names = list(
        PersistentAgentEnabledTool.objects.filter(agent=agent)
        .exclude(tool_full_name__in=list(blacklisted_tools))
        .values_list("tool_full_name", flat=True)
    )

    enabled_builtin_rows = PersistentAgentEnabledTool.objects.filter(
        agent=agent,
        tool_full_name__in=list(BUILTIN_TOOL_REGISTRY.keys()),
    ).exclude(tool_full_name__in=list(blacklisted_tools))
    existing_names = {
        entry.get("function", {}).get("name")
        for entry in definitions
        if isinstance(entry, dict)
    }

    if is_custom_tools_available_for_agent(agent):
        enabled_custom_tools = PersistentAgentCustomTool.objects.filter(
            agent=agent,
            tool_name__in=enabled_names,
        ).order_by("tool_name")
        for tool in enabled_custom_tools:
            if tool.tool_name in existing_names:
                continue
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.tool_name,
                        "description": tool.description,
                        "parameters": _custom_tool_parameters_for_llm(tool.parameters_schema),
                    },
                }
            )
            existing_names.add(tool.tool_name)

    for tool_name in enabled_eval_tool_names:
        if tool_name in existing_names:
            continue
        tool_def = get_eval_synthetic_tool_definition(agent, tool_name)
        if not tool_def:
            continue
        definitions.append(_sanitize_tool_definition_for_llm(tool_def))
        existing_names.add(tool_name)

    for row in enabled_builtin_rows:
        registry_entry = BUILTIN_TOOL_REGISTRY.get(row.tool_full_name)
        if not registry_entry:
            continue
        # Tool rows can predate their associated system skill. Create only a
        # missing state here so loading tools never overrides an explicit disable.
        _ensure_system_skill_enabled_for_builtin_tool_name(
            agent,
            row.tool_full_name,
            reactivate=False,
        )
        if not _is_builtin_tool_available(row.tool_full_name, agent, include_hidden=True):
            continue
        tool_def = _build_builtin_tool_definition(row.tool_full_name, registry_entry)
        if not tool_def:
            continue
        tool_name = (
            tool_def.get("function", {}).get("name")
            if isinstance(tool_def, dict)
            else None
        )
        if tool_name and tool_name not in existing_names:
            definitions.append(_sanitize_tool_definition_for_llm(tool_def))
            existing_names.add(tool_name)

    return definitions


def _normalize_mcp_tool_name(tool_name: str, catalog: Dict[str, "ToolCatalogEntry"]) -> Optional[str]:
    if not tool_name.startswith("mcp_"):
        return None

    normalized = tool_name.replace("mcp_bright_data_", "mcp_brightdata_")
    if normalized in catalog:
        return normalized
    legacy_brightdata = normalized.replace("mcp_brightdata_linkedin_", "mcp_brightdata_web_data_linkedin_", 1)
    if legacy_brightdata != normalized and legacy_brightdata in catalog:
        return legacy_brightdata

    tool_name_collapsed = tool_name.replace("_", "").lower()
    for candidate in catalog:
        if candidate.replace("_", "").lower() == tool_name_collapsed:
            return candidate

    return None


def resolve_tool_entry(agent: PersistentAgent, tool_name: str) -> Optional[ToolCatalogEntry]:
    """Return catalog entry for the given tool name if available.

    Attempts fuzzy matching for MCP tools when exact match fails.
    """
    if is_tool_blacklisted_for_agent(agent, tool_name):
        return None

    local_catalog = _build_available_tool_index(
        agent,
        include_hidden_builtin=True,
        include_mcp=False,
    )

    entry = local_catalog.get(tool_name)
    if entry:
        return entry

    candidates = [tool_name]
    if tool_name.startswith("mcp_"):
        normalized = tool_name.replace("mcp_bright_data_", "mcp_brightdata_")
        legacy_brightdata = normalized.replace(
            "mcp_brightdata_linkedin_",
            "mcp_brightdata_web_data_linkedin_",
            1,
        )
        candidates.extend(name for name in (normalized, legacy_brightdata) if name not in candidates)

    manager = _get_manager()
    for candidate in candidates:
        info = manager.prepare_tool_for_agent(agent, candidate, require_enabled=False)
        if not info:
            continue
        if is_tool_blacklisted_for_agent(agent, info.full_name):
            return None
        if (
            info.server_name == PIPEDREAM_TOOL_SERVER_NAME
            and not is_pipedream_tool_visible_to_agent(agent, info.tool_name)
        ):
            return None
        if candidate != tool_name:
            logger.info("Normalized MCP tool name '%s' -> '%s'", tool_name, info.full_name)
        return ToolCatalogEntry(
            provider="mcp",
            full_name=info.full_name,
            description=info.description,
            parameters=info.parameters,
            tool_server=info.server_name,
            tool_name=info.tool_name,
            server_config_id=info.config_id,
            mcp_info=info,
        )

    return None


def _should_execute_mcp_tool_isolated(entry: ToolCatalogEntry) -> bool:
    if not entry.full_name.startswith("mcp_brightdata_"):
        return False
    manager = _get_manager()
    return manager.is_platform_brightdata_config(entry.server_config_id)


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
                _ensure_system_skill_enabled_for_tool(agent, entry)
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


def execute_enabled_tool(
    agent: PersistentAgent,
    tool_name: str,
    params: Dict[str, Any],
    *,
    isolated_mcp: bool = False,
    current_sqlite_db_path: Optional[str] = None,
    resolved_entry: Optional[ToolCatalogEntry] = None,
) -> Dict[str, Any]:
    """Execute an enabled tool, routing to the appropriate provider."""
    entry = resolved_entry or resolve_tool_entry(agent, tool_name)
    if not entry:
        return {"status": "error", "message": f"Tool '{tool_name}' is not available"}

    resolved_name = entry.full_name

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

    if _is_pipedream_entry(entry):
        params = _normalize_tool_params_unicode_escapes(params)

    if entry.provider == "mcp":
        if _should_execute_mcp_tool_isolated(entry):
            result = execute_mcp_tool_isolated(
                agent,
                resolved_name,
                params,
                tool_info=entry.mcp_info,
            )
        else:
            result = execute_mcp_tool(
                agent,
                resolved_name,
                params,
                tool_info=entry.mcp_info,
            )
        return result

    if entry.provider == "eval":
        if not is_eval_synthetic_tool_name(resolved_name):
            return {"status": "error", "message": f"Unknown eval synthetic tool '{resolved_name}'."}

        try:
            row = PersistentAgentEnabledTool.objects.filter(
                agent=agent,
                tool_full_name=resolved_name,
            ).first()
        except DatabaseError:
            logger.exception("Failed to load enabled entry for eval tool %s", resolved_name)
            row = None

        if row:
            row.last_used_at = datetime.now(UTC)
            row.usage_count = (row.usage_count or 0) + 1
            update_fields = ["last_used_at", "usage_count"]
            metadata_updates = _apply_tool_metadata(row, entry)
            if metadata_updates:
                update_fields.extend(metadata_updates)
            try:
                row.save(update_fields=list(dict.fromkeys(update_fields)))
            except DatabaseError:
                logger.exception("Failed to record usage for eval tool %s", resolved_name)

        return get_eval_synthetic_tool_fallback_result(resolved_name, params)

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
                    track_sandbox_unavailable(
                        agent,
                        request_source="tool_request",
                        tool_name=resolved_name,
                    )
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

    if entry.provider == "custom":
        try:
            row = PersistentAgentEnabledTool.objects.filter(
                agent=agent,
                tool_full_name=resolved_name,
            ).first()
        except Exception:
            row = None
            logger.exception("Failed to load enabled entry for custom tool %s", resolved_name)

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
                logger.exception("Failed to record usage for custom tool %s", resolved_name)

        custom_tool = PersistentAgentCustomTool.objects.filter(
            agent=agent,
            tool_name=resolved_name,
        ).first()
        if not custom_tool:
            return {
                "status": "error",
                "message": f"Custom tool '{resolved_name}' is not available for this agent.",
            }
        return execute_custom_tool(
            agent,
            custom_tool,
            params,
            current_sqlite_db_path=current_sqlite_db_path,
        )

    return {"status": "error", "message": f"Tool '{resolved_name}' has no execution handler"}


def is_parallel_safe_tool_name(tool_name: str) -> bool:
    """Return whether the tool name is on the explicit parallel-safe allowlist."""
    entry = BUILTIN_TOOL_REGISTRY.get(tool_name)
    return bool(entry and entry.get("parallel_safe"))


def get_parallel_safe_tool_rejection_reason(tool_name: str, params: Dict[str, Any]) -> Optional[str]:
    """Return the rejection reason when a tool call is not parallel-safe."""
    if not is_parallel_safe_tool_name(tool_name):
        return f"unsafe_tool:{tool_name}"
    if tool_name == HTTP_REQUEST_TOOL_NAME:
        # Parallel-safe HTTP is intentionally read-only in v1. Non-GET methods can
        # have side effects, and downloads write files into filespace.
        method = str((params or {}).get("method") or "GET").strip().upper()
        if method != "GET":
            return "http_request_requires_get"
        download = (params or {}).get("download")
        if download in (True, "true", "True", 1):
            return "http_request_download_not_supported"
    return None
