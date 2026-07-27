import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Union
from uuid import UUID

from pottery import Redlock

from config.redis_client import get_redis_client

logger = logging.getLogger(__name__)

_QUEUED_KEY_TEMPLATE = "agent-event-processing:queued:{agent_id}"
_DEFAULT_QUEUE_TTL_SECONDS = 3600
_HEARTBEAT_KEY_TEMPLATE = "agent-event-processing:heartbeat:{agent_id}"
_DEFAULT_HEARTBEAT_TTL_SECONDS = 600
_STOP_REQUESTED_KEY_TEMPLATE = "agent-event-processing:stop-requested:{agent_id}"
_DEFAULT_STOP_REQUESTED_TTL_SECONDS = 3600
_HUMAN_INBOUND_GENERATION_KEY_TEMPLATE = "agent-event-processing:human-inbound-generation:{agent_id}"
_HUMAN_INBOUND_CONSUMED_GENERATION_KEY_TEMPLATE = "agent-event-processing:human-inbound-consumed-generation:{agent_id}"
_QUEUED_AGENT_SET_KEY = "agent-event-processing:index:queued"
_HEARTBEAT_AGENT_SET_KEY = "agent-event-processing:index:heartbeat"
_LOCKED_AGENT_SET_KEY = "agent-event-processing:index:locked"
_PENDING_SET_KEY = "agent-event-processing:pending"
_PENDING_GENERATION_HASH_KEY = "agent-event-processing:pending:generation"
_PENDING_QUEUE_HASH_KEY = "agent-event-processing:pending:queue"
_PENDING_GENERIC_SET_KEY = "agent-event-processing:pending:generic"
_PENDING_DRAIN_SCHEDULE_KEY = "agent-event-processing:pending:drain:schedule"
_DEFAULT_PENDING_SET_TTL_SECONDS = 3600
_DEFAULT_PENDING_DRAIN_SCHEDULE_TTL_SECONDS = 60
_INTERACTIVE_PROCESSING_QUEUE = "agent_interactive"

_ENQUEUE_PENDING_SCRIPT = """
-- gobii_pending_enqueue_v1
local added = redis.call('SADD', KEYS[1], ARGV[1])
local generation = tonumber(ARGV[2]) or 0
local generation_advanced = false
if generation > 0 then
    local current_generation = tonumber(redis.call('HGET', KEYS[2], ARGV[1]) or '0')
    if generation > current_generation then
        redis.call('HSET', KEYS[2], ARGV[1], generation)
        generation_advanced = true
    end
end
if generation == 0 or ARGV[6] == '1' then
    redis.call('SADD', KEYS[4], ARGV[1])
end
local current_queue = redis.call('HGET', KEYS[3], ARGV[1])
if ARGV[3] ~= '' and current_queue ~= ARGV[5] and (
    not current_queue
    or generation_advanced
    or ARGV[3] == ARGV[5]
) then
    redis.call('HSET', KEYS[3], ARGV[1], ARGV[3])
end
local ttl = tonumber(ARGV[4]) or 0
if ttl > 0 then
    redis.call('EXPIRE', KEYS[1], ttl)
    redis.call('EXPIRE', KEYS[2], ttl)
    redis.call('EXPIRE', KEYS[3], ttl)
    redis.call('EXPIRE', KEYS[4], ttl)
end
return added
"""

_CLAIM_PENDING_SCRIPT = """
-- gobii_pending_claim_v1
if redis.call('SREM', KEYS[1], ARGV[1]) == 0 then
    return {}
end
local generation = redis.call('HGET', KEYS[2], ARGV[1]) or ''
local queue = redis.call('HGET', KEYS[3], ARGV[1]) or ''
local generic = redis.call('SISMEMBER', KEYS[4], ARGV[1])
redis.call('HDEL', KEYS[2], ARGV[1])
redis.call('HDEL', KEYS[3], ARGV[1])
redis.call('SREM', KEYS[4], ARGV[1])
return {ARGV[1], generation, queue, generic}
"""

_CLAIM_PENDING_MANY_SCRIPT = """
-- gobii_pending_claim_many_v1
local agent_ids = redis.call('SPOP', KEYS[1], tonumber(ARGV[1]))
local result = {}
for _, agent_id in ipairs(agent_ids) do
    local generation = redis.call('HGET', KEYS[2], agent_id) or ''
    local queue = redis.call('HGET', KEYS[3], agent_id) or ''
    local generic = redis.call('SISMEMBER', KEYS[4], agent_id)
    redis.call('HDEL', KEYS[2], agent_id)
    redis.call('HDEL', KEYS[3], agent_id)
    redis.call('SREM', KEYS[4], agent_id)
    table.insert(result, agent_id)
    table.insert(result, generation)
    table.insert(result, queue)
    table.insert(result, generic)
end
return result
"""


@dataclass(frozen=True)
class PendingDrainSettings:
    pending_set_ttl_seconds: int
    pending_drain_delay_seconds: int
    pending_drain_limit: int
    pending_drain_schedule_ttl_seconds: int


@dataclass(frozen=True)
class PendingAgentWork:
    agent_id: str
    inbound_generation: int | None = None
    queue: str | None = None
    has_generic_work: bool = False


def _queued_key(agent_id: Union[str, UUID]) -> str:
    return _QUEUED_KEY_TEMPLATE.format(agent_id=agent_id)


def _heartbeat_key(agent_id: Union[str, UUID]) -> str:
    return _HEARTBEAT_KEY_TEMPLATE.format(agent_id=agent_id)


def _stop_requested_key(agent_id: Union[str, UUID]) -> str:
    return _STOP_REQUESTED_KEY_TEMPLATE.format(agent_id=agent_id)


def _human_inbound_generation_key(agent_id: Union[str, UUID]) -> str:
    return _HUMAN_INBOUND_GENERATION_KEY_TEMPLATE.format(agent_id=agent_id)


def _human_inbound_consumed_generation_key(agent_id: Union[str, UUID]) -> str:
    return _HUMAN_INBOUND_CONSUMED_GENERATION_KEY_TEMPLATE.format(agent_id=agent_id)


def processing_lock_storage_keys(agent_id: Union[str, UUID]) -> tuple[str, str]:
    normalized_agent_id = str(agent_id)
    prefix = getattr(Redlock, "_KEY_PREFIX", "redlock")
    return (
        f"{prefix}:agent-event-processing:{normalized_agent_id}",
        f"agent-event-processing:{normalized_agent_id}",
    )


def _smembers_as_strings(redis_client, key: str) -> list[str]:
    values = getattr(redis_client, "smembers", lambda _key: set())(key)
    normalized: list[str] = []
    for value in values:
        if isinstance(value, (bytes, bytearray)):
            normalized.append(value.decode("utf-8", "ignore"))
        else:
            normalized.append(str(value))
    return normalized


def get_processing_queued_agent_ids(*, client=None) -> list[str]:
    try:
        redis_client = client or get_redis_client()
        return _smembers_as_strings(redis_client, _QUEUED_AGENT_SET_KEY)
    except Exception:
        logger.exception("Failed to list queued processing agents")
        return []


def get_processing_heartbeat_agent_ids(*, client=None) -> list[str]:
    try:
        redis_client = client or get_redis_client()
        return _smembers_as_strings(redis_client, _HEARTBEAT_AGENT_SET_KEY)
    except Exception:
        logger.exception("Failed to list heartbeat processing agents")
        return []


def get_processing_locked_agent_ids(*, client=None) -> list[str]:
    try:
        redis_client = client or get_redis_client()
        return _smembers_as_strings(redis_client, _LOCKED_AGENT_SET_KEY)
    except Exception:
        logger.exception("Failed to list locked processing agents")
        return []


def set_processing_queued_flag(
    agent_id: Union[str, UUID],
    *,
    ttl: int = _DEFAULT_QUEUE_TTL_SECONDS,
    client=None,
) -> None:
    """Mark the agent as having queued processing work."""
    try:
        redis_client = client or get_redis_client()
        key = _queued_key(agent_id)
        pipeline = getattr(redis_client, "pipeline", None)
        if callable(pipeline):
            pipe = pipeline()
            pipe.set(key, "1")
            if ttl > 0:
                pipe.expire(key, ttl)
            pipe.sadd(_QUEUED_AGENT_SET_KEY, str(agent_id))
            pipe.execute()
            return

        redis_client.set(key, "1")
        if ttl > 0:
            redis_client.expire(key, ttl)
        redis_client.sadd(_QUEUED_AGENT_SET_KEY, str(agent_id))
    except Exception:
        logger.exception("Failed to set processing queued flag for agent %s", agent_id)


def clear_processing_queued_flag(agent_id: Union[str, UUID], *, client=None) -> None:
    """Clear the queued processing flag for the agent."""
    try:
        redis_client = client or get_redis_client()
        pipeline = getattr(redis_client, "pipeline", None)
        if callable(pipeline):
            pipe = pipeline()
            pipe.delete(_queued_key(agent_id))
            pipe.srem(_QUEUED_AGENT_SET_KEY, str(agent_id))
            pipe.execute()
            return

        redis_client.delete(_queued_key(agent_id))
        redis_client.srem(_QUEUED_AGENT_SET_KEY, str(agent_id))
    except Exception:
        logger.exception("Failed to clear processing queued flag for agent %s", agent_id)


def _coerce_generation(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def bump_human_inbound_generation(
    agent_id: Union[str, UUID],
    *,
    ttl: int = _DEFAULT_QUEUE_TTL_SECONDS,
    client=None,
) -> int:
    """Increment and return the generation for human-authored inbound messages."""
    try:
        redis_client = client or get_redis_client()
        key = _human_inbound_generation_key(agent_id)
        consumed_key = _human_inbound_consumed_generation_key(agent_id)
        generation = int(redis_client.incr(key))
        consumed = get_consumed_human_inbound_generation(agent_id, client=redis_client)
        if generation <= consumed:
            generation = int(redis_client.incr(key, amount=consumed - generation + 1))
        if ttl > 0:
            redis_client.expire(key, ttl)
            redis_client.expire(consumed_key, ttl)
        return generation
    except Exception:
        logger.exception("Failed to bump human inbound generation for agent %s", agent_id)
        return 0


def get_human_inbound_generation(agent_id: Union[str, UUID], *, client=None) -> int:
    """Return the latest human-inbound generation for the agent."""
    try:
        redis_client = client or get_redis_client()
        return _coerce_generation(redis_client.get(_human_inbound_generation_key(agent_id)))
    except Exception:
        logger.exception("Failed to read human inbound generation for agent %s", agent_id)
        return 0


def get_consumed_human_inbound_generation(agent_id: Union[str, UUID], *, client=None) -> int:
    """Return the latest human-inbound generation accepted by the orchestrator."""
    try:
        redis_client = client or get_redis_client()
        return _coerce_generation(redis_client.get(_human_inbound_consumed_generation_key(agent_id)))
    except Exception:
        logger.exception("Failed to read consumed human inbound generation for agent %s", agent_id)
        return 0


def mark_human_inbound_generation_consumed(
    agent_id: Union[str, UUID],
    generation: int | str | None,
    *,
    ttl: int = _DEFAULT_QUEUE_TTL_SECONDS,
    client=None,
) -> int:
    """Record that the orchestrator accepted a prompt at this generation."""
    parsed_generation = _coerce_generation(generation)
    if parsed_generation <= 0:
        return 0

    try:
        redis_client = client or get_redis_client()
        key = _human_inbound_consumed_generation_key(agent_id)
        current_key = _human_inbound_generation_key(agent_id)
        previous = _coerce_generation(redis_client.get(key))
        next_generation = max(previous, parsed_generation)
        redis_client.set(key, str(next_generation))
        if ttl > 0:
            redis_client.expire(key, ttl)
            redis_client.expire(current_key, ttl)
        return next_generation
    except Exception:
        logger.exception("Failed to mark human inbound generation consumed for agent %s", agent_id)
        return parsed_generation


def is_human_inbound_generation_consumed(
    agent_id: Union[str, UUID],
    generation: int | str | None,
    *,
    client=None,
) -> bool:
    """Return True when a queued human-inbound task is redundant."""
    parsed_generation = _coerce_generation(generation)
    if parsed_generation <= 0:
        return False

    current = get_human_inbound_generation(agent_id, client=client)
    consumed = get_consumed_human_inbound_generation(agent_id, client=client)
    return consumed >= parsed_generation and current <= consumed


def clear_processing_work_state(agent_id: Union[str, UUID], client=None) -> None:
    """Clear queued and pending processing state for a single agent."""
    redis_client = client
    if redis_client is None:
        try:
            redis_client = get_redis_client()
        except Exception:
            logger.exception("Failed to acquire Redis client while clearing processing state for agent %s", agent_id)
            return

    try:
        pipeline = getattr(redis_client, "pipeline", None)
        if callable(pipeline):
            pipe = pipeline()
            pipe.delete(_queued_key(agent_id))
            pipe.srem(_QUEUED_AGENT_SET_KEY, str(agent_id))
            pipe.srem(_PENDING_SET_KEY, str(agent_id))
            pipe.hdel(_PENDING_GENERATION_HASH_KEY, str(agent_id))
            pipe.hdel(_PENDING_QUEUE_HASH_KEY, str(agent_id))
            pipe.srem(_PENDING_GENERIC_SET_KEY, str(agent_id))
            pipe.srem(_LOCKED_AGENT_SET_KEY, str(agent_id))
            pipe.srem(_HEARTBEAT_AGENT_SET_KEY, str(agent_id))
            pipe.execute()
            return

        redis_client.delete(_queued_key(agent_id))
        redis_client.srem(_QUEUED_AGENT_SET_KEY, str(agent_id))
    except Exception:
        logger.exception("Failed to clear queued processing state for agent %s", agent_id)

    try:
        redis_client.srem(_PENDING_SET_KEY, str(agent_id))
        redis_client.hdel(_PENDING_GENERATION_HASH_KEY, str(agent_id))
        redis_client.hdel(_PENDING_QUEUE_HASH_KEY, str(agent_id))
        redis_client.srem(_PENDING_GENERIC_SET_KEY, str(agent_id))
        redis_client.srem(_LOCKED_AGENT_SET_KEY, str(agent_id))
        redis_client.srem(_HEARTBEAT_AGENT_SET_KEY, str(agent_id))
    except Exception:
        logger.exception("Failed to clear pending processing state for agent %s", agent_id)


def set_processing_stop_requested(
    agent_id: Union[str, UUID],
    *,
    ttl: int = _DEFAULT_STOP_REQUESTED_TTL_SECONDS,
    client=None,
) -> None:
    """Record a graceful stop request for the agent."""
    try:
        redis_client = client or get_redis_client()
        if ttl > 0:
            redis_client.set(_stop_requested_key(agent_id), "1", ex=ttl)
        else:
            redis_client.set(_stop_requested_key(agent_id), "1")
    except Exception:
        logger.exception("Failed to set stop request for agent %s", agent_id)


def is_processing_stop_requested(agent_id: Union[str, UUID], client=None) -> bool:
    """Check whether a graceful stop has been requested for the agent."""
    try:
        redis_client = client or get_redis_client()
        return bool(redis_client.exists(_stop_requested_key(agent_id)))
    except Exception:
        logger.exception("Failed to check stop request for agent %s", agent_id)
        return False


def clear_processing_stop_requested(agent_id: Union[str, UUID], client=None) -> None:
    """Clear the graceful stop request for the agent."""
    try:
        redis_client = client or get_redis_client()
        redis_client.delete(_stop_requested_key(agent_id))
    except Exception:
        logger.exception("Failed to clear stop request for agent %s", agent_id)


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
    worker_pid: int | None = None,
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
        "worker_pid": worker_pid,
        "stage": stage,
        "started_at": started_at if started_at is not None else now,
        "last_seen": now,
    }
    try:
        redis_client = client or get_redis_client()
        pipeline = getattr(redis_client, "pipeline", None)
        if callable(pipeline):
            pipe = pipeline()
            pipe.set(_heartbeat_key(agent_id), json.dumps(payload), ex=ttl)
            pipe.sadd(_HEARTBEAT_AGENT_SET_KEY, str(agent_id))
            pipe.execute()
            return

        redis_client.set(_heartbeat_key(agent_id), json.dumps(payload), ex=ttl)
        redis_client.sadd(_HEARTBEAT_AGENT_SET_KEY, str(agent_id))
    except Exception:
        logger.exception("Failed to set processing heartbeat for agent %s", agent_id)


def clear_processing_heartbeat(agent_id: Union[str, UUID], client=None) -> None:
    """Clear the processing heartbeat for the agent."""
    try:
        redis_client = client or get_redis_client()
        pipeline = getattr(redis_client, "pipeline", None)
        if callable(pipeline):
            pipe = pipeline()
            pipe.delete(_heartbeat_key(agent_id))
            pipe.srem(_HEARTBEAT_AGENT_SET_KEY, str(agent_id))
            pipe.execute()
            return

        redis_client.delete(_heartbeat_key(agent_id))
        redis_client.srem(_HEARTBEAT_AGENT_SET_KEY, str(agent_id))
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


def mark_processing_lock_active(agent_id: Union[str, UUID], *, client=None) -> None:
    try:
        redis_client = client or get_redis_client()
        redis_client.sadd(_LOCKED_AGENT_SET_KEY, str(agent_id))
    except Exception:
        logger.exception("Failed to mark processing lock active for agent %s", agent_id)


def clear_processing_lock_active(agent_id: Union[str, UUID], *, client=None) -> None:
    try:
        redis_client = client or get_redis_client()
        redis_client.srem(_LOCKED_AGENT_SET_KEY, str(agent_id))
    except Exception:
        logger.exception("Failed to clear processing lock active for agent %s", agent_id)


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
    inbound_generation: int | str | None = None,
    queue: str | None = None,
    has_generic_work: bool = False,
    ttl: int = _DEFAULT_PENDING_SET_TTL_SECONDS,
    client=None,
) -> bool:
    """Coalesce pending work for an agent. Returns True if newly added."""
    try:
        redis_client = client or get_redis_client()
        result = redis_client.eval(
            _ENQUEUE_PENDING_SCRIPT,
            4,
            _PENDING_SET_KEY,
            _PENDING_GENERATION_HASH_KEY,
            _PENDING_QUEUE_HASH_KEY,
            _PENDING_GENERIC_SET_KEY,
            str(agent_id),
            _coerce_generation(inbound_generation),
            str(queue or ""),
            max(0, int(ttl)),
            _INTERACTIVE_PROCESSING_QUEUE,
            "1" if has_generic_work else "0",
        )
        return bool(result)
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


def _pending_work_from_script_result(result: Any) -> PendingAgentWork | None:
    if not result or len(result) < 4:
        return None
    agent_id, generation, queue, generic = result[:4]
    if isinstance(agent_id, (bytes, bytearray)):
        agent_id = agent_id.decode("utf-8", "ignore")
    if isinstance(queue, (bytes, bytearray)):
        queue = queue.decode("utf-8", "ignore")
    parsed_generation = _coerce_generation(generation)
    return PendingAgentWork(
        agent_id=str(agent_id),
        inbound_generation=parsed_generation or None,
        queue=str(queue) or None,
        has_generic_work=bool(int(generic or 0)),
    )


def claim_pending_agent(
    agent_id: Union[str, UUID],
    *,
    client=None,
) -> PendingAgentWork | None:
    """Atomically claim one agent's coalesced pending work."""
    try:
        redis_client = client or get_redis_client()
        result = redis_client.eval(
            _CLAIM_PENDING_SCRIPT,
            4,
            _PENDING_SET_KEY,
            _PENDING_GENERATION_HASH_KEY,
            _PENDING_QUEUE_HASH_KEY,
            _PENDING_GENERIC_SET_KEY,
            str(agent_id),
        )
        return _pending_work_from_script_result(result)
    except Exception:
        logger.exception("Failed to claim pending processing for agent %s", agent_id)
        return None


def remove_pending_agent(agent_id: Union[str, UUID], client=None) -> None:
    """Remove and discard one agent's pending work."""
    claim_pending_agent(agent_id, client=client)


def claim_pending_agents(
    *,
    limit: int,
    client=None,
) -> list[PendingAgentWork]:
    """Atomically claim up to ``limit`` coalesced pending records."""
    if limit <= 0:
        return []
    try:
        redis_client = client or get_redis_client()
        result = redis_client.eval(
            _CLAIM_PENDING_MANY_SCRIPT,
            4,
            _PENDING_SET_KEY,
            _PENDING_GENERATION_HASH_KEY,
            _PENDING_QUEUE_HASH_KEY,
            _PENDING_GENERIC_SET_KEY,
            limit,
        )
    except Exception:
        logger.exception("Failed to claim pending agents")
        return []

    claimed: list[PendingAgentWork] = []
    for index in range(0, len(result or []), 4):
        pending_work = _pending_work_from_script_result(result[index:index + 4])
        if pending_work is not None:
            claimed.append(pending_work)
    return claimed


def pop_pending_agents(
    *,
    limit: int,
    client=None,
) -> list[str]:
    """Pop up to limit agent IDs from the pending processing set."""
    return [
        pending_work.agent_id
        for pending_work in claim_pending_agents(limit=limit, client=client)
    ]


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
