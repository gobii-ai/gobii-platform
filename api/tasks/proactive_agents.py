import logging

from celery import shared_task
from django.conf import settings

from api.services.proactive_activation import ProactiveActivationService

logger = logging.getLogger(__name__)


@shared_task(name="api.tasks.schedule_proactive_agents")
def schedule_proactive_agents_task(batch_size: int = 10) -> int:
    """Periodic task to trigger proactive agent outreach."""
    if getattr(settings, "GOBII_RELEASE_ENV", "local") != "prod":
        logger.info("Proactive agent scheduling skipped; task runs only in production.")
        return 0

    triggered_agents = ProactiveActivationService.trigger_agents(batch_size=batch_size)
    if not triggered_agents:
        return 0

    from api.agent.tasks import process_agent_events_task  # Local import to avoid circular dependency

    success = 0
    for agent in triggered_agents:
        try:
            process_agent_events_task.delay(str(agent.id))
            success += 1
        except Exception:
            logger.exception("Failed to enqueue event processing for proactive agent %s", agent.id)

    return success
