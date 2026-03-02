import json
from os import environ
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.urls import reverse

from api.models import BrowserModelEndpoint, LLMProvider, PersistentModelEndpoint


@tag("batch_console_api")
class ConsoleLlmEndpointTestApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username="llm-endpoint-test-admin@example.com",
            email="llm-endpoint-test-admin@example.com",
            password="pass1234",
            is_staff=True,
        )
        self.client = Client()
        self.client.force_login(self.admin)
        self.provider = LLMProvider.objects.create(
            key="console-endpoint-provider",
            display_name="Console Endpoint Provider",
            enabled=True,
            model_prefix="openrouter/",
            browser_backend=LLMProvider.BrowserBackend.OPENAI_COMPAT,
            env_var_name="TEST_CONSOLE_PROVIDER_KEY",
        )

    def _post_json(self, url_name: str, payload: dict):
        return self.client.post(
            reverse(url_name),
            data=json.dumps(payload),
            content_type="application/json",
        )

    @patch("console.api_views.run_completion")
    def test_browser_test_endpoint_uses_raw_model(self, mock_run_completion):
        endpoint = BrowserModelEndpoint.objects.create(
            key="browser-endpoint-raw-model",
            provider=self.provider,
            browser_model="gpt-4o-mini",
            browser_base_url="https://proxy.example/v1",
            enabled=True,
        )
        mock_run_completion.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="READY"))],
            usage={"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5},
        )

        with patch.dict(environ, {"TEST_CONSOLE_PROVIDER_KEY": "test-key"}):
            response = self._post_json(
                "console_llm_test_endpoint",
                {"endpoint_id": str(endpoint.id), "kind": "browser"},
            )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["model"], "gpt-4o-mini")
        mock_run_completion.assert_called_once()
        self.assertEqual(mock_run_completion.call_args.kwargs["model"], "gpt-4o-mini")

    @patch("console.api_views.run_completion")
    def test_persistent_test_endpoint_still_normalizes_model(self, mock_run_completion):
        endpoint = PersistentModelEndpoint.objects.create(
            key="persistent-endpoint-prefixed-model",
            provider=self.provider,
            litellm_model="gpt-4.1-mini",
            enabled=True,
        )
        mock_run_completion.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="READY"))],
            usage={"total_tokens": 8, "prompt_tokens": 4, "completion_tokens": 4},
        )

        with patch.dict(environ, {"TEST_CONSOLE_PROVIDER_KEY": "test-key"}):
            response = self._post_json(
                "console_llm_test_endpoint",
                {"endpoint_id": str(endpoint.id), "kind": "persistent"},
            )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["model"], "openrouter/gpt-4.1-mini")
        mock_run_completion.assert_called_once()
        self.assertEqual(mock_run_completion.call_args.kwargs["model"], "openrouter/gpt-4.1-mini")

    def test_browser_endpoint_validation_still_rejects_prefixed_model(self):
        create_response = self.client.post(
            reverse("console_llm_browser_endpoints"),
            data=json.dumps(
                {
                    "provider_id": str(self.provider.id),
                    "key": "browser-prefixed-model",
                    "model": "openrouter/gpt-4o-mini",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(create_response.status_code, 400)
        self.assertEqual(
            create_response.content.decode(),
            "Store browser models without the provider prefix; it is applied at runtime when necessary.",
        )
