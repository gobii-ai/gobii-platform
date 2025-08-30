"""
Celery task for processing persistent agent events.

This module provides the Celery task wrapper for agent event processing,
handling orchestration and retry semantics while delegating the core 
business logic to the event processing module.
"""
from __future__ import annotations

import logging
from celery import shared_task
from opentelemetry import baggage, trace
from django.db import transaction

from ..core.event_processing import process_agent_events

tracer = trace.get_tracer("gobii.utils")
logger = logging.getLogger(__name__)

@shared_task(bind=True, name="api.agent.tasks.process_agent_events")
def process_agent_events_task(self, persistent_agent_id: str, budget_id: str | None = None, branch_id: str | None = None, depth: int | None = None) -> None:  # noqa: D401, ANN001
    """Celery task that triggers event processing for one persistent agent."""

    # Get the Celery-provided span and rename it for clarity
    span = trace.get_current_span()
    span.update_name("PROCESS Agent Events")
    span.set_attribute("persistent_agent.id", str(persistent_agent_id))

    # Make the agent ID available to downstream spans/processors
    baggage.set_baggage("persistent_agent.id", str(persistent_agent_id))

    # Delegate to core logic
    process_agent_events(persistent_agent_id, budget_id=budget_id, branch_id=branch_id, depth=depth)


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
        
    except PersistentAgent.DoesNotExist:
        logger.warning(
            "PersistentAgent %s does not exist - removing orphaned Celery beat task", 
            persistent_agent_id
        )
        # Remove the orphaned beat task to prevent future recurring failures
        _remove_orphaned_celery_beat_task(persistent_agent_id) 