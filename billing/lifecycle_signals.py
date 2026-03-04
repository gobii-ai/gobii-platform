import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.dispatch import Signal

logger = logging.getLogger(__name__)

TRIAL_CANCEL_SCHEDULED = "trial_cancel_scheduled"
TRIAL_ENDED_NON_RENEWAL = "trial_ended_non_renewal"
TRIAL_CONVERSION_FAILED = "trial_conversion_failed"
SUBSCRIPTION_DELINQUENCY_ENTERED = "subscription_delinquency_entered"

trial_cancel_scheduled = Signal()
trial_ended_non_renewal = Signal()
trial_conversion_failed = Signal()
subscription_delinquency_entered = Signal()

_SIGNALS_BY_NAME = {
    TRIAL_CANCEL_SCHEDULED: trial_cancel_scheduled,
    TRIAL_ENDED_NON_RENEWAL: trial_ended_non_renewal,
    TRIAL_CONVERSION_FAILED: trial_conversion_failed,
    SUBSCRIPTION_DELINQUENCY_ENTERED: subscription_delinquency_entered,
}


@dataclass(frozen=True)
class BillingLifecyclePayload:
    owner_type: str
    owner_id: str
    actor_user_id: int | None
    subscription_id: str | None = None
    invoice_id: str | None = None
    stripe_event_id: str | None = None
    subscription_status: str | None = None
    attempt_count: int | None = None
    final_attempt: bool | None = None
    occurred_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_signal_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "owner_type": self.owner_type,
            "owner_id": self.owner_id,
            "actor_user_id": self.actor_user_id,
        }
        if self.subscription_id:
            kwargs["subscription_id"] = self.subscription_id
        if self.invoice_id:
            kwargs["invoice_id"] = self.invoice_id
        if self.stripe_event_id:
            kwargs["stripe_event_id"] = self.stripe_event_id
        if self.subscription_status:
            kwargs["subscription_status"] = self.subscription_status
        if self.attempt_count is not None:
            kwargs["attempt_count"] = self.attempt_count
        if self.final_attempt is not None:
            kwargs["final_attempt"] = self.final_attempt
        if self.occurred_at is not None:
            kwargs["occurred_at"] = self.occurred_at
        if self.metadata:
            kwargs["metadata"] = self.metadata
        return kwargs


def emit_billing_lifecycle_event(
    event_name: str,
    *,
    payload: BillingLifecyclePayload,
    sender: object | None = None,
) -> None:
    signal = _SIGNALS_BY_NAME.get(event_name)
    if signal is None:
        raise ValueError(f"Unknown billing lifecycle event: {event_name}")

    sender_obj = sender or emit_billing_lifecycle_event
    # Keep both the typed payload and flattened kwargs:
    # current handlers primarily use `payload`, while future receivers can bind
    # directly to stable scalar kwargs (e.g. subscription_id) without depending
    # on dataclass shape or object unpacking.
    results = signal.send_robust(
        sender=sender_obj,
        event_name=event_name,
        payload=payload,
        **payload.to_signal_kwargs(),
    )
    for receiver, result in results:
        if isinstance(result, Exception):
            logger.error(
                "Billing lifecycle receiver %s failed for event %s: %s",
                receiver,
                event_name,
                result,
                exc_info=(type(result), result, result.__traceback__),
            )
