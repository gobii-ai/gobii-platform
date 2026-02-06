import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.urls import reverse

from api.models import (
    EmbeddingsModelEndpoint,
    EmbeddingsTierEndpoint,
    FileHandlerModelEndpoint,
    FileHandlerTierEndpoint,
    ImageGenerationModelEndpoint,
    ImageGenerationTierEndpoint,
    LLMProvider,
)


@tag("batch_console_api")
class ConsoleLlmAuxModelApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username="llm-admin@example.com",
            email="llm-admin@example.com",
            password="pass1234",
            is_staff=True,
        )
        self.client = Client()
        self.client.force_login(self.admin)
        self.provider = LLMProvider.objects.create(
            key="test-provider",
            display_name="Test Provider",
            enabled=True,
        )

    def _json_post(self, url_name: str, payload: dict, *args):
        return self.client.post(
            reverse(url_name, args=args),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _json_patch(self, url_name: str, payload: dict, *args):
        return self.client.patch(
            reverse(url_name, args=args),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_embedding_endpoint_and_tier_lifecycle(self):
        create_resp = self._json_post(
            "console_llm_embedding_endpoints",
            {
                "provider_id": str(self.provider.id),
                "key": "embed-small",
                "model": "text-embedding-3-small",
                "api_base": "https://example.com/v1",
            },
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.content)
        endpoint_id = create_resp.json()["endpoint_id"]

        patch_resp = self._json_patch(
            "console_llm_embedding_endpoint_detail",
            {
                "model": "text-embedding-3-large",
                "low_latency": True,
                "provider_id": None,
            },
            endpoint_id,
        )
        self.assertEqual(patch_resp.status_code, 200, patch_resp.content)
        endpoint = EmbeddingsModelEndpoint.objects.get(id=endpoint_id)
        self.assertEqual(endpoint.litellm_model, "text-embedding-3-large")
        self.assertTrue(endpoint.low_latency)
        self.assertIsNone(endpoint.provider_id)

        tier_resp = self._json_post("console_llm_embedding_tiers", {"description": "Tier A"})
        self.assertEqual(tier_resp.status_code, 200, tier_resp.content)
        tier_id = tier_resp.json()["tier_id"]

        attach_resp = self._json_post(
            "console_llm_embedding_tier_endpoints",
            {"endpoint_id": endpoint_id, "weight": 1.25},
            tier_id,
        )
        self.assertEqual(attach_resp.status_code, 200, attach_resp.content)
        tier_endpoint_id = attach_resp.json()["tier_endpoint_id"]

        update_weight_resp = self._json_patch(
            "console_llm_embedding_tier_endpoint_detail",
            {"weight": 2.5},
            tier_endpoint_id,
        )
        self.assertEqual(update_weight_resp.status_code, 200, update_weight_resp.content)
        tier_endpoint = EmbeddingsTierEndpoint.objects.get(id=tier_endpoint_id)
        self.assertEqual(tier_endpoint.weight, 2.5)

        delete_tier_endpoint_resp = self.client.delete(
            reverse("console_llm_embedding_tier_endpoint_detail", args=[tier_endpoint_id])
        )
        self.assertEqual(delete_tier_endpoint_resp.status_code, 200, delete_tier_endpoint_resp.content)

        delete_tier_resp = self.client.delete(reverse("console_llm_embedding_tier_detail", args=[tier_id]))
        self.assertEqual(delete_tier_resp.status_code, 200, delete_tier_resp.content)

        delete_endpoint_resp = self.client.delete(
            reverse("console_llm_embedding_endpoint_detail", args=[endpoint_id])
        )
        self.assertEqual(delete_endpoint_resp.status_code, 200, delete_endpoint_resp.content)

    def test_file_handler_endpoint_guard_and_supports_vision(self):
        create_resp = self._json_post(
            "console_llm_file_handler_endpoints",
            {
                "provider_id": str(self.provider.id),
                "key": "file-handler-model",
                "model": "gpt-4o-mini",
                "supports_vision": True,
            },
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.content)
        endpoint_id = create_resp.json()["endpoint_id"]

        patch_resp = self._json_patch(
            "console_llm_file_handler_endpoint_detail",
            {
                "supports_vision": False,
                "provider_id": None,
            },
            endpoint_id,
        )
        self.assertEqual(patch_resp.status_code, 200, patch_resp.content)
        endpoint = FileHandlerModelEndpoint.objects.get(id=endpoint_id)
        self.assertFalse(endpoint.supports_vision)
        self.assertIsNone(endpoint.provider_id)

        tier_resp = self._json_post("console_llm_file_handler_tiers", {"description": "Tier FH"})
        self.assertEqual(tier_resp.status_code, 200, tier_resp.content)
        tier_id = tier_resp.json()["tier_id"]

        attach_resp = self._json_post(
            "console_llm_file_handler_tier_endpoints",
            {"endpoint_id": endpoint_id, "weight": 1},
            tier_id,
        )
        self.assertEqual(attach_resp.status_code, 200, attach_resp.content)
        tier_endpoint_id = attach_resp.json()["tier_endpoint_id"]
        self.assertTrue(FileHandlerTierEndpoint.objects.filter(id=tier_endpoint_id).exists())

        blocked_delete_resp = self.client.delete(
            reverse("console_llm_file_handler_endpoint_detail", args=[endpoint_id])
        )
        self.assertEqual(blocked_delete_resp.status_code, 400, blocked_delete_resp.content)

        delete_tier_endpoint_resp = self.client.delete(
            reverse("console_llm_file_handler_tier_endpoint_detail", args=[tier_endpoint_id])
        )
        self.assertEqual(delete_tier_endpoint_resp.status_code, 200, delete_tier_endpoint_resp.content)

        delete_tier_resp = self.client.delete(reverse("console_llm_file_handler_tier_detail", args=[tier_id]))
        self.assertEqual(delete_tier_resp.status_code, 200, delete_tier_resp.content)

        delete_endpoint_resp = self.client.delete(
            reverse("console_llm_file_handler_endpoint_detail", args=[endpoint_id])
        )
        self.assertEqual(delete_endpoint_resp.status_code, 200, delete_endpoint_resp.content)

    def test_image_generation_endpoint_and_tier_lifecycle(self):
        create_resp = self._json_post(
            "console_llm_image_generation_endpoints",
            {
                "provider_id": str(self.provider.id),
                "key": "img-gen-model",
                "model": "google/gemini-2.5-flash-image",
                "supports_image_to_image": True,
            },
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.content)
        endpoint_id = create_resp.json()["endpoint_id"]
        endpoint = ImageGenerationModelEndpoint.objects.get(id=endpoint_id)
        self.assertTrue(endpoint.supports_image_to_image)

        patch_resp = self._json_patch(
            "console_llm_image_generation_endpoint_detail",
            {
                "model": "black-forest-labs/flux.2-pro",
                "low_latency": True,
                "supports_image_to_image": False,
                "provider_id": None,
            },
            endpoint_id,
        )
        self.assertEqual(patch_resp.status_code, 200, patch_resp.content)
        endpoint = ImageGenerationModelEndpoint.objects.get(id=endpoint_id)
        self.assertEqual(endpoint.litellm_model, "black-forest-labs/flux.2-pro")
        self.assertTrue(endpoint.low_latency)
        self.assertFalse(endpoint.supports_image_to_image)
        self.assertIsNone(endpoint.provider_id)

        tier_resp = self._json_post("console_llm_image_generation_tiers", {"description": "Tier IMG"})
        self.assertEqual(tier_resp.status_code, 200, tier_resp.content)
        tier_id = tier_resp.json()["tier_id"]

        attach_resp = self._json_post(
            "console_llm_image_generation_tier_endpoints",
            {"endpoint_id": endpoint_id, "weight": 1},
            tier_id,
        )
        self.assertEqual(attach_resp.status_code, 200, attach_resp.content)
        tier_endpoint_id = attach_resp.json()["tier_endpoint_id"]
        self.assertTrue(ImageGenerationTierEndpoint.objects.filter(id=tier_endpoint_id).exists())

        blocked_delete_resp = self.client.delete(
            reverse("console_llm_image_generation_endpoint_detail", args=[endpoint_id])
        )
        self.assertEqual(blocked_delete_resp.status_code, 400, blocked_delete_resp.content)

        delete_tier_endpoint_resp = self.client.delete(
            reverse("console_llm_image_generation_tier_endpoint_detail", args=[tier_endpoint_id])
        )
        self.assertEqual(delete_tier_endpoint_resp.status_code, 200, delete_tier_endpoint_resp.content)

        delete_tier_resp = self.client.delete(reverse("console_llm_image_generation_tier_detail", args=[tier_id]))
        self.assertEqual(delete_tier_resp.status_code, 200, delete_tier_resp.content)

        delete_endpoint_resp = self.client.delete(
            reverse("console_llm_image_generation_endpoint_detail", args=[endpoint_id])
        )
        self.assertEqual(delete_endpoint_resp.status_code, 200, delete_endpoint_resp.content)
