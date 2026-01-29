"""
Celery task for processing persistent agent events.

This module provides the Celery task wrapper for agent event processing,
handling orchestration and retry semantics while delegating the core
business logic to the event processing module.
"""

import logging
import os
import time
from uuid import UUID
from typing import Any, Dict, Optional, Sequence

from celery import Task, shared_task
from opentelemetry import baggage, trace
import redis
from waffle import switch_is_active
from django.conf import settings
from django.db import transaction
from django.core.exceptions import ValidationError

from config.redis_client import get_redis_client
from ..core.event_processing import process_agent_events, _lock_storage_keys
from ...services.referral_service import ReferralService
from ..core.processing_flags import (
    claim_pending_drain_slot,
    clear_pending_drain_slot,
    clear_processing_queued_flag,
    count_pending_agents,
    get_processing_heartbeat,
    get_pending_drain_settings,
    is_agent_pending,
    pop_pending_agents,
    set_processing_queued_flag,
)

tracer = trace.get_tracer("gobii.utils")
logger = logging.getLogger(__name__)


def _is_task_quota_error(exc: ValidationError) -> bool:
    messages: list[str] = []

    try:
        if isinstance(getattr(exc, "message_dict", None), dict):
            for value in exc.message_dict.values():
                if isinstance(value, (list, tuple)):
                    messages.extend(str(item) for item in value)
                else:
                    messages.append(str(value))
    except Exception:
        # Swallow parsing issues; we'll fall back to generic matching
        pass

    try:
        messages.extend(str(m) for m in getattr(exc, "messages", []))
    except Exception:
        pass

    combined = " ".join(messages).lower()
    return "task quota exceeded" in combined or "task credits" in combined


def _extract_agent_id(args: Sequence[Any] | None, kwargs: dict[str, Any] | None) -> str | None:
    if args and len(args) > 0 and args[0]:
        return str(args[0])
    if kwargs and kwargs.get("persistent_agent_id"):
        return str(kwargs["persistent_agent_id"])
    return None


def _normalize_agent_id(agent_id: Any) -> str | None:
    if isinstance(agent_id, UUID):
        return str(agent_id)
    try:
        return str(UUID(str(agent_id)))
    except (TypeError, ValueError, AttributeError):
        return None


def _broadcast_processing_state(agent_id: str) -> None:
    try:
        from api.models import PersistentAgent

        agent = PersistentAgent.objects.filter(id=agent_id).first()
        if not agent:
            return
        from console.agent_chat.signals import _broadcast_processing

        _broadcast_processing(agent)
    except Exception:
        logger.debug("Failed to broadcast processing snapshot for agent %s", agent_id, exc_info=True)


class ProcessAgentEventsTaskBase(Task):
    """Task base that records queued processing state before enqueueing."""

    def apply_async(self, args=None, kwargs=None, **options):
        agent_id = _extract_agent_id(args, kwargs)
        if agent_id:
            set_processing_queued_flag(agent_id)
            _broadcast_processing_state(agent_id)
        return super().apply_async(args=args, kwargs=kwargs, **options)


@shared_task(
    bind=True,
    base=ProcessAgentEventsTaskBase,
    name="api.agent.tasks.process_agent_events",
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_agent_events_task(
    self,
    persistent_agent_id: str,
    budget_id: str | None = None,
    branch_id: str | None = None,
    depth: int | None = None,
    eval_run_id: str | None = None,
    mock_config: Optional[Dict[str, Any]] = None,
    burn_follow_up_token: str | None = None,
) -> None:  # noqa: D401, ANN001
    """Celery task that triggers event processing for one persistent agent."""
    from api.evals.execution import set_current_eval_routing_profile

    # Get the Celery-provided span and rename it for clarity
    span = trace.get_current_span()
    span.update_name("PROCESS Agent Events")
    span.set_attribute("persistent_agent.id", str(persistent_agent_id))

    # Make the agent ID available to downstream spans/processors
    baggage.set_baggage("persistent_agent.id", str(persistent_agent_id))

    delivery_info = getattr(self.request, "delivery_info", {}) or {}
    redelivered = bool(getattr(self.request, "redelivered", False)) or bool(
        delivery_info.get("redelivered")
    )
    current_worker_pid = os.getpid()
    if redelivered:
        logger.warning(
            "process_agent_events_task redelivered for agent %s (task_id=%s)",
            persistent_agent_id,
            getattr(self.request, "id", None),
        )
        span.set_attribute("celery.redelivered", True)
        stale_threshold_seconds = int(settings.AGENT_EVENT_PROCESSING_REDELIVERY_STALE_THRESHOLD_SECONDS)
        pid_grace_seconds = max(
            0,
            int(settings.AGENT_EVENT_PROCESSING_REDELIVERY_PID_GRACE_SECONDS),
        )
        if stale_threshold_seconds > 0 or pid_grace_seconds > 0:
            heartbeat = get_processing_heartbeat(persistent_agent_id)
            last_seen = None
            heartbeat_worker_pid = None
            if isinstance(heartbeat, dict):
                last_seen = heartbeat.get("last_seen")
                heartbeat_worker_pid = heartbeat.get("worker_pid")
            try:
                last_seen = float(last_seen) if last_seen is not None else None
            except (TypeError, ValueError):
                last_seen = None

            now = time.time()
            should_clear = False
            try:
                heartbeat_worker_pid = (
                    int(heartbeat_worker_pid) if heartbeat_worker_pid is not None else None
                )
            except (TypeError, ValueError):
                heartbeat_worker_pid = None

            if heartbeat_worker_pid is not None and heartbeat_worker_pid != current_worker_pid:
                # Different worker, check against grace period
                if pid_grace_seconds == 0 or last_seen is None or (now - last_seen) > pid_grace_seconds:
                    should_clear = True
            elif stale_threshold_seconds > 0:
                # Same worker or no PID, check against stale threshold
                if last_seen is None or (now - last_seen) > stale_threshold_seconds:
                    should_clear = True

            if should_clear:
                lock_key = f"agent-event-processing:{persistent_agent_id}"
                try:
                    redis_client = get_redis_client()
                    deleted = 0
                    for storage_key in _lock_storage_keys(lock_key):
                        deleted += int(redis_client.delete(storage_key) or 0)
                    if deleted:
                        logger.warning(
                            "Cleared stale lock(s) for redelivered agent %s (threshold=%ss)",
                            persistent_agent_id,
                            stale_threshold_seconds,
                        )
                        span.add_event("Cleared stale lock for redelivered task")
                except redis.exceptions.RedisError:
                    logger.exception(
                        "Failed to clear stale lock for redelivered agent %s",
                        persistent_agent_id,
                    )

    # Look up and set the routing profile from the eval run (if any)
    # This is needed because context variables don't propagate across Celery tasks
    routing_profile = None
    if eval_run_id:
        try:
            from api.models import EvalRun
            eval_run = EvalRun.objects.select_related("llm_routing_profile").filter(id=eval_run_id).first()
            if eval_run and eval_run.llm_routing_profile:
                routing_profile = eval_run.llm_routing_profile
                span.set_attribute("eval.routing_profile", routing_profile.name)
        except Exception:
            logger.debug("Failed to look up routing profile for eval_run %s", eval_run_id, exc_info=True)

    try:
        set_current_eval_routing_profile(routing_profile)
        # Delegate to core logic
        process_agent_events(
            persistent_agent_id,
            budget_id=budget_id,
            branch_id=branch_id,
            depth=depth,
            eval_run_id=eval_run_id,
            mock_config=mock_config,
            burn_follow_up_token=burn_follow_up_token,
            worker_pid=current_worker_pid,
        )
    finally:
        set_current_eval_routing_profile(None)
        # Ensure queued flag clears even if processing short-circuits,
        # but keep it set when pending work is queued for retry.
        if not is_agent_pending(persistent_agent_id):
            clear_processing_queued_flag(persistent_agent_id)
        _broadcast_processing_state(persistent_agent_id)

        # Check for deferred referral credits on successful agent processing
        try:
            from api.models import PersistentAgent
            agent = PersistentAgent.objects.select_related('user').filter(id=persistent_agent_id).first()
            if settings.DEFERRED_REFERRAL_CREDITS_ENABLED and agent and agent.user_id:
                ReferralService.check_and_grant_deferred_referral_credits(agent.user)
        except Exception:
            logger.exception(
                "Failed to check/grant deferred referral credits for agent %s",
                persistent_agent_id,
            )


@shared_task(bind=True, name="api.agent.tasks.process_pending_agent_events")
def process_pending_agent_events_task(
    self,
    max_agents: int | None = None,
    delay_seconds: int | None = None,
) -> None:  # noqa: D401, ANN001
    """Drain the pending agent set and re-queue processing tasks."""
    redis_client = get_redis_client()
    clear_pending_drain_slot(client=redis_client)

    pending_settings = get_pending_drain_settings()
    limit = int(max_agents if max_agents is not None else pending_settings.pending_drain_limit)
    agent_ids = pop_pending_agents(limit=limit, client=redis_client)
    if not agent_ids:
        return

    valid_agent_ids: list[str] = []
    invalid_agent_ids: list[str] = []
    for agent_id in agent_ids:
        normalized = _normalize_agent_id(agent_id)
        if normalized:
            valid_agent_ids.append(normalized)
        else:
            invalid_agent_ids.append(str(agent_id))

    if invalid_agent_ids:
        logger.warning(
            "Pending drain skipped %s invalid agent id(s): %s",
            len(invalid_agent_ids),
            invalid_agent_ids[:5],
        )

    for agent_id in valid_agent_ids:
        process_agent_events_task.delay(agent_id)

    remaining = count_pending_agents(client=redis_client)
    if remaining <= 0:
        logger.info(
            "Pending drain processed: drained=%s skipped_invalid=%s remaining=0 rescheduled=False",
            len(valid_agent_ids),
            len(invalid_agent_ids),
        )
        return

    delay = int(delay_seconds if delay_seconds is not None else pending_settings.pending_drain_delay_seconds)
    schedule_ttl = pending_settings.pending_drain_schedule_ttl_seconds
    if delay_seconds is not None:
        schedule_ttl = max(30, delay * 6)
    rescheduled = False
    if claim_pending_drain_slot(ttl=schedule_ttl, client=redis_client):
        process_pending_agent_events_task.apply_async(countdown=delay)
        rescheduled = True
    logger.info(
        "Pending drain processed: drained=%s skipped_invalid=%s remaining=%s rescheduled=%s delay=%s",
        len(valid_agent_ids),
        len(invalid_agent_ids),
        remaining,
        rescheduled,
        delay if rescheduled else None,
    )


def _remove_orphaned_celery_beat_task(agent_id: str) -> None:
    """Remove the associated Celery Beat schedule task for a non-existent agent."""
    from celery import current_app as celery_app
    from redbeat import RedBeatSchedulerEntry

    task_name = f"persistent-agent-schedule:{agent_id}"
    app = celery_app
    try:
        # Use the app instance to avoid potential context issues
        with app.connection():
            entry = RedBeatSchedulerEntry.from_key(f"redbeat:{task_name}", app=app)
            entry.delete()
        logger.info("Removed orphaned Celery Beat task for non-existent agent %s", agent_id)
    except KeyError:
        # Task doesn't exist, which is fine.
        logger.info("No Celery Beat task found for non-existent agent %s", agent_id)
    except Exception as e:
        # Catch other potential errors during deletion
        logger.error(
            "Error removing orphaned Celery Beat task for agent %s: %s", agent_id, e
        )


@shared_task(bind=True, name="api.agent.tasks.process_agent_cron_trigger")
def process_agent_cron_trigger_task(self, persistent_agent_id: str, cron_expression: str) -> None:  # noqa: D401, ANN001
    """
    Celery task that handles cron trigger events for persistent agents.
    
    This task creates the cron trigger record first, then delegates to
    the standard event processing pipeline.
    """
    from ...models import PersistentAgent, PersistentAgentStep, PersistentAgentCronTrigger

    # Get the Celery-provided span and rename it for clarity
    span = trace.get_current_span()
    span.update_name("PROCESS Agent Cron Trigger")
    span.set_attribute("persistent_agent.id", str(persistent_agent_id))
    span.set_attribute("cron.expression", cron_expression)

    # Make the agent ID available to downstream spans/processors
    baggage.set_baggage("persistent_agent.id", str(persistent_agent_id))

    try:
        from constants.feature_flags import AGENT_CRON_THROTTLE
        from config.redis_client import get_redis_client
        from api.services.cron_throttle import (
            cron_throttle_gate_key,
            cron_throttle_footer_cooldown_key,
            cron_throttle_pending_footer_key,
            evaluate_free_plan_cron_throttle,
        )

        agent = (
            PersistentAgent.objects
            .select_related(
                "organization",
                "organization__billing",
                "user",
                "user__billing",
                "preferred_contact_endpoint",
            )
            .filter(id=persistent_agent_id)
            .first()
        )
        if agent is None:
            raise PersistentAgent.DoesNotExist

        if switch_is_active(AGENT_CRON_THROTTLE):
            decision = evaluate_free_plan_cron_throttle(agent, cron_expression)
            if decision.throttling_applies:
                redis_client = get_redis_client()
                ttl_seconds = max(1, int(decision.effective_interval_seconds))
                try:
                    acquired = redis_client.set(
                        cron_throttle_gate_key(str(agent.id)),
                        "1",
                        ex=ttl_seconds,
                        nx=True,
                    )
                except Exception:
                    logger.exception(
                        "Cron throttle redis gate failed for agent %s; allowing cron execution.",
                        agent.id,
                    )
                    acquired = True

                if not acquired:
                    try:
                        cooldown_key = cron_throttle_footer_cooldown_key(str(agent.id))
                        if not redis_client.exists(cooldown_key):
                            pending_key = cron_throttle_pending_footer_key(str(agent.id))
                            pending_ttl_days = int(getattr(settings, "AGENT_CRON_THROTTLE_MAX_INTERVAL_DAYS", 30))
                            pending_ttl_seconds = max(1, pending_ttl_days * 86400)
                            redis_client.set(
                                pending_key,
                                "1",
                                ex=pending_ttl_seconds,
                                nx=True,
                            )
                    except Exception:
                        logger.debug(
                            "Failed to mark cron throttle footer pending for agent %s",
                            agent.id,
                            exc_info=True,
                        )
                    logger.info(
                        "Skipping cron trigger for agent %s due to free-plan throttling (stage=%s interval=%ss)",
                        agent.id,
                        decision.stage,
                        decision.effective_interval_seconds,
                    )
                    return

        # Create the cron trigger record first
        with transaction.atomic():
            agent = PersistentAgent.objects.select_for_update().get(id=persistent_agent_id)
            
            # Create a step for this cron trigger
            step = PersistentAgentStep.objects.create(
                agent=agent,
                description=f"Cron trigger: {cron_expression}",
            )
            
            # Create the cron trigger record
            PersistentAgentCronTrigger.objects.create(
                step=step,
                cron_expression=cron_expression,
            )
        
        # Now delegate to the standard event processing pipeline (top-level)
        process_agent_events(persistent_agent_id)
        
    except ValidationError as exc:
        if _is_task_quota_error(exc):
            logger.info(
                "Skipping cron trigger for agent %s due to task quota: %s",
                persistent_agent_id,
                exc,
            )
            return
        raise

    except PersistentAgent.DoesNotExist:
        logger.warning(
            "PersistentAgent %s does not exist - removing orphaned Celery beat task", 
            persistent_agent_id
        )
        # Remove the orphaned beat task to prevent future recurring failures
        _remove_orphaned_celery_beat_task(persistent_agent_id) 
