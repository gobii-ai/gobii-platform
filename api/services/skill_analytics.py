"""Shared analytics helpers for global and agent skill lifecycle events."""

from typing import Iterable

from api.agent.tools.skill_utils import normalize_skill_tool_ids
from api.models import GlobalAgentSkill, PersistentAgent, PersistentAgentSkill
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

SKILL_ORIGIN_LOCAL = "local"
SKILL_ORIGIN_GLOBAL_IMPORT = "global_import"
SKILL_ORIGIN_FORKED_FROM_GLOBAL = "forked_from_global"


def infer_agent_skill_origin(
    skill: PersistentAgentSkill | None,
    *,
    had_global_ancestor: bool = False,
) -> str:
    """Infer the skill origin label used in analytics properties."""
    if skill is not None and skill.global_skill_id:
        return SKILL_ORIGIN_GLOBAL_IMPORT
    if had_global_ancestor:
        return SKILL_ORIGIN_FORKED_FROM_GLOBAL
    return SKILL_ORIGIN_LOCAL


def track_global_agent_skill_event(
    *,
    user_id: int | str | None,
    event: AnalyticsEvent,
    skill: GlobalAgentSkill,
    source: AnalyticsSource,
) -> None:
    """Track analytics for admin-managed global skill template changes."""
    if not user_id:
        return

    Analytics.track_event(
        user_id=user_id,
        event=event,
        source=source,
        properties=_build_global_skill_properties(skill),
    )


def track_agent_skill_event(
    *,
    agent: PersistentAgent,
    event: AnalyticsEvent,
    skill_name: str,
    tools: Iterable[str] | None,
    skill_origin: str,
    skill_version: int | None = None,
    global_skill: GlobalAgentSkill | None = None,
    source: AnalyticsSource = AnalyticsSource.AGENT,
) -> None:
    """Track analytics for agent skill lifecycle changes."""
    if not agent.user_id:
        return

    Analytics.track_event(
        user_id=agent.user_id,
        event=event,
        source=source,
        properties=_build_agent_skill_properties(
            agent=agent,
            skill_name=skill_name,
            tools=tools,
            skill_origin=skill_origin,
            skill_version=skill_version,
            global_skill=global_skill,
        ),
    )


def _build_global_skill_properties(skill: GlobalAgentSkill) -> dict[str, object]:
    tool_ids = list(normalize_skill_tool_ids(skill.tools))
    return {
        "global_skill_id": str(skill.id),
        "global_skill_name": skill.name,
        "tool_ids": tool_ids,
        "tool_count": len(tool_ids),
        "is_active": skill.is_active,
    }


def _build_agent_skill_properties(
    *,
    agent: PersistentAgent,
    skill_name: str,
    tools: Iterable[str] | None,
    skill_origin: str,
    skill_version: int | None = None,
    global_skill: GlobalAgentSkill | None = None,
) -> dict[str, object]:
    tool_ids = list(normalize_skill_tool_ids(tools or []))
    properties: dict[str, object] = {
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "skill_name": skill_name,
        "tool_ids": tool_ids,
        "tool_count": len(tool_ids),
        "skill_origin": skill_origin,
    }
    if skill_version is not None:
        properties["skill_version"] = skill_version
    if global_skill is not None:
        properties["global_skill_id"] = str(global_skill.id)
        properties["global_skill_name"] = global_skill.name
    return Analytics.with_org_properties(properties, organization=agent.organization)
