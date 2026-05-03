"""Code-defined system skill definitions and search-time matching."""

from dataclasses import dataclass, field
from typing import Mapping, Optional


@dataclass(frozen=True)
class SystemSkillDocLink:
    """A documentation link surfaced only during setup and troubleshooting."""

    title: str
    url: str
    description: str = ""


@dataclass(frozen=True)
class SystemSkillField:
    """A single profile field required or accepted by a system skill."""

    key: str
    name: str
    description: str = ""
    required: bool = True
    default: Optional[str] = None
    how_to_get: str = ""
    docs: tuple[SystemSkillDocLink, ...] = ()


@dataclass(frozen=True)
class SystemSkillDefinition:
    """A code-defined, discoverable system skill alias over real tools."""

    skill_key: str
    name: str
    search_summary: str
    tool_names: tuple[str, ...]
    enables: tuple[str, ...] = ()
    use_when: tuple[str, ...] = ()
    query_aliases: tuple[str, ...] = ()
    prompt_instructions: str = ""
    default_enabled: bool = False
    required_profile_fields: tuple[SystemSkillField, ...] = ()
    optional_profile_fields: tuple[SystemSkillField, ...] = ()
    default_values: Mapping[str, str] = field(default_factory=dict)
    setup_instructions: str = ""
    setup_steps: tuple[str, ...] = ()
    setup_docs: tuple[SystemSkillDocLink, ...] = ()
    troubleshooting_tips: tuple[str, ...] = ()
    bootstrap_profile_key: str = "default"
    bootstrap_profile_label: str = ""

    def profile_fields(self) -> tuple[SystemSkillField, ...]:
        return tuple(self.required_profile_fields) + tuple(self.optional_profile_fields)

    def search_terms(self) -> tuple[str, ...]:
        terms = [
            self.skill_key.replace("_", " "),
            self.name,
            *self.query_aliases,
            *self.use_when,
            *self.enables,
        ]
        seen: set[str] = set()
        normalized: list[str] = []
        for term in terms:
            value = str(term or "").strip().lower()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return tuple(normalized)

from .defaults import DEFAULT_SYSTEM_SKILL_DEFINITIONS


SYSTEM_SKILL_REGISTRY: dict[str, SystemSkillDefinition] = dict(DEFAULT_SYSTEM_SKILL_DEFINITIONS)


def get_system_skill_definition(skill_key: str) -> Optional[SystemSkillDefinition]:
    if not isinstance(skill_key, str):
        return None
    return SYSTEM_SKILL_REGISTRY.get(skill_key.strip())


def _score_definition(query: str, definition: SystemSkillDefinition) -> int:
    text = str(query or "").strip().lower()
    if not text:
        return 0

    score = 0
    for term in definition.search_terms():
        if not term:
            continue
        if term in text:
            score = max(score, len(term) + 10)
            continue

        term_tokens = [token for token in term.replace("-", " ").split() if len(token) > 2]
        if term_tokens and all(token in text for token in term_tokens):
            score = max(score, len(term_tokens) + 4)

    return score


def shortlist_system_skills(
    query: str,
    *,
    available_tool_names: set[str],
    limit: int = 5,
) -> list[SystemSkillDefinition]:
    if limit <= 0 or not SYSTEM_SKILL_REGISTRY:
        return []

    scored: list[tuple[int, str, SystemSkillDefinition]] = []
    for definition in SYSTEM_SKILL_REGISTRY.values():
        if not definition.tool_names:
            continue
        if not set(definition.tool_names).issubset(available_tool_names):
            continue
        score = _score_definition(query, definition)
        if score <= 0:
            continue
        scored.append((score, definition.skill_key, definition))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [definition for _score, _key, definition in scored[:limit]]
