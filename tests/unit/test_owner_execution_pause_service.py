from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.models import Organization
from api.services.owner_execution_pause import (
    pause_owner_execution,
    pause_owner_execution_by_ref,
)
from util.analytics import AnalyticsEvent, AnalyticsSource


@tag("batch_owner_billing")
class OwnerExecutionPauseAnalyticsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="pause-analytics@example.com",
            email="pause-analytics@example.com",
            password="password123",
        )
        self.org = Organization.objects.create(
            name="Pause Analytics Org",
            slug="pause-analytics-org",
            plan="free",
            created_by=self.user,
        )

    @patch("api.services.owner_execution_pause.Analytics.track_event_anonymous")
    @patch("api.services.owner_execution_pause.Analytics.track_event")
    def test_direct_pause_defaults_to_na_medium(
        self,
        mock_track_event,
        mock_track_event_anonymous,
    ):
        changed = pause_owner_execution(
            self.user,
            "trial_conversion_failed",
            source="billing.lifecycle.trial_conversion_failed",
            trigger_agent_cleanup=False,
        )

        self.assertTrue(changed)
        mock_track_event_anonymous.assert_not_called()
        mock_track_event.assert_called_once()

        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.user.id)
        self.assertEqual(kwargs["event"], AnalyticsEvent.ACCOUNT_EXECUTION_PAUSED)
        self.assertEqual(kwargs["source"], AnalyticsSource.NA)
        self.assertEqual(kwargs["properties"]["owner_type"], "user")
        self.assertEqual(kwargs["properties"]["owner_id"], str(self.user.id))
        self.assertEqual(kwargs["properties"]["execution_pause_reason"], "trial_conversion_failed")
        self.assertEqual(kwargs["properties"]["pause_source"], "billing.lifecycle.trial_conversion_failed")
        self.assertFalse(kwargs["properties"]["trigger_agent_cleanup"])
        self.assertIn("paused_at", kwargs["properties"])

    @patch("api.services.owner_execution_pause.Analytics.track_event_anonymous")
    @patch("api.services.owner_execution_pause.Analytics.track_event")
    def test_pause_by_ref_uses_api_medium_for_billing_callers(
        self,
        mock_track_event,
        mock_track_event_anonymous,
    ):
        changed = pause_owner_execution_by_ref(
            "organization",
            self.org.id,
            "billing_delinquency",
            source="billing.lifecycle.subscription_delinquency_entered",
            trigger_agent_cleanup=False,
        )

        self.assertTrue(changed)
        mock_track_event_anonymous.assert_not_called()
        mock_track_event.assert_called_once()

        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.user.id)
        self.assertEqual(kwargs["event"], AnalyticsEvent.ACCOUNT_EXECUTION_PAUSED)
        self.assertEqual(kwargs["source"], AnalyticsSource.API)
        self.assertEqual(kwargs["properties"]["owner_type"], "organization")
        self.assertEqual(kwargs["properties"]["owner_id"], str(self.org.id))
        self.assertEqual(kwargs["properties"]["execution_pause_reason"], "billing_delinquency")
        self.assertEqual(
            kwargs["properties"]["pause_source"],
            "billing.lifecycle.subscription_delinquency_entered",
        )

    @patch("api.services.owner_execution_pause.Analytics.track_event")
    def test_reason_update_does_not_emit_duplicate_pause_event(self, mock_track_event):
        paused_at = timezone.now()
        billing = self.user.billing
        billing.execution_paused = True
        billing.execution_pause_reason = "billing_delinquency"
        billing.execution_paused_at = paused_at
        billing.save(
            update_fields=[
                "execution_paused",
                "execution_pause_reason",
                "execution_paused_at",
            ]
        )

        changed = pause_owner_execution(
            self.user,
            "trial_conversion_failed",
            source="billing.lifecycle.subscription_delinquency_entered",
            paused_at=paused_at,
            trigger_agent_cleanup=False,
        )

        self.assertTrue(changed)
        mock_track_event.assert_not_called()
