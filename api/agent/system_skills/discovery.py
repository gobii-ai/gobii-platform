"""Prompt-time discovery hints for disabled system skills."""

import re
from dataclasses import dataclass

from api.models import (
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentSystemSkillState,
    PersistentAgentToolCall,
)

from .registry import SYSTEM_SKILL_REGISTRY, SystemSkillDefinition
from .service import get_available_system_skill_tool_names


SYSTEM_SKILL_DISCOVERY_LIMIT = 2
_NON_ALPHANUMERIC_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class SystemSkillDiscoverySuggestion:
    skill_key: str
    name: str
    search_query: str
    precedes: str


def normalize_discovery_text(value: object) -> str:
    """Normalize prose so discovery phrases match across punctuation and spacing."""
    return _NON_ALPHANUMERIC_RE.sub(" ", str(value or "").casefold()).strip()


def _contains_discovery_trigger(normalized_text: str, trigger: str) -> bool:
    normalized_trigger = normalize_discovery_text(trigger)
    if not normalized_text or not normalized_trigger:
        return False
    return f" {normalized_trigger} " in f" {normalized_text} "


def matching_system_skill_definitions(text: object) -> list[SystemSkillDefinition]:
    """Return skills with an explicit high-confidence discovery trigger in text."""
    normalized_text = normalize_discovery_text(text)
    if not normalized_text:
        return []

    return [
        definition
        for definition in SYSTEM_SKILL_REGISTRY.values()
        if definition.discovery_triggers
        and any(
            _contains_discovery_trigger(normalized_text, trigger)
            for trigger in definition.discovery_triggers
        )
    ]


def _recent_discovery_context(agent: PersistentAgent) -> tuple[str, object | None]:
    messages = list(
        PersistentAgentMessage.objects.filter(
            owner_agent=agent,
            is_outbound=False,
        ).order_by("-timestamp", "-seq")[:3]
    )
    parts = [agent.charter, *(message.body for message in messages)]
    return " ".join(part for part in parts if part), messages[0].timestamp if messages else None


def _searched_for_definition_since(
    agent: PersistentAgent,
    definition: SystemSkillDefinition,
    since,
) -> bool:
    if since is None:
        return False

    calls = PersistentAgentToolCall.objects.filter(
        step__agent=agent,
        tool_name="search_tools",
        step__created_at__gte=since,
    )

    expected_query = normalize_discovery_text(definition.discovery_query or definition.name)
    expected_key = normalize_discovery_text(definition.skill_key)
    for params in calls.values_list("tool_params", flat=True):
        query = normalize_discovery_text(params.get("query")) if isinstance(params, dict) else ""
        if query and (expected_query in query or expected_key in query):
            return True
    return False


def get_system_skill_discovery_suggestions(
    agent: PersistentAgent,
    *,
    limit: int = SYSTEM_SKILL_DISCOVERY_LIMIT,
) -> list[SystemSkillDiscoverySuggestion]:
    """Suggest matching disabled skills without changing agent state."""
    if limit <= 0:
        return []

    context_text, latest_inbound_at = _recent_discovery_context(agent)
    definitions = matching_system_skill_definitions(context_text)
    if not definitions:
        return []

    enabled_keys = set(
        PersistentAgentSystemSkillState.objects.filter(
            agent=agent,
            is_enabled=True,
        ).values_list("skill_key", flat=True)
    )
    available_tool_names = get_available_system_skill_tool_names(agent)

    suggestions: list[SystemSkillDiscoverySuggestion] = []
    for definition in definitions:
        if definition.skill_key in enabled_keys:
            continue
        if not definition.should_render_prompt(agent):
            continue
        if not set(definition.tool_names).issubset(available_tool_names):
            continue
        if _searched_for_definition_since(agent, definition, latest_inbound_at):
            continue
        suggestions.append(
            SystemSkillDiscoverySuggestion(
                skill_key=definition.skill_key,
                name=definition.name,
                search_query=definition.discovery_query or definition.name.casefold(),
                precedes=definition.discovery_precedes,
            )
        )
        if len(suggestions) >= limit:
            break

    return suggestions


def format_system_skill_discovery_prompt(
    agent: PersistentAgent,
) -> tuple[str, tuple[str, ...]]:
    """Render an internal system-prompt hint for matching disabled skills."""
    suggestions = get_system_skill_discovery_suggestions(agent)
    if not suggestions:
        return "", ()

    lines = [
        "## Suggested Capability Discovery",
        "",
        "The current work strongly matches system-skill guidance that is not enabled:",
    ]
    for suggestion in suggestions:
        lines.append(
            f'- {suggestion.name} (`{suggestion.skill_key}`): REQUIRED NEXT RELEVANT ACTION: call '
            f'`search_tools("{suggestion.search_query}")` before using {suggestion.precedes} for this work. '
            "Use that exact discovery query by itself; do not replace or expand it with task details."
        )
    lines.extend(
        [
            "Follow each suggestion even when an enabled web, search, data, or integration tool could perform the "
            "underlying action.",
            "This is internal capability guidance. Do not mention the hint, system skill, or discovery step to the user.",
        ]
    )
    return "\n".join(lines), tuple(suggestion.skill_key for suggestion in suggestions)
