import logging

from waffle import switch_is_active

from constants.feature_flags import (
    OWNER_EXECUTION_PAUSE_ON_BILLING_DELINQUENCY,
    OWNER_EXECUTION_PAUSE_ON_TRIAL_CONVERSION_FAILED,
)
from api.services.owner_execution_pause import (
    EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY,
    EXECUTION_PAUSE_REASON_TRIAL_CONVERSION_FAILED,
    pause_owner_execution_by_ref,
)
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


def _billing_pause_switch_enabled(switch_name: str, *, event_name: str, payload) -> bool:
    if switch_is_active(switch_name):
        return True

    logger.info(
        "Skipping owner execution pause for %s on %s/%s because switch '%s' is disabled.",
        event_name,
        payload.owner_type,
        payload.owner_id,
        switch_name,
    )
    return False


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
        for key, value in payload.metadata.items():
            properties.setdefault(key, value)
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
    """
    This is called when a trial is canceled, but the user is still on the trial - it is NOT the end of the trial.
    """
    _track_event(
        payload=payload,
        event=AnalyticsEvent.BILLING_TRIAL_CANCEL_SCHEDULED,
        event_name=TRIAL_CANCEL_SCHEDULED,
    )


def _handle_trial_ended_non_renewal(sender, payload, **_kwargs) -> None:
    """
    This is called when a trial ends, but the user is not renewing.
    """
    _track_event(
        payload=payload,
        event=AnalyticsEvent.BILLING_TRIAL_ENDED,
        event_name=TRIAL_ENDED_NON_RENEWAL,
    )

    # TODO: Make sure we downgrade users credit to free


def _handle_trial_conversion_failed(sender, payload, **_kwargs) -> None:
    """
    This is called when a trial payment fails and enters Past Due state. User has not cancelled, though
    """
    _track_event(
        payload=payload,
        event=AnalyticsEvent.BILLING_TRIAL_PAYMENT_FAILURE,
        event_name=TRIAL_CONVERSION_FAILED,
    )

    if _billing_pause_switch_enabled(
        OWNER_EXECUTION_PAUSE_ON_TRIAL_CONVERSION_FAILED,
        event_name=TRIAL_CONVERSION_FAILED,
        payload=payload,
    ):
        pause_owner_execution_by_ref(
            payload.owner_type,
            payload.owner_id,
            EXECUTION_PAUSE_REASON_TRIAL_CONVERSION_FAILED,
            source="billing.lifecycle.trial_conversion_failed",
            paused_at=payload.occurred_at,
        )


def _handle_subscription_delinquency_entered(sender, payload, **_kwargs) -> None:
    """
    This is called when a subscription enters a delinquency state. User has not canceled, though
    """
    _track_event(
        payload=payload,
        event=AnalyticsEvent.BILLING_DELINQUENCY_ENTERED,
        event_name=SUBSCRIPTION_DELINQUENCY_ENTERED,
    )

    if _billing_pause_switch_enabled(
        OWNER_EXECUTION_PAUSE_ON_BILLING_DELINQUENCY,
        event_name=SUBSCRIPTION_DELINQUENCY_ENTERED,
        payload=payload,
    ):
        pause_owner_execution_by_ref(
            payload.owner_type,
            payload.owner_id,
            EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY,
            source="billing.lifecycle.subscription_delinquency_entered",
            paused_at=payload.occurred_at,
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
