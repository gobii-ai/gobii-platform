"""Unit tests for LLM failover (DB-only)."""
import os
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase, tag, override_settings
from django.utils import timezone

from api.agent.core.llm_config import (
    get_llm_config,
    get_llm_config_with_failover,
    PROVIDER_CONFIG,
    LLMNotConfiguredError,
    invalidate_llm_bootstrap_cache,
    get_provider_config,
    get_summarization_llm_config,
)
from api.openrouter import DEFAULT_API_BASE
from tests.utils.llm_seed import seed_persistent_basic, clear_llm_db


@tag("batch_event_llm")
class TestLLMFailover(TestCase):
    def setUp(self):  # noqa: D401
        super().setUp()
        invalidate_llm_bootstrap_cache()

    def _seed_premium_setup(self, include_premium: bool = True):
        LLMProvider = apps.get_model('api', 'LLMProvider')
        PersistentModelEndpoint = apps.get_model('api', 'PersistentModelEndpoint')
        PersistentTokenRange = apps.get_model('api', 'PersistentTokenRange')
        PersistentLLMTier = apps.get_model('api', 'PersistentLLMTier')
        PersistentTierEndpoint = apps.get_model('api', 'PersistentTierEndpoint')

        provider = LLMProvider.objects.create(
            key='anthropic',
            display_name='Anthropic',
            enabled=True,
            env_var_name='ANTHROPIC_API_KEY',
            browser_backend='ANTHROPIC',
        )
        premium_endpoint = PersistentModelEndpoint.objects.create(
            key='anthropic_premium',
            provider=provider,
            enabled=True,
            litellm_model='anthropic/premium-model',
            supports_tool_choice=True,
        )
        standard_endpoint = PersistentModelEndpoint.objects.create(
            key='anthropic_standard',
            provider=provider,
            enabled=True,
            litellm_model='anthropic/standard-model',
            supports_tool_choice=True,
        )

        token_range = PersistentTokenRange.objects.create(name='default', min_tokens=0, max_tokens=None)
        standard_tier = PersistentLLMTier.objects.create(token_range=token_range, order=1)
        PersistentTierEndpoint.objects.create(tier=standard_tier, endpoint=standard_endpoint, weight=1.0)

        if include_premium:
            premium_tier = PersistentLLMTier.objects.create(token_range=token_range, order=1, is_premium=True)
            PersistentTierEndpoint.objects.create(tier=premium_tier, endpoint=premium_endpoint, weight=1.0)

        return {
            "premium_endpoint": premium_endpoint,
            "standard_endpoint": standard_endpoint,
        }

    def _make_agent_stub(self, *, plan_id: str = "free", days_since_joined: int | None = 60):
        UserModel = get_user_model()
        user = UserModel.objects.create_user(
            username=f"user-{uuid.uuid4().hex[:8]}",
            email=f"{uuid.uuid4().hex[:8]}@example.com",
            password="test-pass",
        )
        if days_since_joined is not None:
            user.date_joined = timezone.now() - timedelta(days=days_since_joined)
            user.save(update_fields=["date_joined"])

        UserBilling = apps.get_model('api', 'UserBilling')
        billing, created = UserBilling.objects.get_or_create(
            user=user,
            defaults={"subscription": plan_id},
        )
        if not created and billing.subscription != plan_id:
            billing.subscription = plan_id
            billing.save(update_fields=["subscription"])

        return SimpleNamespace(id=uuid.uuid4(), user=user, organization=None)

    def test_simple_config_anthropic_primary(self):
        seed_persistent_basic(include_openrouter=False)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            model, params = get_llm_config()
            self.assertEqual(model, "anthropic/claude-sonnet-4-20250514")
            self.assertIn("temperature", params)

    def test_simple_config_google_primary(self):
        # Re-seed with Google-first (adjust weights by reseeding order)
        clear_llm_db()
        seed_persistent_basic(include_openrouter=False)
        # Provide only Google key so Anthropic endpoint is skipped
        with mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}, clear=True):
            model, params = get_llm_config()
            self.assertEqual(model, "vertex_ai/gemini-2.5-pro")
            self.assertIn("vertex_project", params)
            self.assertIn("vertex_location", params)

    def test_simple_config_no_providers(self):
        seed_persistent_basic(include_openrouter=False)
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(LLMNotConfiguredError):
                get_llm_config()

    def test_failover_config_includes_all_tier_endpoints(self):
        seed_persistent_basic(include_openrouter=True)
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
            "OPENROUTER_API_KEY": "openrouter-key",
        }, clear=True):
            configs = get_llm_config_with_failover(token_count=200)
            providers = [c[0] for c in configs]  # endpoint keys
            models = [c[1] for c in configs]
            self.assertIn("anthropic_sonnet4", providers)
            self.assertIn("google_gemini_25_pro", providers)
            self.assertIn("openrouter_glm_45", providers)
            self.assertIn("anthropic/claude-sonnet-4-20250514", models)
            self.assertIn("vertex_ai/gemini-2.5-pro", models)
            self.assertIn("openrouter/z-ai/glm-4.5", models)
            self.assertEqual(len(configs), 3)

    def test_provider_config_structure(self):
        """Provider config contains expected keys."""
        required_providers = ["anthropic", "google", "openai", "openai_gpt5", "openrouter_glm", "fireworks_qwen3_235b_a22b"]
        for provider in required_providers:
            self.assertIn(provider, PROVIDER_CONFIG)
            config = PROVIDER_CONFIG[provider]
            self.assertIn("env_var", config)
            self.assertIn("model", config)

    def test_failover_with_agent_id_logging(self):
        """Agent ID is properly passed through for logging."""
        seed_persistent_basic(include_openrouter=True)
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=True):
            configs = get_llm_config_with_failover(agent_id="test-agent-123")
            self.assertTrue(len(configs) >= 1)
            provider, _, _ = configs[0]
            # DB providers are endpoint keys
            self.assertIn(provider, ["openrouter_glm_45", "anthropic_sonnet4", "google_gemini_25_pro"])

    def test_openrouter_configs_include_attribution_headers(self):
        seed_persistent_basic(include_openrouter=True)
        referer = "https://example.com"
        title = "Example App"
        with override_settings(
            PUBLIC_SITE_URL=referer,
            PUBLIC_BRAND_NAME=title,
        ):
            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "openrouter-key"}, clear=True):
                configs = get_llm_config_with_failover(token_count=12000)
                openrouter_configs = [cfg for cfg in configs if cfg[0] == "openrouter_glm_45"]
                self.assertTrue(openrouter_configs)
                _, _, params = openrouter_configs[0]
                self.assertEqual(
                    params.get("extra_headers"),
                    {"HTTP-Referer": referer, "X-Title": title},
                )

    def test_get_provider_config_includes_openrouter_headers(self):
        referer = "https://example.com/app"
        title = "Example App"
        with override_settings(
            PUBLIC_SITE_URL=referer,
            PUBLIC_BRAND_NAME=title,
        ):
            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "openrouter-key"}, clear=True):
                model, params = get_provider_config("openrouter_glm")
                self.assertEqual(model, "openrouter/z-ai/glm-4.5")
                self.assertEqual(
                    params.get("extra_headers"),
                    {"HTTP-Referer": referer, "X-Title": title},
                )

    def test_gpt5_temperature_is_forced(self):
        """Runtime configuration coerces GPT-5 to temperature=1 even without overrides."""

        clear_llm_db()
        LLMProvider = apps.get_model('api', 'LLMProvider')
        PersistentModelEndpoint = apps.get_model('api', 'PersistentModelEndpoint')
        PersistentTokenRange = apps.get_model('api', 'PersistentTokenRange')
        PersistentLLMTier = apps.get_model('api', 'PersistentLLMTier')
        PersistentTierEndpoint = apps.get_model('api', 'PersistentTierEndpoint')

        provider = LLMProvider.objects.create(
            key='openai',
            display_name='OpenAI',
            enabled=True,
            env_var_name='OPENAI_API_KEY',
            browser_backend='OPENAI',
        )
        endpoint = PersistentModelEndpoint.objects.create(
            key='openai_primary',
            provider=provider,
            enabled=True,
            litellm_model='openai/gpt-5',
            supports_tool_choice=True,
        )

        token_range = PersistentTokenRange.objects.create(name='default', min_tokens=0, max_tokens=None)
        tier = PersistentLLMTier.objects.create(token_range=token_range, order=1)
        PersistentTierEndpoint.objects.create(tier=tier, endpoint=endpoint, weight=1.0)

        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            configs = get_llm_config_with_failover(token_count=0)

        self.assertTrue(configs)
        _, model, params = configs[0]
        self.assertEqual(model, 'openai/gpt-5')
        self.assertEqual(params.get("temperature"), 1.0)

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_premium_tiers_preferred_for_paid_plan(self):
        clear_llm_db()
        seeded = self._seed_premium_setup(include_premium=True)
        agent = self._make_agent_stub(plan_id="startup", days_since_joined=60)

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-premium"}, clear=True):
            configs = get_llm_config_with_failover(
                token_count=0,
                agent=agent,
                agent_id=str(agent.id),
            )

        self.assertTrue(configs)
        self.assertEqual(configs[0][0], seeded["premium_endpoint"].key)

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_premium_plan_falls_back_without_premium_tier(self):
        clear_llm_db()
        seeded = self._seed_premium_setup(include_premium=False)
        agent = self._make_agent_stub(plan_id="startup", days_since_joined=45)

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-standard"}, clear=True):
            configs = get_llm_config_with_failover(
                token_count=0,
                agent=agent,
                agent_id=str(agent.id),
            )

        self.assertTrue(configs)
        self.assertEqual(configs[0][0], seeded["standard_endpoint"].key)

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_new_account_prefers_premium_tier(self):
        clear_llm_db()
        seeded = self._seed_premium_setup(include_premium=True)
        agent = self._make_agent_stub(plan_id="free", days_since_joined=5)

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-premium"}, clear=True):
            configs = get_llm_config_with_failover(
                token_count=0,
                agent=agent,
                agent_id=str(agent.id),
            )

        self.assertTrue(configs)
        self.assertEqual(configs[0][0], seeded["premium_endpoint"].key)

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_summarization_prefers_premium_tier(self):
        clear_llm_db()
        seeded = self._seed_premium_setup(include_premium=True)
        agent = self._make_agent_stub(plan_id="startup", days_since_joined=60)

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-premium"}, clear=True):
            model, params = get_summarization_llm_config(agent=agent)

        self.assertEqual(model, seeded["premium_endpoint"].litellm_model)
        self.assertIn("temperature", params)


@tag("batch_event_llm")
class TestTokenBasedTierSelection(TestCase):
    """DB-only selection scenarios that formerly used token-based tiers."""

    def setUp(self):  # noqa: D401
        super().setUp()
        invalidate_llm_bootstrap_cache()

    def test_db_seeded_range_small_has_endpoints(self):
        seed_persistent_basic(include_openrouter=True)
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
            "OPENROUTER_API_KEY": "openrouter-key",
        }, clear=True):
            configs = get_llm_config_with_failover(token_count=2000)
            providers = [c[0] for c in configs]
            self.assertIn("anthropic_sonnet4", providers)
            self.assertIn("google_gemini_25_pro", providers)
            self.assertIn("openrouter_glm_45", providers)

    def test_db_seeded_selection_medium(self):
        seed_persistent_basic(include_openrouter=True)
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
            "OPENROUTER_API_KEY": "openrouter-key",
        }, clear=True):
            configs = get_llm_config_with_failover(token_count=15000)
            providers = [c[0] for c in configs]
            self.assertIn("openrouter_glm_45", providers)
            self.assertIn("anthropic_sonnet4", providers)

    def test_db_seeded_selection_large(self):
        seed_persistent_basic(include_openrouter=True)
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
        }, clear=True):
            configs = get_llm_config_with_failover(token_count=25000)
            providers = [c[0] for c in configs]
            self.assertIn("google_gemini_25_pro", providers)
            self.assertIn("anthropic_sonnet4", providers)

    # Legacy token-based structure tests have been removed in favor of DB-only selection.

    def test_db_seeded_distribution_medium(self):
        seed_persistent_basic(include_openrouter=True)
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
            "OPENROUTER_API_KEY": "openrouter-key",
        }, clear=True):
            counts = {"openrouter_glm_45": 0, "anthropic_sonnet4": 0, "google_gemini_25_pro": 0}
            for _ in range(50):
                configs = get_llm_config_with_failover(token_count=15000)
                first = configs[0][0]
                if first in counts:
                    counts[first] += 1
            self.assertGreater(sum(counts.values()), 0)


    def test_db_seeded_selection_large_includes_google(self):
        seed_persistent_basic(include_openrouter=True)
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
        }, clear=True):
            configs = get_llm_config_with_failover(token_count=25000)
            providers = [c[0] for c in configs]
            self.assertIn("google_gemini_25_pro", providers)

    def test_missing_providers_are_skipped(self):
        seed_persistent_basic(include_openrouter=True)
        with mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "google-key"}, clear=True):
            configs = get_llm_config_with_failover(token_count=2000)
            providers = [c[0] for c in configs]
            self.assertIn("google_gemini_25_pro", providers)

    def test_no_providers_raises(self):
        seed_persistent_basic(include_openrouter=False)
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(LLMNotConfiguredError):
                get_llm_config_with_failover(token_count=2000)

    # provider_tiers override is no longer supported in DB-only selection; removed.

    def test_weighted_distribution_db_seeded(self):
        seed_persistent_basic(include_openrouter=True)
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
            "OPENROUTER_API_KEY": "openrouter-key",
        }, clear=True):
            counts = {"openrouter_glm_45": 0, "anthropic_sonnet4": 0, "google_gemini_25_pro": 0}
            for _ in range(50):
                configs = get_llm_config_with_failover(token_count=15000)
                first = configs[0][0]
                if first in counts:
                    counts[first] += 1
            self.assertGreater(sum(counts.values()), 0)
