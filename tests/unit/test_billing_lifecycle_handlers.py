from unittest.mock import patch

from django.test import SimpleTestCase, tag

from constants.feature_flags import (
    OWNER_EXECUTION_PAUSE_ON_BILLING_DELINQUENCY,
    OWNER_EXECUTION_PAUSE_ON_TRIAL_CONVERSION_FAILED,
)
from billing.lifecycle_handlers import register_billing_lifecycle_handlers
from billing.lifecycle_signals import (
    BillingLifecyclePayload,
    SUBSCRIPTION_DELINQUENCY_ENTERED,
    TRIAL_CANCEL_SCHEDULED,
    TRIAL_CONVERSION_FAILED,
    TRIAL_ENDED_NON_RENEWAL,
    emit_billing_lifecycle_event,
)
from util.analytics import AnalyticsEvent


@tag("batch_billing")
class BillingLifecycleHandlerTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        register_billing_lifecycle_handlers()

    @patch("billing.lifecycle_handlers.Analytics.track_event")
    def test_trial_cancel_scheduled_tracks_event(self, mock_track_event):
        emit_billing_lifecycle_event(
            TRIAL_CANCEL_SCHEDULED,
            payload=BillingLifecyclePayload(
                owner_type="user",
                owner_id="42",
                actor_user_id=42,
                subscription_id="sub_123",
            ),
        )

        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["event"], AnalyticsEvent.BILLING_TRIAL_CANCEL_SCHEDULED)
        self.assertEqual(kwargs["user_id"], 42)
        self.assertEqual(kwargs["properties"]["stripe.subscription_id"], "sub_123")

    @patch("billing.lifecycle_handlers.Analytics.track_event")
    def test_trial_ended_non_renewal_tracks_event(self, mock_track_event):
        emit_billing_lifecycle_event(
            TRIAL_ENDED_NON_RENEWAL,
            payload=BillingLifecyclePayload(
                owner_type="user",
                owner_id="24",
                actor_user_id=24,
                subscription_id="sub_ended",
            ),
        )

        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["event"], AnalyticsEvent.BILLING_TRIAL_ENDED)
        self.assertEqual(kwargs["user_id"], 24)

    @patch("billing.lifecycle_handlers.switch_is_active", return_value=True)
    @patch("billing.lifecycle_handlers.pause_owner_execution_by_ref")
    @patch("billing.lifecycle_handlers.Analytics.track_event")
    def test_trial_conversion_failed_tracks_event(self, mock_track_event, mock_pause_owner, mock_switch):
        emit_billing_lifecycle_event(
            TRIAL_CONVERSION_FAILED,
            payload=BillingLifecyclePayload(
                owner_type="organization",
                owner_id="org-1",
                actor_user_id=7,
                subscription_id="sub_fail",
                invoice_id="in_fail",
                attempt_count=1,
                metadata={
                    "stripe.customer_id": "cus_fail",
                    "amount_due": 25.0,
                    "currency": "USD",
                    "plan": "startup",
                    "failure_reason": "The card was declined.",
                    "failure_code": "card_declined",
                    "decline_code": "do_not_honor",
                    "payment_method_type": "card",
                    "organization": True,
                    "organization_id": "org-1",
                    "organization_name": "Acme Org",
                },
            ),
        )

        mock_track_event.assert_not_called()
        mock_switch.assert_called_once_with(OWNER_EXECUTION_PAUSE_ON_TRIAL_CONVERSION_FAILED)
        mock_pause_owner.assert_called_once()

    @patch("billing.lifecycle_handlers.switch_is_active", return_value=False)
    @patch("billing.lifecycle_handlers.pause_owner_execution_by_ref")
    @patch("billing.lifecycle_handlers.Analytics.track_event")
    def test_trial_conversion_failed_does_not_pause_when_switch_disabled(
        self,
        mock_track_event,
        mock_pause_owner,
        mock_switch,
    ):
        emit_billing_lifecycle_event(
            TRIAL_CONVERSION_FAILED,
            payload=BillingLifecyclePayload(
                owner_type="organization",
                owner_id="org-1",
                actor_user_id=7,
                subscription_id="sub_fail",
                invoice_id="in_fail",
                attempt_count=1,
            ),
        )

        mock_track_event.assert_not_called()
        mock_switch.assert_called_once_with(OWNER_EXECUTION_PAUSE_ON_TRIAL_CONVERSION_FAILED)
        mock_pause_owner.assert_not_called()

    @patch("billing.lifecycle_handlers.switch_is_active", return_value=True)
    @patch("billing.lifecycle_handlers.pause_owner_execution_by_ref")
    @patch("billing.lifecycle_handlers.Analytics.track_event")
    def test_subscription_delinquency_entered_tracks_event(
        self,
        mock_track_event,
        mock_pause_owner,
        mock_switch,
    ):
        emit_billing_lifecycle_event(
            SUBSCRIPTION_DELINQUENCY_ENTERED,
            payload=BillingLifecyclePayload(
                owner_type="organization",
                owner_id="org-2",
                actor_user_id=8,
                subscription_id="sub_past_due",
                subscription_status="past_due",
            ),
        )

        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["event"], AnalyticsEvent.BILLING_DELINQUENCY_ENTERED)
        self.assertEqual(kwargs["user_id"], 8)
        self.assertEqual(kwargs["properties"]["subscription_status"], "past_due")
        mock_switch.assert_called_once_with(OWNER_EXECUTION_PAUSE_ON_BILLING_DELINQUENCY)
        mock_pause_owner.assert_called_once()

    @patch("billing.lifecycle_handlers.switch_is_active", return_value=False)
    @patch("billing.lifecycle_handlers.pause_owner_execution_by_ref")
    @patch("billing.lifecycle_handlers.Analytics.track_event")
    def test_subscription_delinquency_does_not_pause_when_switch_disabled(
        self,
        mock_track_event,
        mock_pause_owner,
        mock_switch,
    ):
        emit_billing_lifecycle_event(
            SUBSCRIPTION_DELINQUENCY_ENTERED,
            payload=BillingLifecyclePayload(
                owner_type="organization",
                owner_id="org-2",
                actor_user_id=8,
                subscription_id="sub_past_due",
                subscription_status="past_due",
            ),
        )

        mock_track_event.assert_called_once()
        mock_switch.assert_called_once_with(OWNER_EXECUTION_PAUSE_ON_BILLING_DELINQUENCY)
        mock_pause_owner.assert_not_called()
