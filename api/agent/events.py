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


def _stream_key(agent_id: str | UUID) -> str:
    return f"pa:events:{{{agent_id}}}"


def get_agent_event_stream_key(agent_id: str | UUID) -> str:
    """Return the Redis stream key for a persistent agent."""
    return _stream_key(agent_id)


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
