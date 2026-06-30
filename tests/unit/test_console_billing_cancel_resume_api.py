import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone
from dateutil.relativedelta import relativedelta


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
    @patch("console.views._assign_stripe_api_key", return_value=None)
    @patch("console.views.get_active_subscription", return_value=SimpleNamespace(id="sub_123"))
    @patch("console.views._sync_subscription_after_direct_update")
    @patch("console.views.stripe.Subscription.modify")
    def test_resume_subscription_clears_pause_collection_when_customer_paused(
        self,
        mock_modify,
        mock_sync_subscription,
        mock_get_active_subscription,
        mock_assign_key,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)
        self.user.billing.execution_paused = True
        self.user.billing.execution_pause_reason = "customer_account_pause"
        self.user.billing.execution_paused_at = timezone.now()
        self.user.billing.execution_pause_resume_at = timezone.now()
        self.user.billing.save(
            update_fields=[
                "execution_paused",
                "execution_pause_reason",
                "execution_paused_at",
                "execution_pause_resume_at",
            ]
        )
        mock_modify.return_value = SimpleNamespace(
            id="sub_123",
            pause_collection=None,
        )

        resp = self.client.post(reverse("resume_subscription"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

        mock_modify.assert_called_once()
        _, kwargs = mock_modify.call_args
        self.assertEqual(kwargs.get("cancel_at_period_end"), False)
        self.assertEqual(kwargs.get("pause_collection"), "")
        mock_sync_subscription.assert_called_once_with(mock_modify.return_value)
        self.user.billing.refresh_from_db()
        self.assertFalse(self.user.billing.execution_paused)
        self.assertEqual(self.user.billing.execution_pause_reason, "")
        self.assertIsNone(self.user.billing.execution_pause_resume_at)

    @patch("console.views.stripe_status")
    @patch("console.views._assign_stripe_api_key", return_value=None)
    @patch("console.views.get_active_subscription", return_value=SimpleNamespace(id="sub_123"))
    @patch("console.views._sync_subscription_after_direct_update")
    @patch("console.views.stripe.Subscription.modify")
    def test_resume_subscription_cancels_scheduled_customer_pause(
        self,
        mock_modify,
        mock_sync_subscription,
        mock_get_active_subscription,
        mock_assign_key,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)
        self.user.billing.scheduled_customer_pause_effective_at = timezone.now() + timedelta(days=30)
        self.user.billing.scheduled_customer_pause_resume_at = timezone.now() + timedelta(days=60)
        self.user.billing.scheduled_customer_pause_subscription_id = "sub_123"
        self.user.billing.save(
            update_fields=[
                "scheduled_customer_pause_effective_at",
                "scheduled_customer_pause_resume_at",
                "scheduled_customer_pause_subscription_id",
            ]
        )
        mock_modify.return_value = SimpleNamespace(
            id="sub_123",
            pause_collection=None,
        )

        resp = self.client.post(reverse("resume_subscription"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

        mock_modify.assert_called_once()
        _, kwargs = mock_modify.call_args
        self.assertEqual(kwargs.get("cancel_at_period_end"), False)
        self.assertEqual(kwargs.get("pause_collection"), "")
        mock_sync_subscription.assert_called_once_with(mock_modify.return_value)
        self.user.billing.refresh_from_db()
        self.assertFalse(self.user.billing.execution_paused)
        self.assertIsNone(self.user.billing.scheduled_customer_pause_effective_at)
        self.assertIsNone(self.user.billing.scheduled_customer_pause_resume_at)
        self.assertEqual(self.user.billing.scheduled_customer_pause_subscription_id, "")

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

    @patch("console.views.stripe_status")
    @patch("console.views._assign_stripe_api_key", return_value=None)
    @patch("console.views.get_stripe_customer", return_value=SimpleNamespace(id="cus_123"))
    @patch("console.views.get_active_subscription", return_value=SimpleNamespace(id="sub_123"))
    @patch("console.views.Analytics.track_event")
    @patch("console.views._sync_subscription_after_direct_update")
    @patch("console.views.stripe.Subscription.modify")
    @patch("console.views.stripe.Subscription.retrieve")
    def test_churnkey_pause_subscription_schedules_pause_after_paid_period(
        self,
        mock_retrieve,
        mock_modify,
        mock_sync_subscription,
        mock_track_event,
        mock_get_active_subscription,
        mock_get_stripe_customer,
        mock_assign_key,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)
        period_end = (timezone.now() + timedelta(days=30)).replace(microsecond=0)
        expected_resume_at = period_end + relativedelta(months=+1)
        mock_retrieve.return_value = SimpleNamespace(
            id="sub_123",
            customer="cus_123",
            status="active",
            current_period_end=int(period_end.timestamp()),
        )
        mock_modify.return_value = SimpleNamespace(
            id="sub_123",
            customer="cus_123",
            status="active",
            current_period_end=int(period_end.timestamp()),
            pause_collection=SimpleNamespace(
                behavior="void",
                resumes_at=int(expected_resume_at.timestamp()),
            ),
        )

        resp = self.client.post(
            reverse("churnkey_pause_subscription"),
            data=json.dumps({"subscriptionId": "sub_123", "pauseDuration": 1}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload.get("success"))
        mock_retrieve.assert_called_once_with("sub_123")
        mock_modify.assert_called_once()
        args, kwargs = mock_modify.call_args
        self.assertEqual(args[0], "sub_123")
        self.assertEqual(
            kwargs.get("pause_collection"),
            {
                "behavior": "void",
                "resumes_at": int(expected_resume_at.timestamp()),
            },
        )
        mock_sync_subscription.assert_called_once_with(mock_modify.return_value)
        self.user.billing.refresh_from_db()
        self.assertFalse(self.user.billing.execution_paused)
        self.assertEqual(self.user.billing.scheduled_customer_pause_effective_at, period_end)
        self.assertEqual(self.user.billing.scheduled_customer_pause_resume_at, expected_resume_at)
        self.assertEqual(self.user.billing.scheduled_customer_pause_subscription_id, "sub_123")
        mock_track_event.assert_called_once()
        self.assertEqual(
            mock_track_event.call_args.kwargs["properties"]["update_type"],
            "subscription_pause_scheduled",
        )

    @patch("console.views.stripe_status")
    @patch("console.views.get_stripe_customer")
    @patch("console.views.stripe.Subscription.retrieve")
    def test_churnkey_pause_subscription_rejects_invalid_duration(
        self,
        mock_retrieve,
        mock_get_stripe_customer,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)

        resp = self.client.post(
            reverse("churnkey_pause_subscription"),
            data=json.dumps({"subscriptionId": "sub_123", "pauseDuration": 6}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json().get("success", True))
        mock_get_stripe_customer.assert_not_called()
        mock_retrieve.assert_not_called()

    @patch("console.views.stripe_status")
    @patch("console.views.get_stripe_customer")
    @patch("console.views.stripe.Subscription.retrieve")
    def test_churnkey_pause_subscription_rejects_unsupported_interval(
        self,
        mock_retrieve,
        mock_get_stripe_customer,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)

        resp = self.client.post(
            reverse("churnkey_pause_subscription"),
            data=json.dumps({"subscriptionId": "sub_123", "pauseDuration": 1, "pauseInterval": "day"}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json().get("success", True))
        mock_get_stripe_customer.assert_not_called()
        mock_retrieve.assert_not_called()

    @patch("console.views.stripe_status")
    @patch("console.views._assign_stripe_api_key", return_value=None)
    @patch("console.views.get_stripe_customer", return_value=SimpleNamespace(id="cus_123"))
    @patch("console.views.get_active_subscription", return_value=SimpleNamespace(id="sub_123"))
    @patch("console.views._sync_subscription_after_direct_update")
    @patch("console.views.stripe.Subscription.retrieve")
    def test_sync_billing_subscription_state_refreshes_user_subscription(
        self,
        mock_retrieve,
        mock_sync_subscription,
        mock_get_active_subscription,
        mock_get_stripe_customer,
        mock_assign_key,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)
        mock_retrieve.return_value = SimpleNamespace(id="sub_123", customer="cus_123")

        resp = self.client.post(
            reverse("sync_billing_subscription_state"),
            data=json.dumps({"subscriptionId": "sub_123"}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))
        mock_retrieve.assert_called_once_with("sub_123")
        mock_sync_subscription.assert_called_once_with(mock_retrieve.return_value)

    @patch("console.views.stripe_status")
    @patch("console.views._assign_stripe_api_key", return_value=None)
    @patch("console.views.get_stripe_customer", return_value=SimpleNamespace(id="cus_123"))
    @patch("console.views.get_active_subscription", return_value=SimpleNamespace(id="sub_123"))
    @patch("console.views._sync_subscription_after_direct_update")
    @patch("console.views.stripe.Subscription.retrieve")
    def test_sync_billing_subscription_state_marks_customer_account_pause(
        self,
        mock_retrieve,
        mock_sync_subscription,
        mock_get_active_subscription,
        mock_get_stripe_customer,
        mock_assign_key,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)
        resume_at = int((timezone.now().replace(microsecond=0)).timestamp()) + 3600
        mock_retrieve.return_value = SimpleNamespace(
            id="sub_123",
            customer="cus_123",
            pause_collection=SimpleNamespace(
                behavior="mark_uncollectible",
                resumes_at=resume_at,
            ),
        )

        resp = self.client.post(
            reverse("sync_billing_subscription_state"),
            data=json.dumps({"subscriptionId": "sub_123"}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))
        self.user.billing.refresh_from_db()
        self.assertTrue(self.user.billing.execution_paused)
        self.assertEqual(self.user.billing.execution_pause_reason, "customer_account_pause")
        self.assertIsNotNone(self.user.billing.execution_pause_resume_at)
        mock_sync_subscription.assert_called_once_with(mock_retrieve.return_value)

    @patch("console.views.stripe_status")
    @patch("console.views._assign_stripe_api_key", return_value=None)
    @patch("console.views.get_stripe_customer", return_value=SimpleNamespace(id="cus_123"))
    @patch("console.views.get_active_subscription", return_value=SimpleNamespace(id="sub_123"))
    @patch("console.views._sync_subscription_after_direct_update")
    @patch("console.views.stripe.Subscription.retrieve")
    def test_sync_billing_subscription_state_rejects_foreign_subscription(
        self,
        mock_retrieve,
        mock_sync_subscription,
        mock_get_active_subscription,
        mock_get_stripe_customer,
        mock_assign_key,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)
        mock_retrieve.return_value = SimpleNamespace(id="sub_123", customer="cus_other")

        resp = self.client.post(
            reverse("sync_billing_subscription_state"),
            data=json.dumps({"subscriptionId": "sub_123"}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json().get("success", True))
        mock_sync_subscription.assert_not_called()

    @patch("console.views.stripe_status")
    @patch("console.views._assign_stripe_api_key", return_value=None)
    @patch("console.views.get_stripe_customer", return_value=SimpleNamespace(id="cus_123"))
    @patch("console.views.get_active_subscription", return_value=SimpleNamespace(id="sub_active"))
    @patch("console.views._sync_subscription_after_direct_update")
    @patch("console.views.stripe.Subscription.retrieve")
    def test_sync_billing_subscription_state_rejects_non_active_same_customer_subscription(
        self,
        mock_retrieve,
        mock_sync_subscription,
        mock_get_active_subscription,
        mock_get_stripe_customer,
        mock_assign_key,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)
        mock_retrieve.return_value = SimpleNamespace(id="sub_old", customer="cus_123")
        self.user.billing.execution_paused = True
        self.user.billing.execution_pause_reason = "customer_account_pause"
        self.user.billing.execution_paused_at = timezone.now()
        self.user.billing.save(
            update_fields=[
                "execution_paused",
                "execution_pause_reason",
                "execution_paused_at",
            ]
        )

        resp = self.client.post(
            reverse("sync_billing_subscription_state"),
            data=json.dumps({"subscriptionId": "sub_old"}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json().get("success", True))
        mock_sync_subscription.assert_not_called()
        self.user.billing.refresh_from_db()
        self.assertTrue(self.user.billing.execution_paused)
        self.assertEqual(self.user.billing.execution_pause_reason, "customer_account_pause")

    @patch("console.views.stripe_status")
    @patch("console.views.get_stripe_customer")
    @patch("console.views.stripe.Subscription.retrieve")
    def test_sync_billing_subscription_state_rejects_non_object_payload(
        self,
        mock_retrieve,
        mock_get_stripe_customer,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)

        resp = self.client.post(
            reverse("sync_billing_subscription_state"),
            data=json.dumps(["sub_123"]),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json().get("success", True))
        mock_get_stripe_customer.assert_not_called()
        mock_retrieve.assert_not_called()
