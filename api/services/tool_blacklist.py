import logging
from typing import Any, Iterable

from django.apps import apps
from django.core.cache import cache
from django.core.exceptions import AppRegistryNotReady
from django.db.utils import DatabaseError

from api.agent.core.llm_config import get_agent_llm_tier

logger = logging.getLogger(__name__)
_TIER_TOOL_BLACKLISTS_CACHE_KEY = "intelligence_tier_tool_blacklists:v1"


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

    blacklists = cache.get(_TIER_TOOL_BLACKLISTS_CACHE_KEY)
    if blacklists is None:
        blacklists = _load_tier_tool_blacklists()
        cache.set(_TIER_TOOL_BLACKLISTS_CACHE_KEY, blacklists, timeout=300)

    return set(blacklists.get(str(tier_key), []))


def _load_tier_tool_blacklists() -> dict[str, list[str]]:
    try:
        IntelligenceTier = apps.get_model("api", "IntelligenceTier")
        rows = IntelligenceTier.objects.all().values_list("key", "blacklisted_tools")
    except (AppRegistryNotReady, DatabaseError, LookupError):
        logger.debug("Failed to load intelligence tier tool blacklists", exc_info=True)
        return {}

    return {
        str(key): normalize_tool_blacklist(raw_value)
        for key, raw_value in rows
    }


def invalidate_tool_blacklist_cache() -> None:
    cache.delete(_TIER_TOOL_BLACKLISTS_CACHE_KEY)


def get_agent_tool_blacklist(agent: Any | None) -> set[str]:
    if agent is None:
        return set()
    _refresh_agent_preferred_tier_id(agent)
    tier = get_agent_llm_tier(agent)
    return get_tier_tool_blacklist(tier.value)


def _refresh_agent_preferred_tier_id(agent: Any) -> None:
    agent_id = getattr(agent, "id", None)
    if not agent_id:
        return

    try:
        PersistentAgent = apps.get_model("api", "PersistentAgent")
        current_tier_id = (
            PersistentAgent.objects.filter(id=agent_id)
            .values_list("preferred_llm_tier_id", flat=True)
            .first()
        )
    except (AppRegistryNotReady, DatabaseError, LookupError):
        logger.debug("Failed to refresh preferred tier for agent %s", agent_id, exc_info=True)
        return

    if current_tier_id is None or current_tier_id == getattr(agent, "preferred_llm_tier_id", None):
        return

    agent.preferred_llm_tier_id = current_tier_id
    fields_cache = getattr(getattr(agent, "_state", None), "fields_cache", None)
    if isinstance(fields_cache, dict):
        fields_cache.pop("preferred_llm_tier", None)


def is_tool_blacklisted_for_agent(agent: Any | None, tool_name: str | None) -> bool:
    if not tool_name:
        return False
    return tool_name in get_agent_tool_blacklist(agent)


def tool_blacklist_error(tool_name: str) -> dict[str, str]:
    return {
        "status": "error",
        "message": f"Tool '{tool_name}' is unavailable for this agent's intelligence tier.",
    }
