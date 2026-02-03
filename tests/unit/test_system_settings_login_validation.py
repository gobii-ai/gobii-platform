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
