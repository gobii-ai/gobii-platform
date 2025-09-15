"""Celery tasks for agent lifecycle cleanup."""
from __future__ import annotations

import logging
from typing import Optional, Dict

from celery import shared_task

from config.redis_client import get_redis_client
from api.services.agent_lifecycle import AgentCleanupRegistry
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource


logger = logging.getLogger(__name__)


def _idempotency_key(agent_id: str, reason: str) -> str:
    return f"pa:cleanup:done:{agent_id}:{reason}"


@shared_task(name="api.tasks.agent_shutdown_cleanup")
def agent_shutdown_cleanup_task(agent_id: str, reason: str, meta: Optional[Dict] = None) -> None:  # noqa: D401, ANN001
    """Execute registered cleanup handlers for an agent shutdown event.

    Handlers MUST be idempotent. We also apply a short‑lived idempotency key in
    Redis to reduce duplicate work across rapid successive triggers.
    """
    try:
        redis = get_redis_client()
    except Exception:
        redis = None

    key = _idempotency_key(agent_id, reason)

    # Best‑effort idempotency guard
    if redis is not None:
        try:
            already = redis.set(name=key, value="1", nx=True, ex=60 * 10)  # 10 minutes
            if not already:
                logger.info("Skipping duplicate cleanup for agent=%s reason=%s", agent_id, reason)
                return
        except Exception:
            logger.exception("Idempotency guard failed for agent=%s reason=%s", agent_id, reason)

    handlers = AgentCleanupRegistry.get_for_reason(reason)
    logger.info("Running %d cleanup handler(s) for agent=%s reason=%s", len(handlers), agent_id, reason)

    for h in handlers:
        try:
            h(agent_id, reason, meta or {})
        except Exception:
            # Handlers should self‑contain their errors, but double‑guard here.
            logger.exception("Cleanup handler %s failed for agent=%s", getattr(h, "__name__", str(h)), agent_id)

    # Analytics breadcrumb (best effort): include reason + meta (no DB lookups)
    try:
        user_id = None
        if meta and isinstance(meta, dict):
            user_id = meta.get("user_id") or None
        props = {"agent_id": str(agent_id), "reason": str(reason)}
        if meta:
            # include a small, safe subset
            for k in list(meta.keys())[:5]:
                v = meta.get(k)
                try:
                    props[f"meta.{k}"] = str(v)[:200]
                except Exception:
                    props[f"meta.{k}"] = "<unserializable>"

        if user_id:
            Analytics.track_event(user_id=user_id, event=AnalyticsEvent.PERSISTENT_AGENT_SHUTDOWN, source=AnalyticsSource.NA, properties=props)
        else:
            # fallback anonymous
            Analytics.track_event_anonymous(anonymous_id=str(agent_id), event=AnalyticsEvent.PERSISTENT_AGENT_SHUTDOWN, source=AnalyticsSource.NA, properties=props)
    except Exception:
        logger.exception("Failed to emit analytics for agent shutdown breadcrumb agent=%s", agent_id)
