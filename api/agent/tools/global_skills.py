"""Helpers for platform-managed global skills."""

import logging
from typing import Any, Iterable

from django.db import transaction
from django.db.models import Q

from api.models import GlobalAgentSkill, PersistentAgent, PersistentAgentSkill
from .skill_utils import normalize_skill_tool_ids

logger = logging.getLogger(__name__)

def get_compatible_global_skills(agent: PersistentAgent) -> list[GlobalAgentSkill]:
    """Return active global skills whose required tools are available to the agent."""
    from .tool_manager import get_available_tool_ids

    available_tool_ids = get_available_tool_ids(agent)
    compatible: list[GlobalAgentSkill] = []
    for skill in GlobalAgentSkill.objects.filter(is_active=True).order_by("name"):
        required_tools = set(normalize_skill_tool_ids(skill.tools))
        if required_tools.issubset(available_tool_ids):
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
    new_rows: list[PersistentAgentSkill] = []

    for skill in selected_skills:
        if skill.id in imported_skill_ids:
            already_enabled.append(skill.name)
            continue
        if skill.name in existing_names:
            conflicts.append(skill.name)
            continue
        new_rows.append(
            PersistentAgentSkill(
                agent=agent,
                global_skill=skill,
                name=skill.name,
                description=skill.description,
                version=1,
                tools=list(normalize_skill_tool_ids(skill.tools)),
                instructions=skill.instructions,
            )
        )
        enabled.append(skill.name)
        existing_names.add(skill.name)
        imported_skill_ids.add(skill.id)

    if new_rows:
        with transaction.atomic():
            PersistentAgentSkill.objects.bulk_create(new_rows)

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
        "tool_manager": tool_manager_result,
    }
