import os
import uuid
from unittest import mock

from django.apps import apps
from django.test import TestCase, tag

from api.agent.core.file_handler_config import get_file_handler_llm_config
from api.evals.execution import (
    get_current_eval_routing_profile,
    set_current_eval_routing_profile,
)
from tests.utils.llm_seed import clear_llm_db, get_intelligence_tier


@tag("batch_agent_tools")
class FileHandlerConfigTests(TestCase):
    def setUp(self):
        super().setUp()
        clear_llm_db()
        self._clear_file_handler_config()

    def tearDown(self):
        self._clear_file_handler_config()
        clear_llm_db()
        super().tearDown()

    def _clear_file_handler_config(self):
        FileHandlerTierEndpoint = apps.get_model("api", "FileHandlerTierEndpoint")
        FileHandlerLLMTier = apps.get_model("api", "FileHandlerLLMTier")
        FileHandlerModelEndpoint = apps.get_model("api", "FileHandlerModelEndpoint")
        FileHandlerTierEndpoint.objects.all().delete()
        FileHandlerLLMTier.objects.all().delete()
        FileHandlerModelEndpoint.objects.all().delete()

    def _make_provider(self):
        LLMProvider = apps.get_model("api", "LLMProvider")
        return LLMProvider.objects.create(
            key=f"openrouter-{uuid.uuid4().hex[:8]}",
            display_name="OpenRouter",
            enabled=True,
            env_var_name="OPENROUTER_API_KEY",
            browser_backend="OPENAI",
        )

    def _make_profile(self):
        LLMRoutingProfile = apps.get_model("api", "LLMRoutingProfile")
        return LLMRoutingProfile.objects.create(
            name=f"profile-{uuid.uuid4().hex[:8]}",
            display_name="Profile",
            is_active=False,
        )

    def _make_persistent_endpoint(self, provider, *, key: str, model: str, supports_vision: bool):
        PersistentModelEndpoint = apps.get_model("api", "PersistentModelEndpoint")
        return PersistentModelEndpoint.objects.create(
            key=key,
            provider=provider,
            enabled=True,
            litellm_model=model,
            supports_vision=supports_vision,
        )

    def _make_global_file_handler_endpoint(self, provider):
        FileHandlerModelEndpoint = apps.get_model("api", "FileHandlerModelEndpoint")
        FileHandlerLLMTier = apps.get_model("api", "FileHandlerLLMTier")
        FileHandlerTierEndpoint = apps.get_model("api", "FileHandlerTierEndpoint")
        endpoint = FileHandlerModelEndpoint.objects.create(
            key="global-file-handler",
            provider=provider,
            enabled=True,
            litellm_model="openrouter/global-file-handler",
            supports_vision=True,
        )
        tier = FileHandlerLLMTier.objects.create(order=1)
        FileHandlerTierEndpoint.objects.create(tier=tier, endpoint=endpoint, weight=1.0)
        return endpoint

    def _add_profile_tier_endpoint(self, profile, endpoint, *, tier_key: str = "standard"):
        ProfileTokenRange = apps.get_model("api", "ProfileTokenRange")
        ProfilePersistentTier = apps.get_model("api", "ProfilePersistentTier")
        ProfilePersistentTierEndpoint = apps.get_model("api", "ProfilePersistentTierEndpoint")
        token_range, _created = ProfileTokenRange.objects.get_or_create(
            profile=profile,
            name="default",
            defaults={
                "min_tokens": 0,
                "max_tokens": None,
            },
        )
        tier = ProfilePersistentTier.objects.create(
            token_range=token_range,
            order=1,
            intelligence_tier=get_intelligence_tier(tier_key),
        )
        ProfilePersistentTierEndpoint.objects.create(tier=tier, endpoint=endpoint, weight=1.0)

    def test_profile_vision_endpoint_wins_over_global_file_handler_tier(self):
        provider = self._make_provider()
        profile_endpoint = self._make_persistent_endpoint(
            provider,
            key="profile-tier-vision",
            model="openrouter/profile-tier-vision",
            supports_vision=True,
        )
        profile = self._make_profile()
        self._add_profile_tier_endpoint(profile, profile_endpoint)
        self._make_global_file_handler_endpoint(provider)

        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-openrouter"}, clear=True):
            config = get_file_handler_llm_config(routing_profile=profile)

        self.assertIsNotNone(config)
        self.assertEqual(config.endpoint_key, "profile-tier-vision")
        self.assertEqual(config.model, "openrouter/profile-tier-vision")
        self.assertTrue(config.supports_vision)

    def test_profile_without_vision_endpoint_falls_back_to_global_file_handler_tier(self):
        provider = self._make_provider()
        text_endpoint = self._make_persistent_endpoint(
            provider,
            key="profile-text-only",
            model="openrouter/text-only",
            supports_vision=False,
        )
        profile = self._make_profile()
        self._add_profile_tier_endpoint(profile, text_endpoint)
        self._make_global_file_handler_endpoint(provider)

        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-openrouter"}, clear=True):
            config = get_file_handler_llm_config(routing_profile=profile)

        self.assertIsNotNone(config)
        self.assertEqual(config.endpoint_key, "global-file-handler")
        self.assertEqual(config.model, "openrouter/global-file-handler")
        self.assertTrue(config.supports_vision)

    def test_profile_failover_can_use_higher_tier_vision_endpoint(self):
        provider = self._make_provider()
        standard_endpoint = self._make_persistent_endpoint(
            provider,
            key="profile-standard-text-only",
            model="openrouter/standard-text-only",
            supports_vision=False,
        )
        higher_vision_endpoint = self._make_persistent_endpoint(
            provider,
            key="profile-ultra-vision",
            model="openrouter/profile-ultra-vision",
            supports_vision=True,
        )
        profile = self._make_profile()
        self._add_profile_tier_endpoint(profile, standard_endpoint, tier_key="standard")
        self._add_profile_tier_endpoint(profile, higher_vision_endpoint, tier_key="ultra")
        self._make_global_file_handler_endpoint(provider)

        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-openrouter"}, clear=True):
            config = get_file_handler_llm_config(routing_profile=profile)

        self.assertIsNotNone(config)
        self.assertEqual(config.endpoint_key, "profile-ultra-vision")
        self.assertEqual(config.model, "openrouter/profile-ultra-vision")
        self.assertTrue(config.supports_vision)

    def test_profile_failover_falls_back_to_env_key_when_encrypted_key_is_bad(self):
        provider = self._make_provider()
        provider.api_key_encrypted = b"not-valid-ciphertext"
        provider.save(update_fields=["api_key_encrypted", "updated_at"])
        vision_endpoint = self._make_persistent_endpoint(
            provider,
            key="profile-tier-vision",
            model="openrouter/profile-tier-vision",
            supports_vision=True,
        )
        profile = self._make_profile()
        self._add_profile_tier_endpoint(profile, vision_endpoint)

        with (
            mock.patch("api.encryption.SecretsEncryption.decrypt_value", side_effect=ValueError("bad key")),
            mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-openrouter"}, clear=True),
        ):
            config = get_file_handler_llm_config(routing_profile=profile)

        self.assertIsNotNone(config)
        self.assertEqual(config.endpoint_key, "profile-tier-vision")
        self.assertEqual(config.params["api_key"], "sk-openrouter")

    def test_eval_routing_profile_is_used_without_explicit_argument(self):
        provider = self._make_provider()
        vision_endpoint = self._make_persistent_endpoint(
            provider,
            key="eval-profile-tier-vision",
            model="openrouter/eval-profile-tier-vision",
            supports_vision=True,
        )
        profile = self._make_profile()
        self._add_profile_tier_endpoint(profile, vision_endpoint)
        self._make_global_file_handler_endpoint(provider)
        previous_profile = get_current_eval_routing_profile()

        try:
            set_current_eval_routing_profile(profile)
            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-openrouter"}, clear=True):
                config = get_file_handler_llm_config()
        finally:
            set_current_eval_routing_profile(previous_profile)

        self.assertIsNotNone(config)
        self.assertEqual(config.endpoint_key, "eval-profile-tier-vision")
        self.assertEqual(config.model, "openrouter/eval-profile-tier-vision")
