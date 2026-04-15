from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, tag
from unittest.mock import patch

from api.admin import DailyCreditConfigAdmin
from api.models import DailyCreditConfig, PersistentAgent, BrowserUseAgent
from django.contrib.auth import get_user_model
import uuid
from api.services.persistent_agents import PersistentAgentProvisioningService
from api.services.daily_credit_settings import (
    get_daily_credit_settings_for_plan,
    get_daily_credit_settings_for_plan_version,
    invalidate_daily_credit_settings_cache,
)
from constants.plans import PlanNames
from tests.utils.llm_seed import get_intelligence_tier


@tag("agent_credit_soft_target_batch")
class DailyCreditSettingsTests(TestCase):
    def setUp(self):
        invalidate_daily_credit_settings_cache()
        User = get_user_model()
        self.user = User.objects.create_user(
            username=f"owner-{uuid.uuid4()}",
            email=f"owner-{uuid.uuid4()}@example.com",
            password="pass1234",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Multiplier Browser",
        )
        self.request_factory = RequestFactory()
        self.daily_credit_admin = DailyCreditConfigAdmin(DailyCreditConfig, AdminSite())

    def upsert_daily_credit_config(self, plan_name, **overrides):
        defaults = {
            "slider_min": Decimal("0"),
            "slider_max": Decimal("50"),
            "slider_step": Decimal("1"),
            "default_daily_credit_target": 5 if plan_name == PlanNames.FREE else 10,
            "burn_rate_threshold_per_hour": Decimal("3"),
            "offpeak_burn_rate_threshold_per_hour": Decimal("3"),
            "burn_rate_window_minutes": 60,
            "hard_limit_multiplier": Decimal("2"),
        }
        defaults.update(overrides)
        return DailyCreditConfig.objects.update_or_create(
            plan_name=plan_name,
            defaults=defaults,
        )

    def test_zero_burn_rate_threshold_is_preserved(self):
        self.upsert_daily_credit_config(
            PlanNames.FREE,
            burn_rate_threshold_per_hour=Decimal("0"),
        )

        settings = get_daily_credit_settings_for_plan(PlanNames.FREE)
        self.assertEqual(settings.burn_rate_threshold_per_hour, Decimal("0"))

    def test_slider_values_must_be_whole_numbers(self):
        config = DailyCreditConfig(
            plan_name=PlanNames.FREE,
            slider_min=Decimal("0.5"),
            slider_max=Decimal("50"),
            slider_step=Decimal("1"),
            default_daily_credit_target=5,
            burn_rate_threshold_per_hour=Decimal("3"),
            burn_rate_window_minutes=60,
            hard_limit_multiplier=Decimal("2"),
        )

        with self.assertRaises(ValidationError):
            config.full_clean()

        config.slider_min = Decimal("1")
        config.slider_max = Decimal("10.5")
        with self.assertRaises(ValidationError):
            config.full_clean()

        config.slider_max = Decimal("10")
        config.slider_step = Decimal("1.2")
        with self.assertRaises(ValidationError):
            config.full_clean()

    def test_hard_limit_multiplier_can_be_configured(self):
        self.upsert_daily_credit_config(
            PlanNames.FREE,
            hard_limit_multiplier=Decimal("1.5"),
        )

        settings = get_daily_credit_settings_for_plan(PlanNames.FREE)
        self.assertEqual(settings.hard_limit_multiplier, Decimal("1.5"))

        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Multiplier Agent",
            charter="Test multiplier",
            browser_use_agent=self.browser_agent,
            daily_credit_limit=4,
        )
        hard_limit = agent.get_daily_credit_hard_limit()
        self.assertEqual(hard_limit, Decimal("6.00"))

    def test_offpeak_burn_rate_threshold_can_be_configured(self):
        self.upsert_daily_credit_config(
            PlanNames.FREE,
            burn_rate_threshold_per_hour=Decimal("4"),
            offpeak_burn_rate_threshold_per_hour=Decimal("2.5"),
        )

        settings = get_daily_credit_settings_for_plan(PlanNames.FREE)
        self.assertEqual(settings.burn_rate_threshold_per_hour, Decimal("4"))
        self.assertEqual(settings.offpeak_burn_rate_threshold_per_hour, Decimal("2.5"))

    def test_offpeak_threshold_falls_back_to_burn_threshold_when_missing(self):
        with patch(
            "api.services.daily_credit_settings._load_settings",
            return_value={
                "by_plan_version": {},
                "by_plan_name": {
                    PlanNames.FREE: {
                        "burn_rate_threshold_per_hour": "7.5",
                    }
                },
            },
        ):
            settings = get_daily_credit_settings_for_plan_version(None, PlanNames.FREE)

        self.assertEqual(settings.burn_rate_threshold_per_hour, Decimal("7.5"))
        self.assertEqual(settings.offpeak_burn_rate_threshold_per_hour, Decimal("7.5"))

    def test_default_daily_credit_limit_scales_with_intelligence_tier(self):
        self.upsert_daily_credit_config(
            PlanNames.STARTUP,
            default_daily_credit_target=10,
        )
        with patch("config.settings.GOBII_PROPRIETARY_MODE", True):
            self.user.billing.subscription = PlanNames.STARTUP
            self.user.billing.save(update_fields=["subscription"])

            premium_tier = get_intelligence_tier("premium")
            result = PersistentAgentProvisioningService.provision(
                user=self.user,
                name="Premium Tier Agent",
                charter="Test premium tier daily credits",
                preferred_llm_tier=premium_tier,
            )
            self.assertEqual(result.agent.daily_credit_limit, 20)

    def test_default_daily_credit_target_is_db_backed(self):
        self.upsert_daily_credit_config(
            PlanNames.FREE,
            default_daily_credit_target=7,
        )

        settings = get_daily_credit_settings_for_plan(PlanNames.FREE)
        self.assertEqual(settings.default_daily_credit_target, 7)

    def test_admin_checkbox_applies_new_default_to_agents_matching_old_default(self):
        config, _created = self.upsert_daily_credit_config(
            PlanNames.FREE,
            default_daily_credit_target=5,
        )
        standard_tier = get_intelligence_tier("standard")
        premium_tier = get_intelligence_tier("premium")

        standard_browser = BrowserUseAgent.objects.create(user=self.user, name="Standard Browser")
        standard_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Standard Agent",
            charter="",
            browser_use_agent=standard_browser,
            preferred_llm_tier=standard_tier,
            daily_credit_limit=5,
        )

        premium_browser = BrowserUseAgent.objects.create(user=self.user, name="Premium Browser")
        premium_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Premium Agent",
            charter="",
            browser_use_agent=premium_browser,
            preferred_llm_tier=premium_tier,
            daily_credit_limit=10,
        )

        custom_browser = BrowserUseAgent.objects.create(user=self.user, name="Custom Browser")
        custom_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Custom Agent",
            charter="",
            browser_use_agent=custom_browser,
            preferred_llm_tier=standard_tier,
            daily_credit_limit=9,
        )

        other_user = get_user_model().objects.create_user(
            username=f"owner-{uuid.uuid4()}",
            email=f"owner-{uuid.uuid4()}@example.com",
            password="pass1234",
        )
        other_user.billing.subscription = PlanNames.STARTUP
        other_user.billing.save(update_fields=["subscription"])
        other_browser = BrowserUseAgent.objects.create(user=other_user, name="Startup Browser")
        other_agent = PersistentAgent.objects.create(
            user=other_user,
            name="Startup Agent",
            charter="",
            browser_use_agent=other_browser,
            preferred_llm_tier=standard_tier,
            daily_credit_limit=10,
        )

        request = self.request_factory.post("/admin/api/dailycreditconfig/free/change/")
        request.user = self.user
        form = type(
            "DailyCreditConfigFormStub",
            (),
            {
                "cleaned_data": {
                    "apply_default_daily_credit_target_to_matching_agents": True,
                },
                "changed_data": ["default_daily_credit_target"],
            },
        )()

        config.default_daily_credit_target = 8

        with patch.object(self.daily_credit_admin, "message_user"):
            self.daily_credit_admin.save_model(request, config, form, change=True)

        standard_agent.refresh_from_db()
        premium_agent.refresh_from_db()
        custom_agent.refresh_from_db()
        other_agent.refresh_from_db()

        self.assertEqual(standard_agent.daily_credit_limit, 8)
        self.assertEqual(premium_agent.daily_credit_limit, 16)
        self.assertEqual(custom_agent.daily_credit_limit, 9)
        self.assertEqual(other_agent.daily_credit_limit, 10)
