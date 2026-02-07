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


def _analytics_user_id(raw_user_id, hashed_external_id):
    if raw_user_id is not None:
        normalized = str(raw_user_id).strip()
        if normalized:
            try:
                return int(normalized)
            except (TypeError, ValueError):
                return normalized
    return hashed_external_id or "anonymous"


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
    analytics_user_id = _analytics_user_id(
        ((payload or {}).get("user") or {}).get("id"),
        evt["ids"]["external_id"],
    )
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
                    user_id=analytics_user_id,
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
                    user_id=analytics_user_id,
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
