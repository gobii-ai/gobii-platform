"""Helpers for platform-managed global skills."""

import logging
from typing import Any, Iterable

from django.db import transaction
from django.db.models import Q
from django.utils.text import get_valid_filename

from api.agent.files.filespace_service import write_bytes_to_dir
from api.models import (
    GlobalAgentSkill,
    GlobalAgentSkillCustomTool,
    PersistentAgent,
    PersistentAgentCustomTool,
    PersistentAgentSkill,
)
from api.services.skill_analytics import (
    SKILL_ORIGIN_GLOBAL_IMPORT,
    track_agent_skill_event,
)
from util.analytics import AnalyticsEvent
from .custom_tools import (
    is_custom_tools_available_for_agent,
    normalize_custom_tool_name,
    normalize_custom_tool_parameters_schema,
    normalize_custom_tool_timeout_seconds,
    validate_custom_tool_source_code,
)
from .skill_utils import normalize_skill_secret_requirements, normalize_skill_tool_ids

logger = logging.getLogger(__name__)


def _prefetch_global_skill_catalog():
    return GlobalAgentSkill.objects.filter(is_active=True).prefetch_related("bundled_custom_tools").order_by("name")


def _managed_global_skill_tool_path(skill_name: str, tool_name: str) -> str:
    safe_skill_name = get_valid_filename(skill_name or "global_skill") or "global_skill"
    safe_tool_name = get_valid_filename(tool_name or "custom_tool") or "custom_tool"
    base_name = safe_tool_name if safe_tool_name.endswith(".py") else f"{safe_tool_name}.py"
    return f"/tools/global_skills/{safe_skill_name}/{base_name}"


def _read_bundled_custom_tool_source(tool: GlobalAgentSkillCustomTool) -> tuple[str | None, str | None]:
    if not tool.source_file:
        return None, f"Bundled custom tool '{tool.tool_name}' is missing source_file."

    try:
        with tool.source_file.open("rb") as source_file:
            raw = source_file.read()
    except OSError as exc:
        return None, f"Failed reading bundled custom tool '{tool.tool_name}': {exc}"

    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, f"Bundled custom tool '{tool.tool_name}' source must be UTF-8 text."


def _prepare_bundled_custom_tools(
    agent: PersistentAgent,
    skill: GlobalAgentSkill,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    bundled_tools = list(skill.bundled_custom_tools.all())
    bundled_tools.sort(key=lambda bundled_tool: bundled_tool.tool_name)
    if not bundled_tools:
        return [], None
    if not is_custom_tools_available_for_agent(agent):
        return None, "Bundled custom tools require sandbox compute."

    prepared: list[dict[str, Any]] = []
    for bundled_tool in bundled_tools:
        normalized_name = normalize_custom_tool_name(bundled_tool.tool_name or bundled_tool.name)
        if normalized_name is None:
            return None, f"Bundled custom tool '{bundled_tool.name}' has an invalid tool_name."
        display_name = (bundled_tool.name or "").strip() or normalized_name[0]
        tool_name = normalized_name[1]
        description = (bundled_tool.description or "").strip()
        parameters_schema = normalize_custom_tool_parameters_schema(bundled_tool.parameters_schema)
        if parameters_schema is None:
            return None, f"Bundled custom tool '{tool_name}' has an invalid parameters_schema."
        timeout_seconds = normalize_custom_tool_timeout_seconds(bundled_tool.timeout_seconds)
        if timeout_seconds is None:
            return None, f"Bundled custom tool '{tool_name}' has an invalid timeout_seconds."

        source_text, source_error = _read_bundled_custom_tool_source(bundled_tool)
        if source_error:
            return None, source_error
        assert source_text is not None
        validation_error = validate_custom_tool_source_code(
            source_text,
            bundled_tool.source_file.name or f"{tool_name}.py",
        )
        if validation_error:
            return None, f"Bundled custom tool '{tool_name}' is invalid: {validation_error}"

        source_path = _managed_global_skill_tool_path(skill.name, tool_name)
        existing_tool = PersistentAgentCustomTool.objects.filter(agent=agent, tool_name=tool_name).first()
        if existing_tool and existing_tool.source_path != source_path:
            return None, (
                f"Bundled custom tool '{tool_name}' conflicts with existing local custom tool "
                f"at {existing_tool.source_path}."
            )

        prepared.append(
            {
                "name": display_name,
                "tool_name": tool_name,
                "description": description,
                "source_path": source_path,
                "source_text": source_text,
                "parameters_schema": parameters_schema,
                "timeout_seconds": timeout_seconds,
            }
        )

    return prepared, None


def _materialize_bundled_custom_tools(
    agent: PersistentAgent,
    prepared_tools: list[dict[str, Any]],
) -> tuple[bool, str | None]:
    for prepared in prepared_tools:
        write_result = write_bytes_to_dir(
            agent=agent,
            content_bytes=prepared["source_text"].encode("utf-8"),
            path=prepared["source_path"],
            mime_type="text/x-python",
            extension=".py",
            overwrite=True,
        )
        if write_result.get("status") != "ok":
            return False, write_result.get("message") or "Failed to copy bundled custom tool source."
        PersistentAgentCustomTool.objects.update_or_create(
            agent=agent,
            tool_name=prepared["tool_name"],
            defaults={
                "name": prepared["name"],
                "description": prepared["description"],
                "source_path": prepared["source_path"],
                "parameters_schema": prepared["parameters_schema"],
                "entrypoint": "run",
                "timeout_seconds": prepared["timeout_seconds"],
            },
        )
    return True, None


def get_compatible_global_skills(agent: PersistentAgent) -> list[GlobalAgentSkill]:
    """Return active global skills whose required tools are available to the agent."""
    from .tool_manager import get_available_tool_ids

    available_tool_ids = get_available_tool_ids(agent)
    custom_tools_available = is_custom_tools_available_for_agent(agent)
    compatible: list[GlobalAgentSkill] = []
    for skill in _prefetch_global_skill_catalog():
        bundled_tool_ids = set(skill.get_bundled_custom_tool_ids())
        if bundled_tool_ids and not custom_tools_available:
            continue
        required_tools = set(normalize_skill_tool_ids(skill.tools))
        if required_tools.issubset(available_tool_ids | bundled_tool_ids):
            compatible.append(skill)
    return compatible


def enable_global_skills(
    agent: PersistentAgent,
    skill_names: Iterable[str],
    *,
    available_skills: Iterable[GlobalAgentSkill] | None = None,
) -> dict[str, Any]:
    """Import compatible global skills into the agent's local skill history."""
    requested: list[str] = []
    seen: set[str] = set()
    for name in skill_names or []:
        if not isinstance(name, str):
            continue
        normalized = name.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        requested.append(normalized)

    catalog = list(available_skills) if available_skills is not None else get_compatible_global_skills(agent)
    skills_by_name = {skill.name: skill for skill in catalog}
    selected_skills = [skills_by_name[name] for name in requested if name in skills_by_name]
    if not selected_skills:
        return {
            "status": "success",
            "enabled": [],
            "already_enabled": [],
            "conflicts": [],
            "tool_manager": None,
        }

    requested_names = {skill.name for skill in selected_skills}
    selected_skill_ids = {skill.id for skill in selected_skills}
    existing_rows = list(
        PersistentAgentSkill.objects.filter(agent=agent).filter(
            Q(global_skill_id__in=selected_skill_ids) | Q(name__in=requested_names)
        )
    )
    imported_skill_ids = {row.global_skill_id for row in existing_rows if row.global_skill_id is not None}
    existing_names = {row.name for row in existing_rows}

    enabled: list[str] = []
    already_enabled: list[str] = []
    conflicts: list[str] = []
    failed: list[str] = []
    imported_rows: list[PersistentAgentSkill] = []

    for skill in selected_skills:
        if skill.id in imported_skill_ids:
            already_enabled.append(skill.name)
            continue
        if skill.name in existing_names:
            already_enabled.append(skill.name)
            continue
        prepared_tools, preparation_error = _prepare_bundled_custom_tools(agent, skill)
        if preparation_error:
            conflicts.append(skill.name)
            logger.warning(
                "Failed preparing bundled custom tools for global skill %s on agent %s: %s",
                skill.name,
                agent.id,
                preparation_error,
            )
            continue

        try:
            normalized_secrets = list(normalize_skill_secret_requirements(skill.secrets))
        except ValueError as exc:
            failed.append(skill.name)
            logger.warning(
                "Failed normalizing secrets for global skill %s on agent %s: %s",
                skill.name,
                agent.id,
                exc,
            )
            continue

        effective_tools = list(skill.get_effective_tool_ids())
        try:
            with transaction.atomic():
                success, materialization_error = _materialize_bundled_custom_tools(agent, prepared_tools or [])
                if not success:
                    raise ValueError(materialization_error or "Failed to materialize bundled custom tools.")
                imported_row = PersistentAgentSkill.objects.create(
                    agent=agent,
                    global_skill=skill,
                    name=skill.name,
                    description=skill.description,
                    version=1,
                    tools=effective_tools,
                    secrets=normalized_secrets,
                    instructions=skill.instructions,
                )
        except Exception as exc:
            failed.append(skill.name)
            logger.warning(
                "Failed importing global skill %s for agent %s: %s",
                skill.name,
                agent.id,
                exc,
                exc_info=True,
            )
            continue

        imported_rows.append(imported_row)
        enabled.append(skill.name)
        existing_names.add(skill.name)
        imported_skill_ids.add(skill.id)

    for row in imported_rows:
        track_agent_skill_event(
            agent=agent,
            event=AnalyticsEvent.PERSISTENT_AGENT_GLOBAL_SKILL_IMPORTED,
            skill_name=row.name,
            skill_version=row.version,
            tools=row.tools,
            skill_origin=SKILL_ORIGIN_GLOBAL_IMPORT,
            global_skill=row.global_skill,
        )

    tool_manager_result = None
    if enabled:
        from .tool_manager import ensure_skill_tools_enabled

        tool_manager_result = ensure_skill_tools_enabled(agent)
        logger.info(
            "Imported %d global skills for agent %s: %s",
            len(enabled),
            agent.id,
            ", ".join(enabled),
        )

    return {
        "status": "success",
        "enabled": enabled,
        "already_enabled": already_enabled,
        "conflicts": conflicts,
        "failed": failed,
        "tool_manager": tool_manager_result,
    }
