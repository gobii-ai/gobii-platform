from datetime import datetime
from typing import Any, Mapping


DELINQUENT_STATUS = "past_due"
FUTURE_DELINQUENT_STATUSES = {"past_due", "unpaid", "incomplete"}


def _normalize_status(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


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


def _previous_attributes(raw_previous_attributes: Any) -> Mapping[str, Any]:
    if isinstance(raw_previous_attributes, Mapping):
        return raw_previous_attributes
    return {}


def is_trial_cancel_scheduled(
    *,
    event_type: str,
    current_status: str | None,
    current_cancel_at_period_end: bool | None,
    previous_attributes: Any,
) -> bool:
    if event_type != "customer.subscription.updated":
        return False
    if _normalize_status(current_status) != "trialing":
        return False
    if current_cancel_at_period_end is not True:
        return False

    previous = _previous_attributes(previous_attributes)
    previous_cancel = _coerce_bool(previous.get("cancel_at_period_end"))
    return previous_cancel is False


def is_trial_ended_non_renewal(
    *,
    event_type: str,
    current_status: str | None,
    previous_attributes: Any,
    trial_end_dt: datetime | None,
    current_period_end_dt: datetime | None,
    now_dt: datetime,
) -> bool:
    normalized_status = _normalize_status(current_status)
    # Treat either explicit deletion events or canceled status updates as terminal cancellation signals.
    if event_type != "customer.subscription.deleted" and normalized_status != "canceled":
        return False

    if trial_end_dt is None or trial_end_dt > now_dt:
        return False
    if current_period_end_dt is None:
        return False
    if trial_end_dt.date() != current_period_end_dt.date():
        return False

    previous = _previous_attributes(previous_attributes)
    previous_status = _normalize_status(previous.get("status"))
    if previous_status == "trialing":
        return True

    previous_cancel = _coerce_bool(previous.get("cancel_at_period_end"))
    if previous_cancel is True:
        return True

    # Stripe deleted payloads do not always include previous_attributes.
    return event_type == "customer.subscription.deleted"


def is_trial_conversion_failure(
    *,
    billing_reason: str | None,
    trial_end_dt: datetime | None,
    line_period_start_dt: datetime | None,
    subscription_current_period_start_dt: datetime | None,
    subscription_status: str | None,
    attempt_count: int | None,
) -> bool:
    if billing_reason != "subscription_cycle":
        return False
    if trial_end_dt is None:
        return False
    if attempt_count is not None and attempt_count > 1:
        return False

    normalized_status = _normalize_status(subscription_status)
    if normalized_status is not None and normalized_status not in FUTURE_DELINQUENT_STATUSES:
        return False

    if line_period_start_dt and trial_end_dt.date() == line_period_start_dt.date():
        return True
    if (
        subscription_current_period_start_dt
        and trial_end_dt.date() == subscription_current_period_start_dt.date()
    ):
        return True
    return False


def is_subscription_delinquency_entered(
    *,
    event_type: str,
    current_status: str | None,
    previous_attributes: Any,
) -> bool:
    if event_type != "customer.subscription.updated":
        return False
    if _normalize_status(current_status) != DELINQUENT_STATUS:
        return False

    previous = _previous_attributes(previous_attributes)
    previous_status = _normalize_status(previous.get("status"))
    if previous_status is None:
        return False
    return previous_status != DELINQUENT_STATUS
