import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from celery import shared_task
from django.apps import apps
from django.contrib.auth import get_user_model
from django.conf import settings
from django.db.models import Q

from constants.plans import PlanNames
from util.analytics import Analytics
from util.integrations import stripe_status
from util.payments_helper import PaymentsHelper
from util.subscription_helper import (
    get_active_subscription,
    get_owner_plan,
    get_subscription_base_price,
    reconcile_user_plan_from_stripe,
    resolve_plan_from_subscription_data,
)
from .providers import get_providers
from .providers.base import TemporaryError, PermanentError
from .schema import normalize_event
from .telemetry import trace_event
from .value_utils import calculate_conversion_value


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
_QUALIFYING_COMPLETE_REGISTRATION_ORG_ROLES = ("owner", "billing_admin")


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


def _subscription_guard_id_from_payload(payload: dict) -> str | None:
    guard_subscription_id = (payload or {}).get("subscription_guard_id")
    if guard_subscription_id is not None:
        normalized_guard_id = str(guard_subscription_id).strip()
        if normalized_guard_id:
            return normalized_guard_id

    properties = ((payload or {}).get("properties") or {})
    subscription_id = properties.get("subscription_id")
    if not subscription_id:
        return None

    normalized_subscription_id = str(subscription_id).strip()
    return normalized_subscription_id or None


def _payload_event_name(payload: dict) -> str:
    return str((payload or {}).get("event_name") or "").strip() or "UnknownEvent"


def _should_send_subscription_guarded_event(payload: dict) -> tuple[bool, str | None]:
    normalized_subscription_id = _subscription_guard_id_from_payload(payload)
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
            "Skipping %s marketing event because subscription %s is set to cancel at period end.",
            _payload_event_name(payload),
            normalized_subscription_id,
        )
        return False, decision_source

    if subscription_status == "canceled":
        logger.info(
            "Skipping %s marketing event because subscription %s is already canceled.",
            _payload_event_name(payload),
            normalized_subscription_id,
        )
        return False, decision_source

    return True, decision_source


def _track_subscription_guarded_skip(payload: dict, *, reason: str, decision_source: str | None = None) -> None:
    user_payload = (payload or {}).get("user") or {}
    analytics_user_id = _analytics_user_id(user_payload.get("id"), None)
    properties_payload = (payload or {}).get("properties") or {}
    event_name = _payload_event_name(payload)
    skip_properties = {
        "event_name": event_name,
        "reason": reason,
    }

    subscription_id = _subscription_guard_id_from_payload(payload) or properties_payload.get("subscription_id")
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


def _payload_user(payload: dict):
    user_payload = (payload or {}).get("user") or {}
    raw_user_id = user_payload.get("id")
    normalized_user_id = str(raw_user_id).strip() if raw_user_id is not None else ""
    if not normalized_user_id:
        return None

    user_model = get_user_model()
    try:
        return user_model.objects.get(pk=normalized_user_id)
    except user_model.DoesNotExist:
        logger.info("Unable to resolve CompleteRegistration user %s", normalized_user_id)
        return None


def _plan_monthly_value(plan: dict | None) -> Decimal | None:
    if not isinstance(plan, dict):
        return None

    plan_id = str(plan.get("id") or "").strip().lower()
    candidate_keys = ["price"]
    if plan_id == PlanNames.ORG_TEAM:
        candidate_keys.insert(0, "price_per_seat")

    for key in candidate_keys:
        raw_value = plan.get(key)
        if raw_value is None or isinstance(raw_value, bool):
            continue
        try:
            return Decimal(str(raw_value))
        except (InvalidOperation, TypeError, ValueError):
            continue

    return None


def _complete_registration_candidate_owners(user) -> list[dict[str, Any]]:
    if user is None or getattr(user, "id", None) is None:
        return []

    candidates = [{"owner": user, "owner_type": "user"}]

    try:
        Organization = apps.get_model("api", "Organization")
        OrganizationMembership = apps.get_model("api", "OrganizationMembership")
    except LookupError:
        return candidates

    organizations = (
        Organization.objects.filter(is_active=True)
        .filter(
            Q(created_by_id=user.id)
            | Q(
                organizationmembership__user_id=user.id,
                organizationmembership__status=OrganizationMembership.OrgStatus.ACTIVE,
                organizationmembership__role__in=_QUALIFYING_COMPLETE_REGISTRATION_ORG_ROLES,
            )
        )
        .select_related("billing", "billing__plan_version", "billing__plan_version__plan")
        .distinct()
    )

    candidates.extend(
        {"owner": organization, "owner_type": "organization"}
        for organization in organizations
    )
    return candidates


def _resolve_complete_registration_metrics(
    owner,
    *,
    owner_type: str,
    fallback_plan_id: str | None = None,
) -> dict[str, Any]:
    if owner_type == "user":
        resolved_plan = reconcile_user_plan_from_stripe(owner) or get_owner_plan(owner) or {}
    else:
        resolved_plan = get_owner_plan(owner) or {}

    plan_id = str((resolved_plan or {}).get("id") or fallback_plan_id or PlanNames.FREE).strip().lower()
    if not plan_id:
        plan_id = PlanNames.FREE

    currency = str((resolved_plan or {}).get("currency") or "USD").strip().upper() or "USD"
    active_subscription = get_active_subscription(
        owner,
        preferred_plan_id=plan_id if plan_id != PlanNames.FREE else None,
        sync_with_stripe=True,
    )

    monthly_value = None
    if active_subscription is not None:
        subscription_plan, _plan_version, _licensed_item = resolve_plan_from_subscription_data(
            getattr(active_subscription, "stripe_data", {}) or {},
            owner_type=owner_type,
        )
        if subscription_plan:
            resolved_plan = {
                **resolved_plan,
                **subscription_plan,
            }
            plan_id = str((resolved_plan or {}).get("id") or fallback_plan_id or PlanNames.FREE).strip().lower()
            if not plan_id:
                plan_id = PlanNames.FREE
            currency = str((resolved_plan or {}).get("currency") or currency or "USD").strip().upper() or "USD"

        monthly_value, subscription_currency = get_subscription_base_price(active_subscription)
        if isinstance(subscription_currency, str) and subscription_currency.strip():
            currency = subscription_currency.strip().upper()

    if monthly_value is None:
        monthly_value = _plan_monthly_value(resolved_plan)

    value = 0.0
    if plan_id != PlanNames.FREE:
        resolved_value = calculate_conversion_value(
            monthly_value,
            conversion_rate=settings.CAPI_START_TRIAL_CONV_RATE,
        )
        if resolved_value is not None:
            value = resolved_value

    return {
        "owner": owner,
        "owner_type": owner_type,
        "plan_id": plan_id,
        "currency": currency,
        "value": value,
    }


def _complete_registration_metrics_score(metrics: dict[str, Any]) -> tuple[int, float, int]:
    return (
        1 if metrics.get("plan_id") != PlanNames.FREE else 0,
        float(metrics.get("value") or 0.0),
        1 if metrics.get("owner_type") == "organization" else 0,
    )


def _hydrate_complete_registration_value(payload: dict) -> dict:
    payload = dict(payload or {})
    properties = dict(payload.get("properties") or {})
    payload["properties"] = properties

    user = _payload_user(payload)
    fallback_plan_id = str(properties.get("plan") or PlanNames.FREE).strip().lower() or PlanNames.FREE
    candidate_metrics = [
        _resolve_complete_registration_metrics(
            candidate["owner"],
            owner_type=candidate["owner_type"],
            fallback_plan_id=fallback_plan_id,
        )
        for candidate in _complete_registration_candidate_owners(user)
    ]

    if candidate_metrics:
        best_metrics = max(candidate_metrics, key=_complete_registration_metrics_score)
        properties["plan"] = best_metrics["plan_id"]
        properties["value"] = best_metrics["value"]
        properties["currency"] = best_metrics["currency"]
        return payload

    properties["plan"] = fallback_plan_id
    properties["value"] = 0.0
    properties["currency"] = "USD"
    return payload


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
    should_send, decision_source = _should_send_subscription_guarded_event(payload)
    if not should_send:
        _track_subscription_guarded_skip(
            payload,
            reason="subscription_canceled_or_cancel_at_period_end",
            decision_source=decision_source,
        )
        return
    _dispatch_marketing_event(payload)


@shared_task(
    bind=True,
    autoretry_for=(TemporaryError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=6,
)
def enqueue_complete_registration_marketing_event(self, payload: dict):
    if not settings.GOBII_PROPRIETARY_MODE:
        return
    _dispatch_marketing_event(_hydrate_complete_registration_value(payload))


@shared_task(
    bind=True,
    autoretry_for=(TemporaryError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=6,
)
def enqueue_delayed_subscription_guarded_marketing_event(self, payload: dict):
    if not settings.GOBII_PROPRIETARY_MODE:
        return
    should_send, decision_source = _should_send_subscription_guarded_event(payload)
    if not should_send:
        _track_subscription_guarded_skip(
            payload,
            reason="subscription_canceled_or_cancel_at_period_end",
            decision_source=decision_source,
        )
        return
    _dispatch_marketing_event(payload)
