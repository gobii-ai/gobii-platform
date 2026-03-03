import logging

from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

from .lifecycle_signals import (
    SUBSCRIPTION_DELINQUENCY_ENTERED,
    TRIAL_CANCEL_SCHEDULED,
    TRIAL_CONVERSION_FAILED,
    TRIAL_ENDED_NON_RENEWAL,
    subscription_delinquency_entered,
    trial_cancel_scheduled,
    trial_conversion_failed,
    trial_ended_non_renewal,
)

logger = logging.getLogger(__name__)


def _base_properties(payload) -> dict:
    properties = {
        "owner_type": payload.owner_type,
        "owner_id": payload.owner_id,
    }
    if payload.subscription_id:
        properties["stripe.subscription_id"] = payload.subscription_id
    if payload.invoice_id:
        properties["stripe.invoice_id"] = payload.invoice_id
    if payload.stripe_event_id:
        properties["stripe.event_id"] = payload.stripe_event_id
    if payload.subscription_status:
        properties["subscription_status"] = payload.subscription_status
    if payload.attempt_count is not None:
        properties["attempt_number"] = payload.attempt_count
    if payload.final_attempt is not None:
        properties["final_attempt"] = payload.final_attempt
    if payload.metadata:
        properties.update(payload.metadata)
    return properties


def _track_event(*, payload, event: AnalyticsEvent, event_name: str) -> None:
    if payload.actor_user_id is None:
        logger.info(
            "Skipping billing lifecycle analytics for %s: no actor user id for owner %s/%s",
            event_name,
            payload.owner_type,
            payload.owner_id,
        )
        return

    Analytics.track_event(
        user_id=payload.actor_user_id,
        event=event,
        source=AnalyticsSource.API,
        properties=_base_properties(payload),
    )


def _handle_trial_cancel_scheduled(sender, payload, **_kwargs) -> None:
    _track_event(
        payload=payload,
        event=AnalyticsEvent.BILLING_TRIAL_CANCEL_SCHEDULED,
        event_name=TRIAL_CANCEL_SCHEDULED,
    )


def _handle_trial_ended_non_renewal(sender, payload, **_kwargs) -> None:
    _track_event(
        payload=payload,
        event=AnalyticsEvent.BILLING_TRIAL_ENDED,
        event_name=TRIAL_ENDED_NON_RENEWAL,
    )


def _handle_trial_conversion_failed(sender, payload, **_kwargs) -> None:
    _track_event(
        payload=payload,
        event=AnalyticsEvent.BILLING_TRIAL_PAYMENT_FAILURE,
        event_name=TRIAL_CONVERSION_FAILED,
    )


def _handle_subscription_delinquency_entered(sender, payload, **_kwargs) -> None:
    _track_event(
        payload=payload,
        event=AnalyticsEvent.BILLING_DELINQUENCY_ENTERED,
        event_name=SUBSCRIPTION_DELINQUENCY_ENTERED,
    )


def register_billing_lifecycle_handlers() -> None:
    trial_cancel_scheduled.connect(
        _handle_trial_cancel_scheduled,
        dispatch_uid="billing.lifecycle.trial_cancel_scheduled",
    )
    trial_ended_non_renewal.connect(
        _handle_trial_ended_non_renewal,
        dispatch_uid="billing.lifecycle.trial_ended_non_renewal",
    )
    trial_conversion_failed.connect(
        _handle_trial_conversion_failed,
        dispatch_uid="billing.lifecycle.trial_conversion_failed",
    )
    subscription_delinquency_entered.connect(
        _handle_subscription_delinquency_entered,
        dispatch_uid="billing.lifecycle.subscription_delinquency_entered",
    )
