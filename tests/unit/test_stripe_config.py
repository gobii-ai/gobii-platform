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

    def test_get_stripe_settings_prefers_env_secrets(self):
        config = StripeConfig.objects.create(
            release_env=settings.GOBII_RELEASE_ENV,
            live_mode=True,
        )
        config.startup_price_id = "price_startup_test"
        config.startup_additional_task_price_id = "price_startup_extra_test"
        config.startup_product_id = "prod_startup_test"
        config.startup_dedicated_ip_product_id = "prod_startup_dedicated_test"
        config.startup_dedicated_ip_price_id = "price_startup_dedicated_test"
        config.org_team_product_id = "prod_org_test"
        config.org_team_price_id = "price_org_test"
        config.org_team_additional_task_price_id = "price_org_additional_test"
        config.org_team_dedicated_ip_product_id = "prod_org_dedicated_test"
        config.org_team_dedicated_ip_price_id = "price_org_dedicated_test"
        config.task_meter_id = "meter_task_test"
        config.task_meter_event_name = "task_test"
        config.org_task_meter_id = "meter_org_test"
        config.org_team_task_meter_id = "meter_org_team_test"
        config.org_team_task_meter_event_name = "task_org_team_test"
        config.set_webhook_secret("whsec_test")

        # Simulate legacy database secrets that should now be ignored
        config.set_value("live_secret_key", "sk_live_db", is_secret=True)
        config.set_value("test_secret_key", "sk_test_db", is_secret=True)

        with self.settings(STRIPE_LIVE_SECRET_KEY="sk_live_env", STRIPE_TEST_SECRET_KEY="sk_test_env"):
            invalidate_stripe_settings_cache()
            stripe_settings = get_stripe_settings(force_reload=True)

        self.assertTrue(stripe_settings.live_mode)
        self.assertEqual(stripe_settings.live_secret_key, "sk_live_env")
        self.assertEqual(stripe_settings.test_secret_key, "sk_test_env")
        self.assertEqual(stripe_settings.webhook_secret, "whsec_test")
        self.assertEqual(stripe_settings.task_meter_event_name, "task_test")
        self.assertEqual(stripe_settings.org_team_price_id, "price_org_test")
        self.assertEqual(stripe_settings.org_team_additional_task_price_id, "price_org_additional_test")
        self.assertEqual(stripe_settings.startup_dedicated_ip_product_id, "prod_startup_dedicated_test")
        self.assertEqual(stripe_settings.startup_dedicated_ip_price_id, "price_startup_dedicated_test")
        self.assertEqual(stripe_settings.org_team_dedicated_ip_product_id, "prod_org_dedicated_test")
        self.assertEqual(stripe_settings.org_team_dedicated_ip_price_id, "price_org_dedicated_test")
        self.assertEqual(stripe_settings.org_team_task_meter_id, "meter_org_team_test")
        self.assertEqual(stripe_settings.org_team_task_meter_event_name, "task_org_team_test")
        self.assertEqual(PaymentsHelper.get_stripe_key(), "sk_live_env")

        product_id = plan_module.get_plan_product_id("startup")
        self.assertEqual(product_id, "prod_startup_test")

        plan = plan_module.get_plan_by_product_id("prod_org_test")
        self.assertIsNotNone(plan)
        self.assertEqual(plan["id"], "org_team")
        self.assertEqual(
            plan_module.PLAN_CONFIG["startup"]["dedicated_ip_product_id"],
            "prod_startup_dedicated_test",
        )
        self.assertEqual(
            plan_module.PLAN_CONFIG["startup"]["dedicated_ip_price_id"],
            "price_startup_dedicated_test",
        )
        self.assertEqual(
            plan_module.PLAN_CONFIG["org_team"]["dedicated_ip_product_id"],
            "prod_org_dedicated_test",
        )
        self.assertEqual(
            plan_module.PLAN_CONFIG["org_team"]["dedicated_ip_price_id"],
            "price_org_dedicated_test",
        )

    def test_webhook_secret_persists_entries(self):
        config = StripeConfig.objects.create(
            release_env=settings.GOBII_RELEASE_ENV,
            live_mode=False,
        )
        config.startup_product_id = "prod_123"
        config.set_webhook_secret("whsec_123")

        product_entry = config.entries.get(name="startup_product_id")
        secret_entry = config.entries.get(name="webhook_secret")

        self.assertFalse(product_entry.is_secret)
        self.assertEqual(product_entry.value_text, "prod_123")
        self.assertTrue(secret_entry.is_secret)
        self.assertTrue(secret_entry.value_encrypted)
        self.assertEqual(config.webhook_secret, "whsec_123")
