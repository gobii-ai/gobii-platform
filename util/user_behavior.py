import math
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any

from django.contrib.auth import get_user_model
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from util.subscription_helper import get_active_subscription, get_stripe_customer


try:
    from djstripe.models import Subscription as DjstripeSubscription

    DJSTRIPE_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    DjstripeSubscription = None  # type: ignore
    DJSTRIPE_AVAILABLE = False


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=dt_timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed
    return None


def _is_user_owner(owner) -> bool:
    user_model = get_user_model()
    return isinstance(owner, user_model)


def get_owner_trial_started_at(owner) -> datetime | None:
    if owner is None or getattr(owner, "id", None) is None:
        return None

    from api.models import TaskCredit

    if _is_user_owner(owner):
        trial_credit = (
            TaskCredit.objects.filter(user=owner, free_trial_start=True)
            .order_by("granted_date")
            .only("granted_date")
            .first()
        )
        if trial_credit is not None:
            return trial_credit.granted_date

    if not DJSTRIPE_AVAILABLE:
        return None

    try:
        customer = get_stripe_customer(owner)
    except TypeError:
        return None
    if customer is None:
        return None

    subscriptions = customer.subscriptions.all().order_by("trial_start", "trial_end", "current_period_start")
    for subscription in subscriptions:
        trial_started_at = _coerce_datetime(getattr(subscription, "trial_start", None))
        if trial_started_at is not None:
            return trial_started_at

        stripe_data = getattr(subscription, "stripe_data", {}) or {}
        trial_started_at = _coerce_datetime(stripe_data.get("trial_start"))
        if trial_started_at is not None:
            return trial_started_at

    return None


def get_trial_started_at(user) -> datetime | None:
    return get_owner_trial_started_at(user)


def _subscription_has_trial_window(subscription) -> bool:
    if _coerce_datetime(getattr(subscription, "trial_start", None)) is not None:
        return True
    if _coerce_datetime(getattr(subscription, "trial_end", None)) is not None:
        return True

    stripe_data = getattr(subscription, "stripe_data", {}) or {}
    return (
        _coerce_datetime(stripe_data.get("trial_start")) is not None
        or _coerce_datetime(stripe_data.get("trial_end")) is not None
    )


def _get_cancel_scheduled_at(subscription) -> datetime | None:
    # Stripe does not preserve the exact local transition time for
    # cancel_at_period_end, so the last synced row timestamp is our best proxy.
    return (
        _coerce_datetime(getattr(subscription, "djstripe_updated", None))
        or _coerce_datetime(getattr(subscription, "djstripe_created", None))
        or _coerce_datetime(getattr(subscription, "created", None))
    )


def is_fast_cancel_owner(owner) -> bool:
    if owner is None or getattr(owner, "id", None) is None:
        return False
    if not DJSTRIPE_AVAILABLE:
        return False

    trial_started_at = get_owner_trial_started_at(owner)
    if trial_started_at is None:
        return False

    try:
        customer = get_stripe_customer(owner)
    except TypeError:
        return False
    if customer is None:
        return False

    cutoff_at = trial_started_at + timedelta(hours=settings.TRIAL_FAST_CANCEL_CUTOFF_HOURS)
    subscriptions = customer.subscriptions.filter(cancel_at_period_end=True).order_by(
        "djstripe_updated",
        "djstripe_created",
        "created",
    )
    for subscription in subscriptions:
        if not _subscription_has_trial_window(subscription):
            continue
        cancel_scheduled_at = _get_cancel_scheduled_at(subscription)
        if cancel_scheduled_at is None:
            continue
        if trial_started_at <= cancel_scheduled_at <= cutoff_at:
            return True

    return False


def is_fast_cancel_user(user) -> bool:
    return is_fast_cancel_owner(user)


def get_custom_capi_event_delay_seconds(owner) -> int:
    trial_started_at = get_owner_trial_started_at(owner)
    buffer_seconds = settings.CAPI_CUSTOM_EVENT_DELAY_BUFFER_HOURS * 3600
    if trial_started_at is None:
        return buffer_seconds

    cutoff_at = trial_started_at + timedelta(hours=settings.TRIAL_FAST_CANCEL_CUTOFF_HOURS)
    remaining_seconds = max((cutoff_at - timezone.now()).total_seconds(), 0)
    return int(math.ceil(remaining_seconds + buffer_seconds))


def is_owner_currently_in_trial(owner) -> bool:
    if owner is None or getattr(owner, "id", None) is None:
        return False

    try:
        active_subscription = get_active_subscription(owner)
    except TypeError:
        return False
    if active_subscription is None:
        return False

    stripe_data = getattr(active_subscription, "stripe_data", {}) or {}
    subscription_status = str(
        stripe_data.get("status")
        or getattr(active_subscription, "status", "")
        or ""
    ).strip().lower()
    return subscription_status == "trialing"


def is_user_currently_in_trial(user) -> bool:
    return is_owner_currently_in_trial(user)


def count_messages_sent_to_gobii(user) -> int:
    if user is None or getattr(user, "id", None) is None:
        return 0

    from api.models import (
        CommsChannel,
        PersistentAgentCommsEndpoint,
        PersistentAgentMessage,
        UserPhoneNumber,
    )

    message_filter = Q(
        is_outbound=False,
        owner_agent__isnull=False,
    )
    channel_filter = Q(
        from_endpoint__channel=CommsChannel.WEB,
        from_endpoint__address__startswith=f"web://user/{user.id}/agent/",
    )

    normalized_email = PersistentAgentCommsEndpoint.normalize_address(
        CommsChannel.EMAIL,
        getattr(user, "email", None),
    )
    if normalized_email:
        channel_filter |= Q(
            from_endpoint__channel=CommsChannel.EMAIL,
            from_endpoint__address__iexact=normalized_email,
        )

    verified_numbers = list(
        UserPhoneNumber.objects.filter(user=user, is_verified=True).values_list("phone_number", flat=True)
    )
    if verified_numbers:
        channel_filter |= Q(
            from_endpoint__channel=CommsChannel.SMS,
            from_endpoint__address__in=verified_numbers,
        )

    return PersistentAgentMessage.objects.filter(message_filter & channel_filter).count()
