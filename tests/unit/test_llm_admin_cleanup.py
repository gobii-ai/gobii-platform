from django.contrib import admin
from django.test import SimpleTestCase, tag

from api import admin as api_admin  # noqa: F401
from api.models import (
    BrowserLLMPolicy,
    BrowserLLMTier,
    BrowserModelEndpoint,
    EmbeddingsLLMTier,
    EmbeddingsModelEndpoint,
    FileHandlerLLMTier,
    FileHandlerModelEndpoint,
    ImageGenerationModelEndpoint,
    IntelligenceTier,
    LLMProvider,
    LLMRoutingProfile,
    PersistentLLMTier,
    PersistentModelEndpoint,
    PersistentTokenRange,
    ProfileBrowserTier,
    ProfileEmbeddingsTier,
    ProfilePersistentTier,
    ProfileTokenRange,
    VideoGenerationModelEndpoint,
)


@tag("batch_llm_admin_cleanup")
class LLMAdminCleanupTests(SimpleTestCase):
    def test_staff_managed_llm_models_are_not_registered_in_django_admin(self):
        staff_managed_models = (
            LLMProvider,
            PersistentModelEndpoint,
            PersistentTokenRange,
            PersistentLLMTier,
            EmbeddingsModelEndpoint,
            EmbeddingsLLMTier,
            FileHandlerModelEndpoint,
            FileHandlerLLMTier,
            ImageGenerationModelEndpoint,
            VideoGenerationModelEndpoint,
            BrowserModelEndpoint,
            BrowserLLMTier,
            LLMRoutingProfile,
            ProfileTokenRange,
            ProfilePersistentTier,
            ProfileBrowserTier,
            ProfileEmbeddingsTier,
        )

        for model in staff_managed_models:
            with self.subTest(model=model.__name__):
                self.assertFalse(admin.site.is_registered(model))

    def test_admin_only_llm_models_remain_registered(self):
        self.assertTrue(admin.site.is_registered(IntelligenceTier))
        self.assertTrue(admin.site.is_registered(BrowserLLMPolicy))
