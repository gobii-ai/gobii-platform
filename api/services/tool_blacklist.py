import logging
from typing import Any, Iterable

from django.apps import apps
from django.core.exceptions import AppRegistryNotReady
from django.db.utils import DatabaseError

from api.agent.core.llm_config import get_agent_llm_tier

logger = logging.getLogger(__name__)


def normalize_tool_blacklist(value: Any) -> list[str]:
    """Return stable, de-duplicated tool names from admin or JSON input."""
    if value in (None, ""):
        return []

    if isinstance(value, str):
        candidates: Iterable[Any] = value.replace(",", "\n").splitlines()
    elif isinstance(value, (list, tuple, set)):
        candidates = value
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        name = str(item).strip()
        if not name or name in seen:
            continue
        normalized.append(name)
        seen.add(name)
    return normalized


def get_tier_tool_blacklist(tier_key: str | None) -> set[str]:
    if not tier_key:
        return set()

    try:
        IntelligenceTier = apps.get_model("api", "IntelligenceTier")
        raw_value = (
            IntelligenceTier.objects.filter(key=tier_key)
            .values_list("blacklisted_tools", flat=True)
            .first()
        )
    except (AppRegistryNotReady, DatabaseError, LookupError):
        logger.debug("Failed to load tool blacklist for intelligence tier %s", tier_key, exc_info=True)
        return set()

    return set(normalize_tool_blacklist(raw_value))


def get_agent_tool_blacklist(agent: Any | None) -> set[str]:
    if agent is None:
        return set()
    tier = get_agent_llm_tier(agent)
    return get_tier_tool_blacklist(tier.value)


def is_tool_blacklisted_for_agent(agent: Any | None, tool_name: str | None) -> bool:
    if not tool_name:
        return False
    return tool_name in get_agent_tool_blacklist(agent)


def tool_blacklist_error(tool_name: str) -> dict[str, str]:
    return {
        "status": "error",
        "message": f"Tool '{tool_name}' is unavailable for this agent's intelligence tier.",
    }
