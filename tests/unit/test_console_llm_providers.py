import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.urls import reverse

from api.models import LLMProvider
from console.llm_serializers import build_llm_overview


@tag("batch_console_api")
class ConsoleLLMProviderTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username="admin-llm-provider@example.com",
            email="admin-llm-provider@example.com",
            password="pass1234",
            is_staff=True,
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def test_staff_can_create_provider_with_full_fields(self):
        response = self.client.post(
            reverse("console_llm_providers"),
            data=json.dumps(
                {
                    "display_name": "Test Router",
                    "key": "test-router",
                    "api_key": "secret-value",
                    "env_var_name": "TEST_ROUTER_API_KEY",
                    "model_prefix": "test-router/",
                    "browser_backend": LLMProvider.BrowserBackend.OPENAI_COMPAT,
                    "supports_safety_identifier": True,
                    "vertex_project": "vertex-project",
                    "vertex_location": "us-east4",
                    "enabled": True,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        provider = LLMProvider.objects.get(key="test-router")
        self.assertEqual(provider.display_name, "Test Router")
        self.assertEqual(provider.env_var_name, "TEST_ROUTER_API_KEY")
        self.assertEqual(provider.model_prefix, "test-router/")
        self.assertEqual(provider.browser_backend, LLMProvider.BrowserBackend.OPENAI_COMPAT)
        self.assertTrue(provider.supports_safety_identifier)
        self.assertEqual(provider.vertex_project, "vertex-project")
        self.assertEqual(provider.vertex_location, "us-east4")
        self.assertTrue(provider.enabled)
        self.assertTrue(provider.api_key_encrypted)

    def test_create_provider_rejects_duplicate_key(self):
        LLMProvider.objects.create(key="existing", display_name="Existing")

        response = self.client.post(
            reverse("console_llm_providers"),
            data=json.dumps({"display_name": "Duplicate", "key": "existing"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Provider key already exists", response.content)

    def test_create_provider_rejects_invalid_browser_backend(self):
        response = self.client.post(
            reverse("console_llm_providers"),
            data=json.dumps(
                {
                    "display_name": "Invalid Backend",
                    "key": "invalid-backend",
                    "browser_backend": "NOT_A_BACKEND",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"browser_backend must be one of", response.content)
        self.assertFalse(LLMProvider.objects.filter(key="invalid-backend").exists())

    def test_update_provider_rejects_invalid_browser_backend(self):
        provider = LLMProvider.objects.create(
            key="patch-backend",
            display_name="Patch Backend",
            browser_backend=LLMProvider.BrowserBackend.OPENAI,
        )

        response = self.client.patch(
            reverse("console_llm_provider_detail", args=[provider.id]),
            data=json.dumps({"browser_backend": "NOT_A_BACKEND"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"browser_backend must be one of", response.content)
        provider.refresh_from_db()
        self.assertEqual(provider.browser_backend, LLMProvider.BrowserBackend.OPENAI)

    def test_overview_includes_provider_model_prefix(self):
        provider = LLMProvider.objects.create(
            key="prefixed",
            display_name="Prefixed",
            model_prefix="prefixed/",
        )

        overview = build_llm_overview()
        provider_payload = next(entry for entry in overview["providers"] if entry["id"] == str(provider.id))

        self.assertEqual(provider_payload["model_prefix"], "prefixed/")
