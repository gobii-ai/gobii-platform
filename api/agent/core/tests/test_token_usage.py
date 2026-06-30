from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag
from django.urls import reverse

from api.agent.core.token_usage import compute_cost_breakdown
from api.llm.utils import normalize_model_name, normalize_pricing_model
from api.models import LLMProvider, PersistentModelEndpoint
from console.llm_serializers import _serialize_persistent_endpoint


@tag("batch_token_usage")
class ModelNormalizationTests(SimpleTestCase):
    def test_openai_compatible_api_base_prefixes_slash_model_for_litellm_provider_routing(self):
        provider = SimpleNamespace(
            model_prefix="",
            browser_backend=LLMProvider.BrowserBackend.OPENAI_COMPAT,
        )

        model = normalize_model_name(
            provider,
            "deepseek-ai/DeepSeek-V4-Flash",
            api_base="https://api.makora.example/v1",
        )

        self.assertEqual(model, "openai/deepseek-ai/DeepSeek-V4-Flash")

    def test_openai_compatible_api_base_does_not_double_prefix_openai_model(self):
        provider = SimpleNamespace(
            model_prefix="",
            browser_backend=LLMProvider.BrowserBackend.OPENAI_COMPAT,
        )

        model = normalize_model_name(
            provider,
            "openai/deepseek-ai/DeepSeek-V4-Flash",
            api_base="https://api.makora.example/v1",
        )

        self.assertEqual(model, "openai/deepseek-ai/DeepSeek-V4-Flash")

    def test_pricing_model_does_not_use_openai_compatible_prefixing(self):
        provider = SimpleNamespace(
            model_prefix="",
            browser_backend=LLMProvider.BrowserBackend.OPENAI_COMPAT,
        )
        endpoint = SimpleNamespace(litellm_pricing_model="deepseek/deepseek-chat")

        model = normalize_pricing_model(
            endpoint,
            provider,
        )

        self.assertEqual(model, "deepseek/deepseek-chat")

    def test_azure_responses_api_prefixes_deployment_for_persistent_llm_routing(self):
        provider = SimpleNamespace(
            key="azure",
            model_prefix="",
            browser_backend=LLMProvider.BrowserBackend.OPENAI_COMPAT,
        )

        model = normalize_model_name(
            provider,
            "gpt-5-deployment",
            api_base="https://example.openai.azure.com",
            responses_api=True,
        )

        self.assertEqual(model, "azure/responses/gpt-5-deployment")

    def test_azure_responses_api_keeps_non_openai_model_on_completion_route(self):
        provider = SimpleNamespace(
            key="azure",
            model_prefix="",
            browser_backend=LLMProvider.BrowserBackend.OPENAI_COMPAT,
        )

        model = normalize_model_name(
            provider,
            "deepseek-v4-flash",
            api_base="https://example.services.ai.azure.com",
            responses_api=True,
        )

        self.assertEqual(model, "azure/deepseek-v4-flash")

    def test_azure_non_openai_responses_prefix_is_not_generated_from_raw_model(self):
        provider = SimpleNamespace(
            key="azure",
            model_prefix="",
            browser_backend=LLMProvider.BrowserBackend.OPENAI_COMPAT,
        )

        model = normalize_model_name(
            provider,
            "responses/deepseek-v4-flash",
            api_base="https://example.services.ai.azure.com",
            responses_api=True,
        )

        self.assertEqual(model, "azure/deepseek-v4-flash")

    def test_azure_prefixing_requires_responses_api_mode(self):
        provider = SimpleNamespace(
            key="azure",
            model_prefix="",
            browser_backend=LLMProvider.BrowserBackend.OPENAI_COMPAT,
        )

        model = normalize_model_name(
            provider,
            "text-embedding-3-large",
            api_base="https://example.openai.azure.com",
        )

        self.assertEqual(model, "openai/text-embedding-3-large")


@tag("batch_token_usage")
class CostBreakdownPricingModelTests(SimpleTestCase):
    def test_uses_pricing_model_for_litellm_model_info_without_replacing_model(self):
        token_usage = {
            "model": "openai/deepseek-ai/DeepSeek-V4-Flash",
            "pricing_model": "openai/gpt-4o-mini",
            "provider": "makora",
            "prompt_tokens": 10,
            "completion_tokens": 5,
        }

        with patch(
            "api.agent.core.token_usage.litellm.get_model_info",
            return_value={
                "input_cost_per_token": 0.001,
                "output_cost_per_token": 0.002,
            },
        ) as get_model_info:
            costs = compute_cost_breakdown(token_usage, raw_usage=None)

        self.assertEqual(token_usage["model"], "openai/deepseek-ai/DeepSeek-V4-Flash")
        self.assertEqual(costs["input_cost_total"], costs["output_cost"])
        self.assertEqual(costs["total_cost"], costs["input_cost_total"] + costs["output_cost"])
        get_model_info.assert_called_once_with(
            model="openai/gpt-4o-mini",
            custom_llm_provider="openai",
        )

    def test_falls_back_to_model_when_pricing_model_is_missing(self):
        token_usage = {
            "model": "openai/gpt-4o-mini",
            "provider": "openai",
            "prompt_tokens": 10,
            "completion_tokens": 5,
        }

        with patch(
            "api.agent.core.token_usage.litellm.get_model_info",
            return_value={
                "input_cost_per_token": 0.001,
                "output_cost_per_token": 0.002,
            },
        ) as get_model_info:
            compute_cost_breakdown(token_usage, raw_usage=None)

        get_model_info.assert_called_once_with(
            model="openai/gpt-4o-mini",
            custom_llm_provider="openai",
        )


@tag("batch_token_usage")
class PersistentEndpointPricingModelApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="llm-admin",
            email="llm-admin@example.com",
            password="password",
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.provider = LLMProvider.objects.create(
            key="makora",
            display_name="Makora",
            enabled=True,
        )

    def test_persistent_endpoint_create_update_clear_and_serialize_pricing_model(self):
        create_response = self.client.post(
            reverse("console_llm_persistent_endpoints"),
            data={
                "provider_id": str(self.provider.id),
                "key": "makora_deepseek_flash",
                "model": "deepseek-ai/DeepSeek-V4-Flash",
                "litellm_pricing_model": "deepseek/deepseek-chat",
            },
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 200)

        endpoint = PersistentModelEndpoint.objects.get(key="makora_deepseek_flash")
        self.assertEqual(endpoint.litellm_model, "deepseek-ai/DeepSeek-V4-Flash")
        self.assertEqual(endpoint.litellm_pricing_model, "deepseek/deepseek-chat")

        serialized = _serialize_persistent_endpoint(endpoint, {})
        self.assertEqual(serialized["litellm_pricing_model"], "deepseek/deepseek-chat")

        update_response = self.client.patch(
            reverse("console_llm_persistent_endpoint_detail", args=[endpoint.id]),
            data={"litellm_pricing_model": ""},
            content_type="application/json",
        )
        self.assertEqual(update_response.status_code, 200)

        endpoint.refresh_from_db()
        self.assertIsNone(endpoint.litellm_pricing_model)
