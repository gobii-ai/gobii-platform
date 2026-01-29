import logging

from celery import shared_task

from observability import traced
from api.services.burn_rate_snapshots import refresh_burn_rate_snapshots

logger = logging.getLogger(__name__)


@shared_task(bind=True, ignore_result=True, name="api.tasks.refresh_burn_rate_snapshots")
def refresh_burn_rate_snapshots_task(self) -> int:
    with traced("BURN_RATE Refresh Snapshots") as span:
        refreshed = refresh_burn_rate_snapshots()
        span.set_attribute("burn_rate_snapshots.count", refreshed)
        logger.info("Refreshed %s burn rate snapshots", refreshed)
        return refreshed

