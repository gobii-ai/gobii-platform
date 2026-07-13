import os

from dataclasses import fields
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, tag

from api.admin import StripeConfigAdmin
from api.admin_forms import StripeConfigForm
from api.models import StripeConfig
from config import plans as plan_module
from config.stripe_config import StripeSettings, get_stripe_settings, invalidate_stripe_settings_cache
from config.stripe_fields import (
    ORG_TEAM,
    SCALE,
    STARTUP,
    STRIPE_CONFIG_FIELDS,
    STRIPE_LEGACY_ENTRY_NAMES,
    TASK_METERS,
    admin_fields,
)
from util.payments_helper import PaymentsHelper
from constants.plans import PlanNames


EXPECTED_RUNTIME_FIELDS = (
    "startup_product_id",
    "startup_price_id",
    "startup_trial_days",
    "startup_additional_task_price_id",
    "startup_task_pack_product_id",
    "startup_task_pack_price_ids",
    "startup_contact_cap_product_id",
    "startup_contact_cap_price_ids",
    "startup_browser_task_limit_product_id",
    "startup_browser_task_limit_price_ids",
    "startup_advanced_captcha_resolution_product_id",
    "startup_advanced_captcha_resolution_price_id",
    "scale_price_id",
    "scale_trial_days",
    "scale_additional_task_price_id",
    "scale_task_pack_product_id",
    "scale_task_pack_price_ids",
    "scale_product_id",
    "scale_contact_cap_product_id",
    "scale_contact_cap_price_ids",
    "scale_browser_task_limit_product_id",
    "scale_browser_task_limit_price_ids",
    "scale_advanced_captcha_resolution_product_id",
    "scale_advanced_captcha_resolution_price_id",
    "startup_dedicated_ip_product_id",
    "startup_dedicated_ip_price_id",
    "scale_dedicated_ip_product_id",
    "scale_dedicated_ip_price_id",
    "org_team_product_id",
    "org_team_price_id",
    "org_team_additional_task_product_id",
    "org_team_additional_task_price_id",
    "org_team_task_pack_product_id",
    "org_team_task_pack_price_ids",
    "org_team_contact_cap_product_id",
    "org_team_contact_cap_price_ids",
    "org_team_browser_task_limit_product_id",
    "org_team_browser_task_limit_price_ids",
    "org_team_advanced_captcha_resolution_product_id",
    "org_team_advanced_captcha_resolution_price_id",
    "org_team_dedicated_ip_product_id",
    "org_team_dedicated_ip_price_id",
    "task_meter_id",
    "task_meter_event_name",
    "org_task_meter_id",
    "org_team_task_meter_id",
    "org_team_task_meter_event_name",
)

EXPECTED_LEGACY_ALIASES = (
    "startup_advanced_captcha_resolution_price_ids",
    "scale_advanced_captcha_resolution_price_ids",
    "org_team_advanced_captcha_resolution_price_ids",
)


@tag("batch_stripe_config")
class StripeConfigHelperTests(TestCase):
    def setUp(self):
        StripeConfig.objects.all().delete()
        invalidate_stripe_settings_cache()

    def test_registry_matches_runtime_model_form_and_legacy_contract(self):
        registry_names = tuple(spec.name for spec in STRIPE_CONFIG_FIELDS)
        settings_names = tuple(
            field.name
            for field in fields(StripeSettings)
            if field.name
            not in {"release_env", "live_mode", "live_secret_key", "test_secret_key", "webhook_secret"}
        )

        self.assertEqual(registry_names, EXPECTED_RUNTIME_FIELDS)
        self.assertEqual(set(settings_names), set(EXPECTED_RUNTIME_FIELDS))
        self.assertEqual(STRIPE_LEGACY_ENTRY_NAMES, EXPECTED_LEGACY_ALIASES)
        self.assertTrue(all(isinstance(getattr(StripeConfig, name), property) for name in EXPECTED_RUNTIME_FIELDS))
        self.assertTrue(all(isinstance(getattr(StripeConfig, name), property) for name in EXPECTED_LEGACY_ALIASES))
        self.assertEqual(
            tuple(StripeConfigForm.base_fields),
            ("release_env", "live_mode", "webhook_secret", "clear_webhook_secret", *EXPECTED_RUNTIME_FIELDS),
        )

        fieldsets = dict(StripeConfigAdmin.fieldsets)
        self.assertEqual(fieldsets[STARTUP]["fields"], admin_fields(STARTUP))
        self.assertEqual(fieldsets[SCALE]["fields"], admin_fields(SCALE))
        self.assertEqual(fieldsets[ORG_TEAM]["fields"], admin_fields(ORG_TEAM))
        self.assertEqual(fieldsets[TASK_METERS]["fields"], admin_fields(TASK_METERS))

        aliases = {
            spec.name: (spec.legacy_env_name, spec.legacy_entry_name)
            for spec in STRIPE_CONFIG_FIELDS
            if spec.legacy_entry_name
        }
        self.assertEqual(
            aliases,
            {
                "startup_advanced_captcha_resolution_price_id": (
                    "STRIPE_STARTUP_ADVANCED_CAPTCHA_RESOLUTION_PRICE_IDS",
                    "startup_advanced_captcha_resolution_price_ids",
                ),
                "scale_advanced_captcha_resolution_price_id": (
                    "STRIPE_SCALE_ADVANCED_CAPTCHA_RESOLUTION_PRICE_IDS",
                    "scale_advanced_captcha_resolution_price_ids",
                ),
                "org_team_advanced_captcha_resolution_price_id": (
                    "STRIPE_ORG_TEAM_ADVANCED_CAPTCHA_RESOLUTION_PRICE_IDS",
                    "org_team_advanced_captcha_resolution_price_ids",
                ),
            },
        )

    def test_generated_model_properties_round_trip_all_codecs(self):
        config = StripeConfig.objects.create(release_env=settings.GOBII_RELEASE_ENV, live_mode=False)

        config.startup_product_id = "prod_round_trip"
        config.startup_trial_days = -5
        config.startup_task_pack_price_ids = ["price_one", " price_two "]
        config.startup_advanced_captcha_resolution_price_id = "price_captcha"
        config.set_webhook_secret("whsec_round_trip")

        self.assertEqual(config.startup_product_id, "prod_round_trip")
        self.assertEqual(config.startup_trial_days, 0)
        self.assertEqual(config.startup_task_pack_price_ids, ["price_one", "price_two"])
        self.assertEqual(config.startup_advanced_captcha_resolution_price_id, "price_captcha")
        self.assertEqual(config.webhook_secret, "whsec_round_trip")
        self.assertEqual(config.get_value("startup_trial_days"), "0")
        self.assertEqual(config.get_value("startup_task_pack_price_ids"), "price_one,price_two")

        config.set_value("startup_trial_days", "not-an-integer")
        config.set_value("startup_task_pack_price_ids", '["price_json_one", "price_json_two"]')
        self.assertEqual(config.startup_trial_days, 0)
        self.assertEqual(config.startup_task_pack_price_ids, ["price_json_one", "price_json_two"])

        secret_entry = config.entries.get(name="webhook_secret")
        self.assertTrue(secret_entry.is_secret)
        self.assertEqual(secret_entry.value_text, "")
        self.assertTrue(secret_entry.value_encrypted)

    def test_database_scalar_empty_overrides_environment_but_list_empty_falls_back(self):
        config = StripeConfig.objects.create(release_env=settings.GOBII_RELEASE_ENV, live_mode=False)
        config.set_value("startup_product_id", "")
        config.set_value("startup_task_pack_price_ids", "")

        with patch.dict(
            os.environ,
            {
                "STRIPE_STARTUP_PRODUCT_ID": "prod_from_environment",
                "STRIPE_STARTUP_TASK_PACK_PRICE_IDS": "price_env_one,price_env_two",
            },
        ):
            stripe_settings = get_stripe_settings(force_reload=True)

        self.assertEqual(stripe_settings.startup_product_id, "")
        self.assertEqual(stripe_settings.startup_task_pack_price_ids, ("price_env_one", "price_env_two"))

    def test_trial_days_missing_or_invalid_resolve_to_zero(self):
        with patch.dict(os.environ, {"STRIPE_STARTUP_TRIAL_DAYS": "invalid"}):
            self.assertEqual(get_stripe_settings(force_reload=True).startup_trial_days, 0)

        config = StripeConfig.objects.create(release_env=settings.GOBII_RELEASE_ENV, live_mode=False)
        config.set_value("startup_trial_days", "invalid")
        self.assertEqual(get_stripe_settings(force_reload=True).startup_trial_days, 0)
        config.clear_value("startup_trial_days")
        self.assertEqual(get_stripe_settings(force_reload=True).startup_trial_days, 0)

    def test_captcha_price_precedence_uses_singular_legacy_then_environment(self):
        config = StripeConfig.objects.create(release_env=settings.GOBII_RELEASE_ENV, live_mode=False)
        config.startup_advanced_captcha_resolution_price_id = "price_db_singular"
        config.startup_advanced_captcha_resolution_price_ids = ["price_db_legacy"]

        environment = {
            "STRIPE_STARTUP_ADVANCED_CAPTCHA_RESOLUTION_PRICE_ID": "price_env_singular",
            "STRIPE_STARTUP_ADVANCED_CAPTCHA_RESOLUTION_PRICE_IDS": "price_env_legacy",
        }
        with patch.dict(os.environ, environment):
            self.assertEqual(
                get_stripe_settings(force_reload=True).startup_advanced_captcha_resolution_price_id,
                "price_db_singular",
            )
            config.clear_value("startup_advanced_captcha_resolution_price_id")
            self.assertEqual(
                get_stripe_settings(force_reload=True).startup_advanced_captcha_resolution_price_id,
                "price_db_legacy",
            )
            config.clear_value("startup_advanced_captcha_resolution_price_ids")
            self.assertEqual(
                get_stripe_settings(force_reload=True).startup_advanced_captcha_resolution_price_id,
                "price_env_singular",
            )

        with patch.dict(
            os.environ,
            {
                "STRIPE_STARTUP_ADVANCED_CAPTCHA_RESOLUTION_PRICE_ID": "",
                "STRIPE_STARTUP_ADVANCED_CAPTCHA_RESOLUTION_PRICE_IDS": "price_env_legacy",
            },
        ):
            self.assertEqual(
                get_stripe_settings(force_reload=True).startup_advanced_captcha_resolution_price_id,
                "price_env_legacy",
            )

    def test_generated_form_initialization_validation_save_and_webhook_behavior(self):
        config = StripeConfig.objects.create(release_env=settings.GOBII_RELEASE_ENV, live_mode=False)
        config.startup_product_id = "prod_initial"
        config.startup_trial_days = 4
        config.startup_task_pack_price_ids = ["price_initial_one", "price_initial_two"]
        config.startup_advanced_captcha_resolution_price_ids = ["price_legacy_initial"]
        config.set_webhook_secret("whsec_existing")

        initial_form = StripeConfigForm(instance=config)
        self.assertEqual(initial_form.fields["startup_product_id"].initial, "prod_initial")
        self.assertEqual(initial_form.fields["startup_trial_days"].initial, 4)
        self.assertEqual(
            initial_form.fields["startup_task_pack_price_ids"].initial,
            "price_initial_one,price_initial_two",
        )
        self.assertEqual(
            initial_form.fields["startup_advanced_captcha_resolution_price_id"].initial,
            "price_legacy_initial",
        )

        invalid_form = StripeConfigForm(
            data={
                "release_env": settings.GOBII_RELEASE_ENV,
                "startup_advanced_captcha_resolution_price_id": "price_one,price_two",
            },
            instance=config,
        )
        self.assertFalse(invalid_form.is_valid())
        self.assertIn("startup_advanced_captcha_resolution_price_id", invalid_form.errors)

        save_form = StripeConfigForm(
            data={
                "release_env": settings.GOBII_RELEASE_ENV,
                "live_mode": "on",
                "webhook_secret": "",
                "startup_product_id": " prod_saved ",
                "startup_trial_days": "7",
                "startup_task_pack_price_ids": "price_saved_one,price_saved_two",
                "startup_advanced_captcha_resolution_price_id": "price_captcha_saved",
            },
            instance=config,
        )
        self.assertTrue(save_form.is_valid(), save_form.errors)
        save_form.save()
        self.assertEqual(config.startup_product_id, "prod_saved")
        self.assertEqual(config.startup_trial_days, 7)
        self.assertEqual(config.startup_task_pack_price_ids, ["price_saved_one", "price_saved_two"])
        self.assertEqual(config.startup_advanced_captcha_resolution_price_id, "price_captcha_saved")
        self.assertEqual(config.startup_advanced_captcha_resolution_price_ids, ["price_captcha_saved"])
        self.assertEqual(config.webhook_secret, "whsec_existing")

        clear_form = StripeConfigForm(
            data={
                "release_env": settings.GOBII_RELEASE_ENV,
                "clear_webhook_secret": "on",
            },
            instance=config,
        )
        self.assertTrue(clear_form.is_valid(), clear_form.errors)
        clear_form.save()
        self.assertFalse(config.has_value("webhook_secret"))

    def test_model_save_invalidates_cached_settings(self):
        with patch.dict(os.environ, {"STRIPE_STARTUP_PRODUCT_ID": "prod_environment"}):
            self.assertEqual(get_stripe_settings(force_reload=True).startup_product_id, "prod_environment")
            config = StripeConfig.objects.create(
                release_env=settings.GOBII_RELEASE_ENV,
                live_mode=False,
            )
            config.startup_product_id = "prod_database"
            config.save()
            self.assertEqual(get_stripe_settings().startup_product_id, "prod_database")

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

        with self.settings(
            STRIPE_LIVE_SECRET_KEY="sk_live_env",
            STRIPE_TEST_SECRET_KEY="sk_test_env",
        ):
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
