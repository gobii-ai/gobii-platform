import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from api.models import AgentComputeSession
from api.services.compute_control import terminate

logger = logging.getLogger(__name__)


@shared_task(name="api.tasks.sandbox_idle_sweep")
def sandbox_idle_sweep() -> int:
    ttl_seconds = int(getattr(settings, "SANDBOX_IDLE_TTL_SECONDS", 3600))
    if ttl_seconds <= 0:
        return 0

    cutoff = timezone.now() - timedelta(seconds=ttl_seconds)
    sessions = AgentComputeSession.objects.select_related("agent").filter(
        state=AgentComputeSession.State.RUNNING,
        last_activity_at__lt=cutoff,
        last_activity_at__isnull=False,
    )

    stopped = 0
    for session in sessions:
        try:
            terminate(session.agent, reason="Idle TTL expired")
            stopped += 1
        except Exception:
            logger.exception("Failed to stop idle sandbox session for agent %s", session.agent_id)

    return stopped
