"""Utilities for managing generated tags for persistent agents."""

import logging
from typing import Iterable, List

from api.agent.short_description import compute_charter_hash
from api.models import PersistentAgent

logger = logging.getLogger(__name__)

MAX_TAGS = 5
MAX_TAG_LENGTH = 64
_WRAP_CHARS = '`"\'[]{}(),.;:'


def strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned

    remainder = cleaned[3:]
    closing_index = remainder.rfind("```")
    if closing_index != -1:
        body = remainder[:closing_index]
    else:
        body = remainder

    body = body.strip()
    if not body:
        return body

    if "\n" in body:
        first_line, rest = body.split("\n", 1)
        if rest and first_line and len(first_line.split()) == 1:
            body = rest
    else:
        parts = body.split(None, 1)
        if len(parts) == 2 and parts[0].isalpha():
            body = parts[1]

    return body.strip()


def _normalize_token(token: str) -> str:
    cleaned = " ".join(token.split())
    cleaned = strip_code_fence(cleaned)
    cleaned = cleaned.strip()

    # Remove language prefixes like `json`.
    if cleaned.lower().startswith("json "):
        cleaned = cleaned[5:].strip()

    cleaned = cleaned.strip(_WRAP_CHARS).strip()
    # If we still have wrapping brackets/quotes, strip again.
    cleaned = cleaned.strip(_WRAP_CHARS).strip()

    # Remove lingering ``` fragments.
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].rstrip()
        cleaned = cleaned.strip(_WRAP_CHARS).strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[3:].lstrip()
        cleaned = cleaned.strip(_WRAP_CHARS).strip()

    return cleaned


def normalize_tags(raw_tags: Iterable[str]) -> List[str]:
    """Return a cleaned, de-duplicated list of tags, capped at MAX_TAGS."""
    normalized: List[str] = []
    seen: set[str] = set()

    for tag in raw_tags:
        if not isinstance(tag, str):
            continue
        cleaned = _normalize_token(tag)
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


__all__ = ["MAX_TAGS", "normalize_tags", "maybe_schedule_agent_tags", "strip_code_fence"]
