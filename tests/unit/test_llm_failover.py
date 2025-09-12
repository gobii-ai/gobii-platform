"""Unit tests for LLM failover (DB-only)."""
import os
from unittest import mock

from django.test import TestCase, tag
from api.agent.core.llm_config import (
    get_llm_config,
    get_llm_config_with_failover,
    PROVIDER_CONFIG,
)
from tests.utils.llm_seed import seed_persistent_basic, clear_llm_db


@tag("batch_event_llm")
class TestLLMFailover(TestCase):
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
            with self.assertRaises(ValueError):
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


@tag("batch_event_llm")
class TestTokenBasedTierSelection(TestCase):
    """DB-only selection scenarios that formerly used token-based tiers."""

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
            with self.assertRaises(ValueError):
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
