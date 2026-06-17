"""Shared Redis-backed inbound message debounce helpers."""

import logging
import math
import time
from typing import Callable

import redis
from django.conf import settings

from config.redis_client import get_redis_client

logger = logging.getLogger(__name__)


DelayedProcessCallback = Callable[..., None]
TaskFactory = Callable[[], object]
PipelineCallback = Callable[[object, int], None]
RedisCallback = Callable[[object], None]


def debounce_ttl(delay_seconds: int) -> int:
    return max(60, delay_seconds * 6)


def coerce_redis_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def schedule_inbound_processing(
    agent_id: str,
    *,
    debounce_seconds: int,
    deadline_key: str,
    scheduled_key: str,
    process_callback: DelayedProcessCallback,
    task_factory: TaskFactory,
    log_label: str,
    extra_pipeline_writes: PipelineCallback | None = None,
    delete_keys: tuple[str, ...] = (),
) -> dict[str, object]:
    normalized_agent_id = str(agent_id)
    if debounce_seconds <= 0:
        process_callback(normalized_agent_id)
        return {"debounced": False, "debounce_seconds": 0, "scheduled": True}

    deadline = time.time() + debounce_seconds
    ttl = debounce_ttl(debounce_seconds)
    try:
        redis_client = get_redis_client()
        pipeline = redis_client.pipeline(transaction=True)
        pipeline.set(deadline_key, f"{deadline:.6f}", ex=ttl)
        pipeline.set(scheduled_key, "1", ex=ttl, nx=True)
        if extra_pipeline_writes:
            extra_pipeline_writes(pipeline, ttl)
        results = pipeline.execute()
        scheduled = bool(results[1])
    except redis.exceptions.RedisError:
        logger.exception("Failed scheduling %s inbound debounce for agent %s.", log_label, normalized_agent_id)
        process_callback(normalized_agent_id, countdown=debounce_seconds)
        return {"debounced": False, "debounce_seconds": debounce_seconds, "scheduled": True, "fallback": True}

    if scheduled:
        if settings.CELERY_TASK_ALWAYS_EAGER:
            redis_client.delete(deadline_key, scheduled_key, *delete_keys)
            process_callback(normalized_agent_id)
            return {"debounced": False, "debounce_seconds": debounce_seconds, "scheduled": True, "eager": True}
        task_factory().apply_async(args=[normalized_agent_id], countdown=debounce_seconds)

    return {"debounced": True, "debounce_seconds": debounce_seconds, "scheduled": scheduled}


def process_inbound_debounce(
    agent_id: str,
    *,
    debounce_seconds: int,
    deadline_key: str,
    scheduled_key: str,
    process_callback: DelayedProcessCallback,
    task_factory: TaskFactory,
    log_label: str,
    before_deadline_check: RedisCallback | None = None,
    extra_expire_keys: tuple[str, ...] = (),
    delete_keys: tuple[str, ...] = (),
) -> None:
    normalized_agent_id = str(agent_id)
    if debounce_seconds <= 0:
        process_callback(normalized_agent_id)
        return

    now = time.time()
    try:
        redis_client = get_redis_client()
        if before_deadline_check:
            before_deadline_check(redis_client)
        deadline = coerce_redis_float(redis_client.get(deadline_key))
        if deadline is not None and deadline > now:
            if settings.CELERY_TASK_ALWAYS_EAGER:
                redis_client.delete(deadline_key, scheduled_key, *delete_keys)
                process_callback(normalized_agent_id)
                return

            countdown = max(1, math.ceil(deadline - now))
            ttl = debounce_ttl(max(debounce_seconds, countdown))
            redis_client.expire(deadline_key, ttl)
            redis_client.expire(scheduled_key, ttl)
            for key in extra_expire_keys:
                redis_client.expire(key, ttl)
            task_factory().apply_async(args=[normalized_agent_id], countdown=countdown)
            return

        redis_client.delete(deadline_key, scheduled_key, *delete_keys)
    except redis.exceptions.RedisError:
        logger.exception("Failed processing %s inbound debounce for agent %s.", log_label, normalized_agent_id)

    process_callback(normalized_agent_id)
