from unittest.mock import patch

from django.test import TestCase, override_settings, tag

from api.models import LLMProvider, PersistentModelEndpoint
from setup.forms import LLMConfigForm
from setup.views import SetupWizardView


@tag("batch_setup_cookies")
class SetupWizardCsrfTests(TestCase):
    @override_settings(CSRF_COOKIE_NAME="gobii_platform_csrftoken")
    def test_setup_wizard_uses_configured_csrf_cookie_name(self):
        with patch("setup.views.is_initial_setup_complete", return_value=False), patch(
            "setup.views.SetupWizardView._ensure_database_ready"
        ):
            response = self.client.get("/setup/")

        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('meta name="csrf-cookie-name" content="gobii_platform_csrftoken"', content)
        self.assertIn("function getCsrfCookieName()", content)
        self.assertIn("window.getCsrfTokenValue ? window.getCsrfTokenValue() : ''", content)
        self.assertNotIn("document.cookie.match(/csrftoken=([^;]+)/)", content)

    def test_configure_orchestrator_creates_missing_builtin_endpoint(self):
        provider = LLMProvider.objects.create(
            key="openai",
            display_name="OpenAI",
            enabled=True,
            env_var_name="OPENAI_API_KEY",
            browser_backend=LLMProvider.BrowserBackend.OPENAI,
        )

        view = SetupWizardView()
        configured_provider, endpoint = view._configure_orchestrator(
            {
                "orchestrator_provider": LLMConfigForm.PROVIDER_OPENAI,
                "orchestrator_api_key": "",
                "orchestrator_model": "gpt-4.1",
                "orchestrator_api_base": "",
                "orchestrator_supports_tool_choice": True,
                "orchestrator_use_parallel_tools": True,
                "orchestrator_supports_vision": True,
            }
        )

        self.assertEqual(configured_provider.pk, provider.pk)
        self.assertEqual(endpoint.key, "openai_gpt4_1")
        self.assertTrue(PersistentModelEndpoint.objects.filter(key="openai_gpt4_1").exists())
