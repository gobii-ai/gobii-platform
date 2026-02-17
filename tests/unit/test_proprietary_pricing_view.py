from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from constants.plans import PlanNames


@tag("batch_pages")
class PricingPageCtaCopyTests(TestCase):
    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("proprietary.views.get_stripe_settings")
    def test_unauthenticated_pricing_cta_uses_trial_copy(self, mock_get_stripe_settings):
        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=7,
            scale_trial_days=14,
        )

        response = self.client.get(reverse("proprietary:pricing"))

        self.assertEqual(response.status_code, 200)
        plans = {
            plan["code"]: plan
            for plan in response.context["pricing_plans"]
        }
        self.assertEqual(plans[PlanNames.STARTUP]["cta"], "Start 7-day Free Trial")
        self.assertEqual(plans[PlanNames.SCALE]["cta"], "Start 14-day Free Trial")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("proprietary.views.customer_has_any_individual_subscription", return_value=True)
    @patch("proprietary.views.get_stripe_customer")
    @patch("proprietary.views.get_user_plan", return_value={"id": PlanNames.FREE})
    @patch("proprietary.views.get_stripe_settings")
    def test_free_user_pricing_cta_uses_subscribe_copy_with_prior_subscription_history(
        self,
        mock_get_stripe_settings,
        _mock_get_user_plan,
        mock_get_stripe_customer,
        _mock_customer_has_history,
    ):
        user = get_user_model().objects.create_user(
            username="pricingfree@example.com",
            email="pricingfree@example.com",
            password="pw",
        )
        self.client.force_login(user)

        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=7,
            scale_trial_days=14,
        )
        mock_get_stripe_customer.return_value = SimpleNamespace(id="cus_123")

        response = self.client.get(reverse("proprietary:pricing"))

        self.assertEqual(response.status_code, 200)
        plans = {
            plan["code"]: plan
            for plan in response.context["pricing_plans"]
        }
        self.assertEqual(plans[PlanNames.STARTUP]["cta"], "Subscribe to Pro")
        self.assertEqual(plans[PlanNames.SCALE]["cta"], "Subscribe to Scale")
