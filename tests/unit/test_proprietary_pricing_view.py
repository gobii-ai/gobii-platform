from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from waffle.testutils import override_flag

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
        self.assertIsNone(plans[PlanNames.STARTUP]["trial_cancel_text"])
        self.assertIsNone(plans[PlanNames.SCALE]["trial_cancel_text"])

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("proprietary.views.get_stripe_settings")
    def test_unauthenticated_pricing_uses_generic_trial_cta_when_flag_enabled(
        self,
        mock_get_stripe_settings,
    ):
        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=7,
            scale_trial_days=14,
        )

        with override_flag("cta_start_free_trial", active=True):
            response = self.client.get(reverse("proprietary:pricing"))

        self.assertEqual(response.status_code, 200)
        plans = {
            plan["code"]: plan
            for plan in response.context["pricing_plans"]
        }
        self.assertEqual(plans[PlanNames.STARTUP]["cta"], "Start Free Trial")
        self.assertEqual(plans[PlanNames.SCALE]["cta"], "Start Free Trial")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("proprietary.views.get_stripe_settings")
    def test_unauthenticated_pricing_renders_no_charge_trial_text_when_flag_enabled(
        self,
        mock_get_stripe_settings,
    ):
        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=7,
            scale_trial_days=14,
        )

        with override_flag("cta_no_charge_during_trial", active=True):
            response = self.client.get(reverse("proprietary:pricing"))

        self.assertEqual(response.status_code, 200)
        plans = {
            plan["code"]: plan
            for plan in response.context["pricing_plans"]
        }
        self.assertEqual(
            plans[PlanNames.STARTUP]["trial_cancel_text"],
            "No charge if you cancel during the 7-day trial. Takes 30 seconds.",
        )
        self.assertEqual(
            plans[PlanNames.SCALE]["trial_cancel_text"],
            "No charge if you cancel during the 14-day trial. Takes 30 seconds.",
        )
        self.assertContains(response, "No charge if you cancel during the 7-day trial. Takes 30 seconds.")
        self.assertContains(response, "No charge if you cancel during the 14-day trial. Takes 30 seconds.")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("proprietary.views.get_stripe_settings")
    def test_unauthenticated_pricing_renders_trial_cancel_text_when_flag_enabled(
        self,
        mock_get_stripe_settings,
    ):
        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=7,
            scale_trial_days=14,
        )

        with override_flag("cta_pricing_cancel_text_under_btn", active=True):
            response = self.client.get(reverse("proprietary:pricing"))

        self.assertEqual(response.status_code, 200)
        plans = {
            plan["code"]: plan
            for plan in response.context["pricing_plans"]
        }
        self.assertEqual(
            plans[PlanNames.STARTUP]["trial_cancel_text"],
            "Cancel anytime during the 7-day trial",
        )
        self.assertEqual(
            plans[PlanNames.SCALE]["trial_cancel_text"],
            "Cancel anytime during the 14-day trial",
        )
        self.assertContains(response, "Cancel anytime during the 7-day trial")
        self.assertContains(response, "Cancel anytime during the 14-day trial")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("proprietary.views.get_stripe_settings")
    def test_unauthenticated_pricing_uses_unlock_copy_when_flag_enabled(
        self,
        mock_get_stripe_settings,
    ):
        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=7,
            scale_trial_days=14,
        )

        with override_flag("cta_unlock_agent_copy", active=True):
            response = self.client.get(reverse("proprietary:pricing"))

        self.assertEqual(response.status_code, 200)
        plans = {
            plan["code"]: plan
            for plan in response.context["pricing_plans"]
        }
        self.assertEqual(plans[PlanNames.STARTUP]["cta"], "Start for Free")
        self.assertEqual(plans[PlanNames.SCALE]["cta"], "Start for Free")
        self.assertEqual(plans[PlanNames.STARTUP]["trial_cancel_text"], "No charge today. Cancel anytime.")
        self.assertEqual(plans[PlanNames.SCALE]["trial_cancel_text"], "No charge today. Cancel anytime.")
        self.assertContains(response, "No charge today. Cancel anytime.")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("proprietary.views.evaluate_user_trial_eligibility", return_value=SimpleNamespace(eligible=False))
    @patch("proprietary.views.get_user_plan", return_value={"id": PlanNames.FREE})
    @patch("proprietary.views.get_stripe_settings")
    def test_free_user_pricing_cta_uses_subscribe_copy_with_prior_subscription_history(
        self,
        mock_get_stripe_settings,
        _mock_get_user_plan,
        _mock_trial_eligibility,
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

        response = self.client.get(reverse("proprietary:pricing"))

        self.assertEqual(response.status_code, 200)
        plans = {
            plan["code"]: plan
            for plan in response.context["pricing_plans"]
        }
        self.assertEqual(plans[PlanNames.STARTUP]["cta"], "Subscribe to Pro")
        self.assertEqual(plans[PlanNames.SCALE]["cta"], "Subscribe to Scale")
        self.assertIsNone(plans[PlanNames.STARTUP]["trial_cancel_text"])
        self.assertIsNone(plans[PlanNames.SCALE]["trial_cancel_text"])

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("proprietary.views.get_user_plan", return_value={"id": PlanNames.STARTUP})
    @patch("proprietary.views.evaluate_user_trial_eligibility", return_value=SimpleNamespace(eligible=True))
    @patch("proprietary.views.get_stripe_settings")
    def test_paid_plan_pricing_keeps_non_trial_cta_copy_when_unlock_flag_enabled(
        self,
        mock_get_stripe_settings,
        _mock_trial_eligibility,
        _mock_get_user_plan,
    ):
        user = get_user_model().objects.create_user(
            username="pricingstartup@example.com",
            email="pricingstartup@example.com",
            password="pw",
        )
        self.client.force_login(user)
        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=7,
            scale_trial_days=14,
        )

        with override_flag("cta_unlock_agent_copy", active=True):
            response = self.client.get(reverse("proprietary:pricing"))

        self.assertEqual(response.status_code, 200)
        plans = {
            plan["code"]: plan
            for plan in response.context["pricing_plans"]
        }
        self.assertEqual(plans[PlanNames.STARTUP]["cta"], "Current Plan")
        self.assertEqual(plans[PlanNames.SCALE]["cta"], "Upgrade to Scale")
        self.assertIsNone(plans[PlanNames.STARTUP]["trial_cancel_text"])
        self.assertIsNone(plans[PlanNames.SCALE]["trial_cancel_text"])

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("proprietary.views.evaluate_user_trial_eligibility", return_value=SimpleNamespace(eligible=False))
    @patch("proprietary.views.get_user_plan", return_value={"id": PlanNames.FREE})
    @patch("proprietary.views.get_stripe_settings")
    def test_free_user_pricing_uses_trial_copy_when_enforcement_flag_disabled(
        self,
        mock_get_stripe_settings,
        _mock_get_user_plan,
        mock_trial_eligibility,
    ):
        user = get_user_model().objects.create_user(
            username="pricingflagoff@example.com",
            email="pricingflagoff@example.com",
            password="pw",
        )
        self.client.force_login(user)

        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=7,
            scale_trial_days=14,
        )

        with override_flag("user_trial_eligibility_enforcement", active=False):
            response = self.client.get(reverse("proprietary:pricing"))

        self.assertEqual(response.status_code, 200)
        plans = {
            plan["code"]: plan
            for plan in response.context["pricing_plans"]
        }
        self.assertEqual(plans[PlanNames.STARTUP]["cta"], "Start 7-day Free Trial")
        self.assertEqual(plans[PlanNames.SCALE]["cta"], "Start 14-day Free Trial")
        mock_trial_eligibility.assert_not_called()
