import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse


@tag("batch_billing")
class ConsoleBillingCancelResumeApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="billing-cancel-owner",
            email="billing-cancel-owner@example.com",
            password="pw12345",
        )
        self.client.force_login(self.user)

    @patch("console.views.stripe_status")
    @patch("console.views._assign_stripe_api_key", return_value=None)
    @patch("console.views.get_active_subscription", return_value=SimpleNamespace(id="sub_123"))
    @patch("console.views.Analytics.track_event")
    @patch("console.views._sync_subscription_after_direct_update")
    @patch("console.views.stripe.Subscription.modify")
    def test_cancel_subscription_sets_cancel_at_period_end(
        self,
        mock_modify,
        mock_sync_subscription,
        mock_track_event,
        mock_get_active_subscription,
        mock_assign_key,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)

        resp = self.client.post(
            reverse("cancel_subscription"),
            data=json.dumps(
                {
                    "reason": "too_expensive",
                    "feedback": "Budget is too tight right now.",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

        mock_modify.assert_called_once()
        mock_sync_subscription.assert_called_once_with(mock_modify.return_value)
        _, kwargs = mock_modify.call_args
        self.assertEqual(kwargs.get("cancel_at_period_end"), True)
        mock_track_event.assert_called_once()
        _, analytics_kwargs = mock_track_event.call_args
        self.assertEqual(
            analytics_kwargs.get("properties"),
            {
                "cancel_feedback_version": 1,
                "cancel_reason_code": "too_expensive",
                "cancel_reason_text": "Budget is too tight right now.",
            },
        )

    @patch("console.views.stripe_status")
    @patch("console.views._assign_stripe_api_key", return_value=None)
    @patch("console.views.get_active_subscription", return_value=SimpleNamespace(id="sub_123"))
    @patch("console.views.Analytics.track_event")
    @patch("console.views._sync_subscription_after_direct_update")
    @patch("console.views.stripe.Subscription.modify")
    def test_cancel_subscription_sanitizes_feedback_payload(
        self,
        mock_modify,
        mock_sync_subscription,
        mock_track_event,
        mock_get_active_subscription,
        mock_assign_key,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)

        long_feedback = "x" * 520
        resp = self.client.post(
            reverse("cancel_subscription"),
            data=json.dumps(
                {
                    "reason": "OTHER",
                    "feedback": long_feedback,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

        mock_modify.assert_called_once()
        mock_sync_subscription.assert_called_once_with(mock_modify.return_value)
        mock_track_event.assert_called_once()
        _, analytics_kwargs = mock_track_event.call_args
        properties = analytics_kwargs.get("properties")
        self.assertEqual(properties.get("cancel_feedback_version"), 1)
        self.assertEqual(properties.get("cancel_reason_code"), "other")
        self.assertEqual(len(properties.get("cancel_reason_text", "")), 500)

    @patch("console.views.stripe_status")
    @patch("console.views._assign_stripe_api_key", return_value=None)
    @patch("console.views.get_active_subscription", return_value=SimpleNamespace(id="sub_123"))
    @patch("console.views.Analytics.track_event")
    @patch("util.subscription_helper.Subscription.sync_from_stripe_data", side_effect=RuntimeError("sync failure"))
    @patch("console.views.stripe.Subscription.modify")
    def test_cancel_subscription_sync_failures_are_best_effort(
        self,
        mock_modify,
        mock_subscription_sync,
        mock_track_event,
        mock_get_active_subscription,
        mock_assign_key,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)

        resp = self.client.post(
            reverse("cancel_subscription"),
            data=json.dumps(
                {
                    "reason": "too_expensive",
                    "feedback": "Sync errors should not block this response.",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))
        mock_modify.assert_called_once()
        mock_subscription_sync.assert_called_once_with(mock_modify.return_value)
        mock_track_event.assert_called_once()

    @patch("console.views.stripe_status")
    @patch("console.views._assign_stripe_api_key", return_value=None)
    @patch("console.views.get_active_subscription", return_value=SimpleNamespace(id="sub_123"))
    @patch("console.views._sync_subscription_after_direct_update")
    @patch("console.views.stripe.Subscription.modify")
    def test_resume_subscription_clears_cancel_at_period_end(
        self,
        mock_modify,
        mock_sync_subscription,
        mock_get_active_subscription,
        mock_assign_key,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)

        resp = self.client.post(reverse("resume_subscription"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

        mock_modify.assert_called_once()
        mock_sync_subscription.assert_called_once_with(mock_modify.return_value)
        _, kwargs = mock_modify.call_args
        self.assertEqual(kwargs.get("cancel_at_period_end"), False)

    @patch("console.views.stripe_status")
    @patch("console.views.get_active_subscription", return_value=None)
    def test_resume_subscription_without_active_subscription_returns_400(
        self,
        mock_get_active_subscription,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)

        resp = self.client.post(reverse("resume_subscription"))
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json().get("success", True))
