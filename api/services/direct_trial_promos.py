import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

import stripe
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone
from djstripe.models import Subscription

from api.models import (
    TrialPromo,
    TrialPromoActivationModeChoices,
    TrialPromoNoPaymentMethodEndBehaviorChoices,
    TrialPromoRedemption,
    TrialPromoRedemptionStatusChoices,
)
from api.services.trial_promos import (
    TRIAL_PROMO_META_DISCOUNT_ACTIVE,
    TRIAL_PROMO_META_DISCOUNT_COUPON,
    TRIAL_PROMO_META_CREDIT_AMOUNT,
    TRIAL_PROMO_META_PLAN,
    TRIAL_PROMO_META_REDEMPTION_ID,
    TRIAL_PROMO_META_TRIAL_DAYS,
    TRIAL_PROMO_REDEMPTION_ACTIVE_UNTIL_KEY,
    TRIAL_PROMO_REDEMPTION_ADDITIONAL_PRICE_ID_KEY,
    TRIAL_PROMO_REDEMPTION_COUPON_ID_KEY,
    TRIAL_PROMO_REDEMPTION_DISCOUNT_MONTHS_KEY,
    TRIAL_PROMO_REDEMPTION_LATE_CONVERSION_GRACE_DAYS_KEY,
    TRIAL_PROMO_REDEMPTION_PRICE_ID_KEY,
    TrialPromoError,
    build_trial_promo_metadata,
    get_direct_trial_promo_redemption,
    mark_direct_trial_promo_completed,
    mark_direct_trial_promo_failed,
    mark_direct_trial_promo_subscription,
    reserve_direct_trial_promo_redemption,
)
from constants.plans import PlanNames
from tasks.services import TaskCreditService
from util.subscription_helper import (
    get_or_create_stripe_customer,
    reconcile_user_plan_from_stripe,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DirectTrialActivationResult:
    redemption: TrialPromoRedemption
    subscription_id: str
    schedule_id: str


@dataclass(frozen=True)
class DirectTrialCoupon:
    coupon_id: str
    duration_in_months: int
    percent_off: Decimal | None
    amount_off: int | None
    currency: str


@dataclass(frozen=True)
class DirectTrialActivationTerms:
    plan: str
    price_id: str
    additional_price_id: str
    trial_days: int
    trial_credit_amount: Decimal | None
    coupon: DirectTrialCoupon
    late_conversion_grace_days: int
    active_until: datetime | None


_REDEMPTION_PERCENT_OFF_KEY = "percent_off"
_REDEMPTION_AMOUNT_OFF_KEY = "amount_off"
_REDEMPTION_CURRENCY_KEY = "currency"
_REDEMPTION_SUBSCRIPTION_CREATE_STARTED_KEY = "_subscription_create_started"
_REDEMPTION_ABANDONMENT_REQUESTED_KEY = "_abandonment_requested"
_INTERNAL_REDEMPTION_METADATA_KEYS = {
    _REDEMPTION_SUBSCRIPTION_CREATE_STARTED_KEY,
    _REDEMPTION_ABANDONMENT_REQUESTED_KEY,
}
_TERMINAL_SUBSCRIPTION_STATUSES = {"canceled", "incomplete_expired"}


def _stripe_value(value: Any, key: str, default=None):
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _stripe_id(value: Any) -> str:
    if isinstance(value, str):
        return value
    return str(_stripe_value(value, "id", "") or "")


def _stripe_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=datetime_timezone.utc)
        return value
    try:
        return datetime.fromtimestamp(int(value), tz=datetime_timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError) as exc:
        raise TrialPromoError(
            "stripe_trial_invalid",
            "Stripe did not return a valid trial schedule. Please try again.",
        ) from exc


def _redemption_flag_is_set(
    redemption: TrialPromoRedemption,
    key: str,
) -> bool:
    return str((redemption.metadata or {}).get(key) or "").lower() == "true"


def _set_redemption_flag(
    redemption: TrialPromoRedemption,
    key: str,
) -> None:
    with transaction.atomic():
        locked_redemption = TrialPromoRedemption.objects.select_for_update().get(
            pk=redemption.pk,
        )
        if (
            locked_redemption.status
            != TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_PENDING
        ):
            raise TrialPromoError(
                "activation_no_longer_pending",
                "This trial activation is no longer pending.",
            )
        metadata = dict(locked_redemption.metadata or {})
        if str(metadata.get(key) or "").lower() != "true":
            metadata[key] = "true"
            TrialPromoRedemption.objects.filter(pk=redemption.pk).update(
                metadata=metadata,
                updated_at=timezone.now(),
            )
    redemption.metadata = metadata


def _confirm_customer_has_no_default_payment_method(customer_id: str) -> None:
    try:
        customer = stripe.Customer.retrieve(
            customer_id,
            api_key=stripe.api_key,
        )
    except stripe.error.StripeError as exc:
        raise TrialPromoError(
            "stripe_customer_unavailable",
            "We couldn't confirm this account's billing details. Please try again.",
        ) from exc

    invoice_settings = _stripe_value(customer, "invoice_settings") or {}
    default_payment_method = (
        _stripe_value(invoice_settings, "default_payment_method")
        or _stripe_value(customer, "default_payment_method")
    )
    default_source = _stripe_value(customer, "default_source")
    if _stripe_id(default_payment_method) or _stripe_id(default_source):
        raise TrialPromoError(
            "existing_payment_method_requires_confirmation",
            "This account already has payment details on file. Remove them in Billing "
            "or contact support before starting this no-card trial.",
        )


def _build_activation_snapshot(
    *,
    promo: TrialPromo,
    price_id: str,
    additional_price_id: str,
    coupon: DirectTrialCoupon,
) -> dict[str, str]:
    metadata = build_trial_promo_metadata(promo)
    metadata.update(
        {
            TRIAL_PROMO_REDEMPTION_PRICE_ID_KEY: str(price_id),
            TRIAL_PROMO_REDEMPTION_ADDITIONAL_PRICE_ID_KEY: str(additional_price_id),
            TRIAL_PROMO_REDEMPTION_COUPON_ID_KEY: coupon.coupon_id,
            TRIAL_PROMO_REDEMPTION_DISCOUNT_MONTHS_KEY: str(coupon.duration_in_months),
            TRIAL_PROMO_REDEMPTION_LATE_CONVERSION_GRACE_DAYS_KEY: str(
                promo.late_conversion_grace_days,
            ),
            TRIAL_PROMO_REDEMPTION_ACTIVE_UNTIL_KEY: (
                promo.active_until.isoformat() if promo.active_until else ""
            ),
            _REDEMPTION_PERCENT_OFF_KEY: (
                str(coupon.percent_off) if coupon.percent_off is not None else ""
            ),
            _REDEMPTION_AMOUNT_OFF_KEY: (
                str(coupon.amount_off) if coupon.amount_off is not None else ""
            ),
            _REDEMPTION_CURRENCY_KEY: coupon.currency,
        },
    )
    return metadata


def _parse_optional_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _activation_terms_from_redemption(
    redemption: TrialPromoRedemption,
) -> DirectTrialActivationTerms:
    metadata = redemption.metadata or {}
    try:
        plan = str(metadata[TRIAL_PROMO_META_PLAN]).strip().lower()
        price_id = str(metadata[TRIAL_PROMO_REDEMPTION_PRICE_ID_KEY]).strip()
        additional_price_id = str(
            metadata.get(TRIAL_PROMO_REDEMPTION_ADDITIONAL_PRICE_ID_KEY) or "",
        ).strip()
        trial_days = int(metadata[TRIAL_PROMO_META_TRIAL_DAYS])
        credit_amount = _parse_optional_decimal(
            metadata.get(TRIAL_PROMO_META_CREDIT_AMOUNT),
        )
        coupon_id = str(
            metadata[TRIAL_PROMO_REDEMPTION_COUPON_ID_KEY],
        ).strip()
        discount_months = int(
            metadata[TRIAL_PROMO_REDEMPTION_DISCOUNT_MONTHS_KEY],
        )
        percent_off = _parse_optional_decimal(
            metadata.get(_REDEMPTION_PERCENT_OFF_KEY),
        )
        amount_off_value = metadata.get(_REDEMPTION_AMOUNT_OFF_KEY)
        amount_off = (
            int(amount_off_value)
            if amount_off_value not in (None, "")
            else None
        )
        late_conversion_grace_days = int(
            metadata[TRIAL_PROMO_REDEMPTION_LATE_CONVERSION_GRACE_DAYS_KEY],
        )
        active_until_value = str(
            metadata.get(TRIAL_PROMO_REDEMPTION_ACTIVE_UNTIL_KEY) or "",
        ).strip()
        active_until = (
            datetime.fromisoformat(active_until_value)
            if active_until_value
            else None
        )
    except (
        InvalidOperation,
        KeyError,
        OverflowError,
        TypeError,
        ValueError,
    ) as exc:
        raise TrialPromoError(
            "activation_snapshot_invalid",
            "This trial activation cannot be safely resumed. Please contact support.",
        ) from exc

    if active_until is not None and active_until.tzinfo is None:
        active_until = active_until.replace(tzinfo=datetime_timezone.utc)
    if (
        plan not in (PlanNames.STARTUP, PlanNames.SCALE)
        or not price_id
        or not 1 <= trial_days <= 730
        or not coupon_id
        or not 1 <= discount_months <= 24
        or not 1 <= late_conversion_grace_days <= 365
        or (credit_amount is not None and credit_amount <= 0)
        or (
            (percent_off is None or percent_off <= 0)
            and (amount_off is None or amount_off <= 0)
        )
    ):
        raise TrialPromoError(
            "activation_snapshot_invalid",
            "This trial activation cannot be safely resumed. Please contact support.",
        )

    return DirectTrialActivationTerms(
        plan=plan,
        price_id=price_id,
        additional_price_id=additional_price_id,
        trial_days=trial_days,
        trial_credit_amount=credit_amount,
        coupon=DirectTrialCoupon(
            coupon_id=coupon_id,
            duration_in_months=discount_months,
            percent_off=percent_off,
            amount_off=amount_off,
            currency=str(metadata.get(_REDEMPTION_CURRENCY_KEY) or "").lower(),
        ),
        late_conversion_grace_days=late_conversion_grace_days,
        active_until=active_until,
    )


def _build_direct_trial_stripe_metadata(
    redemption: TrialPromoRedemption,
    activation_terms: DirectTrialActivationTerms,
) -> dict[str, str]:
    base_metadata = {
        str(key): str(value)
        for key, value in (redemption.metadata or {}).items()
        if key not in _INTERNAL_REDEMPTION_METADATA_KEYS
    }
    return {
        **base_metadata,
        TRIAL_PROMO_META_REDEMPTION_ID: str(redemption.pk),
        "gobii_event_id": redemption.event_id,
        "plan": activation_terms.plan,
        TRIAL_PROMO_META_DISCOUNT_COUPON: activation_terms.coupon.coupon_id,
    }


def validate_direct_trial_configuration(
    promo: TrialPromo,
    *,
    price_object,
) -> DirectTrialCoupon:
    if not settings.TRIAL_PROMO_DIRECT_ACTIVATION_ENABLED:
        raise TrialPromoError(
            "direct_activation_disabled",
            "Transparent trial activation is temporarily unavailable.",
        )
    if promo.activation_mode != TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL:
        raise TrialPromoError("invalid_activation_mode", "This campaign does not use transparent activation.")
    if promo.payment_method_required:
        raise TrialPromoError(
            "invalid_payment_configuration",
            "This campaign is not configured for a no-card trial.",
        )
    if (
        promo.no_payment_method_end_behavior
        != TrialPromoNoPaymentMethodEndBehaviorChoices.CANCEL
    ):
        raise TrialPromoError(
            "invalid_trial_end_behavior",
            "This campaign must cancel safely if no payment method is added.",
        )
    return validate_direct_trial_conversion_configuration(
        price_object=price_object,
        coupon_id=promo.conversion_coupon_id,
        discount_months=promo.discount_months,
    )


def validate_direct_trial_conversion_configuration(
    *,
    price_object,
    coupon_id: str,
    discount_months: int,
) -> DirectTrialCoupon:
    expected_coupon_id = str(coupon_id or "").strip()
    try:
        expected_discount_months = int(discount_months)
    except (TypeError, ValueError) as exc:
        raise TrialPromoError(
            "discount_mismatch",
            "This campaign's conversion discount duration is invalid.",
        ) from exc
    if not expected_coupon_id:
        raise TrialPromoError(
            "discount_missing",
            "This campaign's conversion discount is not configured.",
        )

    recurring = getattr(price_object, "recurring", None) or {}
    if not isinstance(recurring, Mapping):
        recurring = {}
    if not recurring:
        stripe_data = getattr(price_object, "stripe_data", None) or {}
        if isinstance(stripe_data, Mapping):
            recurring = stripe_data.get("recurring") or {}
    try:
        interval_count = int(recurring.get("interval_count") or 1)
    except (TypeError, ValueError):
        interval_count = 0
    if recurring.get("interval") != "month" or interval_count != 1:
        raise TrialPromoError(
            "monthly_price_required",
            "Transparent campaigns currently require a monthly plan price.",
        )

    try:
        coupon = stripe.Coupon.retrieve(
            expected_coupon_id,
            api_key=stripe.api_key,
        )
    except stripe.error.StripeError as exc:
        raise TrialPromoError(
            "discount_unavailable",
            "This campaign's conversion discount is temporarily unavailable.",
        ) from exc

    duration = str(_stripe_value(coupon, "duration", "") or "")
    try:
        duration_in_months = int(
            _stripe_value(coupon, "duration_in_months", 0) or 0,
        )
    except (TypeError, ValueError):
        duration_in_months = 0
    valid = _stripe_value(coupon, "valid", True)
    if (
        not valid
        or duration != "repeating"
        or duration_in_months != expected_discount_months
    ):
        raise TrialPromoError(
            "discount_mismatch",
            "This campaign's Stripe coupon does not match its configured discount duration.",
        )

    percent_off_raw = _stripe_value(coupon, "percent_off")
    amount_off_raw = _stripe_value(coupon, "amount_off")
    try:
        percent_off = Decimal(str(percent_off_raw)) if percent_off_raw is not None else None
        amount_off = int(amount_off_raw) if amount_off_raw is not None else None
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TrialPromoError(
            "discount_mismatch",
            "This campaign's Stripe coupon is not valid.",
        ) from exc
    coupon_currency = str(_stripe_value(coupon, "currency", "") or "").lower()
    price_currency = str(getattr(price_object, "currency", "") or "").lower()
    applies_to = _stripe_value(coupon, "applies_to") or {}
    eligible_product_ids = {
        _stripe_id(product_id)
        for product_id in (_stripe_value(applies_to, "products") or [])
        if _stripe_id(product_id)
    }
    price_product_id = _stripe_id(getattr(price_object, "product_id", None))
    if not price_product_id:
        price_product_id = _stripe_id(getattr(price_object, "product", None))
    if not price_product_id:
        price_data = getattr(price_object, "stripe_data", None) or {}
        if isinstance(price_data, Mapping):
            price_product_id = _stripe_id(price_data.get("product"))
    if (
        (percent_off is None or percent_off <= 0)
        and (amount_off is None or amount_off <= 0)
    ) or (
        amount_off is not None
        and (
            not coupon_currency
            or not price_currency
            or coupon_currency != price_currency
        )
    ) or (
        eligible_product_ids
        and (
            not price_product_id
            or price_product_id not in eligible_product_ids
        )
    ):
        raise TrialPromoError(
            "discount_mismatch",
            "This campaign's Stripe coupon is not valid for its plan price.",
        )
    return DirectTrialCoupon(
        coupon_id=_stripe_id(coupon) or expected_coupon_id,
        duration_in_months=duration_in_months,
        percent_off=percent_off,
        amount_off=amount_off,
        currency=coupon_currency,
    )


def _subscription_items(price_id: str, additional_price_id: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [{"price": price_id, "quantity": 1}]
    if additional_price_id:
        items.append({"price": additional_price_id})
    return items


def _create_or_retrieve_subscription(
    *,
    redemption: TrialPromoRedemption,
    customer_id: str,
    items: list[dict[str, Any]],
    trial_days: int,
    metadata: Mapping[str, str],
):
    if redemption.stripe_subscription_id:
        return stripe.Subscription.retrieve(
            redemption.stripe_subscription_id,
            expand=["items.data.price"],
            api_key=stripe.api_key,
        )

    # Persist this before the network call so a lost response can be distinguished
    # from a reservation that never reached Stripe.
    _set_redemption_flag(
        redemption,
        _REDEMPTION_SUBSCRIPTION_CREATE_STARTED_KEY,
    )
    subscription = stripe.Subscription.create(
        customer=customer_id,
        items=items,
        metadata=dict(metadata),
        trial_period_days=trial_days,
        trial_settings={
            "end_behavior": {
                "missing_payment_method": "cancel",
            },
        },
        payment_settings={"save_default_payment_method": "on_subscription"},
        expand=["items.data.price"],
        idempotency_key=f"direct-trial-subscription-{redemption.pk}",
        api_key=stripe.api_key,
    )
    subscription_id = _stripe_id(subscription)
    if not subscription_id:
        raise TrialPromoError(
            "stripe_subscription_invalid",
            "Stripe did not return a subscription. Please try again.",
        )
    mark_direct_trial_promo_subscription(
        redemption,
        stripe_subscription_id=subscription_id,
    )
    return subscription


def _confirm_subscription_trial_settings(subscription) -> None:
    trial_settings = _stripe_value(subscription, "trial_settings") or {}
    end_behavior = _stripe_value(trial_settings, "end_behavior") or {}
    if (
        str(_stripe_value(subscription, "status", "")) != "trialing"
        or not _stripe_value(subscription, "trial_end")
        or _stripe_value(end_behavior, "missing_payment_method") != "cancel"
    ):
        raise TrialPromoError(
            "stripe_trial_unconfirmed",
            "Stripe did not confirm the required no-card trial settings. Please try again.",
        )


def _confirm_discount_schedule(
    schedule,
    *,
    trial_end: int,
    coupon: DirectTrialCoupon,
) -> None:
    phases = _stripe_value(schedule, "phases") or []
    if not isinstance(phases, (list, tuple)) or len(phases) != 2:
        raise TrialPromoError(
            "stripe_schedule_unconfirmed",
            "Stripe did not confirm the required discount schedule. Please try again.",
        )

    trial_phase, discount_phase = phases
    try:
        trial_phase_end = int(_stripe_value(trial_phase, "end_date", 0) or 0)
        discount_phase_start = int(_stripe_value(discount_phase, "start_date", 0) or 0)
        discount_phase_end = int(_stripe_value(discount_phase, "end_date", 0) or 0)
        expected_discount_end = int(
            (
                _stripe_datetime(discount_phase_start)
                + relativedelta(months=coupon.duration_in_months)
            ).timestamp(),
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise TrialPromoError(
            "stripe_schedule_unconfirmed",
            "Stripe did not confirm the required discount schedule. Please try again.",
        ) from exc
    discounts = _stripe_value(discount_phase, "discounts") or []
    if not isinstance(discounts, (list, tuple)):
        raise TrialPromoError(
            "stripe_schedule_unconfirmed",
            "Stripe did not confirm the required discount schedule. Please try again.",
        )
    discount_coupon_ids = {
        _stripe_id(_stripe_value(discount, "coupon"))
        for discount in discounts
    }
    if (
        str(_stripe_value(schedule, "end_behavior", "")) != "release"
        or trial_phase_end != trial_end
        or bool(_stripe_value(trial_phase, "discounts") or [])
        or discount_phase_start != trial_end
        or discount_phase_end != expected_discount_end
        or coupon.coupon_id not in discount_coupon_ids
    ):
        raise TrialPromoError(
            "stripe_schedule_unconfirmed",
            "Stripe did not confirm the required discount schedule. Please try again.",
        )


def _create_or_retrieve_schedule(
    *,
    redemption: TrialPromoRedemption,
    subscription,
    items: list[dict[str, Any]],
    coupon: DirectTrialCoupon,
    metadata: Mapping[str, str],
):
    try:
        trial_start = int(
            _stripe_value(subscription, "trial_start")
            or _stripe_value(subscription, "current_period_start")
            or _stripe_value(subscription, "start_date")
        )
        trial_end = int(_stripe_value(subscription, "trial_end"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise TrialPromoError(
            "stripe_trial_invalid",
            "Stripe did not return a valid trial schedule. Please try again.",
        ) from exc

    recovered_schedule = None
    if redemption.stripe_subscription_schedule_id:
        recovered_schedule = stripe.SubscriptionSchedule.retrieve(
            redemption.stripe_subscription_schedule_id,
            api_key=stripe.api_key,
        )

    subscription_id = _stripe_id(subscription)
    if recovered_schedule is None:
        attached_schedule_id = _stripe_id(_stripe_value(subscription, "schedule"))
        if attached_schedule_id:
            recovered_schedule = stripe.SubscriptionSchedule.retrieve(
                attached_schedule_id,
                api_key=stripe.api_key,
            )
            TrialPromoRedemption.objects.filter(pk=redemption.pk).update(
                stripe_subscription_schedule_id=attached_schedule_id,
            )
            redemption.stripe_subscription_schedule_id = attached_schedule_id

    if recovered_schedule is not None:
        try:
            _confirm_discount_schedule(
                recovered_schedule,
                trial_end=trial_end,
                coupon=coupon,
            )
        except TrialPromoError:
            # Creation and phase configuration are separate Stripe operations.
            # A retry must finish configuring a schedule left between them.
            pass
        else:
            return recovered_schedule

    schedule_id = str(redemption.stripe_subscription_schedule_id or "").strip()
    if not schedule_id:
        schedule = stripe.SubscriptionSchedule.create(
            from_subscription=subscription_id,
            idempotency_key=f"direct-trial-schedule-{redemption.pk}",
            api_key=stripe.api_key,
        )
        schedule_id = _stripe_id(schedule)
        if not schedule_id:
            raise TrialPromoError(
                "stripe_schedule_invalid",
                "Stripe did not return a discount schedule. Please try again.",
            )
        TrialPromoRedemption.objects.filter(pk=redemption.pk).update(
            stripe_subscription_schedule_id=schedule_id,
        )
        redemption.stripe_subscription_schedule_id = schedule_id

    updated_schedule = stripe.SubscriptionSchedule.modify(
        schedule_id,
        end_behavior="release",
        proration_behavior="none",
        phases=[
            {
                "start_date": trial_start,
                "end_date": trial_end,
                "trial_end": trial_end,
                "items": items,
                "discounts": "",
                "proration_behavior": "none",
                "metadata": {
                    TRIAL_PROMO_META_DISCOUNT_ACTIVE: "false",
                },
            },
            {
                "start_date": trial_end,
                "iterations": coupon.duration_in_months,
                "items": items,
                "discounts": [{"coupon": coupon.coupon_id}],
                "proration_behavior": "none",
                "metadata": {
                    TRIAL_PROMO_META_DISCOUNT_ACTIVE: "true",
                    TRIAL_PROMO_META_DISCOUNT_COUPON: coupon.coupon_id,
                },
            },
        ],
        metadata=dict(metadata),
        idempotency_key=f"direct-trial-schedule-phases-{redemption.pk}",
        api_key=stripe.api_key,
    )
    return updated_schedule


def _cancel_partial_subscription(
    subscription_id: str,
    schedule_id: str = "",
) -> bool:
    if schedule_id:
        try:
            stripe.SubscriptionSchedule.cancel(
                schedule_id,
                invoice_now=False,
                prorate=False,
                api_key=stripe.api_key,
            )
        except stripe.error.StripeError:
            logger.exception(
                "Failed to cancel partial transparent trial schedule %s",
                schedule_id,
            )
        else:
            return True

    try:
        stripe.Subscription.delete(
            subscription_id,
            prorate=False,
            api_key=stripe.api_key,
        )
    except stripe.error.StripeError:
        logger.exception(
            "Failed to cancel partial transparent trial subscription %s",
            subscription_id,
        )
        return False
    return True


def _subscription_belongs_to_redemption(
    subscription,
    redemption: TrialPromoRedemption,
) -> bool:
    subscription_id = _stripe_id(subscription)
    if (
        redemption.stripe_subscription_id
        and subscription_id == redemption.stripe_subscription_id
    ):
        return True
    metadata = _stripe_value(subscription, "metadata") or {}
    return (
        str(_stripe_value(metadata, TRIAL_PROMO_META_REDEMPTION_ID, "") or "")
        == str(redemption.pk)
        or str(_stripe_value(metadata, "gobii_event_id", "") or "")
        == redemption.event_id
    )


def _inspect_customer_nonterminal_subscriptions(
    redemption: TrialPromoRedemption,
) -> tuple[Any | None, set[str]]:
    subscriptions = stripe.Subscription.list(
        customer=redemption.stripe_customer_id,
        status="all",
        limit=100,
        api_key=stripe.api_key,
    )
    campaign_subscription = None
    other_subscription_ids: set[str] = set()
    for subscription in subscriptions.auto_paging_iter():
        if (
            str(_stripe_value(subscription, "status", "") or "").lower()
            in _TERMINAL_SUBSCRIPTION_STATUSES
        ):
            continue
        subscription_id = _stripe_id(subscription)
        if _subscription_belongs_to_redemption(subscription, redemption):
            campaign_subscription = campaign_subscription or subscription
        elif subscription_id:
            other_subscription_ids.add(subscription_id)
    return campaign_subscription, other_subscription_ids


def retire_pending_direct_trial_promo_for_paid_user(
    redemption: TrialPromoRedemption,
) -> bool:
    """Resolve whether the paid plan is this campaign trial or another subscription."""
    cleanup_subscription = None
    with transaction.atomic():
        locked_redemption = TrialPromoRedemption.objects.select_for_update().get(
            pk=redemption.pk,
        )
        redemption.status = locked_redemption.status
        redemption.metadata = locked_redemption.metadata
        redemption.stripe_subscription_id = (
            locked_redemption.stripe_subscription_id
        )
        redemption.stripe_subscription_schedule_id = (
            locked_redemption.stripe_subscription_schedule_id
        )
        if (
            redemption.status
            != TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_PENDING
        ):
            return True

        abandonment_requested = _redemption_flag_is_set(
            redemption,
            _REDEMPTION_ABANDONMENT_REQUESTED_KEY,
        )
        subscription_create_started = _redemption_flag_is_set(
            redemption,
            _REDEMPTION_SUBSCRIPTION_CREATE_STARTED_KEY,
        )
        if (
            not redemption.stripe_subscription_id
            and not subscription_create_started
        ):
            mark_direct_trial_promo_failed(redemption)
            logger.info(
                "Retired unstarted transparent trial redemption %s after paid "
                "plan detection",
                redemption.pk,
            )
            return True

        try:
            campaign_subscription, other_subscription_ids = (
                _inspect_customer_nonterminal_subscriptions(redemption)
            )
        except stripe.error.StripeError as exc:
            raise TrialPromoError(
                "subscription_identity_unavailable",
                "We couldn't confirm which subscription belongs to this campaign. "
                "Please try again.",
            ) from exc

        if campaign_subscription is not None:
            campaign_subscription_id = _stripe_id(campaign_subscription)
            if (
                campaign_subscription_id
                and not redemption.stripe_subscription_id
            ):
                mark_direct_trial_promo_subscription(
                    redemption,
                    stripe_subscription_id=campaign_subscription_id,
                )

        if campaign_subscription is None and subscription_create_started:
            activation_terms = _activation_terms_from_redemption(redemption)
            metadata = _build_direct_trial_stripe_metadata(
                redemption,
                activation_terms,
            )
            items = _subscription_items(
                activation_terms.price_id,
                activation_terms.additional_price_id,
            )
            try:
                campaign_subscription = _create_or_retrieve_subscription(
                    redemption=redemption,
                    customer_id=str(
                        redemption.stripe_customer_id or "",
                    ).strip(),
                    items=items,
                    trial_days=activation_terms.trial_days,
                    metadata=metadata,
                )
            except (stripe.error.StripeError, TrialPromoError) as exc:
                logger.warning(
                    "Could not resolve indeterminate transparent trial "
                    "subscription for redemption %s: %s",
                    redemption.pk,
                    exc,
                )
                raise TrialPromoError(
                    "stripe_activation_cleanup_pending",
                    "We couldn't finish resolving an interrupted trial activation. "
                    "Please try again.",
                ) from exc

        if not other_subscription_ids and not abandonment_requested:
            return False

        _set_redemption_flag(
            redemption,
            _REDEMPTION_ABANDONMENT_REQUESTED_KEY,
        )
        cleanup_subscription = campaign_subscription

    subscription_id = (
        _stripe_id(cleanup_subscription)
        or redemption.stripe_subscription_id
    )
    if not subscription_id or not _cancel_partial_subscription(
        subscription_id,
        redemption.stripe_subscription_schedule_id,
    ):
        raise TrialPromoError(
            "stripe_activation_cleanup_pending",
            "Your interrupted trial activation needs attention. "
            "Please contact support before retrying.",
        )

    mark_direct_trial_promo_failed(redemption)
    logger.info(
        "Canceled interrupted transparent trial subscription %s for paid-user "
        "redemption %s",
        subscription_id,
        redemption.pk,
    )
    return True


def _sync_direct_trial_entitlements(
    *,
    user,
    activation_terms: DirectTrialActivationTerms,
    subscription,
) -> None:
    try:
        Subscription.sync_from_stripe_data(
            subscription,
            api_key=stripe.api_key,
        )
        plan = reconcile_user_plan_from_stripe(user) or {}
        plan_id = str(plan.get("id") or "").strip().lower()
        if plan_id != activation_terms.plan:
            raise TrialPromoError(
                "plan_sync_failed",
                "Your trial was created, but account access is still syncing. Please try again.",
            )

        monthly_credits = int(plan["monthly_task_credits"])
        credit_amount = activation_terms.trial_credit_amount
        if credit_amount is None:
            credit_amount = Decimal(monthly_credits)
            if plan_id == PlanNames.SCALE:
                credit_amount /= Decimal(4)

        trial_start = _stripe_datetime(_stripe_value(subscription, "trial_start"))
        trial_end = _stripe_datetime(_stripe_value(subscription, "trial_end"))
        TaskCreditService.grant_subscription_credits(
            user,
            plan=plan,
            invoice_id=(
                _stripe_id(_stripe_value(subscription, "latest_invoice"))
                or f"trial:{_stripe_id(subscription)}:{trial_start.date().isoformat()}"
            ),
            credit_override=credit_amount,
            expiration_date=trial_end + relativedelta(months=1),
            free_trial_start=True,
        )
    except (
        DatabaseError,
        KeyError,
        TypeError,
        ValueError,
        stripe.error.StripeError,
    ) as exc:
        raise TrialPromoError(
            "entitlement_sync_failed",
            "Your trial was created, but account access is still syncing. Please try again.",
        ) from exc


def _activation_result_from_redemption(
    redemption: TrialPromoRedemption,
) -> DirectTrialActivationResult:
    return DirectTrialActivationResult(
        redemption=redemption,
        subscription_id=redemption.stripe_subscription_id,
        schedule_id=redemption.stripe_subscription_schedule_id,
    )


def _claim_direct_trial_cleanup(
    redemption: TrialPromoRedemption,
) -> DirectTrialActivationResult | None:
    with transaction.atomic():
        locked_redemption = TrialPromoRedemption.objects.select_for_update().get(
            pk=redemption.pk,
        )
        if (
            locked_redemption.status
            == TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_COMPLETED
        ):
            return _activation_result_from_redemption(locked_redemption)
        if (
            locked_redemption.status
            != TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_PENDING
        ):
            raise TrialPromoError(
                "activation_no_longer_pending",
                "This trial activation is no longer pending.",
            )

        metadata = dict(locked_redemption.metadata or {})
        if (
            str(
                metadata.get(_REDEMPTION_ABANDONMENT_REQUESTED_KEY) or "",
            ).lower()
            == "true"
        ):
            raise TrialPromoError(
                "stripe_activation_cleanup_pending",
                "This interrupted trial activation is already being cleaned up. "
                "Please try again.",
            )
        metadata[_REDEMPTION_ABANDONMENT_REQUESTED_KEY] = "true"
        TrialPromoRedemption.objects.filter(pk=locked_redemption.pk).update(
            metadata=metadata,
            updated_at=timezone.now(),
        )
        redemption.metadata = metadata
        redemption.stripe_subscription_id = (
            locked_redemption.stripe_subscription_id
        )
        redemption.stripe_subscription_schedule_id = (
            locked_redemption.stripe_subscription_schedule_id
        )
    return None


def _finalize_direct_trial_activation(
    *,
    redemption: TrialPromoRedemption,
    user,
    activation_terms: DirectTrialActivationTerms,
    subscription,
    schedule,
) -> DirectTrialActivationResult:
    trial_end = _stripe_datetime(_stripe_value(subscription, "trial_end"))
    late_conversion_expires_at = activation_terms.active_until
    if late_conversion_expires_at is None:
        late_conversion_expires_at = trial_end + timedelta(
            days=activation_terms.late_conversion_grace_days,
        )
    coupon = activation_terms.coupon
    schedule_id = _stripe_id(schedule)

    with transaction.atomic():
        locked_redemption = TrialPromoRedemption.objects.select_for_update().get(
            pk=redemption.pk,
        )
        if (
            locked_redemption.status
            == TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_COMPLETED
        ):
            return _activation_result_from_redemption(locked_redemption)
        if (
            locked_redemption.status
            != TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_PENDING
            or _redemption_flag_is_set(
                locked_redemption,
                _REDEMPTION_ABANDONMENT_REQUESTED_KEY,
            )
        ):
            raise TrialPromoError(
                "stripe_activation_cleanup_pending",
                "This interrupted trial activation is being cleaned up. "
                "Please try again.",
            )

        _sync_direct_trial_entitlements(
            user=user,
            activation_terms=activation_terms,
            subscription=subscription,
        )
        mark_direct_trial_promo_completed(
            locked_redemption,
            stripe_subscription_id=_stripe_id(subscription),
            stripe_subscription_schedule_id=schedule_id,
            late_conversion_expires_at=late_conversion_expires_at,
            metadata={
                TRIAL_PROMO_REDEMPTION_COUPON_ID_KEY: coupon.coupon_id,
                TRIAL_PROMO_REDEMPTION_DISCOUNT_MONTHS_KEY: (
                    coupon.duration_in_months
                ),
                _REDEMPTION_PERCENT_OFF_KEY: (
                    str(coupon.percent_off)
                    if coupon.percent_off is not None
                    else ""
                ),
                _REDEMPTION_AMOUNT_OFF_KEY: coupon.amount_off,
                _REDEMPTION_CURRENCY_KEY: coupon.currency,
                "trial_end": trial_end.isoformat(),
            },
        )
        locked_redemption.refresh_from_db()

    logger.info(
        "Transparent trial activation completed for redemption %s "
        "(subscription=%s schedule=%s)",
        locked_redemption.pk,
        locked_redemption.stripe_subscription_id,
        locked_redemption.stripe_subscription_schedule_id,
    )
    return _activation_result_from_redemption(locked_redemption)


def activate_direct_trial_promo(
    *,
    promo: TrialPromo,
    user,
    price_object=None,
    price_id: str = "",
    additional_price_id: str = "",
) -> DirectTrialActivationResult:
    redemption = get_direct_trial_promo_redemption(promo=promo, user=user)
    created = False
    customer_payment_method_confirmed_absent = False
    if redemption is None:
        if price_object is None or not str(price_id).strip():
            raise TrialPromoError(
                "activation_terms_missing",
                "This campaign's billing terms are not configured.",
            )
        coupon = validate_direct_trial_configuration(
            promo,
            price_object=price_object,
        )
        try:
            customer = get_or_create_stripe_customer(user)
        except stripe.error.StripeError as exc:
            raise TrialPromoError(
                "stripe_customer_unavailable",
                "We couldn't prepare billing for this special trial. Please try again.",
            ) from exc
        customer_id = str(getattr(customer, "id", "") or "").strip()
        if not customer_id:
            raise TrialPromoError(
                "stripe_customer_unavailable",
                "We couldn't prepare billing for this special trial. Please try again.",
            )
        _confirm_customer_has_no_default_payment_method(customer_id)
        customer_payment_method_confirmed_absent = True
        snapshot = _build_activation_snapshot(
            promo=promo,
            price_id=price_id,
            additional_price_id=additional_price_id,
            coupon=coupon,
        )
        redemption, created = reserve_direct_trial_promo_redemption(
            promo=promo,
            user=user,
            stripe_customer_id=customer_id,
            metadata=snapshot,
        )

    logger.info(
        "Transparent trial activation %s for promo %s, user %s, redemption %s",
        "reserved" if created else "resumed",
        promo.pk,
        user.pk,
        redemption.pk,
    )
    if redemption.status == TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_COMPLETED:
        logger.info(
            "Transparent trial activation already completed for redemption %s",
            redemption.pk,
        )
        return _activation_result_from_redemption(redemption)
    if _redemption_flag_is_set(
        redemption,
        _REDEMPTION_ABANDONMENT_REQUESTED_KEY,
    ):
        raise TrialPromoError(
            "stripe_activation_cleanup_pending",
            "This interrupted trial activation is being cleaned up. "
            "Please try again.",
        )

    activation_terms = _activation_terms_from_redemption(redemption)
    coupon = activation_terms.coupon
    customer_id = str(redemption.stripe_customer_id or "").strip()
    if not customer_id:
        raise TrialPromoError(
            "activation_snapshot_invalid",
            "This trial activation cannot be safely resumed. Please contact support.",
        )
    if (
        not redemption.stripe_subscription_id
        and not customer_payment_method_confirmed_absent
    ):
        _confirm_customer_has_no_default_payment_method(customer_id)
    metadata = _build_direct_trial_stripe_metadata(
        redemption,
        activation_terms,
    )
    items = _subscription_items(
        activation_terms.price_id,
        activation_terms.additional_price_id,
    )

    try:
        subscription = _create_or_retrieve_subscription(
            redemption=redemption,
            customer_id=customer_id,
            items=items,
            trial_days=activation_terms.trial_days,
            metadata=metadata,
        )
        _confirm_subscription_trial_settings(subscription)
        schedule = _create_or_retrieve_schedule(
            redemption=redemption,
            subscription=subscription,
            items=items,
            coupon=coupon,
            metadata=metadata,
        )
        _confirm_discount_schedule(
            schedule,
            trial_end=int(
                _stripe_datetime(
                    _stripe_value(subscription, "trial_end"),
                ).timestamp(),
            ),
            coupon=coupon,
        )
    except (stripe.error.StripeError, TrialPromoError) as exc:
        if (
            isinstance(exc, TrialPromoError)
            and exc.code == "activation_no_longer_pending"
        ):
            redemption.refresh_from_db()
            if (
                redemption.status
                == TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_COMPLETED
            ):
                return _activation_result_from_redemption(redemption)
            raise
        if isinstance(exc, stripe.error.IdempotencyError):
            logger.warning(
                "Transparent trial activation is still in progress for "
                "redemption %s; retaining it for an idempotent retry: %s",
                redemption.pk,
                exc,
            )
            raise TrialPromoError(
                "stripe_activation_pending",
                "Your trial activation is still processing. Please try again.",
            ) from exc
        subscription_id = redemption.stripe_subscription_id
        schedule_id = redemption.stripe_subscription_schedule_id
        subscription_create_is_indeterminate = (
            not subscription_id
            and (
                isinstance(
                    exc,
                    (
                        stripe.error.APIConnectionError,
                        stripe.error.APIError,
                    ),
                )
                or (
                    isinstance(exc, TrialPromoError)
                    and exc.code == "stripe_subscription_invalid"
                )
            )
        )
        if subscription_create_is_indeterminate:
            logger.warning(
                "Transparent trial subscription creation is indeterminate for "
                "redemption %s; retaining it for an idempotent retry: %s",
                redemption.pk,
                exc,
            )
            raise TrialPromoError(
                "stripe_activation_pending",
                "We couldn't confirm whether your trial started. Please try again.",
            ) from exc
        completed_result = _claim_direct_trial_cleanup(redemption)
        if completed_result is not None:
            return completed_result
        subscription_id = redemption.stripe_subscription_id
        schedule_id = redemption.stripe_subscription_schedule_id
        cleanup_complete = not subscription_id or _cancel_partial_subscription(
            subscription_id,
            schedule_id,
        )
        if cleanup_complete:
            mark_direct_trial_promo_failed(redemption)
            message = "We couldn't start this special trial. Please try again."
        else:
            message = "Your trial activation needs attention. Please contact support before retrying."
        logger.warning(
            "Transparent trial activation failed for redemption %s "
            "(subscription=%s schedule=%s cleanup_complete=%s): %s",
            redemption.pk,
            subscription_id or "",
            schedule_id or "",
            cleanup_complete,
            exc,
        )
        raise TrialPromoError("stripe_activation_failed", message) from exc

    try:
        return _finalize_direct_trial_activation(
            redemption=redemption,
            user=user,
            activation_terms=activation_terms,
            subscription=subscription,
            schedule=schedule,
        )
    except TrialPromoError:
        logger.warning(
            "Transparent trial entitlement sync deferred for redemption %s "
            "(subscription=%s schedule=%s)",
            redemption.pk,
            _stripe_id(subscription),
            _stripe_id(schedule),
            exc_info=True,
        )
        raise
