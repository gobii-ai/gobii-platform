"""Runtime helpers for code-defined system skills."""

from typing import Iterable, Optional

from django.db.models import F
from django.utils import timezone

from api.models import PersistentAgent, PersistentAgentEnabledTool, PersistentAgentSystemSkillState
from api.services.pipedream_apps import enable_pipedream_apps_for_agent

from .registry import SystemSkillDefinition, get_system_skill_definition
from .defaults import DEFAULT_SYSTEM_SKILL_DEFINITIONS


def default_enabled_system_skill_keys() -> tuple[str, ...]:
    return tuple(
        skill_key
        for skill_key, definition in DEFAULT_SYSTEM_SKILL_DEFINITIONS.items()
        if definition.default_enabled
    )


def ensure_default_system_skills_enabled(agent: PersistentAgent) -> None:
    default_keys = default_enabled_system_skill_keys()
    if not default_keys:
        return

    existing_keys = set(
        PersistentAgentSystemSkillState.objects.filter(
            agent=agent,
            skill_key__in=default_keys,
        ).values_list("skill_key", flat=True)
    )
    missing_keys = [skill_key for skill_key in default_keys if skill_key not in existing_keys]
    if missing_keys:
        PersistentAgentSystemSkillState.objects.bulk_create(
            [
                PersistentAgentSystemSkillState(agent=agent, skill_key=skill_key, is_enabled=True)
                for skill_key in missing_keys
            ],
            ignore_conflicts=True,
        )

    PersistentAgentSystemSkillState.objects.filter(
        agent=agent,
        skill_key__in=default_keys,
        is_enabled=False,
    ).update(is_enabled=True)


def get_enabled_system_skill_states(agent: PersistentAgent):
    ensure_default_system_skills_enabled(agent)
    return PersistentAgentSystemSkillState.objects.filter(agent=agent, is_enabled=True)


def refresh_system_skills_for_tool(agent: PersistentAgent, tool_name: str, *, used_at=None) -> list[str]:
    normalized_tool = str(tool_name or "").strip()
    if not normalized_tool:
        return []

    used_at = used_at or timezone.now()
    ensure_default_system_skills_enabled(agent)
    matching_keys = [
        definition.skill_key
        for definition in DEFAULT_SYSTEM_SKILL_DEFINITIONS.values()
        if normalized_tool in definition.tool_names
    ]
    if not matching_keys:
        return []

    updated = PersistentAgentSystemSkillState.objects.filter(
        agent=agent,
        skill_key__in=matching_keys,
        is_enabled=True,
    ).update(
        last_used_at=used_at,
        usage_count=F("usage_count") + 1,
    )
    if not updated:
        return []
    return matching_keys


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
    pipedream_apps_enabled: list[str] = []
    pipedream_apps_already_enabled: list[str] = []
    pipedream_apps_invalid: list[str] = []
    pipedream_effective_apps: list[str] = []

    for skill_key in requested:
        definition = catalog.get(skill_key)
        if definition is None:
            invalid.append(skill_key)
            continue

        tool_names = list(definition.tool_names)
        app_slugs = list(definition.pipedream_app_slugs)
        state, _created = PersistentAgentSystemSkillState.objects.get_or_create(
            agent=agent,
            skill_key=skill_key,
            defaults={"is_enabled": True},
        )
        if not state.is_enabled:
            state.is_enabled = True
            state.save(update_fields=["is_enabled"])

        app_enabled = False
        if app_slugs:
            app_result = enable_pipedream_apps_for_agent(
                agent,
                app_slugs,
                available_app_slugs=app_slugs,
            )
            if app_result.get("status") != "success":
                invalid.append(skill_key)
                continue
            pipedream_apps_enabled.extend(app_result.get("enabled", []))
            pipedream_apps_already_enabled.extend(app_result.get("already_enabled", []))
            pipedream_apps_invalid.extend(app_result.get("invalid", []))
            pipedream_effective_apps = list(app_result.get("effective_apps", []))
            app_enabled = bool(app_result.get("enabled"))

        if not tool_names:
            enabled.append(skill_key)
            continue

        enabled_qs = PersistentAgentEnabledTool.objects.filter(
            agent=agent,
            tool_full_name__in=tool_names,
        ).values_list("tool_full_name", flat=True)
        enabled_tool_names = set(enabled_qs)
        if enabled_tool_names.issuperset(tool_names):
            if app_enabled:
                enabled.append(skill_key)
            else:
                already_enabled.append(skill_key)
            continue

        from api.agent.tools.tool_manager import enable_tools

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
        "pipedream_apps": {
            "enabled": list(dict.fromkeys(pipedream_apps_enabled)),
            "already_enabled": list(dict.fromkeys(pipedream_apps_already_enabled)),
            "invalid": list(dict.fromkeys(pipedream_apps_invalid)),
            "effective_apps": pipedream_effective_apps,
        },
    }
