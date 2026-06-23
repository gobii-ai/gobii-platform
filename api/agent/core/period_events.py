"""Best-effort per-agent event dedupe markers."""

import logging
import math
from datetime import timedelta
from typing import Any

from django.utils import timezone

from config.redis_client import get_redis_client

logger = logging.getLogger(__name__)

DAILY_SOFT_LIMIT_EXCEEDED_EVENT = "daily_soft_limit_exceeded"
DAILY_HARD_LIMIT_EXCEEDED_EVENT = "daily_hard_limit_exceeded"
DAILY_HARD_LIMIT_BLOCKED_EVENT = "daily_hard_limit_blocked"
BURN_RATE_RUNTIME_TIER_STEP_DOWN_EVENT = "burn_rate_runtime_tier_step_down"


def _daily_period_parts(now=None) -> tuple[str, int]:
    local_now = timezone.localtime(now or timezone.now())
    period_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_reset = period_start + timedelta(days=1)
    ttl_seconds = max(1, int(math.ceil((next_reset - local_now).total_seconds())))
    return period_start.date().isoformat(), ttl_seconds


def should_emit_daily_agent_event(agent_id: Any, event_key: str) -> bool:
    period_key, ttl_seconds = _daily_period_parts()
    cache_key = f"agent-period-event:{agent_id}:{event_key}:{period_key}"
    try:
        return bool(get_redis_client().set(cache_key, "1", ex=ttl_seconds, nx=True))
    except Exception:
        # Redis availability must not suppress important limit notifications.
        logger.warning(
            "Failed to write agent period event marker %s for agent %s; emitting fail-open.",
            event_key,
            agent_id,
            exc_info=True,
        )
        return True
