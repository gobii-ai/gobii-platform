import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Union
from uuid import UUID

from config.redis_client import get_redis_client

logger = logging.getLogger(__name__)

_QUEUED_KEY_TEMPLATE = "agent-event-processing:queued:{agent_id}"
_DEFAULT_QUEUE_TTL_SECONDS = 3600
_HEARTBEAT_KEY_TEMPLATE = "agent-event-processing:heartbeat:{agent_id}"
_DEFAULT_HEARTBEAT_TTL_SECONDS = 600
_PENDING_SET_KEY = "agent-event-processing:pending"
_PENDING_DRAIN_SCHEDULE_KEY = "agent-event-processing:pending:drain:schedule"
_DEFAULT_PENDING_SET_TTL_SECONDS = 3600
_DEFAULT_PENDING_DRAIN_SCHEDULE_TTL_SECONDS = 60


@dataclass(frozen=True)
class PendingDrainSettings:
    pending_set_ttl_seconds: int
    pending_drain_delay_seconds: int
    pending_drain_limit: int
    pending_drain_schedule_ttl_seconds: int


def _queued_key(agent_id: Union[str, UUID]) -> str:
    return _QUEUED_KEY_TEMPLATE.format(agent_id=agent_id)


def _heartbeat_key(agent_id: Union[str, UUID]) -> str:
    return _HEARTBEAT_KEY_TEMPLATE.format(agent_id=agent_id)


def set_processing_queued_flag(agent_id: Union[str, UUID], *, ttl: int = _DEFAULT_QUEUE_TTL_SECONDS) -> None:
    """Mark the agent as having queued processing work."""
    try:
        client = get_redis_client()
        key = _queued_key(agent_id)
        client.set(key, "1")
        if ttl > 0:
            client.expire(key, ttl)
    except Exception:
        logger.exception("Failed to set processing queued flag for agent %s", agent_id)


def clear_processing_queued_flag(agent_id: Union[str, UUID]) -> None:
    """Clear the queued processing flag for the agent."""
    try:
        client = get_redis_client()
        client.delete(_queued_key(agent_id))
    except Exception:
        logger.exception("Failed to clear processing queued flag for agent %s", agent_id)


def is_processing_queued(agent_id: Union[str, UUID], client=None) -> bool:
    """Check whether the agent currently has queued processing work."""
    try:
        redis_client = client or get_redis_client()
        return bool(redis_client.exists(_queued_key(agent_id)))
    except Exception:
        logger.exception("Failed to check processing queued flag for agent %s", agent_id)
        return False


def set_processing_heartbeat(
    agent_id: Union[str, UUID],
    *,
    ttl: int = _DEFAULT_HEARTBEAT_TTL_SECONDS,
    run_id: str | None = None,
    stage: str | None = None,
    started_at: float | None = None,
    client=None,
) -> None:
    """Record a processing heartbeat for the agent."""
    if ttl <= 0:
        return
    now = time.time()
    payload = {
        "agent_id": str(agent_id),
        "run_id": run_id,
        "stage": stage,
        "started_at": started_at if started_at is not None else now,
        "last_seen": now,
    }
    try:
        redis_client = client or get_redis_client()
        redis_client.set(_heartbeat_key(agent_id), json.dumps(payload), ex=ttl)
    except Exception:
        logger.exception("Failed to set processing heartbeat for agent %s", agent_id)


def clear_processing_heartbeat(agent_id: Union[str, UUID], client=None) -> None:
    """Clear the processing heartbeat for the agent."""
    try:
        redis_client = client or get_redis_client()
        redis_client.delete(_heartbeat_key(agent_id))
    except Exception:
        logger.exception("Failed to clear processing heartbeat for agent %s", agent_id)


def get_processing_heartbeat(agent_id: Union[str, UUID], client=None) -> dict | None:
    """Fetch the last processing heartbeat payload for the agent."""
    try:
        redis_client = client or get_redis_client()
        raw = redis_client.get(_heartbeat_key(agent_id))
    except Exception:
        logger.exception("Failed to read processing heartbeat for agent %s", agent_id)
        return None
    if not raw:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "ignore")
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.exception("Failed to parse processing heartbeat for agent %s", agent_id)
        return None


def pending_set_key() -> str:
    return _PENDING_SET_KEY


def pending_drain_schedule_key() -> str:
    return _PENDING_DRAIN_SCHEDULE_KEY


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)


def get_pending_drain_settings(settings_obj=None) -> PendingDrainSettings:
    if settings_obj is None:
        from django.conf import settings as django_settings

        settings_obj = django_settings

    pending_set_ttl_seconds = _coerce_positive_int(
        getattr(settings_obj, "AGENT_EVENT_PROCESSING_PENDING_SET_TTL_SECONDS", None),
        _DEFAULT_PENDING_SET_TTL_SECONDS,
    )
    pending_drain_delay_seconds = _coerce_positive_int(
        getattr(settings_obj, "AGENT_EVENT_PROCESSING_PENDING_DRAIN_DELAY_SECONDS", None),
        5,
    )
    pending_drain_limit = _coerce_positive_int(
        getattr(settings_obj, "AGENT_EVENT_PROCESSING_PENDING_DRAIN_LIMIT", None),
        50,
    )
    schedule_default = max(30, pending_drain_delay_seconds * 6)
    pending_drain_schedule_ttl_seconds = _coerce_positive_int(
        getattr(settings_obj, "AGENT_EVENT_PROCESSING_PENDING_DRAIN_SCHEDULE_TTL_SECONDS", None),
        schedule_default,
    )
    return PendingDrainSettings(
        pending_set_ttl_seconds=pending_set_ttl_seconds,
        pending_drain_delay_seconds=pending_drain_delay_seconds,
        pending_drain_limit=pending_drain_limit,
        pending_drain_schedule_ttl_seconds=pending_drain_schedule_ttl_seconds,
    )


def enqueue_pending_agent(
    agent_id: Union[str, UUID],
    *,
    ttl: int = _DEFAULT_PENDING_SET_TTL_SECONDS,
    client=None,
) -> bool:
    """Add an agent to the pending processing set. Returns True if newly added."""
    try:
        redis_client = client or get_redis_client()
        added = redis_client.sadd(_PENDING_SET_KEY, str(agent_id))
        if ttl > 0:
            redis_client.expire(_PENDING_SET_KEY, ttl)
        return bool(added)
    except Exception:
        logger.exception("Failed to enqueue pending processing for agent %s", agent_id)
        return False


def is_agent_pending(agent_id: Union[str, UUID], client=None) -> bool:
    """Check whether an agent is in the pending processing set."""
    try:
        redis_client = client or get_redis_client()
        return bool(redis_client.sismember(_PENDING_SET_KEY, str(agent_id)))
    except Exception:
        logger.exception("Failed to check pending processing for agent %s", agent_id)
        return False


def pop_pending_agents(
    *,
    limit: int,
    client=None,
) -> list[str]:
    """Pop up to limit agent IDs from the pending processing set."""
    if limit <= 0:
        return []
    try:
        redis_client = client or get_redis_client()
        result = redis_client.spop(_PENDING_SET_KEY, count=limit)
    except Exception:
        logger.exception("Failed to pop pending agents")
        return []

    if not result:
        return []
    if isinstance(result, list):
        items = result
    else:
        items = [result]
    normalized: list[str] = []
    for item in items:
        if isinstance(item, (bytes, bytearray)):
            normalized.append(item.decode("utf-8", "ignore"))
        else:
            normalized.append(str(item))
    return normalized


def count_pending_agents(client=None) -> int:
    """Return the number of pending agents."""
    try:
        redis_client = client or get_redis_client()
        return int(redis_client.scard(_PENDING_SET_KEY))
    except Exception:
        logger.exception("Failed to count pending agents")
        return 0


def claim_pending_drain_slot(
    *,
    ttl: int = _DEFAULT_PENDING_DRAIN_SCHEDULE_TTL_SECONDS,
    client=None,
) -> bool:
    """Claim the pending-drain schedule slot. Returns True if claimed."""
    try:
        redis_client = client or get_redis_client()
        claimed = redis_client.set(
            _PENDING_DRAIN_SCHEDULE_KEY,
            "1",
            ex=ttl,
            nx=True,
        )
        return bool(claimed)
    except Exception:
        logger.exception("Failed to claim pending drain slot")
        return False


def clear_pending_drain_slot(client=None) -> None:
    """Clear the pending-drain schedule slot."""
    try:
        redis_client = client or get_redis_client()
        redis_client.delete(_PENDING_DRAIN_SCHEDULE_KEY)
    except Exception:
        logger.exception("Failed to clear pending drain slot")
