"""
Common LiteLLM configuration for persistent agents.

This module provides a unified way to configure LiteLLM with tiered failover:
1. Vertex AI Gemini 2.5 Pro (primary)
2. Anthropic Claude Sonnet 4 (fallback)

The configuration uses a similar pattern to browser use tasks for consistency.
"""
import os
import logging
from typing import Dict, List, Tuple, Any
import random

logger = logging.getLogger(__name__)

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
            [("openrouter_glm", 0.70), ("google", 0.10), ("openai_gpt5", 0.10), ("fireworks_gpt_oss_120b", 0.10)],  # Tier 1: 65% GLM-4.5, 15% Google, 10% GPT-OSS-120B, 10% GPT-5
            [("openrouter_glm", 0.34), ("openai_gpt5", 0.33), ("anthropic", 0.33)],  # Tier 2: Even split between GLM-4.5, GPT-5, and Anthropic
            [("openai_gpt5", 1.0)],  # Tier 3: 100% GPT-5 (last resort)
        ]
    },
    # 20000+ tokens: 70% GLM-4.5, 10% Google Gemini 2.5 Pro, 10% GPT-5, 10% GPT-OSS-120B
    "large": {
        "range": (20000, float('inf')),
        "tiers": [
            [("openrouter_glm", 0.70), ("google", 0.10), ("openai_gpt5", 0.10), ("fireworks_gpt_oss_120b", 0.10)],  # Tier 1: 65% GLM-4.5, 15% Google, 10% GPT-OSS-120B, 10% GPT-5
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
    """
    Get the optimal LiteLLM model and configuration using simple priority fallback.
    
    This is kept for backward compatibility and simple use cases.
    For failover scenarios, use get_llm_config_with_failover().
    
    Returns:
        Tuple of (model_name, litellm_params)
        
    Priority:
        1. Vertex AI (Gemini 2.5 Pro) - primary choice
        2. Anthropic (Claude Sonnet 4) - fallback
        3. OpenAI (GPT-4.1) - second fallback  
        4. OpenRouter (Gemini 2.5 Pro) - final fallback
        
    Raises:
        ValueError: If no provider is available
    """
    
    # Check for Google first - primary choice for persistent agents
    google_api_key = os.getenv("GOOGLE_API_KEY")
    if google_api_key:
        logger.info("Using Vertex AI (Google Cloud) as primary LLM provider")
        
        # Get project ID from environment or use default
        vertex_project = os.getenv("GOOGLE_CLOUD_PROJECT", "browser-use-458714")
        
        # Get location from environment or use default to match infrastructure
        vertex_location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-east4")
        
        return (
            "vertex_ai/gemini-2.5-pro",
            {
                "temperature": 0.1,
                "vertex_project": vertex_project,
                "vertex_location": vertex_location,
                # Vertex AI will use GOOGLE_API_KEY from environment
            }
        )

    # Anthropic as fallback
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        logger.info("Using Anthropic as fallback LLM provider")
        return (
            "anthropic/claude-sonnet-4-20250514",
            {
                "temperature": 0.1,
            }
        )

    # OpenAI as third priority fallback
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        logger.info("Using OpenAI as second fallback LLM provider")
        return (
            "openai/gpt-4.1",
            {
                "temperature": 0.1,
            }
        )
        
    # Fallback to OpenRouter GLM-4.5
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        logger.info("Using OpenRouter GLM-4.5 as final fallback LLM provider")
        return (
            "openrouter/z-ai/glm-4.5", 
            {
                "temperature": 0.1,
            }
        )
    
    # No providers available
    raise ValueError(
        "No LLM provider available. Set either GOOGLE_API_KEY, ANTHROPIC_API_KEY, "
        "OPENAI_API_KEY, or OPENROUTER_API_KEY."
    )


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
    token_count: int = 0
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
        ValueError: If no providers are available with valid API keys
    """
    # Use provided tiers or select based on token count
    if provider_tiers is None:
        provider_tiers = get_tier_config_for_tokens(token_count)
        logger.debug(
            "Using token-based tier selection for %d tokens%s",
            token_count,
            f" (agent {agent_id})" if agent_id else ""
        )
    
    failover_configs = []
    
    for tier_idx, tier in enumerate(provider_tiers, start=1):
        # Build list of usable providers in this tier
        tier_providers_with_weights = []
        for provider, weight in tier:
            if provider not in PROVIDER_CONFIG:
                logger.warning("Unknown provider %s; skipping.", provider)
                continue
                
            env_var = PROVIDER_CONFIG[provider]["env_var"]
            if not os.getenv(env_var):
                logger.info(
                    "Skipping provider %s%s — missing env %s",
                    provider,
                    f" for agent {agent_id}" if agent_id else "",
                    env_var,
                )
                continue
                
            tier_providers_with_weights.append((provider, weight))
        
        if not tier_providers_with_weights:
            logger.info(
                "No usable providers in tier %d%s; moving to next tier.",
                tier_idx,
                f" for agent {agent_id}" if agent_id else "",
            )
            continue
        
        # Create weighted-random order of providers for this tier
        remaining_providers = tier_providers_with_weights.copy()
        while remaining_providers:
            providers = [p[0] for p in remaining_providers]
            weights = [p[1] for p in remaining_providers]
            selected_provider = random.choices(providers, weights=weights, k=1)[0]
            
            try:
                model, params = get_provider_config(selected_provider)
                failover_configs.append((selected_provider, model, params))
                logger.debug(
                    "Added provider %s (tier %d) to failover list%s",
                    selected_provider,
                    tier_idx,
                    f" for agent {agent_id}" if agent_id else "",
                )
            except ValueError as e:
                logger.warning("Failed to configure provider %s: %s", selected_provider, e)
            
            remaining_providers = [p for p in remaining_providers if p[0] != selected_provider]
    
    if not failover_configs:
        raise ValueError(
            "No LLM provider available with valid API keys. "
            "Set GOOGLE_API_KEY for primary choice or ANTHROPIC_API_KEY for fallback."
        )
    
    return failover_configs


def get_summarization_llm_config() -> Tuple[str, dict]:
    """
    Get LiteLLM configuration specifically for summarization tasks.
    
    Uses the same provider priority as get_llm_config() but with 
    temperature=0 for deterministic summarization.
    
    Returns:
        Tuple of (model_name, litellm_params)
    """
    model, params = get_llm_config()
    # Ensure temperature is 0 for summarization
    params["temperature"] = 0
    return model, params
