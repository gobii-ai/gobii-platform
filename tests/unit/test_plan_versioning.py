from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.models import (
    DailyCreditConfig,
    EntitlementDefinition,
    Plan,
    PlanVersion,
    PlanVersionEntitlement,
    PlanVersionPrice,
    UserBilling,
)
from billing.plan_resolver import (
    get_owner_plan_context,
    get_plan_context_for_version,
    get_plan_version_by_price_id,
    get_plan_version_by_product_id,
)
from constants.plans import PlanNames
from api.services.daily_credit_settings import (
    get_daily_credit_settings_for_owner,
    invalidate_daily_credit_settings_cache,
)


User = get_user_model()


@tag("batch_plan_versioning")
class PlanVersionResolverTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.create(slug="startup", is_org=False, is_active=True)
        self.plan_version = PlanVersion.objects.create(
            plan=self.plan,
            version_code="v1",
            legacy_plan_code=PlanNames.STARTUP,
            is_active_for_new_subs=True,
            display_name="Startup",
            description="Starter tier",
            marketing_features=[],
        )
        self.entitlement_agents = EntitlementDefinition.objects.create(
            key="max_agents",
            display_name="Max agents",
            description="Maximum number of agents",
            value_type="int",
            unit="agents",
        )
        PlanVersionEntitlement.objects.create(
            plan_version=self.plan_version,
            entitlement=self.entitlement_agents,
            value_int=12,
        )

    def test_resolves_plan_version_by_price_id(self):
        PlanVersionPrice.objects.create(
            plan_version=self.plan_version,
            kind="base",
            price_id="price_startup_base",
            product_id="prod_startup",
            billing_interval="month",
        )

        resolved = get_plan_version_by_price_id("price_startup_base", kind="base")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.id, self.plan_version.id)

        plan_context = get_plan_context_for_version(resolved)
        self.assertEqual(plan_context.get("agent_limit"), 12)
        self.assertEqual(plan_context.get("id"), PlanNames.STARTUP)

    def test_resolves_plan_version_by_product_id(self):
        PlanVersionPrice.objects.create(
            plan_version=self.plan_version,
            kind="base",
            price_id="price_startup_fallback",
            product_id="prod_startup_fallback",
            billing_interval="month",
        )

        resolved = get_plan_version_by_product_id("prod_startup_fallback", kind="base")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.id, self.plan_version.id)

    def test_resolves_shared_price_id_with_context(self):
        org_plan = Plan.objects.create(slug="org_team", is_org=True, is_active=True)
        org_version = PlanVersion.objects.create(
            plan=org_plan,
            version_code="v1",
            legacy_plan_code=PlanNames.ORG_TEAM,
            is_active_for_new_subs=True,
            display_name="Team",
            description="Organization tier",
            marketing_features=[],
        )
        shared_price_id = "price_shared_addon"
        PlanVersionPrice.objects.create(
            plan_version=self.plan_version,
            kind="advanced_captcha_resolution",
            price_id=shared_price_id,
            product_id="prod_shared",
            billing_interval=None,
        )
        PlanVersionPrice.objects.create(
            plan_version=org_version,
            kind="advanced_captcha_resolution",
            price_id=shared_price_id,
            product_id="prod_shared",
            billing_interval=None,
        )

        resolved = get_plan_version_by_price_id(
            shared_price_id,
            kind="advanced_captcha_resolution",
            plan_id=PlanNames.STARTUP,
        )
        self.assertEqual(resolved.id, self.plan_version.id)

        resolved_org = get_plan_version_by_price_id(
            shared_price_id,
            kind="advanced_captcha_resolution",
            owner_type="organization",
        )
        self.assertEqual(resolved_org.id, org_version.id)

    def test_owner_plan_context_prefers_plan_version(self):
        user = User.objects.create_user(username="plan-user", email="plan@example.com")
        billing, _ = UserBilling.objects.get_or_create(user=user, defaults={"subscription": PlanNames.FREE})
        billing.subscription = PlanNames.FREE
        billing.plan_version = self.plan_version
        billing.save(update_fields=["subscription", "plan_version"])

        plan_context = get_owner_plan_context(user)
        self.assertEqual(plan_context.get("agent_limit"), 12)
        self.assertEqual(plan_context.get("plan_version_id"), str(self.plan_version.id))

    def test_daily_credit_settings_prefers_plan_version(self):
        invalidate_daily_credit_settings_cache()
        DailyCreditConfig.objects.create(
            plan_version=self.plan_version,
            slider_min=10,
            slider_max=100,
            slider_step=5,
            burn_rate_threshold_per_hour=3,
            burn_rate_window_minutes=60,
            hard_limit_multiplier=2,
        )

        user = User.objects.create_user(username="credit-user", email="credit@example.com")
        billing, _ = UserBilling.objects.get_or_create(user=user, defaults={"subscription": PlanNames.FREE})
        billing.subscription = PlanNames.FREE
        billing.plan_version = self.plan_version
        billing.save(update_fields=["subscription", "plan_version"])

        settings = get_daily_credit_settings_for_owner(user)
        self.assertEqual(settings.slider_min, 10)
        self.assertEqual(settings.slider_step, 5)
