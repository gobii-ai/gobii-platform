from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, tag, override_settings

from api.agent.tools.custom_tools import _resolve_bridge_base_url


@tag("batch_custom_tools")
class CustomToolBridgeBaseUrlTests(SimpleTestCase):
    @override_settings(
        SANDBOX_CUSTOM_TOOL_BRIDGE_BASE_URL="http://sandbox-bridge.internal:8000",
        PUBLIC_SITE_URL="https://public.example.com",
    )
    def test_prefers_sandbox_specific_bridge_base_url(self):
        self.assertEqual(_resolve_bridge_base_url(), "http://sandbox-bridge.internal:8000")

    @override_settings(
        SANDBOX_CUSTOM_TOOL_BRIDGE_BASE_URL="",
        PUBLIC_SITE_URL="https://public.example.com",
    )
    def test_falls_back_to_public_site_url_when_bridge_url_unset(self):
        self.assertEqual(_resolve_bridge_base_url(), "https://public.example.com")

    @override_settings(
        SANDBOX_CUSTOM_TOOL_BRIDGE_BASE_URL="",
        PUBLIC_SITE_URL="",
    )
    def test_falls_back_to_current_site_domain(self):
        with patch(
            "api.agent.tools.custom_tools.Site.objects.get_current",
            return_value=SimpleNamespace(domain="sandbox.internal:8000"),
        ):
            self.assertEqual(_resolve_bridge_base_url(), "https://sandbox.internal:8000")
