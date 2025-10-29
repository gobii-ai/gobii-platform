"""Utilities for managing generated tags for persistent agents."""

import logging
from typing import Iterable, List

from api.agent.short_description import compute_charter_hash
from api.models import PersistentAgent

logger = logging.getLogger(__name__)

MAX_TAGS = 5
MAX_TAG_LENGTH = 64


def normalize_tags(raw_tags: Iterable[str]) -> List[str]:
    """Return a cleaned, de-duplicated list of tags, capped at MAX_TAGS."""
    normalized: List[str] = []
    seen: set[str] = set()

    for tag in raw_tags:
        if not isinstance(tag, str):
            continue
        cleaned = " ".join(tag.split()).strip()
        if not cleaned:
            continue
        if len(cleaned) > MAX_TAG_LENGTH:
            cleaned = cleaned[:MAX_TAG_LENGTH].rstrip()
        canonical = cleaned.lower()
        if canonical in seen:
            continue
        seen.add(canonical)
        normalized.append(cleaned)
        if len(normalized) >= MAX_TAGS:
            break

    return normalized


def maybe_schedule_agent_tags(agent: PersistentAgent) -> bool:
    """Schedule LLM tag generation when the charter changes."""
    charter = (agent.charter or "").strip()
    if not charter:
        return False

    charter_hash = compute_charter_hash(charter)
    existing = getattr(agent, "tags", None) or []
    existing_normalized = normalize_tags(existing)

    if existing_normalized != existing:
        agent.tags = existing_normalized
        PersistentAgent.objects.filter(id=agent.id).update(tags=existing_normalized)

    if existing_normalized and agent.tags_charter_hash == charter_hash:
        return False

    if agent.tags_requested_hash == charter_hash:
        return False

    updated = PersistentAgent.objects.filter(id=agent.id).update(
        tags_requested_hash=charter_hash
    )
    if not updated:
        return False

    try:
        from api.agent.tasks.agent_tags import generate_agent_tags_task

        generate_agent_tags_task.delay(str(agent.id), charter_hash)
        logger.debug("Queued tag generation for agent %s (hash=%s)", agent.id, charter_hash)
        return True
    except Exception:
        logger.exception("Failed to enqueue tag generation for agent %s", agent.id)
        PersistentAgent.objects.filter(id=agent.id).update(tags_requested_hash="")
        return False


__all__ = ["MAX_TAGS", "normalize_tags", "maybe_schedule_agent_tags"]
