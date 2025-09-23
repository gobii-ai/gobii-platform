from django.conf import settings
from django.test import TestCase, tag

from api.models import StripeConfig
from config import plans as plan_module
from config.stripe_config import get_stripe_settings, invalidate_stripe_settings_cache
from util.payments_helper import PaymentsHelper

@tag("batch_stripe_config")
class StripeConfigHelperTests(TestCase):
    def setUp(self):
        StripeConfig.objects.all().delete()
        invalidate_stripe_settings_cache()

    def test_get_stripe_settings_database(self):
        config = StripeConfig.objects.create(
            release_env=settings.GOBII_RELEASE_ENV,
            live_mode=True,
        )
        config.startup_price_id = "price_startup_test"
        config.startup_additional_task_price_id = "price_startup_extra_test"
        config.startup_product_id = "prod_startup_test"
        config.org_team_product_id = "prod_org_test"
        config.task_meter_id = "meter_task_test"
        config.task_meter_event_name = "task_test"
        config.org_task_meter_id = "meter_org_test"
        config.set_live_secret_key("sk_live_test")
        config.set_test_secret_key("sk_test_test")
        config.set_webhook_secret("whsec_test")

        invalidate_stripe_settings_cache()

        stripe_settings = get_stripe_settings(force_reload=True)

        self.assertTrue(stripe_settings.live_mode)
        self.assertEqual(stripe_settings.live_secret_key, "sk_live_test")
        self.assertEqual(stripe_settings.test_secret_key, "sk_test_test")
        self.assertEqual(stripe_settings.webhook_secret, "whsec_test")
        self.assertEqual(stripe_settings.task_meter_event_name, "task_test")
        self.assertEqual(PaymentsHelper.get_stripe_key(), "sk_live_test")

        product_id = plan_module.get_plan_product_id("startup")
        self.assertEqual(product_id, "prod_startup_test")

        plan = plan_module.get_plan_by_product_id("prod_org_test")
        self.assertIsNotNone(plan)
        self.assertEqual(plan["id"], "org_team")

    def test_set_value_persists_entries(self):
        config = StripeConfig.objects.create(
            release_env=settings.GOBII_RELEASE_ENV,
            live_mode=False,
        )
        config.startup_product_id = "prod_123"
        config.set_live_secret_key("sk_live_123")

        product_entry = config.entries.get(name="startup_product_id")
        secret_entry = config.entries.get(name="live_secret_key")

        self.assertFalse(product_entry.is_secret)
        self.assertEqual(product_entry.value_text, "prod_123")
        self.assertTrue(secret_entry.is_secret)
        self.assertTrue(secret_entry.value_encrypted)
        self.assertEqual(config.live_secret_key, "sk_live_123")
