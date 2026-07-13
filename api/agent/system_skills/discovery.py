"""Prompt-time discovery hints for disabled system skills."""

from api.models import (
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentSystemSkillState,
    PersistentAgentToolCall,
)

from .registry import SYSTEM_SKILL_REGISTRY, normalize_system_skill_key, shortlist_system_skills
from .service import get_available_system_skill_tool_names


SYSTEM_SKILL_DISCOVERY_LIMIT = 2


def _recent_discovery_context(agent: PersistentAgent):
    messages = list(
        PersistentAgentMessage.objects.filter(
            owner_agent=agent,
            is_outbound=False,
        ).order_by("-timestamp", "-seq")[:3]
    )
    parts = [agent.charter, *(message.body for message in messages)]
    return " ".join(part for part in parts if part), messages[0].timestamp if messages else None


def get_system_skill_discovery_suggestions(
    agent: PersistentAgent,
    *,
    limit: int = SYSTEM_SKILL_DISCOVERY_LIMIT,
):
    """Return relevant disabled skills without changing agent state."""
    if limit <= 0:
        return []

    context_text, latest_inbound_at = _recent_discovery_context(agent)
    available_tool_names = get_available_system_skill_tool_names(agent)
    definitions = shortlist_system_skills(
        context_text,
        available_tool_names=available_tool_names,
        limit=len(SYSTEM_SKILL_REGISTRY),
        discovery_only=True,
    )
    if not definitions:
        return []

    enabled_keys = {
        normalize_system_skill_key(skill_key)
        for skill_key in PersistentAgentSystemSkillState.objects.filter(
            agent=agent,
            is_enabled=True,
        ).values_list("skill_key", flat=True)
    }
    searched_keys: set[str] = set()
    if latest_inbound_at is not None:
        tool_params = PersistentAgentToolCall.objects.filter(
            step__agent=agent,
            tool_name="search_tools",
            step__created_at__gte=latest_inbound_at,
        ).values_list("tool_params", flat=True)
        for params in tool_params:
            query = params.get("query") if isinstance(params, dict) else ""
            searched_keys.update(
                definition.skill_key
                for definition in shortlist_system_skills(
                    query,
                    available_tool_names=available_tool_names,
                    limit=len(SYSTEM_SKILL_REGISTRY),
                    discovery_only=True,
                )
            )

    return [
        definition
        for definition in definitions
        if not definition.default_enabled
        and definition.skill_key not in enabled_keys
        and definition.skill_key not in searched_keys
        and definition.should_render_prompt(agent)
    ][:limit]


def format_system_skill_discovery_prompt(
    agent: PersistentAgent,
) -> tuple[str, tuple[str, ...]]:
    """Render internal capability guidance for the dynamic user prompt."""
    suggestions = get_system_skill_discovery_suggestions(agent)
    if not suggestions:
        return "", ()

    lines = [
        "## Suggested Capability Discovery",
        "",
        "The current work strongly matches system-skill guidance that is not enabled:",
    ]
    for definition in suggestions:
        query = definition.name.casefold()
        lines.append(
            f'- {definition.name} (`{definition.skill_key}`): REQUIRED NEXT RELEVANT ACTION: call '
            f'`search_tools("{query}")` before using other task tools. Use that exact discovery query by itself; '
            "do not replace or expand it with task details."
        )
    lines.extend(
        [
            "Follow each suggestion even when an enabled web, search, data, or integration tool could perform the "
            "underlying action.",
            "This is internal capability guidance. Do not mention the hint, system skill, or discovery step to the user.",
        ]
    )
    return "\n".join(lines), tuple(definition.skill_key for definition in suggestions)
