"""Utilities for managing mini descriptions of persistent agents."""

import hashlib
import logging
from typing import Tuple

from api.models import PersistentAgent

logger = logging.getLogger(__name__)


def compute_charter_hash(charter: str) -> str:
    """Return a stable hash for the given charter text."""
    normalized = (charter or "").strip().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def _normalize_text(text: str) -> str:
    """Collapse whitespace and strip the provided text."""
    if not text:
        return ""
    return " ".join(text.split()).strip()


def _truncate_text(text: str, max_length: int) -> str:
    if max_length <= 0 or len(text) <= max_length:
        return text
    truncated = text[: max_length - 1].rstrip()
    return truncated + "…"


def _truncate_words(text: str, max_words: int) -> str:
    if max_words <= 0:
        return ""
    words = [part for part in (text or "").split() if part]
    if not words:
        return ""
    if len(words) < 2 and len(words) >= 1:
        # Respect single-word responses when no alternatives exist.
        return words[0]
    limited = words[: max_words or len(words)]
    if len(limited) < 2 and len(words) >= 2:
        limited = words[:2]
    return " ".join(limited)


def prepare_mini_description(text: str, *, max_words: int = 5, max_length: int = 60) -> str:
    """Normalize and aggressively trim text to a short word phrase."""
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    limited = _truncate_words(normalized, max_words)
    if not limited:
        return ""
    if max_length <= 0:
        return limited
    if len(limited) <= max_length:
        return limited
    return limited[:max_length].rstrip()


def build_listing_description(
    agent: PersistentAgent,
    *,
    max_length: int = 160,
    fallback_message: str = "Agent is initializing…",
) -> Tuple[str, str]:
    """Return a tuple of (description, source) for UI listings.

    Source values: "mini" or "placeholder".
    """
    mini_desc = prepare_mini_description(getattr(agent, "mini_description", ""))
    if mini_desc:
        return _truncate_text(mini_desc, max_length), "mini"

    return fallback_message, "placeholder"


def build_mini_description(
    agent: PersistentAgent,
    *,
    fallback_message: str = "Agent",
) -> Tuple[str, str]:
    """Return a tuple of (mini_description, source) for very small UI slots."""
    mini = prepare_mini_description(getattr(agent, "mini_description", ""))
    if mini:
        return mini, "mini"

    return fallback_message, "placeholder"


def maybe_schedule_mini_description(
    agent: PersistentAgent,
    routing_profile_id: str | None = None,
) -> bool:
    """Schedule mini description generation if needed.

    Args:
        agent: The agent to generate a mini description for.
        routing_profile_id: Optional routing profile ID to use for LLM calls.
    """
    charter = (agent.charter or "").strip()
    if not charter:
        return False

    charter_hash = compute_charter_hash(charter)

    if agent.mini_description and agent.mini_description_charter_hash == charter_hash:
        return False

    if agent.mini_description_requested_hash == charter_hash:
        return False

    updated = PersistentAgent.objects.filter(id=agent.id).update(
        mini_description_requested_hash=charter_hash
    )
    if not updated:
        return False

    try:
        from api.agent.tasks.mini_description import (
            generate_agent_mini_description_task,
        )

        generate_agent_mini_description_task.delay(str(agent.id), charter_hash, routing_profile_id)
        logger.debug(
            "Queued mini description generation for agent %s (hash=%s)",
            agent.id,
            charter_hash,
        )
        return True
    except Exception:
        logger.exception(
            "Failed to enqueue mini description generation for agent %s", agent.id
        )
        PersistentAgent.objects.filter(id=agent.id).update(
            mini_description_requested_hash=""
        )
        return False


__all__ = [
    "build_mini_description",
    "build_listing_description",
    "compute_charter_hash",
    "prepare_mini_description",
    "maybe_schedule_mini_description",
]
