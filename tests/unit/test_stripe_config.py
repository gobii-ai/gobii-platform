import os
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, tag

from api.admin_forms import StripeConfigForm
from api.models import StripeConfig
from config import plans as plan_module
from config.stripe_config import get_stripe_settings, invalidate_stripe_settings_cache
from util.payments_helper import PaymentsHelper
from constants.plans import PlanNames

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
        config.startup_additional_task_price_id = "price_startup_adhoc_test"
        config.startup_task_pack_product_id = "prod_startup_task_pack_test"
        config.startup_task_pack_price_ids = ["price_startup_task_pack_test"]
        config.startup_product_id = "prod_startup_test"
        config.startup_dedicated_ip_product_id = "prod_startup_dedicated_test"
        config.startup_dedicated_ip_price_id = "price_startup_dedicated_test"
        config.scale_price_id = "price_scale_test"
        config.scale_additional_task_price_id = "price_scale_adhoc_test"
        config.scale_task_pack_product_id = "prod_scale_task_pack_test"
        config.scale_task_pack_price_ids = ["price_scale_task_pack_test"]
        config.scale_product_id = "prod_scale_test"
        config.scale_dedicated_ip_product_id = "prod_scale_dedicated_test"
        config.scale_dedicated_ip_price_id = "price_scale_dedicated_test"
        config.org_team_product_id = "prod_org_test"
        config.org_team_price_id = "price_org_test"
        config.org_team_additional_task_product_id = "prod_org_additional_test"
        config.org_team_additional_task_price_id = "price_org_adhoc_test"
        config.org_team_task_pack_product_id = "prod_org_task_pack_test"
        config.org_team_task_pack_price_ids = ["price_org_task_pack_test"]
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
        self.assertEqual(stripe_settings.startup_task_pack_product_id, "prod_startup_task_pack_test")
        self.assertEqual(stripe_settings.startup_task_pack_price_ids, ("price_startup_task_pack_test",))
        self.assertEqual(stripe_settings.org_team_task_pack_product_id, "prod_org_task_pack_test")
        self.assertEqual(stripe_settings.org_team_task_pack_price_ids, ("price_org_task_pack_test",))
        self.assertEqual(stripe_settings.org_team_additional_task_price_id, "price_org_adhoc_test")
        self.assertEqual(stripe_settings.org_team_additional_task_product_id, "prod_org_additional_test")
        self.assertEqual(stripe_settings.startup_additional_task_price_id, "price_startup_adhoc_test")
        self.assertEqual(stripe_settings.startup_dedicated_ip_product_id, "prod_startup_dedicated_test")
        self.assertEqual(stripe_settings.startup_dedicated_ip_price_id, "price_startup_dedicated_test")
        self.assertEqual(stripe_settings.scale_price_id, "price_scale_test")
        self.assertEqual(stripe_settings.scale_task_pack_product_id, "prod_scale_task_pack_test")
        self.assertEqual(stripe_settings.scale_task_pack_price_ids, ("price_scale_task_pack_test",))
        self.assertEqual(stripe_settings.scale_additional_task_price_id, "price_scale_adhoc_test")
        self.assertEqual(stripe_settings.scale_product_id, "prod_scale_test")
        self.assertEqual(stripe_settings.scale_dedicated_ip_product_id, "prod_scale_dedicated_test")
        self.assertEqual(stripe_settings.scale_dedicated_ip_price_id, "price_scale_dedicated_test")
        self.assertEqual(stripe_settings.org_team_dedicated_ip_product_id, "prod_org_dedicated_test")
        self.assertEqual(stripe_settings.org_team_dedicated_ip_price_id, "price_org_dedicated_test")
        self.assertEqual(stripe_settings.org_team_task_meter_id, "meter_org_team_test")
        self.assertEqual(stripe_settings.org_team_task_meter_event_name, "task_org_team_test")
        self.assertEqual(PaymentsHelper.get_stripe_key(), "sk_live_env")

        product_id = plan_module.get_plan_product_id(PlanNames.STARTUP)
        self.assertEqual(product_id, "prod_startup_test")

        scale_product_id = plan_module.get_plan_product_id(PlanNames.SCALE)
        self.assertEqual(scale_product_id, "prod_scale_test")

        plan = plan_module.get_plan_by_product_id("prod_org_test")
        self.assertIsNotNone(plan)
        self.assertEqual(plan["id"], "org_team")
        scale_plan = plan_module.get_plan_by_product_id("prod_scale_test")
        self.assertIsNotNone(scale_plan)
        self.assertEqual(scale_plan["id"], PlanNames.SCALE)
        self.assertEqual(
            plan_module.PLAN_CONFIG[PlanNames.STARTUP]["dedicated_ip_product_id"],
            "prod_startup_dedicated_test",
        )
        self.assertEqual(
            plan_module.PLAN_CONFIG[PlanNames.STARTUP]["dedicated_ip_price_id"],
            "price_startup_dedicated_test",
        )
        self.assertEqual(
            plan_module.PLAN_CONFIG[PlanNames.ORG_TEAM]["dedicated_ip_product_id"],
            "prod_org_dedicated_test",
        )
        self.assertEqual(
            plan_module.PLAN_CONFIG[PlanNames.ORG_TEAM]["dedicated_ip_price_id"],
            "price_org_dedicated_test",
        )
        self.assertEqual(
            plan_module.PLAN_CONFIG[PlanNames.SCALE]["dedicated_ip_product_id"],
            "prod_scale_dedicated_test",
        )
        self.assertEqual(
            plan_module.PLAN_CONFIG[PlanNames.SCALE]["dedicated_ip_price_id"],
            "price_scale_dedicated_test",
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

    def test_get_stripe_settings_uses_env_checkout_defaults_when_db_entries_are_missing(self):
        StripeConfig.objects.create(
            release_env=settings.GOBII_RELEASE_ENV,
            live_mode=False,
        )

        with patch.dict(
            os.environ,
            {
                "STRIPE_CHECKOUT_BILLING_ADDRESS_COLLECTION": "required",
                "STRIPE_CHECKOUT_NAME_COLLECTION_INDIVIDUAL_ENABLED": "true",
            },
            clear=False,
        ):
            invalidate_stripe_settings_cache()
            stripe_settings = get_stripe_settings(force_reload=True)

        self.assertEqual(stripe_settings.checkout_billing_address_collection, "required")
        self.assertTrue(stripe_settings.checkout_name_collection_individual_enabled)

    def test_stripe_config_form_saves_checkout_collection_fields(self):
        config = StripeConfig.objects.create(
            release_env=settings.GOBII_RELEASE_ENV,
            live_mode=False,
        )

        form_data = {
            "release_env": settings.GOBII_RELEASE_ENV,
            "live_mode": "on",
            "checkout_billing_address_collection": "required",
            "checkout_name_collection_individual_enabled": "on",
            "webhook_secret": "",
            "clear_webhook_secret": "",
        }

        form = StripeConfigForm(data=form_data, instance=config)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        config.refresh_from_db()
        self.assertEqual(config.checkout_billing_address_collection, "required")
        self.assertTrue(config.checkout_name_collection_individual_enabled)

    def test_stripe_config_form_preserves_checkout_env_defaults_when_unchanged(self):
        config = StripeConfig.objects.create(
            release_env=settings.GOBII_RELEASE_ENV,
            live_mode=False,
        )

        form_data = {
            "release_env": settings.GOBII_RELEASE_ENV,
            "live_mode": "on",
            "checkout_billing_address_collection": "auto",
            "webhook_secret": "",
            "clear_webhook_secret": "",
        }

        with patch.dict(
            os.environ,
            {
                "STRIPE_CHECKOUT_BILLING_ADDRESS_COLLECTION": "required",
                "STRIPE_CHECKOUT_NAME_COLLECTION_INDIVIDUAL_ENABLED": "true",
            },
            clear=False,
        ):
            form = StripeConfigForm(data=form_data, instance=config)
            self.assertTrue(form.is_valid(), form.errors)
            form.save()

            config.refresh_from_db()
            self.assertFalse(config.has_value("checkout_billing_address_collection"))
            self.assertFalse(config.has_value("checkout_name_collection_individual_enabled"))

            invalidate_stripe_settings_cache()
            stripe_settings = get_stripe_settings(force_reload=True)

        self.assertEqual(stripe_settings.checkout_billing_address_collection, "required")
        self.assertTrue(stripe_settings.checkout_name_collection_individual_enabled)

    def test_stripe_config_form_saves_dedicated_ip_fields(self):
        config = StripeConfig.objects.create(
            release_env=settings.GOBII_RELEASE_ENV,
            live_mode=False,
        )

        form_data = {
            "release_env": settings.GOBII_RELEASE_ENV,
            "live_mode": "on",
            "webhook_secret": "",
            "clear_webhook_secret": "",
            "startup_dedicated_ip_product_id": "prod_startup_dedicated_form",
            "startup_dedicated_ip_price_id": "price_startup_dedicated_form",
            "scale_dedicated_ip_product_id": "prod_scale_dedicated_form",
            "scale_dedicated_ip_price_id": "price_scale_dedicated_form",
            "org_team_dedicated_ip_product_id": "prod_org_dedicated_form",
            "org_team_dedicated_ip_price_id": "price_org_dedicated_form",
        }

        form = StripeConfigForm(data=form_data, instance=config)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        config.refresh_from_db()
        self.assertEqual(config.startup_dedicated_ip_product_id, "prod_startup_dedicated_form")
        self.assertEqual(config.startup_dedicated_ip_price_id, "price_startup_dedicated_form")
        self.assertEqual(config.scale_dedicated_ip_product_id, "prod_scale_dedicated_form")
        self.assertEqual(config.scale_dedicated_ip_price_id, "price_scale_dedicated_form")
        self.assertEqual(config.org_team_dedicated_ip_product_id, "prod_org_dedicated_form")
        self.assertEqual(config.org_team_dedicated_ip_price_id, "price_org_dedicated_form")

    def test_stripe_config_form_saves_task_pack_fields(self):
        config = StripeConfig.objects.create(
            release_env=settings.GOBII_RELEASE_ENV,
            live_mode=False,
        )

        form_data = {
            "release_env": settings.GOBII_RELEASE_ENV,
            "live_mode": "on",
            "webhook_secret": "",
            "clear_webhook_secret": "",
            "startup_task_pack_product_id": "prod_startup_task_pack_form",
            "startup_browser_task_limit_product_id": "prod_startup_browser_task_limit_form",
            "startup_browser_task_limit_price_ids": "price_startup_browser_task_limit_a,price_startup_browser_task_limit_b",
            "scale_task_pack_product_id": "prod_scale_task_pack_form",
            "scale_browser_task_limit_product_id": "prod_scale_browser_task_limit_form",
            "scale_browser_task_limit_price_ids": "price_scale_browser_task_limit_a",
            "org_team_additional_task_product_id": "prod_org_additional_form",
            "org_team_additional_task_price_id": "price_org_additional_form",
            "org_team_task_pack_product_id": "prod_org_task_pack_form",
            "org_team_browser_task_limit_product_id": "prod_org_browser_task_limit_form",
            "org_team_browser_task_limit_price_ids": "price_org_browser_task_limit_a,price_org_browser_task_limit_b",
            "startup_task_pack_price_ids": "price_startup_task_pack_form",
            "scale_task_pack_price_ids": "price_scale_task_pack_form",
            "org_team_task_pack_price_ids": "price_org_task_pack_form",
        }

        form = StripeConfigForm(data=form_data, instance=config)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        config.refresh_from_db()
        self.assertEqual(config.startup_task_pack_product_id, "prod_startup_task_pack_form")
        self.assertEqual(config.startup_browser_task_limit_product_id, "prod_startup_browser_task_limit_form")
        self.assertEqual(
            config.startup_browser_task_limit_price_ids,
            ["price_startup_browser_task_limit_a", "price_startup_browser_task_limit_b"],
        )
        self.assertEqual(config.scale_task_pack_product_id, "prod_scale_task_pack_form")
        self.assertEqual(config.scale_browser_task_limit_product_id, "prod_scale_browser_task_limit_form")
        self.assertEqual(config.scale_browser_task_limit_price_ids, ["price_scale_browser_task_limit_a"])
        self.assertEqual(config.org_team_additional_task_product_id, "prod_org_additional_form")
        self.assertEqual(config.org_team_additional_task_price_id, "price_org_additional_form")
        self.assertEqual(config.org_team_task_pack_product_id, "prod_org_task_pack_form")
        self.assertEqual(config.org_team_browser_task_limit_product_id, "prod_org_browser_task_limit_form")
        self.assertEqual(
            config.org_team_browser_task_limit_price_ids,
            ["price_org_browser_task_limit_a", "price_org_browser_task_limit_b"],
        )
        self.assertEqual(config.startup_task_pack_price_ids, ["price_startup_task_pack_form"])
        self.assertEqual(config.scale_task_pack_price_ids, ["price_scale_task_pack_form"])
        self.assertEqual(config.org_team_task_pack_price_ids, ["price_org_task_pack_form"])
