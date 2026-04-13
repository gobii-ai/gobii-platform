"""Runtime helpers for code-defined system skills."""

from typing import Iterable, Optional

from api.models import PersistentAgent, PersistentAgentEnabledTool
from api.agent.tools.tool_manager import enable_tools

from .registry import SystemSkillDefinition, get_system_skill_definition


def enable_system_skills(
    agent: PersistentAgent,
    skill_keys: Iterable[str],
    *,
    available_skills: Optional[Iterable[SystemSkillDefinition]] = None,
) -> dict[str, object]:
    requested: list[str] = []
    seen: set[str] = set()
    for raw_key in skill_keys or []:
        if not isinstance(raw_key, str):
            continue
        skill_key = raw_key.strip()
        if not skill_key or skill_key in seen:
            continue
        seen.add(skill_key)
        requested.append(skill_key)

    catalog = (
        {definition.skill_key: definition for definition in available_skills}
        if available_skills is not None
        else {
            skill_key: definition
            for skill_key, definition in (
                (skill_key, get_system_skill_definition(skill_key))
                for skill_key in requested
            )
            if definition is not None
        }
    )

    enabled: list[str] = []
    already_enabled: list[str] = []
    invalid: list[str] = []
    evicted: list[str] = []

    for skill_key in requested:
        definition = catalog.get(skill_key)
        if definition is None:
            invalid.append(skill_key)
            continue

        tool_names = list(definition.tool_names)
        if not tool_names:
            invalid.append(skill_key)
            continue

        enabled_qs = PersistentAgentEnabledTool.objects.filter(
            agent=agent,
            tool_full_name__in=tool_names,
        ).values_list("tool_full_name", flat=True)
        enabled_tool_names = set(enabled_qs)
        if enabled_tool_names.issuperset(tool_names):
            already_enabled.append(skill_key)
            continue

        result = enable_tools(agent, tool_names, include_hidden_builtin=True)
        if result.get("status") != "success":
            invalid.append(skill_key)
            continue

        if result.get("evicted"):
            evicted.extend(result.get("evicted", []))
        enabled.append(skill_key)

    return {
        "status": "success",
        "enabled": enabled,
        "already_enabled": already_enabled,
        "invalid": invalid,
        "evicted": list(dict.fromkeys(evicted)),
    }
