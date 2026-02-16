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

_PROVIDER_TARGET_KEY_BY_CLASS = {
    "MetaCAPI": "meta",
    "RedditCAPI": "reddit",
    "TikTokCAPI": "tiktok",
    "GoogleAnalyticsMP": "google_analytics",
}

_PROVIDER_TARGET_ALIASES = {
    "ga": "google_analytics",
    "ga4": "google_analytics",
    "googleanalyticsmp": "google_analytics",
}


def _analytics_user_id(raw_user_id, hashed_external_id):
    if raw_user_id is not None:
        normalized = str(raw_user_id).strip()
        if normalized:
            try:
                return int(normalized)
            except (TypeError, ValueError):
                return normalized
    return hashed_external_id or "anonymous"


def _normalize_provider_targets(raw_targets) -> set[str] | None:
    if not raw_targets:
        return None
    if isinstance(raw_targets, str):
        raw_values = [raw_targets]
    elif isinstance(raw_targets, (list, tuple, set)):
        raw_values = raw_targets
    else:
        return None

    normalized: set[str] = set()
    for raw_value in raw_values:
        if not isinstance(raw_value, str):
            continue
        candidate = raw_value.strip().lower()
        if not candidate:
            continue
        normalized.add(_PROVIDER_TARGET_ALIASES.get(candidate, candidate))

    return normalized or None


def _provider_target_key(provider) -> str:
    provider_name = provider.__class__.__name__
    return _PROVIDER_TARGET_KEY_BY_CLASS.get(provider_name, provider_name.lower())


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
    provider_targets = _normalize_provider_targets((payload or {}).get("provider_targets"))
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
            if provider_targets and _provider_target_key(provider) not in provider_targets:
                continue
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
