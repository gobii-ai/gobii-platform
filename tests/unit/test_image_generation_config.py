from django.test import TestCase, tag

from api.agent.core.image_generation_config import (
    get_image_generation_llm_configs,
    is_image_generation_configured,
)
from api.models import (
    ImageGenerationLLMTier,
    ImageGenerationModelEndpoint,
    ImageGenerationTierEndpoint,
    LLMProvider,
)


@tag("batch_agent_short_description")
class ImageGenerationConfigTests(TestCase):
    def setUp(self):
        self.provider = LLMProvider.objects.create(
            key="test-image-provider",
            display_name="Test Image Provider",
            enabled=True,
        )

    def _add_tier_endpoint(
        self,
        *,
        tier_use_case: str,
        tier_order: int,
        endpoint_key: str,
    ) -> None:
        endpoint = ImageGenerationModelEndpoint.objects.create(
            key=endpoint_key,
            provider=self.provider,
            enabled=True,
            litellm_model=f"{endpoint_key}-model",
            api_base="https://example.com/v1",
        )
        tier = ImageGenerationLLMTier.objects.create(
            use_case=tier_use_case,
            order=tier_order,
            description=f"{tier_use_case} tier {tier_order}",
        )
        ImageGenerationTierEndpoint.objects.create(
            tier=tier,
            endpoint=endpoint,
            weight=1.0,
        )

    def test_create_image_configs_ignore_avatar_tiers(self):
        self._add_tier_endpoint(
            tier_use_case=ImageGenerationLLMTier.UseCase.AVATAR,
            tier_order=1,
            endpoint_key="avatar-only",
        )

        configs = get_image_generation_llm_configs(use_case=ImageGenerationLLMTier.UseCase.CREATE_IMAGE)

        self.assertEqual(configs, [])
        self.assertFalse(
            is_image_generation_configured(use_case=ImageGenerationLLMTier.UseCase.CREATE_IMAGE)
        )

    def test_avatar_configs_prefer_avatar_tiers_over_create_image_fallback(self):
        self._add_tier_endpoint(
            tier_use_case=ImageGenerationLLMTier.UseCase.CREATE_IMAGE,
            tier_order=1,
            endpoint_key="create-image-endpoint",
        )
        self._add_tier_endpoint(
            tier_use_case=ImageGenerationLLMTier.UseCase.AVATAR,
            tier_order=1,
            endpoint_key="avatar-endpoint",
        )

        configs = get_image_generation_llm_configs(
            use_case=ImageGenerationLLMTier.UseCase.AVATAR,
            fallback_use_cases=(ImageGenerationLLMTier.UseCase.CREATE_IMAGE,),
        )

        self.assertEqual([config.endpoint_key for config in configs], ["avatar-endpoint"])

    def test_avatar_configs_fall_back_to_create_image_when_avatar_tiers_absent(self):
        self._add_tier_endpoint(
            tier_use_case=ImageGenerationLLMTier.UseCase.CREATE_IMAGE,
            tier_order=1,
            endpoint_key="create-image-endpoint",
        )

        configs = get_image_generation_llm_configs(
            use_case=ImageGenerationLLMTier.UseCase.AVATAR,
            fallback_use_cases=(ImageGenerationLLMTier.UseCase.CREATE_IMAGE,),
        )

        self.assertEqual([config.endpoint_key for config in configs], ["create-image-endpoint"])
        self.assertTrue(
            is_image_generation_configured(
                use_case=ImageGenerationLLMTier.UseCase.AVATAR,
                fallback_use_cases=(ImageGenerationLLMTier.UseCase.CREATE_IMAGE,),
            )
        )
