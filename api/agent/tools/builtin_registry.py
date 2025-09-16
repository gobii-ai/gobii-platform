"""Registry and helpers for dynamically-enabled built-in agent tools.

This module provides a thin abstraction around a small set of first-party
tools (e.g. ``spawn_web_task``) that we want to expose through the same
discovery/enabling flow as MCP tools.  The tools defined here are not backed by
an MCP server, but they behave similarly from the agent's perspective:

* They are initially disabled and become available only after the agent calls
  ``search_tools``.
* Their enablement status is stored in ``PersistentAgentEnabledTool`` rows so we
  can reuse the existing per-agent bookkeeping and usage tracking.
* When enabled, they contribute OpenAI-compatible tool definitions identical to
  the static versions we previously returned from ``_get_agent_tools``.

The helper functions defined below are used by the search flow (to surface the
tools in the discovery catalog and to persist enablement) and by
``event_processing`` when assembling the final tool list for the agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional

from django.utils import timezone

from ...models import PersistentAgent, PersistentAgentEnabledTool
from .spawn_web_task import get_spawn_web_task_tool


# Tools in this registry are stored in ``PersistentAgentEnabledTool`` with a
# synthetic prefix so they remain disambiguated from MCP tool full-names.
BUILTIN_TOOL_PREFIX = "builtin::"
BUILTIN_TOOL_SERVER = "builtin"


@dataclass(frozen=True)
class DynamicBuiltinTool:
    """Metadata for a dynamically-enabled built-in tool."""

    name: str
    definition_factory: Callable[[], Dict[str, Dict[str, object]]]
    search_summary: Optional[str] = None

    def build_definition(self) -> Dict[str, Dict[str, object]]:
        """Return a fresh OpenAI-compatible tool definition."""

        return self.definition_factory()

    def description(self) -> str:
        definition = self.build_definition()
        return (
            definition.get("function", {}).get("description", "")
            if isinstance(definition, dict)
            else ""
        )

    def parameters(self) -> Dict[str, object]:
        definition = self.build_definition()
        params = (
            definition.get("function", {}).get("parameters", {})
            if isinstance(definition, dict)
            else {}
        )
        return params if isinstance(params, dict) else {}


def _spawn_web_task_summary() -> str:
    return (
        "Fully-featured persistent browser session for browsing, research, "
        "and scraping websites. Handles authentication, cookies, and "
        "multi-step flows."
    )


_DYNAMIC_BUILTIN_TOOLS: Dict[str, DynamicBuiltinTool] = {
    "spawn_web_task": DynamicBuiltinTool(
        name="spawn_web_task",
        definition_factory=get_spawn_web_task_tool,
        search_summary=_spawn_web_task_summary(),
    ),
}


def _storage_name(tool_name: str) -> str:
    return f"{BUILTIN_TOOL_PREFIX}{tool_name}"


def list_dynamic_builtin_names() -> List[str]:
    """Return the names of all dynamically-enabled built-in tools."""

    return list(_DYNAMIC_BUILTIN_TOOLS.keys())


def get_dynamic_builtin_catalog_entries() -> List[Dict[str, str]]:
    """Return catalog entries suitable for the ``search_tools`` prompt."""

    entries: List[Dict[str, str]] = []
    for tool in _DYNAMIC_BUILTIN_TOOLS.values():
        summary = tool.search_summary or tool.description()
        params = tool.parameters()
        entries.append(
            {
                "name": tool.name,
                "summary": summary or "",
                "params": params,
            }
        )
    return entries


def enable_dynamic_builtin_tool(agent: PersistentAgent, tool_name: str) -> str:
    """Enable a built-in tool for the given agent.

    Returns one of ``"enabled"``, ``"already_enabled"``, or ``"invalid"``.
    """

    if tool_name not in _DYNAMIC_BUILTIN_TOOLS:
        return "invalid"

    storage_name = _storage_name(tool_name)
    row, created = PersistentAgentEnabledTool.objects.get_or_create(
        agent=agent,
        tool_full_name=storage_name,
        defaults={
            "tool_server": BUILTIN_TOOL_SERVER,
            "tool_name": tool_name,
        },
    )
    if created:
        return "enabled"

    # Ensure legacy rows are tagged correctly for eviction filters.
    update_fields: List[str] = []
    if row.tool_server != BUILTIN_TOOL_SERVER:
        row.tool_server = BUILTIN_TOOL_SERVER
        update_fields.append("tool_server")
    if row.tool_name != tool_name:
        row.tool_name = tool_name
        update_fields.append("tool_name")
    if update_fields:
        row.save(update_fields=update_fields)
    return "already_enabled"


def enable_dynamic_builtin_tools(agent: PersistentAgent, tool_names: Iterable[str]) -> Dict[str, List[str]]:
    """Enable multiple built-in tools and return status buckets."""

    enabled: List[str] = []
    already_enabled: List[str] = []
    invalid: List[str] = []

    for name in tool_names:
        result = enable_dynamic_builtin_tool(agent, name)
        if result == "enabled":
            enabled.append(name)
        elif result == "already_enabled":
            already_enabled.append(name)
        else:
            invalid.append(name)

    return {
        "enabled": enabled,
        "already_enabled": already_enabled,
        "invalid": invalid,
    }


def _strip_storage_prefix(stored_name: str) -> Optional[str]:
    if stored_name.startswith(BUILTIN_TOOL_PREFIX):
        return stored_name[len(BUILTIN_TOOL_PREFIX) :]
    return None


def get_enabled_dynamic_builtin_names(agent: PersistentAgent) -> List[str]:
    """Return the list of dynamic built-in tool names enabled for ``agent``."""

    rows = PersistentAgentEnabledTool.objects.filter(
        agent=agent, tool_server=BUILTIN_TOOL_SERVER
    )
    names: List[str] = []
    for row in rows:
        name = row.tool_name or _strip_storage_prefix(row.tool_full_name or "")
        if name and name in _DYNAMIC_BUILTIN_TOOLS:
            names.append(name)
    return names


def get_enabled_dynamic_builtin_definitions(agent: PersistentAgent) -> List[Dict[str, Dict[str, object]]]:
    """Return tool definitions for all enabled dynamic built-ins."""

    definitions: List[Dict[str, Dict[str, object]]] = []
    for name in get_enabled_dynamic_builtin_names(agent):
        tool = _DYNAMIC_BUILTIN_TOOLS.get(name)
        if tool:
            definitions.append(tool.build_definition())
    return definitions


def is_dynamic_builtin_tool(tool_name: str) -> bool:
    return tool_name in _DYNAMIC_BUILTIN_TOOLS


def mark_dynamic_builtin_tool_used(agent: PersistentAgent, tool_name: str) -> None:
    """Update usage metadata for a built-in tool when it is executed."""

    if tool_name not in _DYNAMIC_BUILTIN_TOOLS:
        return

    storage_name = _storage_name(tool_name)
    try:
        row = PersistentAgentEnabledTool.objects.get(
            agent=agent, tool_full_name=storage_name
        )
    except PersistentAgentEnabledTool.DoesNotExist:
        return

    row.last_used_at = timezone.now()
    row.usage_count = (row.usage_count or 0) + 1
    row.save(update_fields=["last_used_at", "usage_count"])

