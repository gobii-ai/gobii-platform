from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, tag

from config import settings as settings_module


@tag("oss_readiness_batch")
class ProprietaryDefaultHelperTests(SimpleTestCase):
    def test_returns_fallback_when_not_proprietary(self) -> None:
        defaults_map = {"brand": {"PUBLIC_SUPPORT_EMAIL": "support@example.com"}}
        module = SimpleNamespace(DEFAULTS=defaults_map)

        with mock.patch.object(settings_module, "_proprietary_defaults_module", module), \
                mock.patch.object(settings_module, "GOBII_PROPRIETARY_MODE", False):
            result = settings_module._proprietary_default(
                "brand",
                "PUBLIC_SUPPORT_EMAIL",
                fallback="",
            )

        self.assertEqual(result, "")

    def test_returns_value_when_proprietary_defaults_present(self) -> None:
        defaults_map = {"brand": {"PUBLIC_SUPPORT_EMAIL": "support@example.com"}}
        module = SimpleNamespace(DEFAULTS=defaults_map)

        with mock.patch.object(settings_module, "_proprietary_defaults_module", module), \
                mock.patch.object(settings_module, "GOBII_PROPRIETARY_MODE", True):
            result = settings_module._proprietary_default(
                "brand",
                "PUBLIC_SUPPORT_EMAIL",
                fallback="",
            )

        self.assertEqual(result, "support@example.com")

    def test_community_defaults_provide_links_when_oss(self) -> None:
        with mock.patch.object(settings_module, "GOBII_PROPRIETARY_MODE", False):
            discord = settings_module._community_default(
                "brand",
                "PUBLIC_DISCORD_URL",
                fallback="",
            )
            x_url = settings_module._community_default(
                "brand",
                "PUBLIC_X_URL",
                fallback="",
            )
            github = settings_module._community_default(
                "brand",
                "PUBLIC_GITHUB_URL",
                fallback="",
            )

        self.assertEqual(discord, "https://discord.gg/yyDB8GwxtE")
        self.assertEqual(x_url, "https://x.com/gobii_ai")
        self.assertEqual(github, "https://github.com/gobii-ai")

    def test_returns_fallback_when_key_missing(self) -> None:
        defaults_map = {"brand": {}}
        module = SimpleNamespace(DEFAULTS=defaults_map)

        with mock.patch.object(settings_module, "_proprietary_defaults_module", module), \
                mock.patch.object(settings_module, "GOBII_PROPRIETARY_MODE", True):
            result = settings_module._proprietary_default(
                "brand",
                "UNKNOWN_KEY",
                fallback="fallback",
            )

        self.assertEqual(result, "fallback")
