"""Best-effort scheduling for asynchronous persistent-agent history compaction."""

import logging
import uuid
from typing import Any

from celery.exceptions import CeleryError
from django.conf import settings
from django.db import DatabaseError
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import RedisError

from config.redis_client import get_redis_client

from ...models import (
    PersistentAgent,
    PersistentAgentCommsSnapshot,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentStepSnapshot,
)

logger = logging.getLogger(__name__)


def _lease_key(agent_id: Any) -> str:
    return f"agent-history-compaction:{agent_id}"


def _has_more_than(queryset, limit: int) -> bool:
    return queryset.values_list("pk", flat=True)[limit : limit + 1].exists()


def history_compaction_needed(agent: PersistentAgent) -> bool:
    """Return whether either uncompacted history stream exceeds its limit."""
    step_cutoff = (
        PersistentAgentStepSnapshot.objects.filter(agent=agent)
        .order_by("-snapshot_until")
        .values_list("snapshot_until", flat=True)
        .first()
    ) or agent.created_at
    step_qs = PersistentAgentStep.objects.filter(
        agent=agent,
        created_at__gt=step_cutoff,
    )
    if _has_more_than(step_qs, settings.PA_RAW_STEP_LIMIT):
        return True

    comms_cutoff = (
        PersistentAgentCommsSnapshot.objects.filter(agent=agent)
        .order_by("-snapshot_until")
        .values_list("snapshot_until", flat=True)
        .first()
    ) or agent.created_at
    comms_qs = PersistentAgentMessage.objects.filter(
        owner_agent=agent,
        timestamp__gt=comms_cutoff,
    )
    return _has_more_than(comms_qs, settings.PA_RAW_MSG_LIMIT)


def refresh_history_compaction_lease(agent_id: Any, lease_token: str) -> bool | None:
    """Refresh an owned lease, returning None when Redis is unavailable."""
    if not lease_token:
        return None
    try:
        redis_client = get_redis_client()
        refresh = redis_client.register_script(
            """
            if redis.call("get", KEYS[1]) ~= ARGV[1] then
                return 0
            end
            return redis.call("pexpire", KEYS[1], ARGV[2])
            """
        )
        return bool(
            refresh(
                keys=[_lease_key(agent_id)],
                args=[lease_token, settings.PA_HISTORY_COMPACTION_LEASE_SECONDS * 1000],
            )
        )
    except RedisError:
        logger.warning(
            "Unable to refresh history compaction lease for agent %s",
            agent_id,
            exc_info=True,
        )
        return None


def release_history_compaction_lease(agent_id: Any, lease_token: str) -> None:
    """Release a lease only when it is still owned by ``lease_token``."""
    if not lease_token:
        return
    try:
        redis_client = get_redis_client()
        release = redis_client.register_script(
            """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            end
            return 0
            """
        )
        release(keys=[_lease_key(agent_id)], args=[lease_token])
    except RedisError:
        logger.warning(
            "Unable to release history compaction lease for agent %s",
            agent_id,
            exc_info=True,
        )


def enqueue_history_compaction(
    *,
    agent: PersistentAgent,
    routing_profile: Any = None,
    eval_run_id: str | None = None,
) -> bool:
    """Publish one coalesced background compaction job when history is due."""
    try:
        if not history_compaction_needed(agent):
            return False
    except DatabaseError:
        logger.warning(
            "Unable to check history compaction threshold for agent %s",
            agent.id,
            exc_info=True,
        )
        return False

    lease_token = uuid.uuid4().hex
    try:
        acquired = get_redis_client().set(
            _lease_key(agent.id),
            lease_token,
            nx=True,
            ex=settings.PA_HISTORY_COMPACTION_LEASE_SECONDS,
        )
        if not acquired:
            return False
    except RedisError:
        # The task remains safe and idempotent without coalescing, while the
        # broker may still be independently available.
        logger.warning(
            "Redis unavailable while coalescing history compaction for agent %s",
            agent.id,
            exc_info=True,
        )
        lease_token = ""

    routing_profile_id = str(routing_profile.id) if routing_profile is not None else None
    try:
        from ..tasks.history_compaction import compact_agent_history_task

        compact_agent_history_task.apply_async(
            args=[str(agent.id), lease_token, routing_profile_id, eval_run_id],
            queue="celery",
        )
    except (CeleryError, KombuOperationalError):
        logger.warning(
            "Unable to enqueue history compaction for agent %s",
            agent.id,
            exc_info=True,
        )
        release_history_compaction_lease(agent.id, lease_token)
        return False
    return True
