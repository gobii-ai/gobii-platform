from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, tag

from api.models import DailyCreditConfig, PersistentAgent, BrowserUseAgent
from django.contrib.auth import get_user_model
import uuid
from api.services.daily_credit_settings import (
    get_daily_credit_settings_for_plan,
    invalidate_daily_credit_settings_cache,
)
from constants.plans import PlanNames


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

    def test_zero_burn_rate_threshold_is_preserved(self):
        DailyCreditConfig.objects.update_or_create(
            plan_name=PlanNames.FREE,
            defaults={
                "slider_min": Decimal("0"),
                "slider_max": Decimal("50"),
                "slider_step": Decimal("1"),
                "burn_rate_threshold_per_hour": Decimal("0"),
                "burn_rate_window_minutes": 60,
                "hard_limit_multiplier": Decimal("2"),
            },
        )

        settings = get_daily_credit_settings_for_plan(PlanNames.FREE)
        self.assertEqual(settings.burn_rate_threshold_per_hour, Decimal("0"))

    def test_slider_values_must_be_whole_numbers(self):
        config = DailyCreditConfig(
            plan_name=PlanNames.FREE,
            slider_min=Decimal("0.5"),
            slider_max=Decimal("50"),
            slider_step=Decimal("1"),
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
        DailyCreditConfig.objects.update_or_create(
            plan_name=PlanNames.FREE,
            defaults={
                "slider_min": Decimal("0"),
                "slider_max": Decimal("50"),
                "slider_step": Decimal("1"),
                "burn_rate_threshold_per_hour": Decimal("3"),
                "burn_rate_window_minutes": 60,
                "hard_limit_multiplier": Decimal("1.5"),
            },
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
