"""Utilities for managing short descriptions of persistent agents."""
from __future__ import annotations

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


def prepare_short_description(text: str, max_length: int = 160) -> str:
    """Normalize and truncate LLM output for storage/display."""
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    return _truncate_text(normalized, max_length)


def build_listing_description(
    agent: PersistentAgent,
    *,
    max_length: int = 160,
    fallback_message: str = "Agent is initializing…",
) -> Tuple[str, str]:
    """Return a tuple of (description, source) for UI listings.

    Source values: "short", "charter", or "placeholder".
    """
    short_desc = prepare_short_description(getattr(agent, "short_description", ""), max_length)
    if short_desc:
        return short_desc, "short"

    charter = _normalize_text(getattr(agent, "charter", ""))
    if charter:
        return _truncate_text(charter, max_length), "charter"

    return fallback_message, "placeholder"


def maybe_schedule_short_description(agent: PersistentAgent) -> bool:
    """Schedule short description generation if needed.

    Returns True when a task was enqueued, False otherwise.
    """
    charter = (agent.charter or "").strip()
    if not charter:
        return False

    charter_hash = compute_charter_hash(charter)

    if agent.short_description and agent.short_description_charter_hash == charter_hash:
        return False

    if agent.short_description_requested_hash == charter_hash:
        return False

    updated = PersistentAgent.objects.filter(id=agent.id).update(
        short_description_requested_hash=charter_hash
    )
    if not updated:
        return False

    try:
        from api.agent.tasks.short_description import (
            generate_agent_short_description_task,
        )

        generate_agent_short_description_task.delay(str(agent.id), charter_hash)
        logger.debug(
            "Queued short description generation for agent %s (hash=%s)",
            agent.id,
            charter_hash,
        )
        return True
    except Exception:
        logger.exception(
            "Failed to enqueue short description generation for agent %s", agent.id
        )
        PersistentAgent.objects.filter(id=agent.id).update(
            short_description_requested_hash=""
        )
        return False


__all__ = [
    "build_listing_description",
    "compute_charter_hash",
    "prepare_short_description",
    "maybe_schedule_short_description",
]
