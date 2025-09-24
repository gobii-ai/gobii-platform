from django.conf import settings
from django.test import TestCase, tag

from api.admin_forms import StripeConfigForm
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

    def test_settings_lazy_secret_keys_follow_database(self):
        config = StripeConfig.objects.create(
            release_env=settings.GOBII_RELEASE_ENV,
            live_mode=True,
        )
        config.set_live_secret_key("sk_live_lazy_1")
        config.set_test_secret_key("sk_test_lazy_1")

        invalidate_stripe_settings_cache()

        self.assertEqual(settings.STRIPE_LIVE_SECRET_KEY, "sk_live_lazy_1")
        self.assertEqual(settings.STRIPE_TEST_SECRET_KEY, "sk_test_lazy_1")
        self.assertIsInstance(settings.STRIPE_LIVE_SECRET_KEY, str)

        config.set_live_secret_key("sk_live_lazy_2")
        config.set_test_secret_key("sk_test_lazy_2")

        invalidate_stripe_settings_cache()

        self.assertEqual(settings.STRIPE_LIVE_SECRET_KEY, "sk_live_lazy_2")
        self.assertEqual(settings.STRIPE_TEST_SECRET_KEY, "sk_test_lazy_2")

    def test_settings_lazy_secret_keys_fallback_without_database(self):
        StripeConfig.objects.all().delete()

        with self.settings(
            STRIPE_LIVE_SECRET_FALLBACK="fallback_live",
            STRIPE_TEST_SECRET_FALLBACK="fallback_test",
        ):
            invalidate_stripe_settings_cache()

            self.assertEqual(settings.STRIPE_LIVE_SECRET_KEY, "fallback_live")
            self.assertEqual(settings.STRIPE_TEST_SECRET_KEY, "fallback_test")

    def test_form_initializes_org_team_fields(self):
        config = StripeConfig.objects.create(
            release_env=settings.GOBII_RELEASE_ENV,
            live_mode=False,
        )
        config.org_team_price_id = "price_org"
        config.org_team_additional_task_price_id = "price_org_extra"
        config.org_team_task_meter_id = "meter_org_team"
        config.org_team_task_meter_event_name = "task_event_org_team"

        form = StripeConfigForm(instance=config)

        self.assertEqual(form.fields["org_team_price_id"].initial, "price_org")
        self.assertEqual(
            form.fields["org_team_additional_task_price_id"].initial,
            "price_org_extra",
        )
        self.assertEqual(form.fields["org_team_task_meter_id"].initial, "meter_org_team")
        self.assertEqual(
            form.fields["org_team_task_meter_event_name"].initial,
            "task_event_org_team",
        )

    def test_form_saves_org_team_fields(self):
        config = StripeConfig.objects.create(
            release_env=settings.GOBII_RELEASE_ENV,
            live_mode=False,
        )

        form = StripeConfigForm(
            data={
                "release_env": settings.GOBII_RELEASE_ENV,
                "org_team_price_id": "price_org_form",
                "org_team_additional_task_price_id": " price_org_extra_form ",
                "org_team_task_meter_id": "meter_org_team_form",
                "org_team_task_meter_event_name": "meter_event_form",
            },
            instance=config,
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        config.refresh_from_db()

        self.assertEqual(config.org_team_price_id, "price_org_form")
        self.assertEqual(config.org_team_additional_task_price_id, "price_org_extra_form")
        self.assertEqual(config.org_team_task_meter_id, "meter_org_team_form")
        self.assertEqual(config.org_team_task_meter_event_name, "meter_event_form")
