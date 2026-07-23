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
from opentelemetry import baggage, metrics, trace
import redis
from waffle import switch_is_active
from django.conf import settings
from django.db import transaction
from django.core.exceptions import ValidationError

from config.redis_client import get_redis_client
from api.services.agent_error_logging import log_task_quota_exceeded, validation_error_messages
from api.services.owner_execution_pause import is_owner_execution_paused, resolve_agent_owner
from ..core.event_processing import process_agent_events, _lock_storage_keys
from ...services.referral_service import ReferralService
from ..core.processing_flags import (
    claim_pending_drain_slot,
    clear_pending_drain_slot,
    clear_processing_lock_active,
    clear_processing_queued_flag,
    count_pending_agents,
    get_processing_heartbeat,
    get_pending_drain_settings,
    is_human_inbound_generation_consumed,
    is_agent_pending,
    pop_pending_agents,
    set_processing_queued_flag,
)

tracer = trace.get_tracer("gobii.utils")
logger = logging.getLogger(__name__)

AGENT_DEFAULT_PROCESSING_QUEUE = "celery"
AGENT_INTERACTIVE_PROCESSING_QUEUE = "agent_interactive"
PROCESS_AGENT_EVENTS_QUEUED_AT_KWARG = "_queued_at_ts"
PROCESS_AGENT_EVENTS_QUEUED_QUEUE_KWARG = "_queued_queue"
_queue_latency_histogram = None


def _process_agent_events_queue_latency_histogram():
    global _queue_latency_histogram
    provider = metrics.get_meter_provider()
    meter = provider.get_meter("gobii.agent.tasks")
    provider_class = provider.__class__.__name__
    if provider_class in {"_ProxyMeterProvider", "ProxyMeterProvider", "NoOpMeterProvider"}:
        return meter.create_histogram(
            "gobii.agent.process_events.queue_latency",
            unit="s",
            description="Seconds between process_agent_events_task enqueue and worker start.",
        )
    if _queue_latency_histogram is None:
        _queue_latency_histogram = meter.create_histogram(
            "gobii.agent.process_events.queue_latency",
            unit="s",
            description="Seconds between process_agent_events_task enqueue and worker start.",
        )
    return _queue_latency_histogram


def _record_process_agent_events_queue_latency(
    queued_at_ts: float | int | str | None,
    *,
    queue: str | None,
) -> float | None:
    if queued_at_ts is None:
        return None
    try:
        queued_at = float(queued_at_ts)
    except (TypeError, ValueError):
        return None
    latency_seconds = max(0.0, time.time() - queued_at)
    queue_name = str(queue or "unknown")
    _process_agent_events_queue_latency_histogram().record(
        latency_seconds,
        attributes={
            "celery.queue": queue_name,
            "celery.task_name": "api.agent.tasks.process_agent_events",
        },
    )
    return latency_seconds


def schedule_unseen_web_chat_followup(message) -> None:
    delay_seconds = int(settings.WEB_CHAT_UNSEEN_FOLLOWUP_DELAY_SECONDS)
    if delay_seconds < 0:
        return
    from api.services.web_chat_followups import has_unseen_web_chat_followup_contact

    if not has_unseen_web_chat_followup_contact(message):
        return
    if settings.CELERY_TASK_ALWAYS_EAGER and delay_seconds > 0:
        logger.info(
            "Skipping delayed unseen web-chat follow-up scheduling in eager mode for message %s.",
            message.id,
        )
        return

    transaction.on_commit(
        lambda: process_unseen_web_chat_followup_task.apply_async(
            args=[str(message.id)],
            countdown=delay_seconds,
        )
    )


def _is_task_quota_error(exc: ValidationError) -> bool:
    messages = validation_error_messages(exc)

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
        with tracer.start_as_current_span("Enqueue Process Agent Events"):
            agent_id = _extract_agent_id(args, kwargs)
            kwargs = dict(kwargs or {})
            kwargs.setdefault(PROCESS_AGENT_EVENTS_QUEUED_AT_KWARG, time.time())
            kwargs.setdefault(
                PROCESS_AGENT_EVENTS_QUEUED_QUEUE_KWARG,
                str(options.get("queue") or AGENT_DEFAULT_PROCESSING_QUEUE),
            )
            if agent_id:
                with tracer.start_as_current_span("Set Processing Queued Flag"):
                    set_processing_queued_flag(agent_id)
                with tracer.start_as_current_span("Broadcast Processing Queued State"):
                    _broadcast_processing_state(agent_id)
            with tracer.start_as_current_span("Publish Process Agent Events Task"):
                return super().apply_async(args=args, kwargs=kwargs, **options)


def enqueue_interactive_process_agent_events(
    persistent_agent_id: str,
    *,
    inbound_generation: int | str | None = None,
    eval_run_id: str | None = None,
    prefer_low_latency: bool | None = None,
) -> None:
    """Queue interactive agent processing on the low-latency queue."""
    kwargs: dict[str, Any] = {}
    if inbound_generation is not None:
        kwargs["inbound_generation"] = inbound_generation
    if eval_run_id is not None:
        kwargs["eval_run_id"] = eval_run_id
    if prefer_low_latency is not None:
        kwargs["prefer_low_latency"] = prefer_low_latency
    process_agent_events_task.apply_async(
        args=[str(persistent_agent_id)],
        kwargs=kwargs,
        queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
    )


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
    eval_stop_policy: Optional[Dict[str, Any]] = None,
    burn_follow_up_token: str | None = None,
    inbound_generation: int | str | None = None,
    max_loop_iterations: int | None = None,
    max_iterations_followup_delay_seconds: int | None = None,
    max_iterations_followup_queue: str | None = None,
    prefer_low_latency: bool | None = None,
    _queued_at_ts: float | int | str | None = None,
    _queued_queue: str | None = None,
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
    task_queue = delivery_info.get("routing_key") or _queued_queue or AGENT_DEFAULT_PROCESSING_QUEUE
    queue_latency_seconds = _record_process_agent_events_queue_latency(
        _queued_at_ts,
        queue=str(task_queue),
    )
    span.set_attribute("celery.queue", str(task_queue))
    if queue_latency_seconds is not None:
        span.set_attribute("celery.queue_latency_seconds", queue_latency_seconds)
    if task_queue == AGENT_INTERACTIVE_PROCESSING_QUEUE:
        if max_loop_iterations is None:
            max_loop_iterations = settings.AGENT_INTERACTIVE_MAX_LOOP_ITERATIONS
        if max_iterations_followup_delay_seconds is None:
            max_iterations_followup_delay_seconds = (
                settings.AGENT_INTERACTIVE_MAX_ITERATIONS_FOLLOWUP_DELAY_SECONDS
            )
        if max_iterations_followup_queue is None:
            max_iterations_followup_queue = AGENT_DEFAULT_PROCESSING_QUEUE
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
                        clear_processing_lock_active(persistent_agent_id, client=redis_client)
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
        if is_human_inbound_generation_consumed(persistent_agent_id, inbound_generation):
            logger.info(
                "Skipping redundant processing task for agent %s; inbound generation %s already consumed.",
                persistent_agent_id,
                inbound_generation,
            )
            span.add_event("Processing skipped - inbound generation already consumed")
            return
        # Delegate to core logic
        process_agent_events(
            persistent_agent_id,
            budget_id=budget_id,
            branch_id=branch_id,
            depth=depth,
            eval_run_id=eval_run_id,
            mock_config=mock_config,
            eval_stop_policy=eval_stop_policy,
            burn_follow_up_token=burn_follow_up_token,
            inbound_generation=inbound_generation,
            prefer_low_latency=prefer_low_latency,
            max_loop_iterations=max_loop_iterations,
            max_iterations_followup_delay_seconds=max_iterations_followup_delay_seconds,
            max_iterations_followup_queue=max_iterations_followup_queue,
            worker_pid=current_worker_pid,
        )
    except ValidationError as exc:
        if _is_task_quota_error(exc):
            log_task_quota_exceeded(
                persistent_agent_id,
                exc,
                source="api.agent.tasks.process_events.process_agent_events_task",
                logger=logger,
                task_id=getattr(self.request, "id", None),
            )
        raise
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


@shared_task(bind=True, name="api.agent.tasks.queue_agent_process_events_batch")
def queue_agent_process_events_batch_task(self, agent_ids: list[str]) -> dict[str, int]:  # noqa: D401, ANN001
    """Fan out process-event tasks for a pre-filtered set of active agents."""
    queued_count = 0
    invalid_count = 0

    for agent_id in agent_ids:
        normalized_agent_id = _normalize_agent_id(agent_id)
        if not normalized_agent_id:
            invalid_count += 1
            continue
        process_agent_events_task.delay(normalized_agent_id)
        queued_count += 1

    if invalid_count:
        logger.warning(
            "Skipped %s invalid agent id(s) while queueing staff-scoped process events.",
            invalid_count,
        )

    return {"queued_count": queued_count, "invalid_count": invalid_count}


@shared_task(bind=True, name="api.agent.tasks.process_unseen_web_chat_followup")
def process_unseen_web_chat_followup_task(self, message_id: str) -> None:  # noqa: D401, ANN001
    """Queue agent processing when an outbound web chat message remains unread."""
    from api.models import PersistentAgentSystemMessage
    from api.services.web_chat_followups import prepare_unseen_web_chat_followup

    followup = prepare_unseen_web_chat_followup(message_id)
    if followup is None:
        return

    created = False
    with transaction.atomic():
        already_exists = PersistentAgentSystemMessage.objects.filter(
            agent=followup.agent,
            body__contains=str(followup.message.id),
        ).exists()
        if not already_exists:
            PersistentAgentSystemMessage.objects.create(
                agent=followup.agent,
                body=followup.directive,
            )
            created = True

    if not created:
        return

    logger.info(
        "Queued unseen web-chat follow-up processing for agent %s message %s.",
        followup.agent.id,
        followup.message.id,
    )
    process_agent_events_task.delay(str(followup.agent.id))


@shared_task(bind=True, name="api.agent.tasks.process_planning_timeout")
def process_planning_timeout_task(self, persistent_agent_id: str) -> None:  # noqa: D401, ANN001
    """Create a one-time planning timeout directive and queue agent processing."""
    from api.models import PersistentAgent, PersistentAgentSystemMessage
    from api.services.agent_planning import PLANNING_TIMEOUT_SYSTEM_MESSAGE_MARKER, build_planning_timeout_directive

    queued_agent_id = None
    with transaction.atomic():
        agent = PersistentAgent.objects.select_for_update().filter(id=persistent_agent_id).first()
        if agent is None:
            return

        directive = build_planning_timeout_directive(agent)
        if directive is None:
            return

        already_exists = PersistentAgentSystemMessage.objects.filter(
            agent=agent,
            body__contains=PLANNING_TIMEOUT_SYSTEM_MESSAGE_MARKER,
        ).exists()
        if already_exists:
            return

        PersistentAgentSystemMessage.objects.create(
            agent=agent,
            body=directive,
        )
        queued_agent_id = str(agent.id)

    logger.info(
        "Queued planning-timeout processing for agent %s.",
        queued_agent_id,
    )
    process_agent_events_task.delay(queued_agent_id)


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


@shared_task(
    bind=True,
    name="api.agent.tasks.process_discord_inbound_debounce",
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_discord_inbound_debounce_task(
    self,
    persistent_agent_id: str,
) -> None:  # noqa: D401, ANN001
    """Wake an agent after Discord inbound messages have been quiet long enough."""
    from api.services.discord_messages import process_discord_inbound_debounce

    process_discord_inbound_debounce(persistent_agent_id)


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


def _scheduled_execution_is_throttled(agent, schedule_expression: str) -> bool:
    from api.services.cron_throttle import (
        cron_throttle_footer_cooldown_key,
        cron_throttle_gate_key,
        cron_throttle_pending_footer_key,
        evaluate_free_plan_cron_throttle,
    )
    from config.redis_client import get_redis_client
    from constants.feature_flags import AGENT_CRON_THROTTLE

    if not switch_is_active(AGENT_CRON_THROTTLE):
        return False
    decision = evaluate_free_plan_cron_throttle(agent, schedule_expression)
    if not decision.throttling_applies:
        return False

    redis_client = get_redis_client()
    ttl_seconds = max(1, int(decision.effective_interval_seconds))
    try:
        acquired = redis_client.set(
            cron_throttle_gate_key(str(agent.id)),
            "1",
            ex=ttl_seconds,
            nx=True,
        )
    except redis.RedisError:
        logger.exception(
            "Cron throttle redis gate failed for agent %s; allowing cron execution.",
            agent.id,
        )
        acquired = True

    if acquired:
        return False

    try:
        cooldown_key = cron_throttle_footer_cooldown_key(str(agent.id))
        if not redis_client.exists(cooldown_key):
            pending_key = cron_throttle_pending_footer_key(str(agent.id))
            pending_ttl_days = int(
                getattr(settings, "AGENT_CRON_THROTTLE_MAX_INTERVAL_DAYS", 30)
            )
            redis_client.set(
                pending_key,
                "1",
                ex=max(1, pending_ttl_days * 86400),
                nx=True,
            )
    except redis.RedisError:
        logger.debug(
            "Failed to mark cron throttle footer pending for agent %s",
            agent.id,
            exc_info=True,
        )
    logger.info(
        "Skipping scheduled trigger for agent %s due to free-plan throttling (stage=%s interval=%ss)",
        agent.id,
        decision.stage,
        decision.effective_interval_seconds,
    )
    return True


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
        agent = (
            PersistentAgent.objects.alive()
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

        owner = resolve_agent_owner(agent)
        if owner is not None and is_owner_execution_paused(owner):
            logger.info(
                "Skipping cron trigger for agent %s because owner execution is paused.",
                agent.id,
            )
            return

        if _scheduled_execution_is_throttled(agent, cron_expression):
            return

        # Create the cron trigger record first
        with transaction.atomic():
            agent = PersistentAgent.objects.alive().select_for_update().get(id=persistent_agent_id)
            
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
            log_task_quota_exceeded(
                persistent_agent_id,
                exc,
                source="api.agent.tasks.process_events.process_agent_cron_trigger_task",
                logger=logger,
                task_id=getattr(self.request, "id", None),
            )
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


@shared_task(
    bind=True,
    name="api.agent.tasks.process_agent_schedule_trigger",
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_agent_schedule_trigger_task(
    self,
    persistent_agent_id: str,
    schedule_id: str,
    expected_revision: int,
    scheduled_for: str,
) -> None:  # noqa: D401, ANN001
    """Claim and process one occurrence from an additional agent schedule."""
    from api.models import PersistentAgent, PersistentAgentCronTrigger, PersistentAgentStep
    from api.services.agent_schedules import (
        claim_schedule_occurrence,
        schedule_occurrence_key,
    )

    span = trace.get_current_span()
    span.update_name("PROCESS Agent Schedule Trigger")
    span.set_attribute("persistent_agent.id", str(persistent_agent_id))
    span.set_attribute("agent.schedule.id", str(schedule_id))
    span.set_attribute("agent.schedule.revision", int(expected_revision))
    baggage.set_baggage("persistent_agent.id", str(persistent_agent_id))

    should_process = False
    try:
        with transaction.atomic():
            occurrence_key = schedule_occurrence_key(
                schedule_id,
                int(expected_revision),
                scheduled_for,
            )
            occurrence_exists = PersistentAgentCronTrigger.objects.filter(
                occurrence_key=occurrence_key
            ).exists()
            if occurrence_exists:
                logger.info(
                    "Resuming duplicate scheduled occurrence %s for agent %s.",
                    occurrence_key,
                    persistent_agent_id,
                )
                should_process = True
            else:
                claimed = claim_schedule_occurrence(
                    persistent_agent_id,
                    schedule_id,
                    expected_revision,
                    scheduled_for,
                )
                if claimed is None:
                    # A concurrent delivery may have committed the event while
                    # this task waited on the schedule row lock. Re-running the
                    # idempotent event processor is the crash-recovery path.
                    should_process = PersistentAgentCronTrigger.objects.filter(
                        occurrence_key=occurrence_key
                    ).exists()
                    if not should_process:
                        logger.info(
                            "Ignoring stale or inactive schedule delivery for agent %s schedule %s revision %s.",
                            persistent_agent_id,
                            schedule_id,
                            expected_revision,
                        )
                        return
                else:
                    agent = (
                        PersistentAgent.objects.alive()
                        .select_related(
                            "organization",
                            "organization__billing",
                            "user",
                            "user__billing",
                            "preferred_contact_endpoint",
                        )
                        .get(id=persistent_agent_id)
                    )
                    owner = resolve_agent_owner(agent)
                    if owner is not None and is_owner_execution_paused(owner):
                        logger.info(
                            "Skipping scheduled occurrence %s because owner execution is paused.",
                            claimed.occurrence_key,
                        )
                        return

                    if (
                        claimed.kind == "recurring"
                        and claimed.expression
                        and _scheduled_execution_is_throttled(agent, claimed.expression)
                    ):
                        return

                    try:
                        with transaction.atomic():
                            step = PersistentAgentStep.objects.create(
                                agent=agent,
                                description=(
                                    f"Scheduled trigger: {claimed.name} [{claimed.schedule_key}]"
                                ),
                            )
                            cron_expression = (
                                claimed.expression
                                or f"@once {claimed.scheduled_for.isoformat()}"
                            )
                            PersistentAgentCronTrigger.objects.create(
                                step=step,
                                cron_expression=cron_expression,
                                schedule_id=claimed.schedule_id,
                                schedule_key=claimed.schedule_key,
                                schedule_name=claimed.name,
                                schedule_instruction=claimed.instruction,
                                scheduled_for=claimed.scheduled_for,
                                occurrence_key=claimed.occurrence_key,
                            )
                    except ValidationError as exc:
                        if not _is_task_quota_error(exc):
                            raise
                        log_task_quota_exceeded(
                            persistent_agent_id,
                            exc,
                            source=(
                                "api.agent.tasks.process_events."
                                "process_agent_schedule_trigger_task"
                            ),
                            logger=logger,
                            task_id=getattr(self.request, "id", None),
                        )
                        logger.info(
                            "Skipping scheduled trigger for agent %s due to task quota: %s",
                            persistent_agent_id,
                            exc,
                        )
                        return
                    should_process = True

        if should_process:
            process_agent_events(str(persistent_agent_id))
    except ValidationError as exc:
        if _is_task_quota_error(exc):
            log_task_quota_exceeded(
                persistent_agent_id,
                exc,
                source="api.agent.tasks.process_events.process_agent_schedule_trigger_task",
                logger=logger,
                task_id=getattr(self.request, "id", None),
            )
            logger.info(
                "Skipping scheduled trigger for agent %s due to task quota: %s",
                persistent_agent_id,
                exc,
            )
            return
        raise
    except PersistentAgent.DoesNotExist:
        logger.info(
            "Ignoring scheduled delivery for missing agent %s schedule %s.",
            persistent_agent_id,
            schedule_id,
        )
