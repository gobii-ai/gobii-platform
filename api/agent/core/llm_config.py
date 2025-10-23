"""
Common LiteLLM configuration for persistent agents.

This module provides a unified way to configure LiteLLM with tiered failover:
1. Vertex AI Gemini 2.5 Pro (primary)
2. Anthropic Claude Sonnet 4 (fallback)

The configuration uses a similar pattern to browser use tasks for consistency.
"""
import os
import logging
import random
from typing import Dict, List, Tuple, Any

from django.apps import apps
from django.core.cache import cache
from django.db import connection
from django.db.models import Q
from django.conf import settings

from api.openrouter import get_attribution_headers

logger = logging.getLogger(__name__)


class LLMNotConfiguredError(RuntimeError):
    """Raised when no LLM providers/endpoints are available for use."""


_LLM_BOOTSTRAP_CACHE_KEY = "llm_bootstrap_required:v1"
_LLM_BOOTSTRAP_CACHE_TTL = 30  # seconds

# MODEL TESTING NOTES FOR PERSISTENT AGENTS:
# - GLM-4.5 (OpenRouter): PASSED manual testing - works well with persistent agents
# - Qwen3-235B (Fireworks): NOT WORKING GREAT - performance issues with persistent agents
# - DeepSeek V3.1 (Fireworks): NOT WORKING WELL - issues with persistent agents
# - GPT-OSS-120B (Fireworks): WORKING WELL - good performance with persistent agents
# - Kimi K2 Instruct (Fireworks): NOT GOOD - too loopy behavior, not suitable for persistent agents
# - Add other model test results here as we validate them...

# Provider configuration mapping provider names to environment variables and models
PROVIDER_CONFIG: Dict[str, Dict[str, str]] = {
    "anthropic": {
        "env_var": "ANTHROPIC_API_KEY",
        "model": "anthropic/claude-sonnet-4-20250514"
    },
    "google": {
        "env_var": "GOOGLE_API_KEY", 
        "model": "vertex_ai/gemini-2.5-pro"
    },
    "openai": {
        "env_var": "OPENAI_API_KEY",
        "model": "openai/gpt-4.1"
    },
    "openai_gpt5": {
        "env_var": "OPENAI_API_KEY",
        "model": "openai/gpt-5"
    },
    "openrouter_glm": {
        "env_var": "OPENROUTER_API_KEY",
        "model": "openrouter/z-ai/glm-4.5"
    },
    "fireworks_qwen3_235b_a22b": {
        "env_var": "FIREWORKS_AI_API_KEY",
        "model": "fireworks_ai/accounts/fireworks/models/qwen3-235b-a22b-instruct-2507"
    },
    "fireworks_deepseek_v31": {
        "env_var": "FIREWORKS_AI_API_KEY",
        "model": "fireworks_ai/accounts/fireworks/models/deepseek-v3p1"
    },
    "fireworks_gpt_oss_120b": {
        "env_var": "FIREWORKS_AI_API_KEY",
        "model": "fireworks_ai/accounts/fireworks/models/gpt-oss-120b"
    },
    "fireworks_kimi_k2_instruct": {
        "env_var": "FIREWORKS_AI_API_KEY",
        "model": "fireworks_ai/accounts/fireworks/models/kimi-k2-instruct"
    }
}

# Reference model for consistent token counting before a model is selected
REFERENCE_TOKENIZER_MODEL = "openai/gpt-4o"


# Token-based tier configurations
TOKEN_BASED_TIER_CONFIGS = {
    # 0-7500 tokens: GPT-5/Google split primary, then Google, then Anthropic/GLM-4.5 split
    "small": {
        "range": (0, 7500),
        "tiers": [
            [("openai_gpt5", 0.90), ("google", 0.10)],  # Tier 1: 90% GPT-5, 10% Google Gemini 2.5 Pro
            [("google", 1.0)],  # Tier 2: 100% Google Gemini 2.5 Pro
            [("anthropic", 0.5), ("openrouter_glm", 0.5)],  # Tier 3: 50/50 Anthropic/GLM-4.5 split
        ]
    },
    # 7500-20000 tokens: 70% GLM-4.5, 10% Google Gemini 2.5 Pro, 10% GPT-5, 10% GPT-OSS-120B
    "medium": {
        "range": (7500, 20000),
        "tiers": [
            [("openrouter_glm", 0.70), ("google", 0.10), ("openai_gpt5", 0.10), ("fireworks_gpt_oss_120b", 0.10)],  # Tier 1: 70% GLM-4.5, 10% Google, 10% GPT-5, 10% GPT-OSS-120B
            [("openrouter_glm", 0.34), ("openai_gpt5", 0.33), ("anthropic", 0.33)],  # Tier 2: Even split between GLM-4.5, GPT-5, and Anthropic
            [("openai_gpt5", 1.0)],  # Tier 3: 100% GPT-5 (last resort)
        ]
    },
    # 20000+ tokens: 70% GLM-4.5, 10% Google Gemini 2.5 Pro, 10% GPT-5, 10% GPT-OSS-120B
    "large": {
        "range": (20000, float('inf')),
        "tiers": [
            [("openrouter_glm", 0.70), ("google", 0.10), ("openai_gpt5", 0.10), ("fireworks_gpt_oss_120b", 0.10)],  # Tier 1: 70% GLM-4.5, 10% Google, 10% GPT-5, 10% GPT-OSS-120B
            [("openai_gpt5", 1.0)],  # Tier 2: 100% GPT-5
            [("anthropic", 1.0)],  # Tier 3: 100% Anthropic (Sonnet 4)
            [("fireworks_qwen3_235b_a22b", 1.0)],  # Tier 4: 100% Fireworks Qwen3-235B (last resort)
        ]
    }
}


def get_tier_config_for_tokens(token_count: int) -> List[List[Tuple[str, float]]]:
    """
    Get the appropriate tier configuration based on token count.
    
    Args:
        token_count: Estimated token count for the request
        
    Returns:
        List of tiers with provider weights for the given token range
    """
    for config_name, config in TOKEN_BASED_TIER_CONFIGS.items():
        min_tokens, max_tokens = config["range"]
        if min_tokens <= token_count < max_tokens:
            logger.debug(
                "Selected %s tier config for %d tokens (range: %d-%s)",
                config_name,
                token_count,
                min_tokens,
                "∞" if max_tokens == float('inf') else str(max_tokens)
            )
            return config["tiers"]
    
    # This shouldn't happen since we cover 0 to infinity, but fallback to small tier
    logger.warning("No tier config found for %d tokens, using small tier as fallback", token_count)
    return TOKEN_BASED_TIER_CONFIGS["small"]["tiers"]


def get_llm_config() -> Tuple[str, dict]:
    """DB-only: Return the first configured LiteLLM model+params.

    Uses the DB-backed tier selection. When no configuration exists yet,
    this raises :class:`LLMNotConfiguredError` so callers can handle the
    bootstrap flow (e.g., the setup wizard) without crashing the app.
    """
    try:
        configs = get_llm_config_with_failover(token_count=0, allow_unconfigured=True)
    except Exception as exc:
        raise LLMNotConfiguredError("LLM configuration unavailable") from exc

    if not configs:
        raise LLMNotConfiguredError(
            "No LLM provider available. Complete the setup wizard or supply credentials first."
        )

    _provider_key, model, params = configs[0]
    # Remove any internal-only hints that shouldn't be passed to litellm
    params = {
        k: v
        for k, v in params.items()
        if k not in ("supports_tool_choice", "use_parallel_tool_calls", "supports_vision")
    }
    return model, params


def get_provider_config(provider: str) -> Tuple[str, dict]:
    """
    Get the model name and parameters for a specific provider.
    
    Args:
        provider: Provider name (anthropic, google, openai, openrouter)
        
    Returns:
        Tuple of (model_name, litellm_params)
        
    Raises:
        ValueError: If provider is unknown or API key is missing
    """
    if provider not in PROVIDER_CONFIG:
        raise ValueError(f"Unknown provider: {provider}")
    
    config = PROVIDER_CONFIG[provider]
    env_var = config["env_var"]
    model = config["model"]
    
    api_key = os.getenv(env_var)
    if not api_key:
        raise ValueError(f"Missing API key for {provider}. Set {env_var}")
    
    params = {"temperature": 0.1}
    
    # Add provider-specific parameters
    if provider == "google":
        params.update({
            "vertex_project": os.getenv("GOOGLE_CLOUD_PROJECT", "browser-use-458714"),
            "vertex_location": os.getenv("GOOGLE_CLOUD_LOCATION", "us-east4"),
        })
    elif provider == "openrouter_glm":
        headers = get_attribution_headers()
        if headers:
            params["extra_headers"] = headers
    elif provider == "openai_gpt5":
        # GPT-5 specific parameters
        # Note: GPT-5 only supports temperature=1
        params.update({
            "temperature": 1,  # GPT-5 only supports temperature=1
        })
    
    return model, params


def get_available_providers(provider_tiers: List[List[Tuple[str, float]]] = None) -> List[str]:
    """
    Get list of providers that have valid API keys available.
    
    Args:
        provider_tiers: Optional provider tier configuration
        
    Returns:
        List of provider names that have valid API keys
    """
    provider_tiers = provider_tiers or TOKEN_BASED_TIER_CONFIGS["small"]["tiers"]
    
    available = []
    for tier in provider_tiers:
        for provider, _ in tier:
            if provider in PROVIDER_CONFIG:
                env_var = PROVIDER_CONFIG[provider]["env_var"]
                if os.getenv(env_var):
                    available.append(provider)
    
    return available


def get_llm_config_with_failover(
    provider_tiers: List[List[Tuple[str, float]]] = None,
    agent_id: str = None,
    token_count: int = 0,
    *,
    allow_unconfigured: bool = False,
) -> List[Tuple[str, str, dict]]:
    """
    Get LLM configurations for tiered failover with token-based tier selection.
    
    Args:
        provider_tiers: Optional custom provider tier configuration.
                       If None, uses token-based tiers based on token_count
        agent_id: Optional agent ID for logging
        token_count: Token count for automatic tier selection (default: 0).
                    Used to select appropriate tier when provider_tiers is None.
        
    Returns:
        List of (provider_name, model_name, litellm_params) tuples in failover order

    Raises:
        LLMNotConfiguredError: If no providers are available with valid API keys (unless allow_unconfigured=True)
    """
    # Always attempt DB-backed configuration first; fallback to legacy when empty
    try:
        PersistentTokenRange = apps.get_model('api', 'PersistentTokenRange')
        PersistentLLMTier = apps.get_model('api', 'PersistentLLMTier')

        

        token_range = (
            PersistentTokenRange.objects
            .filter(min_tokens__lte=token_count)
            .filter(Q(max_tokens__gt=token_count) | Q(max_tokens__isnull=True))
            .order_by('min_tokens')
            .last()
        )
    except Exception:
        token_range = None

    if token_range is not None:
        failover_configs: List[Tuple[str, str, dict]] = []
        tiers = PersistentLLMTier.objects.filter(token_range=token_range).order_by('order')
        for tier_idx, tier in enumerate(tiers, start=1):
            # Build usable endpoints in this tier
            endpoints_with_weights = []
            for te in tier.tier_endpoints.select_related('endpoint__provider').all():
                endpoint = te.endpoint
                provider = endpoint.provider
                if not (provider.enabled and endpoint.enabled):
                    continue
                # Effective key present?
                has_admin_key = bool(provider.api_key_encrypted)
                has_env_key = bool(provider.env_var_name and os.getenv(provider.env_var_name))
                # Determine the effective model name, auto-prefixing OpenAI-compatible endpoints when needed
                raw_model = endpoint.litellm_model or ""
                has_api_base = bool(getattr(endpoint, 'api_base', None))
                provider_backend = getattr(provider, 'browser_backend', '')
                is_openai_backend = provider_backend in ("OPENAI", "OPENAI_COMPAT")
                should_auto_prefix = (
                    is_openai_backend
                    and has_api_base
                    and raw_model
                    and '/' not in raw_model
                )
                if should_auto_prefix:
                    effective_model = f"openai/{raw_model}"
                    logger.info(
                        "Auto-prefixed OpenAI-compatible model: endpoint=%s provider=%s original_model=%s prefixed_model=%s api_base=%s",
                        endpoint.key,
                        provider.key,
                        raw_model,
                        effective_model,
                        endpoint.api_base,
                    )
                else:
                    effective_model = raw_model

                # Allow OpenAI-compatible endpoints with no key (api_base + openai/ prefix)
                is_openai_compat = effective_model.startswith('openai/') and has_api_base
                if not (has_admin_key or has_env_key or is_openai_compat):
                    # Skip endpoints that truly require a key but none is configured
                    logger.info(
                        "DB LLM skip endpoint (no key): range=%s tier=%s endpoint=%s provider=%s model=%s api_base=%s",
                        token_range.name,
                        tier.order,
                        endpoint.key,
                        provider.key,
                        effective_model,
                        getattr(endpoint, 'api_base', '') or ''
                    )
                    continue
                endpoints_with_weights.append((endpoint, provider, te.weight, effective_model))

            if not endpoints_with_weights:
                continue

            remaining = endpoints_with_weights.copy()
            while remaining:
                weights = [r[2] for r in remaining]
                selected_idx = random.choices(range(len(remaining)), weights=weights, k=1)[0]
                endpoint, provider, _w, effective_model = remaining.pop(selected_idx)

                params: Dict[str, Any] = {"temperature": 0.1}
                # Inject API key directly into LiteLLM params (DB-only routing).
                try:
                    effective_key = None
                    if provider.api_key_encrypted:
                        from api.encryption import SecretsEncryption
                        effective_key = SecretsEncryption.decrypt_value(provider.api_key_encrypted)
                    if not effective_key and provider.env_var_name:
                        effective_key = os.getenv(provider.env_var_name)
                    if effective_key:
                        params["api_key"] = effective_key
                    else:
                        # For OpenAI-compatible proxies that allow no auth, pass a dummy key
                        if endpoint.litellm_model.startswith('openai/') and getattr(endpoint, 'api_base', None):
                            params["api_key"] = "sk-noauth"
                except Exception:
                    pass
                if endpoint.temperature_override is not None:
                    params["temperature"] = float(endpoint.temperature_override)
                if provider.key == 'google':
                    vertex_project = provider.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT", "browser-use-458714")
                    vertex_location = provider.vertex_location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-east4")
                    params.update({
                        "vertex_project": vertex_project,
                        "vertex_location": vertex_location,
                    })
                if provider.key == 'openrouter':
                    headers = get_attribution_headers()
                    if headers:
                        params["extra_headers"] = headers

                # Support OpenAI-compatible endpoints for persistent agents via LiteLLM
                # When using an OpenAI-compatible proxy, set litellm_model to 'openai/<your-model>'
                # and configure api_base on the endpoint (e.g., http://vllm-host:port/v1)
                if effective_model.startswith('openai/') and getattr(endpoint, 'api_base', None):
                    params["api_base"] = endpoint.api_base
                    logger.info(
                        "DB LLM endpoint configured with api_base: endpoint=%s provider=%s model=%s api_base=%s has_key=%s",
                        endpoint.key,
                        provider.key,
                        effective_model,
                        endpoint.api_base,
                        bool(params.get('api_key')),
                    )

                # Add tool-choice capability hint for callers (not passed to litellm)
                params_with_hints = dict(params)
                params_with_hints["supports_tool_choice"] = bool(endpoint.supports_tool_choice)
                params_with_hints["supports_vision"] = bool(getattr(endpoint, "supports_vision", False))
                # Expose whether the endpoint prefers parallel tool-calling. This is a caller hint.
                try:
                    params_with_hints["use_parallel_tool_calls"] = bool(getattr(endpoint, "use_parallel_tool_calls", True))
                except Exception:
                    params_with_hints["use_parallel_tool_calls"] = True
                failover_configs.append((endpoint.key, effective_model, params_with_hints))

        if failover_configs:
            _cache_bootstrap_status(False)
            return failover_configs

    if allow_unconfigured:
        _cache_bootstrap_status(True)
        return []

    _cache_bootstrap_status(True)
    raise LLMNotConfiguredError(
        "No LLM providers are currently configured. Complete the setup wizard before running agents."
    )


def get_summarization_llm_config() -> Tuple[str, dict]:
    """
    Get LiteLLM configuration specifically for summarization tasks.

    Uses the same provider priority as get_llm_config() but with
    temperature=0 for deterministic summarization.

    Returns:
        Tuple of (model_name, litellm_params)
    """
    # DB-only: pick primary config and adjust temperature for summarisation
    configs = get_llm_config_with_failover(token_count=0)
    _provider_key, model, params_with_hints = configs[0]
    # Remove internal-only hints that shouldn't be passed to litellm
    params = {
        k: v for k, v in params_with_hints.items()
        if k not in ("supports_tool_choice", "use_parallel_tool_calls", "supports_vision")
    }

    # Default to deterministic temperature unless the endpoint already
    # specifies a requirement (e.g., GPT-5 must run at temperature=1).
    if "temperature" not in params or params["temperature"] is None:
        params["temperature"] = 0

    if model.startswith("openai/gpt-5") and params.get("temperature") != 1:
        params["temperature"] = 1

    return model, params


def _cache_bootstrap_status(is_required: bool) -> None:
    """Cache bootstrap status so repeated UI checks avoid heavy DB queries."""
    try:
        cache.set(_LLM_BOOTSTRAP_CACHE_KEY, bool(is_required), _LLM_BOOTSTRAP_CACHE_TTL)
    except Exception:
        logger.debug("Unable to cache LLM bootstrap status", exc_info=True)


def invalidate_llm_bootstrap_cache() -> None:
    """Invalidate cached bootstrap status after config changes."""
    try:
        cache.delete(_LLM_BOOTSTRAP_CACHE_KEY)
    except Exception:
        logger.debug("Unable to invalidate LLM bootstrap cache", exc_info=True)


def is_llm_bootstrap_required(*, force_refresh: bool = False) -> bool:
    """Return True when the platform lacks any usable LLM configuration."""
    if getattr(settings, "LLM_BOOTSTRAP_OPTIONAL", False):
        return False
    if not force_refresh:
        cached = cache.get(_LLM_BOOTSTRAP_CACHE_KEY)
        if cached is not None:
            return bool(cached)

    try:
        configs = get_llm_config_with_failover(token_count=0, allow_unconfigured=True)
        required = not bool(configs)
    except Exception:
        required = True

    _cache_bootstrap_status(required)
    return required


__all__ = [
    "get_llm_config",
    "get_llm_config_with_failover",
    "REFERENCE_TOKENIZER_MODEL",
    "get_summarization_llm_config",
    "LLMNotConfiguredError",
    "invalidate_llm_bootstrap_cache",
    "is_llm_bootstrap_required",
]
