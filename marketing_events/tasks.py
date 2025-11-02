import logging
import time

from celery import shared_task
from django.conf import settings

from .providers import get_providers
from .providers.base import TemporaryError, PermanentError
from .schema import normalize_event
from .telemetry import trace_event


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
        return
    with trace_event(evt):
        for provider in get_providers():
            try:
                response = provider.send(evt)
            except TemporaryError:
                raise
            except PermanentError as e:
                logging.getLogger(__name__).warning(
                    f"PermanentError sending marketing event: {e}",
                    exc_info=True,
                )
                continue
