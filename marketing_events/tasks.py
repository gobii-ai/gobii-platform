import logging
import time

from celery import shared_task
from django.conf import settings

from util.analytics import Analytics
from .providers import get_providers
from .providers.base import TemporaryError, PermanentError
from .schema import normalize_event
from .telemetry import trace_event


logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(TemporaryError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=6,
)
def enqueue_marketing_event(self, payload: dict):
    if not getattr(settings, "GOBII_PROPRIETARY_MODE", False):
        return
    evt = normalize_event(payload)
    # Basic staleness guard: reject events older than 7 days
    if evt["event_time"] < int(time.time()) - 7 * 24 * 3600:
        logger.info(
            f"Dropping stale marketing event for user: {evt['ids']['external_id']}",
            extra={"event_name": evt["event_name"], "event_id": evt["event_id"]},
        )
        return
    with trace_event(evt):
        for provider in get_providers():
            provider_name = provider.__class__.__name__
            try:
                response = provider.send(evt)
                # Track successful CAPI send for observability
                Analytics.track(
                    user_id=evt["ids"]["external_id"] or "anonymous",
                    event="CAPI Event Sent",
                    properties={
                        "provider": provider_name,
                        "event_name": evt["event_name"],
                        "event_id": evt["event_id"],
                    },
                )
            except TemporaryError:
                raise
            except PermanentError as e:
                logger.warning(
                    f"PermanentError sending marketing event: {e}",
                    exc_info=True,
                )
                # Track CAPI failure for observability
                Analytics.track(
                    user_id=evt["ids"]["external_id"] or "anonymous",
                    event="CAPI Event Failed",
                    properties={
                        "provider": provider_name,
                        "event_name": evt["event_name"],
                        "event_id": evt["event_id"],
                        "error": str(e),
                        "error_type": "permanent",
                    },
                )
                continue
