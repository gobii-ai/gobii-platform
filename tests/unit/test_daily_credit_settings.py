from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, tag

from api.models import DailyCreditConfig
from api.services.daily_credit_settings import (
    get_daily_credit_settings,
    invalidate_daily_credit_settings_cache,
)


@tag("agent_credit_soft_target_batch")
class DailyCreditSettingsTests(TestCase):
    def setUp(self):
        invalidate_daily_credit_settings_cache()

    def test_zero_burn_rate_threshold_is_preserved(self):
        DailyCreditConfig.objects.create(
            slider_min=Decimal("0"),
            slider_max=Decimal("50"),
            slider_step=Decimal("1"),
            burn_rate_threshold_per_hour=Decimal("0"),
            burn_rate_window_minutes=60,
        )

        settings = get_daily_credit_settings()
        self.assertEqual(settings.burn_rate_threshold_per_hour, Decimal("0"))

    def test_slider_values_must_be_whole_numbers(self):
        config = DailyCreditConfig(
            slider_min=Decimal("0.5"),
            slider_max=Decimal("50"),
            slider_step=Decimal("1"),
            burn_rate_threshold_per_hour=Decimal("3"),
            burn_rate_window_minutes=60,
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
