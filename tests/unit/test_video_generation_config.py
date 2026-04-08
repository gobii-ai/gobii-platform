from django.test import TestCase, tag

from api.agent.core.video_generation_config import (
    get_video_generation_llm_configs,
    is_video_generation_configured,
)
from api.models import (
    VideoGenerationLLMTier,
    VideoGenerationModelEndpoint,
    VideoGenerationTierEndpoint,
    LLMProvider,
)


@tag("batch_video_generation")
class VideoGenerationConfigTests(TestCase):
    def setUp(self):
        self.provider = LLMProvider.objects.create(
            key="test-video-provider",
            display_name="Test Video Provider",
            enabled=True,
        )

    def _add_tier_endpoint(
        self,
        *,
        tier_use_case: str = VideoGenerationLLMTier.UseCase.CREATE_VIDEO,
        tier_order: int,
        endpoint_key: str,
        supports_image_to_video: bool = False,
    ) -> None:
        endpoint = VideoGenerationModelEndpoint.objects.create(
            key=endpoint_key,
            provider=self.provider,
            enabled=True,
            litellm_model=f"{endpoint_key}-model",
            api_base="https://example.com/v1",
            supports_image_to_video=supports_image_to_video,
        )
        tier, _ = VideoGenerationLLMTier.objects.get_or_create(
            use_case=tier_use_case,
            order=tier_order,
            defaults={"description": f"{tier_use_case} tier {tier_order}"},
        )
        VideoGenerationTierEndpoint.objects.create(
            tier=tier,
            endpoint=endpoint,
            weight=1.0,
        )

    def test_no_configs_when_no_endpoints(self):
        configs = get_video_generation_llm_configs(
            use_case=VideoGenerationLLMTier.UseCase.CREATE_VIDEO,
        )
        self.assertEqual(configs, [])
        self.assertFalse(
            is_video_generation_configured(
                use_case=VideoGenerationLLMTier.UseCase.CREATE_VIDEO,
            )
        )

    def test_returns_configs_when_endpoint_exists(self):
        self._add_tier_endpoint(
            tier_order=1,
            endpoint_key="sora-endpoint",
        )

        configs = get_video_generation_llm_configs(
            use_case=VideoGenerationLLMTier.UseCase.CREATE_VIDEO,
        )

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].endpoint_key, "sora-endpoint")
        self.assertTrue(
            is_video_generation_configured(
                use_case=VideoGenerationLLMTier.UseCase.CREATE_VIDEO,
            )
        )

    def test_supports_image_to_video_flag(self):
        self._add_tier_endpoint(
            tier_order=1,
            endpoint_key="video-with-img",
            supports_image_to_video=True,
        )

        configs = get_video_generation_llm_configs(
            use_case=VideoGenerationLLMTier.UseCase.CREATE_VIDEO,
        )

        self.assertTrue(configs[0].supports_image_to_video)

    def test_disabled_endpoint_excluded(self):
        self._add_tier_endpoint(
            tier_order=1,
            endpoint_key="disabled-ep",
        )
        VideoGenerationModelEndpoint.objects.filter(key="disabled-ep").update(enabled=False)

        configs = get_video_generation_llm_configs(
            use_case=VideoGenerationLLMTier.UseCase.CREATE_VIDEO,
        )
        self.assertEqual(configs, [])

    def test_disabled_provider_excluded(self):
        self._add_tier_endpoint(
            tier_order=1,
            endpoint_key="disabled-prov-ep",
        )
        self.provider.enabled = False
        self.provider.save()

        configs = get_video_generation_llm_configs(
            use_case=VideoGenerationLLMTier.UseCase.CREATE_VIDEO,
        )
        self.assertEqual(configs, [])

    def test_multiple_tiers_ordered(self):
        self._add_tier_endpoint(tier_order=2, endpoint_key="tier-2-ep")
        self._add_tier_endpoint(tier_order=1, endpoint_key="tier-1-ep")

        configs = get_video_generation_llm_configs(
            use_case=VideoGenerationLLMTier.UseCase.CREATE_VIDEO,
        )

        self.assertEqual(
            [c.endpoint_key for c in configs],
            ["tier-1-ep", "tier-2-ep"],
        )

    def test_limit_respected(self):
        self._add_tier_endpoint(tier_order=1, endpoint_key="ep-1")
        self._add_tier_endpoint(tier_order=2, endpoint_key="ep-2")
        self._add_tier_endpoint(tier_order=3, endpoint_key="ep-3")

        configs = get_video_generation_llm_configs(
            use_case=VideoGenerationLLMTier.UseCase.CREATE_VIDEO,
            limit=2,
        )

        self.assertEqual(len(configs), 2)
