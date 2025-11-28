"""
Celery task for processing persistent agent events.

This module provides the Celery task wrapper for agent event processing,
handling orchestration and retry semantics while delegating the core
business logic to the event processing module.
"""

import logging
from typing import Any, Dict, Optional, Sequence

from celery import Task, shared_task
from opentelemetry import baggage, trace
from django.db import transaction
from django.core.exceptions import ValidationError

from ..core.event_processing import process_agent_events
from ..core.processing_flags import clear_processing_queued_flag, set_processing_queued_flag

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


@shared_task(bind=True, base=ProcessAgentEventsTaskBase, name="api.agent.tasks.process_agent_events")
def process_agent_events_task(
    self,
    persistent_agent_id: str,
    budget_id: str | None = None,
    branch_id: str | None = None,
    depth: int | None = None,
    eval_run_id: str | None = None,
    mock_config: Optional[Dict[str, Any]] = None,
) -> None:  # noqa: D401, ANN001
    """Celery task that triggers event processing for one persistent agent."""
    from api.evals.execution import set_current_eval_routing_profile

    # Get the Celery-provided span and rename it for clarity
    span = trace.get_current_span()
    span.update_name("PROCESS Agent Events")
    span.set_attribute("persistent_agent.id", str(persistent_agent_id))

    # Make the agent ID available to downstream spans/processors
    baggage.set_baggage("persistent_agent.id", str(persistent_agent_id))

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
        )
    finally:
        set_current_eval_routing_profile(None)
        # Ensure queued flag clears even if processing short-circuits
        clear_processing_queued_flag(persistent_agent_id)
        _broadcast_processing_state(persistent_agent_id)


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
