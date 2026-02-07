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
    @patch("console.views.stripe.Subscription.modify")
    def test_cancel_subscription_sets_cancel_at_period_end(
        self,
        mock_modify,
        mock_get_active_subscription,
        mock_assign_key,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)

        resp = self.client.post(reverse("cancel_subscription"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

        mock_modify.assert_called_once()
        _, kwargs = mock_modify.call_args
        self.assertEqual(kwargs.get("cancel_at_period_end"), True)

    @patch("console.views.stripe_status")
    @patch("console.views._assign_stripe_api_key", return_value=None)
    @patch("console.views.get_active_subscription", return_value=SimpleNamespace(id="sub_123"))
    @patch("console.views.stripe.Subscription.modify")
    def test_resume_subscription_clears_cancel_at_period_end(
        self,
        mock_modify,
        mock_get_active_subscription,
        mock_assign_key,
        mock_stripe_status,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)

        resp = self.client.post(reverse("resume_subscription"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

        mock_modify.assert_called_once()
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

