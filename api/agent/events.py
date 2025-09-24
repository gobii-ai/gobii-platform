"""Helper utilities for emitting persistent agent change notifications."""
from __future__ import annotations

import json
import logging
from typing import Any, Mapping
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from config.redis_client import get_redis_client

logger = logging.getLogger(__name__)

# Keep streams bounded while retaining enough history for reconnects.
_MAX_STREAM_LENGTH = 2000
_PROCESSING_RESOURCE_TEMPLATE = "agent-event-processing:{agent_id}"


def _stream_key(agent_id: str | UUID) -> str:
    return f"pa:events:{{{agent_id}}}"


def get_agent_event_stream_key(agent_id: str | UUID) -> str:
    """Return the Redis stream key for a persistent agent."""
    return _stream_key(agent_id)


def get_agent_processing_resource_key(agent_id: str | UUID) -> str:
    """Return the raw resource key used for the Redlock."""

    return _PROCESSING_RESOURCE_TEMPLATE.format(agent_id=str(agent_id))


def get_agent_processing_lock_key(agent_id: str | UUID) -> str:
    """Return the actual Redis key maintained by Redlock."""

    from pottery.redlock import Redlock

    resource = get_agent_processing_resource_key(agent_id)
    return f"{Redlock._KEY_PREFIX}:{resource}"


def is_agent_processing(agent_id: str | UUID) -> bool:
    """Return True if the agent's processing lock is currently held."""

    try:
        redis = get_redis_client()
        redis_key = get_agent_processing_lock_key(agent_id)
        resource_key = get_agent_processing_resource_key(agent_id)
        exists = redis.exists(redis_key)
        matching = list(redis.scan_iter(match="*agent-event-processing*"))
        logger.info(
            "processing_lock_check agent=%s redis_key=%s resource_key=%s exists=%s matching_keys=%s",
            agent_id,
            redis_key,
            resource_key,
            int(exists),
            matching,
        )
        return bool(exists)
    except Exception:  # pragma: no cover - defensive; processing status should not break views
        logger.debug("Failed to check processing lock state for %s", agent_id, exc_info=True)
        return False


def _serialise_fields(fields: Mapping[str, Any]) -> dict[str, str]:
    serialised: dict[str, str] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            serialised[str(key)] = json.dumps(value, separators=(",", ":"))
        else:
            serialised[str(key)] = str(value)
    return serialised


def publish_agent_event(agent_id: str | UUID, *, kind: str, resource_id: str | UUID | None = None, payload: Mapping[str, Any] | None = None) -> None:
    """Write an event into the agent's Redis stream immediately."""
    fields = {
        "kind": kind,
        "agent_id": str(agent_id),
        "timestamp": timezone.now().isoformat(timespec="microseconds"),
    }
    if resource_id is not None:
        fields["resource_id"] = str(resource_id)
    if payload:
        fields["payload"] = payload

    try:
        redis = get_redis_client()
        redis.xadd(
            _stream_key(agent_id),
            _serialise_fields(fields),
            maxlen=_MAX_STREAM_LENGTH,
            approximate=True,
        )
    except Exception:  # pragma: no cover - defensive; real Redis errors are rare and logged
        logger.exception("Failed to publish agent event", extra={"agent_id": str(agent_id), "kind": kind})


def publish_agent_event_on_commit(agent_id: str | UUID, *, kind: str, resource_id: str | UUID | None = None, payload: Mapping[str, Any] | None = None) -> None:
    """Schedule an event to publish after the current transaction commits."""

    def _runner() -> None:
        publish_agent_event(agent_id, kind=kind, resource_id=resource_id, payload=payload)

    try:
        transaction.on_commit(_runner)
    except Exception:
        # If we're outside a transaction, fall back to immediate publish.
        logger.debug("transaction.on_commit unavailable; publishing agent event immediately", exc_info=True)
        _runner()
