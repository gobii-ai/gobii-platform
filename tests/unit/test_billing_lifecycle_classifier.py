from datetime import datetime, timezone as dt_timezone

from django.test import SimpleTestCase, tag

from billing.lifecycle_classifier import (
    is_subscription_delinquency_entered,
    is_trial_cancel_scheduled,
    is_trial_conversion_failure,
    is_trial_ended_non_renewal,
)


@tag("batch_billing")
class BillingLifecycleClassifierTests(SimpleTestCase):
    def test_trial_ended_non_renewal_false_when_trial_not_ended_yet(self):
        now_dt = datetime(2025, 9, 7, 12, 0, 0, tzinfo=dt_timezone.utc)
        trial_end_dt = datetime(2025, 9, 8, 8, 0, 0, tzinfo=dt_timezone.utc)
        current_period_end_dt = datetime(2025, 9, 8, 8, 0, 0, tzinfo=dt_timezone.utc)

        result = is_trial_ended_non_renewal(
            event_type="customer.subscription.deleted",
            current_status="canceled",
            previous_attributes={"status": "trialing"},
            trial_end_dt=trial_end_dt,
            current_period_end_dt=current_period_end_dt,
            now_dt=now_dt,
        )

        self.assertFalse(result)

    def test_trial_ended_non_renewal_true_for_deleted_event_without_previous_attributes(self):
        now_dt = datetime(2025, 9, 9, 12, 0, 0, tzinfo=dt_timezone.utc)
        trial_end_dt = datetime(2025, 9, 8, 8, 0, 0, tzinfo=dt_timezone.utc)
        current_period_end_dt = datetime(2025, 9, 8, 8, 0, 0, tzinfo=dt_timezone.utc)

        result = is_trial_ended_non_renewal(
            event_type="customer.subscription.deleted",
            current_status="canceled",
            previous_attributes=None,
            trial_end_dt=trial_end_dt,
            current_period_end_dt=current_period_end_dt,
            now_dt=now_dt,
        )

        self.assertTrue(result)

    def test_trial_conversion_failure_false_when_attempt_count_greater_than_one(self):
        trial_end_dt = datetime(2025, 9, 8, 8, 0, 0, tzinfo=dt_timezone.utc)
        line_period_start_dt = datetime(2025, 9, 8, 8, 0, 0, tzinfo=dt_timezone.utc)

        result = is_trial_conversion_failure(
            billing_reason="subscription_cycle",
            trial_end_dt=trial_end_dt,
            line_period_start_dt=line_period_start_dt,
            subscription_current_period_start_dt=None,
            subscription_status="past_due",
            attempt_count=2,
        )

        self.assertFalse(result)

    def test_trial_conversion_failure_false_when_not_subscription_cycle(self):
        trial_end_dt = datetime(2025, 9, 8, 8, 0, 0, tzinfo=dt_timezone.utc)
        line_period_start_dt = datetime(2025, 9, 8, 8, 0, 0, tzinfo=dt_timezone.utc)

        result = is_trial_conversion_failure(
            billing_reason="subscription_update",
            trial_end_dt=trial_end_dt,
            line_period_start_dt=line_period_start_dt,
            subscription_current_period_start_dt=None,
            subscription_status="past_due",
            attempt_count=1,
        )

        self.assertFalse(result)

    def test_subscription_delinquency_entered_false_when_previous_status_missing(self):
        result = is_subscription_delinquency_entered(
            event_type="customer.subscription.updated",
            current_status="past_due",
            previous_attributes={},
        )

        self.assertFalse(result)

    def test_trial_cancel_scheduled_true_on_trialing_cancel_flip(self):
        result = is_trial_cancel_scheduled(
            event_type="customer.subscription.updated",
            current_status="trialing",
            current_cancel_at_period_end=True,
            previous_attributes={"cancel_at_period_end": False},
        )

        self.assertTrue(result)
