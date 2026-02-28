import logging
import time
from typing import Any

from celery import shared_task
from django.conf import settings

from util.analytics import Analytics
from util.integrations import stripe_status
from util.payments_helper import PaymentsHelper
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


def _extract_value(container: Any, key: str):
    if isinstance(container, dict):
        return container.get(key)

    getter = getattr(container, "get", None)
    if callable(getter):
        try:
            return getter(key)
        except TypeError:
            pass

    try:
        return getattr(container, key)
    except AttributeError:
        return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def _subscription_state_from_db(subscription_id: str) -> tuple[bool | None, str | None]:
    try:
        from djstripe.models import Subscription
    except ImportError:
        return None, None

    subscription = (
        Subscription.objects.filter(id=subscription_id)
        .only("cancel_at_period_end", "status", "stripe_data")
        .first()
    )
    if not subscription:
        return None, None

    cancel_at_period_end = _coerce_bool(getattr(subscription, "cancel_at_period_end", None))
    status = getattr(subscription, "status", None)
    if isinstance(status, str):
        status = status.strip().lower() or None
    else:
        status = None

    stripe_data = getattr(subscription, "stripe_data", {}) or {}
    if isinstance(stripe_data, dict):
        if cancel_at_period_end is None:
            cancel_at_period_end = _coerce_bool(stripe_data.get("cancel_at_period_end"))
        if status is None:
            raw_status = stripe_data.get("status")
            if isinstance(raw_status, str):
                status = raw_status.strip().lower() or None

    return cancel_at_period_end, status


def _subscription_state_from_stripe(subscription_id: str) -> tuple[bool | None, str | None]:
    status = stripe_status()
    if not status.enabled:
        return None, None

    stripe_key = PaymentsHelper.get_stripe_key()
    if not stripe_key:
        return None, None

    try:
        import stripe
    except ImportError:
        return None, None

    stripe.api_key = stripe_key
    try:
        live_subscription = stripe.Subscription.retrieve(subscription_id)
    except stripe.error.StripeError:
        logger.warning(
            "Failed to refresh Stripe subscription %s before StartTrial CAPI send",
            subscription_id,
            exc_info=True,
        )
        return None, None

    cancel_at_period_end = _coerce_bool(_extract_value(live_subscription, "cancel_at_period_end"))
    raw_status = _extract_value(live_subscription, "status")
    normalized_status = raw_status.strip().lower() if isinstance(raw_status, str) else None

    return cancel_at_period_end, normalized_status


def _should_send_start_trial(payload: dict) -> tuple[bool, str | None]:
    properties = ((payload or {}).get("properties") or {})
    subscription_id = properties.get("subscription_id")
    if not subscription_id:
        return True, None

    normalized_subscription_id = str(subscription_id).strip()
    if not normalized_subscription_id:
        return True, None

    decision_source: str | None = None
    cancel_at_period_end, subscription_status = _subscription_state_from_stripe(normalized_subscription_id)
    if cancel_at_period_end is not None or subscription_status is not None:
        decision_source = "stripe"
    if cancel_at_period_end is None and subscription_status is None:
        cancel_at_period_end, subscription_status = _subscription_state_from_db(normalized_subscription_id)
        if cancel_at_period_end is not None or subscription_status is not None:
            decision_source = "db"

    if cancel_at_period_end:
        logger.info(
            "Skipping StartTrial marketing event because subscription %s is set to cancel at period end.",
            normalized_subscription_id,
        )
        return False, decision_source

    if subscription_status == "canceled":
        logger.info(
            "Skipping StartTrial marketing event because subscription %s is already canceled.",
            normalized_subscription_id,
        )
        return False, decision_source

    return True, decision_source


def _track_start_trial_skip(payload: dict, *, reason: str, decision_source: str | None = None) -> None:
    user_payload = (payload or {}).get("user") or {}
    analytics_user_id = _analytics_user_id(user_payload.get("id"), None)
    properties_payload = (payload or {}).get("properties") or {}
    skip_properties = {
        "event_name": "StartTrial",
        "reason": reason,
    }

    subscription_id = properties_payload.get("subscription_id")
    if subscription_id:
        skip_properties["subscription_id"] = str(subscription_id)
    if decision_source:
        skip_properties["decision_source"] = decision_source

    Analytics.track(
        user_id=analytics_user_id,
        event="CAPI Event Skipped",
        properties=skip_properties,
    )


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


def _dispatch_marketing_event(payload: dict):
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
                provider.send(evt)
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


@shared_task(
    bind=True,
    autoretry_for=(TemporaryError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=6,
)
def enqueue_marketing_event(self, payload: dict):
    if not settings.GOBII_PROPRIETARY_MODE:
        return
    _dispatch_marketing_event(payload)


@shared_task(
    bind=True,
    autoretry_for=(TemporaryError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=6,
)
def enqueue_start_trial_marketing_event(self, payload: dict):
    if not settings.GOBII_PROPRIETARY_MODE:
        return
    should_send, decision_source = _should_send_start_trial(payload)
    if not should_send:
        _track_start_trial_skip(
            payload,
            reason="subscription_canceled_or_cancel_at_period_end",
            decision_source=decision_source,
        )
        return
    _dispatch_marketing_event(payload)
