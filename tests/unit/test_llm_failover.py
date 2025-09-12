"""Unit tests for LLM failover (DB-only)."""
import os
from unittest import mock

from django.test import TestCase, tag
from django.apps import apps
from api.agent.core.llm_config import (
    get_llm_config,
    get_llm_config_with_failover,
)


def seed_db_small_tier(anthropic_weight=1.0, google_weight=0.0, include_openrouter=False):
    LLMProvider = apps.get_model('api', 'LLMProvider')
    PersistentModelEndpoint = apps.get_model('api', 'PersistentModelEndpoint')
    PersistentTokenRange = apps.get_model('api', 'PersistentTokenRange')
    PersistentLLMTier = apps.get_model('api', 'PersistentLLMTier')
    PersistentTierEndpoint = apps.get_model('api', 'PersistentTierEndpoint')
    # Clear
    LLMProvider.objects.all().delete()
    PersistentModelEndpoint.objects.all().delete()
    PersistentTierEndpoint.objects.all().delete()
    PersistentLLMTier.objects.all().delete()
    PersistentTokenRange.objects.all().delete()
    # Providers
    a = LLMProvider.objects.create(key='anthropic', display_name='Anthropic', enabled=True, env_var_name='ANTHROPIC_API_KEY', browser_backend='ANTHROPIC')
    g = LLMProvider.objects.create(key='google', display_name='Google', enabled=True, env_var_name='GOOGLE_API_KEY', browser_backend='GOOGLE')
    endpoints = {}
    endpoints['anthro'] = PersistentModelEndpoint.objects.create(key='anthropic_sonnet4', provider=a, enabled=True, litellm_model='anthropic/claude-sonnet-4-20250514')
    endpoints['google'] = PersistentModelEndpoint.objects.create(key='google_gemini_25_pro', provider=g, enabled=True, litellm_model='vertex_ai/gemini-2.5-pro')
    if include_openrouter:
        o = LLMProvider.objects.create(key='openrouter', display_name='OpenRouter', enabled=True, env_var_name='OPENROUTER_API_KEY', browser_backend='OPENAI_COMPAT')
        endpoints['openrouter'] = PersistentModelEndpoint.objects.create(key='openrouter_glm_45', provider=o, enabled=True, litellm_model='openrouter/z-ai/glm-4.5')
    # Ranges
    small = PersistentTokenRange.objects.create(name='small', min_tokens=0, max_tokens=7500)
    tier1 = PersistentLLMTier.objects.create(token_range=small, order=1)
    if anthropic_weight > 0:
        PersistentTierEndpoint.objects.create(tier=tier1, endpoint=endpoints['anthro'], weight=anthropic_weight)
    if google_weight > 0:
        PersistentTierEndpoint.objects.create(tier=tier1, endpoint=endpoints['google'], weight=google_weight)
    if include_openrouter:
        # Add a second tier with openrouter
        tier2 = PersistentLLMTier.objects.create(token_range=small, order=2)
        PersistentTierEndpoint.objects.create(tier=tier2, endpoint=endpoints['openrouter'], weight=1.0)


@tag("batch_event_llm")
class TestLLMFailover(TestCase):
    def test_simple_config_anthropic_primary(self):
        seed_db_small_tier(anthropic_weight=1.0, google_weight=0.0)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            model, params = get_llm_config()
            self.assertEqual(model, "anthropic/claude-sonnet-4-20250514")
            self.assertIn("temperature", params)

    def test_simple_config_google_primary(self):
        seed_db_small_tier(anthropic_weight=0.0, google_weight=1.0)
        with mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}, clear=True):
            model, params = get_llm_config()
            self.assertEqual(model, "vertex_ai/gemini-2.5-pro")
            self.assertIn("vertex_project", params)
            self.assertIn("vertex_location", params)

    def test_simple_config_no_providers(self):
        seed_db_small_tier(anthropic_weight=1.0, google_weight=0.0)
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                get_llm_config()

    def test_failover_config_includes_all_tier_endpoints(self):
        seed_db_small_tier(anthropic_weight=0.75, google_weight=0.25, include_openrouter=True)
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
            "OPENROUTER_API_KEY": "openrouter-key",
        }, clear=True):
            configs = get_llm_config_with_failover(token_count=200)
            providers = [c[0] for c in configs]
            models = [c[1] for c in configs]
            self.assertIn("anthropic_sonnet4", providers)
            self.assertIn("google_gemini_25_pro", providers)
            self.assertIn("openrouter_glm_45", providers)
            self.assertIn("anthropic/claude-sonnet-4-20250514", models)
            self.assertIn("vertex_ai/gemini-2.5-pro", models)
            self.assertIn("openrouter/z-ai/glm-4.5", models)
            self.assertEqual(len(configs), 3)
            
            # All configs should include Google and Anthropic
            providers = [config[0] for config in configs]
            self.assertIn("google", providers)
            self.assertIn("anthropic", providers)

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
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=True):
            configs = get_llm_config_with_failover(agent_id="test-agent-123")
            self.assertTrue(len(configs) >= 1)
            provider, _, _ = configs[0]
            self.assertEqual(provider, "openrouter_glm")


@tag("batch_event_llm")
class TestTokenBasedTierSelection(TestCase):
    """Test token-based tier selection functionality."""

    def test_token_based_tier_configs_structure(self):
        """TOKEN_BASED_TIER_CONFIGS has expected structure."""
        self.assertIn("small", TOKEN_BASED_TIER_CONFIGS)
        self.assertIn("medium", TOKEN_BASED_TIER_CONFIGS)
        self.assertIn("large", TOKEN_BASED_TIER_CONFIGS)
        
        # Check small config (0-7500 tokens)
        small_config = TOKEN_BASED_TIER_CONFIGS["small"]
        self.assertEqual(small_config["range"], (0, 7500))
        self.assertEqual(len(small_config["tiers"]), 3)
        self.assertEqual(small_config["tiers"][0], [("openai_gpt5", 0.90), ("google", 0.10)])
        self.assertEqual(small_config["tiers"][1], [("google", 1.0)])
        self.assertEqual(small_config["tiers"][2], [("anthropic", 0.5), ("openrouter_glm", 0.5)])
        
        # Check medium config (7500-20000 tokens) - test structure not exact weights
        medium_config = TOKEN_BASED_TIER_CONFIGS["medium"]
        self.assertEqual(medium_config["range"], (7500, 20000))
        self.assertEqual(len(medium_config["tiers"]), 3)
        # Test that tier 1 has the expected providers, but not specific weights
        tier1_providers = [provider for provider, weight in medium_config["tiers"][0]]
        self.assertIn("openrouter_glm", tier1_providers)
        self.assertIn("fireworks_gpt_oss_120b", tier1_providers)
        self.assertIn("openai_gpt5", tier1_providers)
        # Verify weights sum to 1.0
        tier1_weights_sum = sum(weight for provider, weight in medium_config["tiers"][0])
        self.assertAlmostEqual(tier1_weights_sum, 1.0, places=3)
        
        # Check large config (20000+ tokens) - test structure not exact weights
        large_config = TOKEN_BASED_TIER_CONFIGS["large"]
        self.assertEqual(large_config["range"], (20000, float('inf')))
        self.assertEqual(len(large_config["tiers"]), 4)
        # Test that tier 1 has the expected providers, but not specific weights
        tier1_providers = [provider for provider, weight in large_config["tiers"][0]]
        self.assertIn("openrouter_glm", tier1_providers)
        self.assertIn("fireworks_gpt_oss_120b", tier1_providers)
        self.assertIn("openai_gpt5", tier1_providers)
        # Verify weights sum to 1.0
        tier1_weights_sum = sum(weight for provider, weight in large_config["tiers"][0])
        self.assertAlmostEqual(tier1_weights_sum, 1.0, places=3)
        self.assertEqual(large_config["tiers"][1], [("openai_gpt5", 1.0)])
        self.assertEqual(large_config["tiers"][2], [("anthropic", 1.0)])
        self.assertEqual(large_config["tiers"][3], [("fireworks_qwen3_235b_a22b", 1.0)])

    def test_get_tier_config_for_tokens_small_range(self):
        """Small token range returns GPT-5/Google split primary with Google secondary configuration."""
        config = get_tier_config_for_tokens(2000)
        self.assertEqual(len(config), 3)
        self.assertEqual(config[0], [("openai_gpt5", 0.90), ("google", 0.10)])
        self.assertEqual(config[1], [("google", 1.0)])
        self.assertEqual(config[2], [("anthropic", 0.5), ("openrouter_glm", 0.5)])

    def test_get_tier_config_for_tokens_medium_range(self):
        """Medium token range returns tier configuration with expected providers."""
        config = get_tier_config_for_tokens(15000)
        self.assertEqual(len(config), 3)
        # Test that tier 1 has expected providers without checking specific weights
        tier1_providers = [provider for provider, weight in config[0]]
        self.assertIn("openrouter_glm", tier1_providers)
        self.assertIn("fireworks_gpt_oss_120b", tier1_providers)
        self.assertIn("openai_gpt5", tier1_providers)
        # Verify weights sum to 1.0
        tier1_weights_sum = sum(weight for provider, weight in config[0])
        self.assertAlmostEqual(tier1_weights_sum, 1.0, places=3)
        self.assertEqual(config[1], [("openrouter_glm", 0.34), ("openai_gpt5", 0.33), ("anthropic", 0.33)])
        self.assertEqual(config[2], [("openai_gpt5", 1.0)])

    def test_get_tier_config_for_tokens_large_range(self):
        """Large token range returns tier configuration with expected providers and fallback."""
        config = get_tier_config_for_tokens(25000)
        self.assertEqual(len(config), 4)
        # Test that tier 1 has expected providers without checking specific weights
        tier1_providers = [provider for provider, weight in config[0]]
        self.assertIn("openrouter_glm", tier1_providers)
        self.assertIn("fireworks_gpt_oss_120b", tier1_providers)
        self.assertIn("openai_gpt5", tier1_providers)
        # Verify weights sum to 1.0
        tier1_weights_sum = sum(weight for provider, weight in config[0])
        self.assertAlmostEqual(tier1_weights_sum, 1.0, places=3)
        self.assertEqual(config[1], [("openai_gpt5", 1.0)])
        self.assertEqual(config[2], [("anthropic", 1.0)])
        self.assertEqual(config[3], [("fireworks_qwen3_235b_a22b", 1.0)])

    def test_get_tier_config_for_tokens_boundary_conditions(self):
        """Boundary conditions work correctly."""
        # Test exact boundaries
        self.assertEqual(get_tier_config_for_tokens(0), TOKEN_BASED_TIER_CONFIGS["small"]["tiers"])
        self.assertEqual(get_tier_config_for_tokens(7499), TOKEN_BASED_TIER_CONFIGS["small"]["tiers"])
        self.assertEqual(get_tier_config_for_tokens(7500), TOKEN_BASED_TIER_CONFIGS["medium"]["tiers"])
        self.assertEqual(get_tier_config_for_tokens(19999), TOKEN_BASED_TIER_CONFIGS["medium"]["tiers"])
        self.assertEqual(get_tier_config_for_tokens(20000), TOKEN_BASED_TIER_CONFIGS["large"]["tiers"])

    def test_token_based_failover_small_range(self):
        """Token-based failover works for small token range."""
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
            "OPENROUTER_API_KEY": "openrouter-key",
            "OPENAI_API_KEY": "openai-key",
        }, clear=True):
            configs = get_llm_config_with_failover(token_count=2000)
            # Should have providers from all tiers
            self.assertGreaterEqual(len(configs), 4)
            
            # First provider should be either GPT-5 (75% chance) or Google (25% chance)
            provider1, model1, _ = configs[0]
            self.assertIn(provider1, ["openai_gpt5", "google"])
            if provider1 == "openai_gpt5":
                self.assertEqual(model1, "openai/gpt-5")
            else:
                self.assertEqual(model1, "vertex_ai/gemini-2.5-pro")
            
            # All providers should be included
            providers = [config[0] for config in configs]
            self.assertIn("openai_gpt5", providers)
            self.assertIn("openrouter_glm", providers)
            self.assertIn("google", providers)
            self.assertIn("anthropic", providers)

    def test_token_based_failover_medium_range(self):
        """Token-based failover works for medium token range."""
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
            "OPENROUTER_API_KEY": "openrouter-key",
        }, clear=True):
            configs = get_llm_config_with_failover(token_count=15000)
            # Medium range now includes Google in tier 1; expect providers from tier 1 and tier 2
            self.assertGreaterEqual(len(configs), 3)
            
            # First provider should be from tier 1; with Google and OpenRouter available,
            # it should be either openrouter_glm or google depending on weighted order
            provider1, model1, _ = configs[0]
            self.assertIn(provider1, ["openrouter_glm", "google"]) 
            if provider1 == "openrouter_glm":
                self.assertEqual(model1, "openrouter/z-ai/glm-4.5")
            else:
                self.assertEqual(model1, "vertex_ai/gemini-2.5-pro")
            
            # Tier 2 has weighted split, tier 3 would be GPT-5 but not available
            providers = [config[0] for config in configs]
            models = [config[1] for config in configs]
            
            self.assertIn("openrouter_glm", providers)
            self.assertIn("anthropic", providers)
            # Google is now included in the medium token tier configuration (tier 1)
            self.assertIn("openrouter/z-ai/glm-4.5", models)
            self.assertIn("anthropic/claude-sonnet-4-20250514", models)
            # Google model may be present from tier 1 depending on weighted order

    def test_token_based_failover_large_range(self):
        """Token-based failover works for large token range."""
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
        }, clear=True):
            configs = get_llm_config_with_failover(token_count=25000)
            # Large tier now includes Google in tier 1; expect at least Google and Anthropic in failover list
            self.assertGreaterEqual(len(configs), 2)
            
            # With Google available in tier 1, first provider should be Google
            provider1, model1, _ = configs[0]
            self.assertEqual(provider1, "google")
            self.assertEqual(model1, "vertex_ai/gemini-2.5-pro")

    def test_token_based_failover_missing_providers(self):
        """Token-based failover gracefully handles missing API keys."""
        # Test with only Google available for small range (normally includes DeepSeek)
        with mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "google-key"}, clear=True):
            configs = get_llm_config_with_failover(token_count=2000)
            # Should fall back to available providers only
            self.assertGreaterEqual(len(configs), 1)
            providers = [config[0] for config in configs]
            self.assertIn("google", providers)
            self.assertNotIn("openrouter_glm", providers)  # Not available without API key

    def test_token_based_failover_no_providers(self):
        """Token-based failover raises error when no providers available."""
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                get_llm_config_with_failover(token_count=10000)

    def test_provider_tiers_overrides_token_count(self):
        """Explicit provider_tiers parameter overrides token count selection."""
        custom_tiers = [[("google", 1.0)], [("anthropic", 1.0)]]
        
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
        }, clear=True):
            configs = get_llm_config_with_failover(
                provider_tiers=custom_tiers,
                token_count=2000  # This is ignored when provider_tiers is provided
            )
            
            # Should use custom_tiers, not token-based config
            self.assertEqual(len(configs), 2)
            providers = [config[0] for config in configs]
            self.assertEqual(providers[0], "google")
            self.assertEqual(providers[1], "anthropic")

    def test_weighted_selection_distribution(self):
        """Weighted selection produces expected distribution over multiple calls."""
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
            "OPENROUTER_API_KEY": "openrouter-key",
            "OPENAI_API_KEY": "openai-key",
        }, clear=True):
            # Test medium range - should get weighted distribution from tier 1 (45% GLM, 45% GPT-OSS, 10% GPT-5)
            # But since FIREWORKS_AI_API_KEY is not set, GPT-OSS won't be available
            provider_counts = {"google": 0, "anthropic": 0, "openrouter_glm": 0, "openai_gpt5": 0}
            num_tests = 100
            
            for _ in range(num_tests):
                configs = get_llm_config_with_failover(token_count=15000)
                first_provider = configs[0][0]
                if first_provider in provider_counts:
                    provider_counts[first_provider] += 1
            
            # Medium range tier 1: 45% GLM, 45% GPT-OSS (not available), 10% GPT-5
            # Should get mix of openrouter_glm and openai_gpt5 from tier 1
            openrouter_percentage = provider_counts["openrouter_glm"] / num_tests
            gpt5_percentage = provider_counts["openai_gpt5"] / num_tests
            
            # Should have some distribution between available providers from tier 1
            self.assertGreater(openrouter_percentage + gpt5_percentage, 0.8)  # Most should be from tier 1
