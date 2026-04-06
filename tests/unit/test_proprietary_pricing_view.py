from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from waffle.testutils import override_flag

from api.models import EntitlementDefinition, Plan, PlanVersion, PlanVersionEntitlement
from constants.plans import PLAN_SLUG_BY_LEGACY_CODE, PlanNames


@tag("batch_pages")
class PricingPageCtaCopyTests(TestCase):
    def _set_monthly_task_credits(self, plan_code: str, credits: int) -> None:
        plan_slug = PLAN_SLUG_BY_LEGACY_CODE[plan_code]
        plan, _ = Plan.objects.get_or_create(
            slug=plan_slug,
            defaults={"is_org": False, "is_active": True},
        )
        update_fields: list[str] = []
        if plan.is_org:
            plan.is_org = False
            update_fields.append("is_org")
        if not plan.is_active:
            plan.is_active = True
            update_fields.append("is_active")
        if update_fields:
            plan.save(update_fields=update_fields)

        plan_version = (
            PlanVersion.objects
            .filter(plan=plan, is_active_for_new_subs=True)
            .order_by("-created_at")
            .first()
        )
        if plan_version is None:
            plan_version = PlanVersion.objects.create(
                plan=plan,
                version_code="test-pricing",
                legacy_plan_code=plan_code,
                is_active_for_new_subs=True,
                display_name=plan_slug.title(),
                description="",
                marketing_features=[],
            )
        elif plan_version.legacy_plan_code != plan_code:
            plan_version.legacy_plan_code = plan_code
            plan_version.save(update_fields=["legacy_plan_code"])

        entitlement, _ = EntitlementDefinition.objects.get_or_create(
            key="monthly_task_credits",
            defaults={
                "display_name": "Monthly task credits",
                "description": "Included monthly task credits.",
                "value_type": "int",
                "unit": "credits",
            },
        )
        PlanVersionEntitlement.objects.update_or_create(
            plan_version=plan_version,
            entitlement=entitlement,
            defaults={"value_int": credits},
        )

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

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("proprietary.views.get_stripe_settings")
    def test_pricing_page_uses_db_backed_task_credit_amounts(self, mock_get_stripe_settings):
        self._set_monthly_task_credits(PlanNames.STARTUP, 750)
        self._set_monthly_task_credits(PlanNames.SCALE, 12500)
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
        self.assertEqual(plans[PlanNames.STARTUP]["task_credits"], 750)
        self.assertEqual(plans[PlanNames.STARTUP]["tasks"], "750")
        self.assertEqual(plans[PlanNames.SCALE]["task_credits"], 12500)
        self.assertEqual(plans[PlanNames.SCALE]["tasks"], "12,500")
        self.assertContains(
            response,
            '<li><span class="font-semibold">750</span> tasks included</li>',
            html=True,
        )
        self.assertContains(
            response,
            '<li><span class="font-semibold">12,500</span> tasks included</li>',
            html=True,
        )
        self.assertContains(response, "$0.10 per task beyond 750")
        self.assertContains(response, "$0.04 per task beyond 12,500")
        self.assertContains(response, "750/month")
        self.assertContains(response, "12,500/month")
        self.assertContains(
            response,
            "Pro includes 750 tasks per month, then charges $0.10 for each additional task. "
            "Scale includes 12,500 tasks per month with $0.04 pricing after that.",
        )
        self.assertContains(
            response,
            "On the Pro tier, additional tasks are $0.10 each, while Scale brings that down "
            "to $0.04 once you pass the included 12,500 tasks.",
        )
