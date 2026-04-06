from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from api.models import EntitlementDefinition, Plan, PlanVersion, PlanVersionEntitlement
from constants.plans import PLAN_SLUG_BY_LEGACY_CODE, PlanNames


User = get_user_model()


@tag("batch_pages")
@override_settings(GOBII_PROPRIETARY_MODE=True)
class GetUserPlanApiTests(TestCase):
    def _set_monthly_task_credits(self, plan_code: str, credits: int) -> None:
        plan_slug = PLAN_SLUG_BY_LEGACY_CODE[plan_code]
        plan, _ = Plan.objects.get_or_create(
            slug=plan_slug,
            defaults={"is_org": False, "is_active": True},
        )
        plan_version, _ = PlanVersion.objects.get_or_create(
            plan=plan,
            version_code="test-api",
            defaults={
                "legacy_plan_code": plan_code,
                "is_active_for_new_subs": True,
                "display_name": plan_slug.title(),
                "description": "",
                "marketing_features": [],
            },
        )
        if plan_version.legacy_plan_code != plan_code or not plan_version.is_active_for_new_subs:
            plan_version.legacy_plan_code = plan_code
            plan_version.is_active_for_new_subs = True
            plan_version.save(update_fields=["legacy_plan_code", "is_active_for_new_subs"])

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

    @patch("console.views.reconcile_user_plan_from_stripe", return_value={"id": PlanNames.FREE})
    def test_user_plan_api_returns_db_backed_task_credit_counts(self, _mock_reconcile_plan):
        self._set_monthly_task_credits(PlanNames.STARTUP, 750)
        self._set_monthly_task_credits(PlanNames.SCALE, 12500)
        user = User.objects.create_user(
            username="plan-api@example.com",
            email="plan-api@example.com",
            password="pw",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("get_user_plan"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["startup_task_credits"], 750)
        self.assertEqual(payload["scale_task_credits"], 12500)
        self.assertEqual(payload["plan"], "free")
