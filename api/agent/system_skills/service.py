"""Runtime helpers for code-defined system skills."""

from typing import Iterable, Optional

from django.db.models import F, Q
from django.utils import timezone

from api.models import PersistentAgent, PersistentAgentEnabledTool, PersistentAgentSystemSkillState
from api.services.pipedream_apps import PIPEDREAM_RUNTIME_NAME, enable_pipedream_apps_for_agent

from .registry import (
    SystemSkillDefinition,
    equivalent_system_skill_keys,
    get_system_skill_definition,
    normalize_system_skill_key,
)
from .defaults import (
    APOLLO_NATIVE_SYSTEM_SKILL_KEY,
    DEFAULT_SYSTEM_SKILL_DEFINITIONS,
    GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY,
    HUBSPOT_NATIVE_SYSTEM_SKILL_KEY,
)


NATIVE_SYSTEM_SKILL_PIPEDREAM_APP_SLUGS = {
    GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY: ("google_sheets", "google_drive", "google_docs"),
    HUBSPOT_NATIVE_SYSTEM_SKILL_KEY: ("hubspot",),
    APOLLO_NATIVE_SYSTEM_SKILL_KEY: ("apollo_io", "apollo_io_oauth"),
}


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


def _system_skill_keys_for_tool(tool_name: str) -> list[str]:
    normalized_tool = str(tool_name or "").strip()
    if not normalized_tool:
        return []
    return [
        definition.skill_key
        for definition in DEFAULT_SYSTEM_SKILL_DEFINITIONS.values()
        if normalized_tool in definition.tool_names
    ]


def _static_system_skill_tool_names(agent: PersistentAgent) -> set[str]:
    from api.agent.tools.static_tools import get_static_tool_names

    return get_static_tool_names(agent)


def get_available_system_skill_tool_names(agent: PersistentAgent) -> set[str]:
    from api.agent.tools.tool_manager import get_available_builtin_tool_entries

    return (
        set(get_available_builtin_tool_entries(agent, include_hidden=True).keys())
        | _static_system_skill_tool_names(agent)
    )


def refresh_system_skills_for_tool(agent: PersistentAgent, tool_name: str, *, used_at=None) -> list[str]:
    normalized_tool = str(tool_name or "").strip()
    if not normalized_tool:
        return []

    used_at = used_at or timezone.now()
    ensure_default_system_skills_enabled(agent)
    matching_keys = _system_skill_keys_for_tool(normalized_tool)
    if not matching_keys:
        return []
    state_keys = []
    for key in matching_keys:
        state_keys.extend(equivalent_system_skill_keys(key))

    updated = PersistentAgentSystemSkillState.objects.filter(
        agent=agent,
        skill_key__in=state_keys,
        is_enabled=True,
    ).update(
        last_used_at=used_at,
        usage_count=F("usage_count") + 1,
    )
    if not updated:
        return []
    return matching_keys


def enable_and_refresh_system_skills_for_tool(agent: PersistentAgent, tool_name: str, *, used_at=None) -> list[str]:
    normalized_tool = str(tool_name or "").strip()
    if not normalized_tool:
        return []

    used_at = used_at or timezone.now()
    ensure_default_system_skills_enabled(agent)
    matching_keys = _system_skill_keys_for_tool(normalized_tool)
    if not matching_keys:
        return []

    refreshed: list[str] = []
    for skill_key in matching_keys:
        state, created = PersistentAgentSystemSkillState.objects.get_or_create(
            agent=agent,
            skill_key=skill_key,
            defaults={
                "is_enabled": True,
                "last_used_at": used_at,
                "usage_count": 1,
            },
        )
        if not created:
            PersistentAgentSystemSkillState.objects.filter(id=state.id).update(
                is_enabled=True,
                last_used_at=used_at,
                usage_count=F("usage_count") + 1,
            )
        refreshed.append(skill_key)
    return refreshed


def _disable_overlapping_pipedream_tools(agent: PersistentAgent, skill_key: str) -> list[str]:
    app_slugs = NATIVE_SYSTEM_SKILL_PIPEDREAM_APP_SLUGS.get(skill_key, ())
    if not app_slugs:
        return []

    prefix_query = Q()
    for app_slug in app_slugs:
        prefix = f"{app_slug}-"
        prefix_query |= Q(tool_full_name__startswith=prefix) | Q(tool_name__startswith=prefix)

    if not prefix_query:
        return []

    queryset = (
        PersistentAgentEnabledTool.objects
        .filter(agent=agent)
        .filter(Q(tool_server=PIPEDREAM_RUNTIME_NAME) | Q(server_config__name=PIPEDREAM_RUNTIME_NAME))
        .filter(prefix_query)
    )
    disabled_tool_names = list(
        queryset.order_by("tool_full_name").values_list("tool_full_name", flat=True)
    )
    if disabled_tool_names:
        queryset.delete()
    return disabled_tool_names


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
        skill_key = normalize_system_skill_key(raw_key)
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
    disabled_pipedream_tools: list[str] = []

    for skill_key in requested:
        definition = catalog.get(skill_key)
        if definition is None:
            invalid.append(skill_key)
            continue

        tool_names = list(definition.tool_names)
        app_slugs = list(definition.pipedream_app_slugs)
        app_enabled = False
        tools_enabled = False
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

        static_tool_names: set[str] = set()
        if tool_names:
            available_tool_names = get_available_system_skill_tool_names(agent)
            if not set(tool_names).issubset(available_tool_names):
                invalid.append(skill_key)
                continue

            static_tool_names = _static_system_skill_tool_names(agent)

            dynamic_tool_names = [tool_name for tool_name in tool_names if tool_name not in static_tool_names]
            enabled_qs = PersistentAgentEnabledTool.objects.filter(
                agent=agent,
                tool_full_name__in=dynamic_tool_names,
            ).values_list("tool_full_name", flat=True)
            enabled_tool_names = set(enabled_qs)
            missing_tool_names = [
                tool_name for tool_name in dynamic_tool_names if tool_name not in enabled_tool_names
            ]
            if missing_tool_names:
                from api.agent.tools.tool_manager import enable_tools

                result = enable_tools(agent, missing_tool_names, include_hidden_builtin=True)
                if result.get("status") != "success" or result.get("invalid"):
                    invalid.append(skill_key)
                    continue

                if result.get("evicted"):
                    evicted.extend(result.get("evicted", []))
                tools_enabled = bool(result.get("enabled"))
                enabled_tool_names.update(result.get("enabled", []))
                enabled_tool_names.update(result.get("already_enabled", []))

            ready_tool_names = enabled_tool_names | static_tool_names
            if not set(tool_names).issubset(ready_tool_names):
                invalid.append(skill_key)
                continue

        state, created = PersistentAgentSystemSkillState.objects.get_or_create(
            agent=agent,
            skill_key=skill_key,
            defaults={"is_enabled": True},
        )
        state_was_enabled = (not created) and state.is_enabled
        if not state.is_enabled:
            state.is_enabled = True
            state.save(update_fields=["is_enabled"])

        if not tool_names or app_enabled or tools_enabled or not state_was_enabled:
            enabled.append(skill_key)
        else:
            already_enabled.append(skill_key)

        disabled_pipedream_tools.extend(_disable_overlapping_pipedream_tools(agent, skill_key))

    return {
        "status": "success",
        "enabled": enabled,
        "already_enabled": already_enabled,
        "invalid": invalid,
        "evicted": list(dict.fromkeys(evicted)),
        "disabled_pipedream_tools": list(dict.fromkeys(disabled_pipedream_tools)),
        "pipedream_apps": {
            "enabled": list(dict.fromkeys(pipedream_apps_enabled)),
            "already_enabled": list(dict.fromkeys(pipedream_apps_already_enabled)),
            "invalid": list(dict.fromkeys(pipedream_apps_invalid)),
            "effective_apps": pipedream_effective_apps,
        },
    }
