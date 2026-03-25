from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings, tag

from api.services import system_settings
from api.models import SystemSetting


@tag("batch_system_settings")
class LoginToggleValidationTests(SimpleTestCase):
    @override_settings(ACCOUNT_ALLOW_PASSWORD_LOGIN=True, ACCOUNT_ALLOW_SOCIAL_LOGIN=True)
    def test_allows_disabling_when_other_enabled(self) -> None:
        with patch.object(system_settings, "_load_db_values", return_value={}):
            system_settings.validate_login_toggle_update(
                "ACCOUNT_ALLOW_PASSWORD_LOGIN",
                False,
                clear=False,
            )

    @override_settings(ACCOUNT_ALLOW_PASSWORD_LOGIN=True, ACCOUNT_ALLOW_SOCIAL_LOGIN=True)
    def test_blocks_disabling_last_login_method(self) -> None:
        with patch.object(
            system_settings,
            "_load_db_values",
            return_value={"ACCOUNT_ALLOW_SOCIAL_LOGIN": "false"},
        ):
            with self.assertRaises(ValueError):
                system_settings.validate_login_toggle_update(
                    "ACCOUNT_ALLOW_PASSWORD_LOGIN",
                    False,
                    clear=False,
                )

    @override_settings(ACCOUNT_ALLOW_PASSWORD_LOGIN=False, ACCOUNT_ALLOW_SOCIAL_LOGIN=False)
    def test_allows_reenabling_login_method(self) -> None:
        with patch.object(
            system_settings,
            "_load_db_values",
            return_value={
                "ACCOUNT_ALLOW_PASSWORD_LOGIN": "false",
                "ACCOUNT_ALLOW_SOCIAL_LOGIN": "false",
            },
        ):
            system_settings.validate_login_toggle_update(
                "ACCOUNT_ALLOW_SOCIAL_LOGIN",
                True,
                clear=False,
            )


@tag("batch_system_settings")
class LoginToggleCacheValidationTests(TestCase):
    @override_settings(ACCOUNT_ALLOW_PASSWORD_LOGIN=True, ACCOUNT_ALLOW_SOCIAL_LOGIN=True)
    def test_validation_ignores_stale_cache(self) -> None:
        system_settings.invalidate_system_settings_cache()
        SystemSetting.objects.create(key="ACCOUNT_ALLOW_PASSWORD_LOGIN", value_text="true")
        SystemSetting.objects.create(key="ACCOUNT_ALLOW_SOCIAL_LOGIN", value_text="true")
        system_settings._write_cached_db_values(
            {
                "ACCOUNT_ALLOW_PASSWORD_LOGIN": "false",
                "ACCOUNT_ALLOW_SOCIAL_LOGIN": "false",
            }
        )

        system_settings.validate_login_toggle_update(
            "ACCOUNT_ALLOW_SOCIAL_LOGIN",
            False,
            clear=False,
        )


@tag("batch_system_settings")
class SandboxSystemSettingsTests(SimpleTestCase):
    def test_sandbox_setting_definitions_exist(self) -> None:
        expected = {
            "SANDBOX_COMPUTE_ENABLED": "bool",
            "SANDBOX_COMPUTE_POD_IMAGE": "string",
            "SANDBOX_COMPUTE_REQUIRE_PROXY": "bool",
        }

        for key, value_type in expected.items():
            definition = system_settings.get_setting_definition(key)
            self.assertIsNotNone(definition)
            definition = system_settings.SYSTEM_SETTING_DEFINITIONS_BY_KEY[key]
            self.assertEqual(definition.category, "Sandbox")
            self.assertEqual(definition.value_type, value_type)

    def test_string_image_setting_uses_trimmed_database_value(self) -> None:
        definition = system_settings.get_setting_definition("SANDBOX_COMPUTE_POD_IMAGE")
        self.assertIsNotNone(definition)
        definition = system_settings.SYSTEM_SETTING_DEFINITIONS_BY_KEY["SANDBOX_COMPUTE_POD_IMAGE"]
        with patch.object(
            system_settings,
            "_load_db_values",
            return_value={"SANDBOX_COMPUTE_POD_IMAGE": "  ghcr.io/gobii-ai/gobii-sandbox-compute:0.2.0  "},
        ):
            payload = system_settings.serialize_setting(definition)

        self.assertEqual(payload["source"], "database")
        self.assertEqual(payload["effective_value"], "ghcr.io/gobii-ai/gobii-sandbox-compute:0.2.0")
        self.assertEqual(payload["db_value"], "ghcr.io/gobii-ai/gobii-sandbox-compute:0.2.0")

    def test_string_image_setting_rejects_blank_value(self) -> None:
        definition = system_settings.get_setting_definition("SANDBOX_COMPUTE_POD_IMAGE")
        self.assertIsNotNone(definition)
        definition = system_settings.SYSTEM_SETTING_DEFINITIONS_BY_KEY["SANDBOX_COMPUTE_POD_IMAGE"]

        with self.assertRaises(ValueError):
            definition.coerce("   ")

    def test_string_image_setting_rejects_non_text_value(self) -> None:
        definition = system_settings.get_setting_definition("SANDBOX_COMPUTE_POD_IMAGE")
        self.assertIsNotNone(definition)
        definition = system_settings.SYSTEM_SETTING_DEFINITIONS_BY_KEY["SANDBOX_COMPUTE_POD_IMAGE"]

        with self.assertRaises(ValueError):
            definition.coerce(123)

    def test_sandbox_getters_apply_database_overrides(self) -> None:
        with patch.object(
            system_settings,
            "_load_db_values",
            return_value={
                "SANDBOX_COMPUTE_ENABLED": "false",
                "SANDBOX_COMPUTE_REQUIRE_PROXY": "true",
                "SANDBOX_COMPUTE_POD_IMAGE": "ghcr.io/gobii-ai/gobii-sandbox-compute:0.2.0",
            },
        ):
            self.assertFalse(system_settings.get_sandbox_compute_enabled())
            self.assertTrue(system_settings.get_sandbox_compute_require_proxy())
            self.assertEqual(
                system_settings.get_sandbox_compute_pod_image(),
                "ghcr.io/gobii-ai/gobii-sandbox-compute:0.2.0",
            )


@tag("batch_system_settings")
class ParallelToolCallSystemSettingsTests(SimpleTestCase):
    @override_settings(MAX_PARALLEL_TOOL_CALLS=4)
    def test_parallel_tool_call_definition_exists(self) -> None:
        definition = system_settings.get_setting_definition("MAX_PARALLEL_TOOL_CALLS")
        self.assertIsNotNone(definition)
        definition = system_settings.SYSTEM_SETTING_DEFINITIONS_BY_KEY["MAX_PARALLEL_TOOL_CALLS"]
        self.assertEqual(definition.category, "Agents")
        self.assertEqual(definition.value_type, "int")
        self.assertEqual(definition.min_value, 1)
        self.assertEqual(definition.env_var, "MAX_PARALLEL_TOOL_CALLS")

    @override_settings(MAX_PARALLEL_TOOL_CALLS=4)
    def test_parallel_tool_call_setting_defaults_to_configured_value(self) -> None:
        definition = system_settings.SYSTEM_SETTING_DEFINITIONS_BY_KEY["MAX_PARALLEL_TOOL_CALLS"]
        with patch.object(system_settings, "_load_db_values", return_value={}):
            payload = system_settings.serialize_setting(definition)

        self.assertEqual(payload["source"], "default")
        self.assertEqual(payload["effective_value"], 4)
        self.assertEqual(payload["fallback_value"], 4)

    @override_settings(MAX_PARALLEL_TOOL_CALLS=4)
    def test_parallel_tool_call_getter_applies_database_override(self) -> None:
        with patch.object(
            system_settings,
            "_load_db_values",
            return_value={"MAX_PARALLEL_TOOL_CALLS": "2"},
        ):
            self.assertEqual(system_settings.get_max_parallel_tool_calls(), 2)
