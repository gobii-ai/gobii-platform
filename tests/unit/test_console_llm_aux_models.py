import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.urls import reverse

from api.models import (
    EmbeddingsModelEndpoint,
    EmbeddingsTierEndpoint,
    FileHandlerModelEndpoint,
    FileHandlerTierEndpoint,
    ImageGenerationLLMTier,
    ImageGenerationModelEndpoint,
    ImageGenerationTierEndpoint,
    LLMProvider,
    PersistentModelEndpoint,
    VideoGenerationLLMTier,
    VideoGenerationModelEndpoint,
    VideoGenerationTierEndpoint,
)
from console.llm_serializers import build_llm_overview


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

    def test_image_generation_tiers_are_scoped_by_use_case_in_api_and_overview(self):
        endpoint = ImageGenerationModelEndpoint.objects.create(
            provider=self.provider,
            key="img-gen-shared",
            litellm_model="google/gemini-2.5-flash-image",
            enabled=True,
        )

        create_image_resp = self._json_post(
            "console_llm_image_generation_tiers",
            {"description": "Create tier", "use_case": "create_image"},
        )
        self.assertEqual(create_image_resp.status_code, 200, create_image_resp.content)
        create_image_tier_id = create_image_resp.json()["tier_id"]

        avatar_resp = self._json_post(
            "console_llm_image_generation_tiers",
            {"description": "Avatar tier", "use_case": "avatar"},
        )
        self.assertEqual(avatar_resp.status_code, 200, avatar_resp.content)
        avatar_tier_id = avatar_resp.json()["tier_id"]

        create_image_tier = ImageGenerationLLMTier.objects.get(id=create_image_tier_id)
        avatar_tier = ImageGenerationLLMTier.objects.get(id=avatar_tier_id)
        self.assertEqual(create_image_tier.use_case, ImageGenerationLLMTier.UseCase.CREATE_IMAGE)
        self.assertEqual(avatar_tier.use_case, ImageGenerationLLMTier.UseCase.AVATAR)
        self.assertEqual(create_image_tier.order, 1)
        self.assertEqual(avatar_tier.order, 1)

        attach_create_resp = self._json_post(
            "console_llm_image_generation_tier_endpoints",
            {"endpoint_id": str(endpoint.id), "weight": 1},
            create_image_tier_id,
        )
        self.assertEqual(attach_create_resp.status_code, 200, attach_create_resp.content)

        overview = build_llm_overview()
        self.assertEqual(len(overview["image_generations"]["create_image_tiers"]), 1)
        self.assertEqual(len(overview["image_generations"]["avatar_tiers"]), 1)
        self.assertEqual(
            overview["image_generations"]["create_image_tiers"][0]["use_case"],
            "create_image",
        )
        self.assertEqual(
            overview["image_generations"]["avatar_tiers"][0]["use_case"],
            "avatar",
        )

        move_avatar_resp = self._json_patch(
            "console_llm_image_generation_tier_detail",
            {"move": "up"},
            avatar_tier_id,
        )
        self.assertEqual(move_avatar_resp.status_code, 400, move_avatar_resp.content)

    def test_video_generation_endpoint_and_tier_lifecycle(self):
        create_resp = self._json_post(
            "console_llm_video_generation_endpoints",
            {
                "provider_id": str(self.provider.id),
                "key": "video-gen-model",
                "model": "openai/sora-2",
                "supports_image_to_video": True,
            },
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.content)
        endpoint_id = create_resp.json()["endpoint_id"]
        endpoint = VideoGenerationModelEndpoint.objects.get(id=endpoint_id)
        self.assertTrue(endpoint.supports_image_to_video)

        patch_resp = self._json_patch(
            "console_llm_video_generation_endpoint_detail",
            {
                "model": "google/veo-3",
                "low_latency": True,
                "supports_image_to_video": False,
                "provider_id": None,
            },
            endpoint_id,
        )
        self.assertEqual(patch_resp.status_code, 200, patch_resp.content)
        endpoint = VideoGenerationModelEndpoint.objects.get(id=endpoint_id)
        self.assertEqual(endpoint.litellm_model, "google/veo-3")
        self.assertTrue(endpoint.low_latency)
        self.assertFalse(endpoint.supports_image_to_video)
        self.assertIsNone(endpoint.provider_id)

        tier_resp = self._json_post(
            "console_llm_video_generation_tiers",
            {"description": "Tier VID", "use_case": "create_video"},
        )
        self.assertEqual(tier_resp.status_code, 200, tier_resp.content)
        tier_id = tier_resp.json()["tier_id"]

        attach_resp = self._json_post(
            "console_llm_video_generation_tier_endpoints",
            {"endpoint_id": endpoint_id, "weight": 1},
            tier_id,
        )
        self.assertEqual(attach_resp.status_code, 200, attach_resp.content)
        tier_endpoint_id = attach_resp.json()["tier_endpoint_id"]
        self.assertTrue(VideoGenerationTierEndpoint.objects.filter(id=tier_endpoint_id).exists())

        blocked_delete_resp = self.client.delete(
            reverse("console_llm_video_generation_endpoint_detail", args=[endpoint_id])
        )
        self.assertEqual(blocked_delete_resp.status_code, 400, blocked_delete_resp.content)

        delete_tier_endpoint_resp = self.client.delete(
            reverse("console_llm_video_generation_tier_endpoint_detail", args=[tier_endpoint_id])
        )
        self.assertEqual(delete_tier_endpoint_resp.status_code, 200, delete_tier_endpoint_resp.content)

        delete_tier_resp = self.client.delete(reverse("console_llm_video_generation_tier_detail", args=[tier_id]))
        self.assertEqual(delete_tier_resp.status_code, 200, delete_tier_resp.content)

        delete_endpoint_resp = self.client.delete(
            reverse("console_llm_video_generation_endpoint_detail", args=[endpoint_id])
        )
        self.assertEqual(delete_endpoint_resp.status_code, 200, delete_endpoint_resp.content)

    def test_video_generation_tiers_are_exposed_in_overview(self):
        endpoint = VideoGenerationModelEndpoint.objects.create(
            provider=self.provider,
            key="video-gen-shared",
            litellm_model="openai/sora-2",
            enabled=True,
        )

        create_video_resp = self._json_post(
            "console_llm_video_generation_tiers",
            {"description": "Create video tier", "use_case": "create_video"},
        )
        self.assertEqual(create_video_resp.status_code, 200, create_video_resp.content)
        create_video_tier_id = create_video_resp.json()["tier_id"]

        create_video_tier = VideoGenerationLLMTier.objects.get(id=create_video_tier_id)
        self.assertEqual(create_video_tier.use_case, VideoGenerationLLMTier.UseCase.CREATE_VIDEO)
        self.assertEqual(create_video_tier.order, 1)

        attach_resp = self._json_post(
            "console_llm_video_generation_tier_endpoints",
            {"endpoint_id": str(endpoint.id), "weight": 1},
            create_video_tier_id,
        )
        self.assertEqual(attach_resp.status_code, 200, attach_resp.content)

        overview = build_llm_overview()
        self.assertEqual(len(overview["video_generations"]["create_video_tiers"]), 1)
        self.assertEqual(
            overview["video_generations"]["create_video_tiers"][0]["use_case"],
            "create_video",
        )

    def test_persistent_endpoint_allow_implied_send_is_exposed_and_mutable(self):
        endpoint = PersistentModelEndpoint.objects.create(
            provider=self.provider,
            key="persistent-default",
            litellm_model="openai/gpt-4o-mini",
            enabled=True,
        )
        self.assertTrue(endpoint.allow_implied_send)

        create_resp = self._json_post(
            "console_llm_persistent_endpoints",
            {
                "provider_id": str(self.provider.id),
                "key": "persistent-no-implied-send",
                "model": "openai/gpt-4.1-mini",
                "allow_implied_send": False,
            },
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.content)
        endpoint_id = create_resp.json()["endpoint_id"]

        created = PersistentModelEndpoint.objects.get(id=endpoint_id)
        self.assertFalse(created.allow_implied_send)

        patch_resp = self._json_patch(
            "console_llm_persistent_endpoint_detail",
            {"allow_implied_send": True},
            endpoint_id,
        )
        self.assertEqual(patch_resp.status_code, 200, patch_resp.content)
        created.refresh_from_db()
        self.assertTrue(created.allow_implied_send)

        overview = build_llm_overview()
        endpoint_payload = next(
            entry
            for provider in overview["providers"]
            if provider["id"] == str(self.provider.id)
            for entry in provider["endpoints"]
            if entry["id"] == str(created.id)
        )
        self.assertTrue(endpoint_payload["allow_implied_send"])
