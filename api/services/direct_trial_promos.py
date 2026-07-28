import logging
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

import stripe
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import DatabaseError
from djstripe.models import Subscription

from api.models import (
    TrialPromo,
    TrialPromoActivationModeChoices,
    TrialPromoNoPaymentMethodEndBehaviorChoices,
    TrialPromoRedemption,
    TrialPromoRedemptionStatusChoices,
)
from api.services.trial_promos import (
    TrialPromoError,
    build_trial_promo_metadata,
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

TRIAL_PROMO_META_DISCOUNT_ACTIVE = "trial_promo_discount_active"
TRIAL_PROMO_META_DISCOUNT_COUPON = "trial_promo_discount_coupon"


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
    if not promo.conversion_coupon_id:
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
            promo.conversion_coupon_id,
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
        or duration_in_months != promo.discount_months
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
    ):
        raise TrialPromoError(
            "discount_mismatch",
            "This campaign's Stripe coupon is not valid for its plan price.",
        )
    return DirectTrialCoupon(
        coupon_id=_stripe_id(coupon) or promo.conversion_coupon_id,
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
    promo: TrialPromo,
    metadata: Mapping[str, str],
):
    if redemption.stripe_subscription_id:
        return stripe.Subscription.retrieve(
            redemption.stripe_subscription_id,
            expand=["items.data.price"],
            api_key=stripe.api_key,
        )

    subscription = stripe.Subscription.create(
        customer=customer_id,
        items=items,
        metadata=dict(metadata),
        trial_period_days=promo.trial_days,
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
    if redemption.stripe_subscription_schedule_id:
        return stripe.SubscriptionSchedule.retrieve(
            redemption.stripe_subscription_schedule_id,
            api_key=stripe.api_key,
        )

    subscription_id = _stripe_id(subscription)
    attached_schedule_id = _stripe_id(_stripe_value(subscription, "schedule"))
    if attached_schedule_id:
        attached_schedule = stripe.SubscriptionSchedule.retrieve(
            attached_schedule_id,
            api_key=stripe.api_key,
        )
        TrialPromoRedemption.objects.filter(pk=redemption.pk).update(
            stripe_subscription_schedule_id=attached_schedule_id,
        )
        redemption.stripe_subscription_schedule_id = attached_schedule_id
        return attached_schedule

    schedule = stripe.SubscriptionSchedule.create(
        from_subscription=subscription_id,
        metadata=dict(metadata),
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


def _sync_direct_trial_entitlements(
    *,
    user,
    promo: TrialPromo,
    subscription,
) -> None:
    try:
        Subscription.sync_from_stripe_data(subscription)
        plan = reconcile_user_plan_from_stripe(user) or {}
        plan_id = str(plan.get("id") or "").strip().lower()
        if plan_id != str(promo.plan).strip().lower():
            raise TrialPromoError(
                "plan_sync_failed",
                "Your trial was created, but account access is still syncing. Please try again.",
            )

        monthly_credits = int(plan["monthly_task_credits"])
        credit_amount = promo.trial_credit_amount
        if credit_amount is None:
            credit_amount = Decimal(monthly_credits)
            if plan_id == PlanNames.SCALE:
                credit_amount /= Decimal(4)

        trial_start = _stripe_datetime(_stripe_value(subscription, "trial_start"))
        trial_end = _stripe_datetime(_stripe_value(subscription, "trial_end"))
        TaskCreditService.grant_subscription_credits(
            user,
            plan=plan,
            invoice_id=f"trial:{_stripe_id(subscription)}:{trial_start.date().isoformat()}",
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


def activate_direct_trial_promo(
    *,
    promo: TrialPromo,
    user,
    price_object,
    price_id: str,
    additional_price_id: str = "",
) -> DirectTrialActivationResult:
    coupon = validate_direct_trial_configuration(promo, price_object=price_object)
    try:
        customer = get_or_create_stripe_customer(user)
    except stripe.error.StripeError as exc:
        raise TrialPromoError(
            "stripe_customer_unavailable",
            "We couldn't prepare billing for this special trial. Please try again.",
        ) from exc
    base_metadata = build_trial_promo_metadata(promo)
    redemption, _created = reserve_direct_trial_promo_redemption(
        promo=promo,
        user=user,
        stripe_customer_id=customer.id,
        metadata=base_metadata,
    )
    logger.info(
        "Transparent trial activation %s for promo %s, user %s, redemption %s",
        "reserved" if _created else "resumed",
        promo.pk,
        user.pk,
        redemption.pk,
    )
    if redemption.status == TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_COMPLETED:
        logger.info(
            "Transparent trial activation already completed for redemption %s",
            redemption.pk,
        )
        return DirectTrialActivationResult(
            redemption=redemption,
            subscription_id=redemption.stripe_subscription_id,
            schedule_id=redemption.stripe_subscription_schedule_id,
        )

    metadata = {
        **base_metadata,
        **build_trial_promo_metadata(promo, redemption=redemption),
        "gobii_event_id": redemption.event_id,
        "plan": promo.plan,
        TRIAL_PROMO_META_DISCOUNT_COUPON: coupon.coupon_id,
    }
    items = _subscription_items(price_id, additional_price_id)

    try:
        subscription = _create_or_retrieve_subscription(
            redemption=redemption,
            customer_id=customer.id,
            items=items,
            promo=promo,
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
        _sync_direct_trial_entitlements(
            user=user,
            promo=promo,
            subscription=subscription,
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
    trial_end = _stripe_datetime(_stripe_value(subscription, "trial_end"))
    schedule_id = _stripe_id(schedule)
    mark_direct_trial_promo_completed(
        redemption,
        stripe_subscription_id=_stripe_id(subscription),
        stripe_subscription_schedule_id=schedule_id,
        trial_end=trial_end,
        metadata={
            "coupon_id": coupon.coupon_id,
            "discount_months": coupon.duration_in_months,
            "percent_off": str(coupon.percent_off) if coupon.percent_off is not None else "",
            "amount_off": coupon.amount_off,
            "currency": coupon.currency,
            "trial_end": trial_end.isoformat(),
        },
    )
    redemption.refresh_from_db()
    logger.info(
        "Transparent trial activation completed for redemption %s "
        "(subscription=%s schedule=%s)",
        redemption.pk,
        redemption.stripe_subscription_id,
        redemption.stripe_subscription_schedule_id,
    )
    return DirectTrialActivationResult(
        redemption=redemption,
        subscription_id=redemption.stripe_subscription_id,
        schedule_id=redemption.stripe_subscription_schedule_id,
    )
