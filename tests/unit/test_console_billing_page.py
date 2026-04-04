from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from constants.plans import PlanNames


@tag("batch_billing")
class BillingPageStripeRadarTests(TestCase):
    @patch("console.views.build_stripe_radar_context", return_value={"publishableKey": "pk_test_billing", "captureUrl": "/radar"})
    @patch("console.views.build_churnkey_cancel_flow_config", return_value={})
    @patch("console.views.AddonEntitlementService.get_addon_context_for_owner", return_value={})
    @patch("console.views.get_stripe_customer", return_value=None)
    @patch("console.views.get_active_subscription", return_value=None)
    @patch("console.views.reconcile_user_plan_from_stripe", return_value={"id": PlanNames.FREE, "name": "Free", "price": 0, "currency": "usd"})
    def test_billing_page_includes_stripe_radar_assets_when_configured(
        self,
        _mock_plan,
        _mock_subscription,
        _mock_customer,
        _mock_addons,
        _mock_churnkey,
        _mock_radar_context,
    ):
        user = get_user_model().objects.create_user(
            email="billing-radar@example.com",
            password="pw",
            username="billing_radar_user",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("billing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "https://js.stripe.com/dahlia/stripe.js")
        self.assertContains(response, "stripe-radar-config")
        self.assertContains(response, "pk_test_billing")
