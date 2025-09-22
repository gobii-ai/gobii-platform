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

    def test_get_stripe_settings_falls_back_to_env(self):
        stripe_settings = get_stripe_settings(force_reload=True)

        self.assertEqual(stripe_settings.startup_price_id, settings.STRIPE_STARTUP_PRICE_ID)
        self.assertEqual(stripe_settings.startup_product_id, settings.STRIPE_STARTUP_PRODUCT_ID)

    def test_get_stripe_settings_prefers_database(self):
        config = StripeConfig(
            release_env=settings.GOBII_RELEASE_ENV,
            live_mode=True,
            startup_price_id="price_startup_test",
            startup_additional_task_price_id="price_startup_extra_test",
            startup_product_id="prod_startup_test",
            org_team_product_id="prod_org_test",
            task_meter_id="meter_task_test",
            task_meter_event_name="task_test",
            org_task_meter_id="meter_org_test",
        )
        config.set_live_secret_key("sk_live_test")
        config.set_test_secret_key("sk_test_test")
        config.set_webhook_secret("whsec_test")
        config.save()

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
