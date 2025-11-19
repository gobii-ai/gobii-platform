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
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Dict, List, Tuple, Any, Optional

from django.apps import apps
from django.core.cache import cache
from django.db import connection
from django.db.models import Q
from django.conf import settings
from django.utils import timezone

from api.openrouter import get_attribution_headers
from api.llm.utils import normalize_model_name
from util.subscription_helper import get_owner_plan
from constants.plans import PlanNames

logger = logging.getLogger(__name__)

_TIER_MULTIPLIER_CACHE_KEY = "persistent_llm_tier_multipliers:v1"
_DEFAULT_TIER_MULTIPLIERS: Dict[str, Decimal] = {
    "standard": Decimal("1.00"),
    "premium": Decimal("1.00"),
    "max": Decimal("5.00"),
}

# Certain models only support a single temperature. When we detect these models
# we silently coerce the temperature to the required value so LiteLLM does not
# reject the request with a BadRequestError.
_MODEL_TEMPERATURE_REQUIREMENTS: Tuple[Tuple[str, float], ...] = (
    ("openai/gpt-5", 1.0),
)


def get_required_temperature_for_model(model: str) -> Optional[float]:
    """Return the fixed temperature required by a given LiteLLM model."""

    for prefix, temperature in _MODEL_TEMPERATURE_REQUIREMENTS:
        if model.startswith(prefix):
            return temperature
    return None


def _apply_required_temperature(model: str, params: Dict[str, Any]) -> None:
    """Mutate ``params`` to satisfy model-specific temperature constraints."""

    required_temp = get_required_temperature_for_model(model)
    if required_temp is None:
        return

    current_temp = params.get("temperature")
    if current_temp is None or float(current_temp) != required_temp:
        logger.debug(
            "Adjusting temperature for model %s from %s to %s", model, current_temp, required_temp
        )
    params["temperature"] = required_temp


_PREMIUM_PLAN_IDS = {"pro", "org", PlanNames.SCALE, "startup", "org_team"}
_PREMIUM_PLAN_NAMES = {"pro", "org", PlanNames.SCALE}
class AgentLLMTier(str, Enum):
    """LLM routing tiers supported by the platform."""

    STANDARD = "standard"
    PREMIUM = "premium"
    MAX = "max"


_TIER_ORDER = {
    AgentLLMTier.STANDARD: 0,
    AgentLLMTier.PREMIUM: 1,
    AgentLLMTier.MAX: 2,
}


def _plan_supports_premium(plan: Optional[dict[str, Any]]) -> bool:
    if not plan:
        return False
    plan_id = str(plan.get("id", "")).lower()
    plan_name = str(plan.get("name", "")).lower()
    return plan_id in _PREMIUM_PLAN_IDS or plan_name in _PREMIUM_PLAN_NAMES


def max_allowed_tier_for_plan(
    plan: Optional[dict[str, Any]],
    *,
    is_organization: bool = False,
) -> AgentLLMTier:
    if is_organization:
        return AgentLLMTier.MAX
    if _plan_supports_premium(plan):
        return AgentLLMTier.MAX
    return AgentLLMTier.STANDARD


def _clamp_tier(target: AgentLLMTier, max_allowed: AgentLLMTier) -> AgentLLMTier:
    if _TIER_ORDER[target] <= _TIER_ORDER[max_allowed]:
        return target
    return max_allowed


def default_preferred_tier_for_owner(owner: Any | None) -> AgentLLMTier:
    """Return the default preferred tier for a given owner."""

    if owner is None:
        return AgentLLMTier.STANDARD

    try:
        plan = get_owner_plan(owner)
    except Exception:
        plan = None

    owner_meta = getattr(owner, "_meta", None)
    is_organization = bool(
        owner_meta and owner_meta.app_label == "api" and owner_meta.model_name == "organization"
    )
    allowed = max_allowed_tier_for_plan(plan, is_organization=is_organization)
    if allowed in (AgentLLMTier.PREMIUM, AgentLLMTier.MAX):
        return AgentLLMTier.PREMIUM
    return AgentLLMTier.STANDARD


def get_llm_tier_multipliers(force_refresh: bool = False) -> Dict[str, Decimal]:
    """Return cached credit multipliers per tier."""

    cached = None if force_refresh else cache.get(_TIER_MULTIPLIER_CACHE_KEY)
    if cached:
        try:
            return {key: Decimal(str(value)) for key, value in cached.items()}
        except Exception:
            logger.debug("Failed to deserialize cached tier multipliers", exc_info=True)

    result: Dict[str, Decimal] = dict(_DEFAULT_TIER_MULTIPLIERS)
    try:
        PersistentLLMTier = apps.get_model("api", "PersistentLLMTier")
        for tier in PersistentLLMTier.objects.all().only("is_max", "is_premium", "credit_multiplier"):
            tier_key = "max" if tier.is_max else ("premium" if tier.is_premium else "standard")
            multiplier = getattr(tier, "credit_multiplier", None) or Decimal("1.00")
            try:
                current = result.get(tier_key, Decimal("1.00"))
                result[tier_key] = max(current, Decimal(multiplier))
            except Exception:
                logger.debug(
                    "Invalid credit multiplier for tier %s (value=%s)",
                    tier_key,
                    multiplier,
                    exc_info=True,
                )
    except Exception:
        logger.debug("Failed to load persistent tier multipliers", exc_info=True)

    cache.set(
        _TIER_MULTIPLIER_CACHE_KEY,
        {key: str(value) for key, value in result.items()},
        timeout=300,
    )
    return result


def invalidate_llm_tier_multiplier_cache() -> None:
    cache.delete(_TIER_MULTIPLIER_CACHE_KEY)


def _normalize_tier_value(tier: AgentLLMTier | str) -> AgentLLMTier:
    if isinstance(tier, AgentLLMTier):
        return tier
    try:
        return AgentLLMTier(str(tier))
    except ValueError:
        return AgentLLMTier.STANDARD


def get_credit_multiplier_for_tier(tier: AgentLLMTier | str) -> Decimal:
    tier_enum = _normalize_tier_value(tier)
    multipliers = get_llm_tier_multipliers()
    return multipliers.get(tier_enum.value, _DEFAULT_TIER_MULTIPLIERS[tier_enum.value])


def apply_tier_credit_multiplier(agent: Any, amount: Optional[Decimal]) -> Optional[Decimal]:
    """Return ``amount`` scaled by the agent's tier multiplier."""

    if amount is None or agent is None:
        return amount
    try:
        base_amount = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    except Exception:
        logger.debug("Unable to normalize credit amount %s for agent %s", amount, getattr(agent, "id", None))
        return amount

    multiplier = get_credit_multiplier_for_tier(get_agent_llm_tier(agent))
    scaled = base_amount * multiplier
    return scaled.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def get_agent_llm_tier(agent: Any, *, is_first_loop: bool | None = None) -> AgentLLMTier:
    """Return the highest LLM tier the provided agent is eligible to use."""

    if not getattr(settings, "GOBII_PROPRIETARY_MODE", False):
        return AgentLLMTier.STANDARD
    if agent is None:
        return AgentLLMTier.STANDARD

    owner = getattr(agent, "organization", None) or getattr(agent, "user", None)
    plan = None
    if owner is not None:
        try:
            plan = get_owner_plan(owner)
        except Exception:
            logger.debug(
                "Failed to resolve owner plan for agent %s",
                getattr(agent, "id", None),
                exc_info=True,
            )
    is_org_owner = bool(getattr(agent, "organization_id", None))
    allowed_tier = max_allowed_tier_for_plan(plan, is_organization=is_org_owner)

    if is_first_loop:
        return _clamp_tier(AgentLLMTier.PREMIUM, allowed_tier)

    preferred_value = getattr(agent, "preferred_llm_tier", None)
    try:
        preferred = AgentLLMTier(preferred_value) if preferred_value else AgentLLMTier.STANDARD
    except ValueError:
        preferred = AgentLLMTier.STANDARD

    return _clamp_tier(preferred, allowed_tier)


def should_prioritize_premium(agent: Any, *, is_first_loop: bool | None = None) -> bool:
    """Return True when the provided agent should prefer premium-or-better tiers."""

    return get_agent_llm_tier(agent, is_first_loop=is_first_loop) != AgentLLMTier.STANDARD


def should_prioritize_max(agent: Any, *, is_first_loop: bool | None = None) -> bool:
    """Return True when the provided agent should route to the max tier."""

    return get_agent_llm_tier(agent, is_first_loop=is_first_loop) is AgentLLMTier.MAX


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
        if k not in ("supports_tool_choice", "use_parallel_tool_calls", "supports_vision", "supports_temperature")
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

    _apply_required_temperature(model, params)

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


def _collect_failover_configs(
    tiers,
    *,
    token_range_name: str,
    tier_label: str,
) -> List[Tuple[str, str, dict]]:
    """Build failover configurations from the provided tier queryset."""

    failover_configs: List[Tuple[str, str, dict]] = []
    for tier in tiers:
        endpoints_with_weights = []
        for te in tier.tier_endpoints.select_related("endpoint__provider").all():
            endpoint = te.endpoint
            provider = endpoint.provider
            if not (provider.enabled and endpoint.enabled):
                continue
            has_admin_key = bool(provider.api_key_encrypted)
            has_env_key = bool(provider.env_var_name and os.getenv(provider.env_var_name))
            raw_model = endpoint.litellm_model or ""
            api_base_value = getattr(endpoint, "api_base", None)
            has_api_base = bool(api_base_value)
            effective_model = normalize_model_name(provider, raw_model, api_base=api_base_value)

            is_openai_compat = effective_model.startswith("openai/") and has_api_base
            if not (has_admin_key or has_env_key or is_openai_compat):
                logger.info(
                    "DB LLM skip endpoint (no key): range=%s tier=%s tier_type=%s "
                    "endpoint=%s provider=%s model=%s api_base=%s",
                    token_range_name,
                    tier.order,
                    tier_label,
                    endpoint.key,
                    provider.key,
                    effective_model,
                    getattr(endpoint, "api_base", "") or "",
                )
                continue
            endpoints_with_weights.append((endpoint, provider, te.weight, effective_model))

        if not endpoints_with_weights:
            continue

        remaining = endpoints_with_weights.copy()
        while remaining:
            weights = [r[2] for r in remaining]
            selected_idx = random.choices(range(len(remaining)), weights=weights, k=1)[0]
            endpoint, provider, _weight, effective_model = remaining.pop(selected_idx)

            supports_temperature = bool(getattr(endpoint, "supports_temperature", True))
            params: Dict[str, Any] = {}
            if supports_temperature:
                params["temperature"] = 0.1
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
                    if endpoint.litellm_model.startswith("openai/") and getattr(endpoint, "api_base", None):
                        params["api_key"] = "sk-noauth"
            except Exception:
                logger.debug("Unable to determine API key for endpoint %s", endpoint.key, exc_info=True)
            if supports_temperature and endpoint.temperature_override is not None:
                params["temperature"] = float(endpoint.temperature_override)
            if provider.key == "google":
                vertex_project = provider.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT", "browser-use-458714")
                vertex_location = provider.vertex_location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-east4")
                params.update(
                    {
                        "vertex_project": vertex_project,
                        "vertex_location": vertex_location,
                    }
                )
            if provider.key == "openrouter":
                headers = get_attribution_headers()
                if headers:
                    params["extra_headers"] = headers

            if effective_model.startswith("openai/") and getattr(endpoint, "api_base", None):
                params["api_base"] = endpoint.api_base
                logger.info(
                    "DB LLM endpoint configured with api_base: endpoint=%s provider=%s "
                    "model=%s api_base=%s has_key=%s tier_type=%s",
                    endpoint.key,
                    provider.key,
                    effective_model,
                    endpoint.api_base,
                    bool(params.get("api_key")),
                    tier_label,
                )

            if supports_temperature:
                _apply_required_temperature(effective_model, params)
            else:
                params.pop("temperature", None)

            params_with_hints = dict(params)
            params_with_hints["supports_temperature"] = supports_temperature
            params_with_hints["supports_tool_choice"] = bool(endpoint.supports_tool_choice)
            params_with_hints["supports_vision"] = bool(getattr(endpoint, "supports_vision", False))
            params_with_hints["use_parallel_tool_calls"] = bool(getattr(endpoint, "use_parallel_tool_calls", True))

            failover_configs.append((endpoint.key, effective_model, params_with_hints))

    return failover_configs


def get_llm_config_with_failover(
    provider_tiers: List[List[Tuple[str, float]]] = None,
    agent_id: str = None,
    token_count: int = 0,
    *,
    allow_unconfigured: bool = False,
    agent: Any | None = None,
    is_first_loop: bool | None = None,
) -> List[Tuple[str, str, dict]]:
    """
    Get LLM configurations for tiered failover with token-based tier selection.
    
    Args:
        provider_tiers: Optional custom provider tier configuration.
                       If None, uses token-based tiers based on token_count
        agent_id: Optional agent ID for logging
        token_count: Token count for automatic tier selection (default: 0).
                    Used to select appropriate tier when provider_tiers is None.
        agent: Optional agent instance (or None). When provided (or resolvable via
            agent_id) and running in proprietary mode, premium tiers may be preferred.
        is_first_loop: Whether this is the first run of the agent (brand-new)
        
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

        if token_range is None:
            smallest_range = PersistentTokenRange.objects.order_by('min_tokens').first()
            largest_range = PersistentTokenRange.objects.order_by('-min_tokens').first()
            if smallest_range and token_count < smallest_range.min_tokens:
                token_range = smallest_range
                logger.info(
                    "Token count %s below configured minimum (%s); using range '%s' as fallback",
                    token_count,
                    smallest_range.min_tokens,
                    smallest_range.name,
                )
            elif largest_range:
                token_range = largest_range
                logger.info(
                    "Token count %s exceeds configured ranges; using highest range '%s' (min=%s) as fallback",
                    token_count,
                    largest_range.name,
                    largest_range.min_tokens,
                )
    except Exception:
        token_range = None

    if token_range is not None:
        agent_instance = agent
        agent_tier = AgentLLMTier.STANDARD
        if getattr(settings, "GOBII_PROPRIETARY_MODE", False):
            if agent_instance is None and agent_id:
                try:
                    PersistentAgent = apps.get_model('api', 'PersistentAgent')
                    agent_instance = (
                        PersistentAgent.objects.select_related("user", "organization").get(id=agent_id)
                    )
                except Exception:
                    logger.debug(
                        "Unable to resolve agent %s for premium tier routing",
                        agent_id,
                        exc_info=True,
                    )
                    agent_instance = None
            agent_tier = get_agent_llm_tier(
                agent_instance,
                is_first_loop=is_first_loop,
            )

        combined_configs: List[Tuple[str, str, dict]] = []

        if agent_tier is AgentLLMTier.MAX:
            max_tiers = PersistentLLMTier.objects.filter(
                token_range=token_range,
                is_max=True,
            ).order_by("order")
            max_configs = _collect_failover_configs(
                max_tiers,
                token_range_name=token_range.name,
                tier_label="max",
            )
            if max_configs:
                combined_configs.extend(max_configs)

        if agent_tier in (AgentLLMTier.MAX, AgentLLMTier.PREMIUM):
            premium_tiers = PersistentLLMTier.objects.filter(
                token_range=token_range,
                is_premium=True,
                is_max=False,
            ).order_by("order")
            premium_configs = _collect_failover_configs(
                premium_tiers,
                token_range_name=token_range.name,
                tier_label="premium",
            )
            if premium_configs:
                combined_configs.extend(premium_configs)

        standard_tiers = PersistentLLMTier.objects.filter(
            token_range=token_range,
            is_premium=False,
            is_max=False,
        ).order_by("order")
        standard_configs = _collect_failover_configs(
            standard_tiers,
            token_range_name=token_range.name,
            tier_label="standard",
        )
        if standard_configs:
            combined_configs.extend(standard_configs)

        if combined_configs:
            _cache_bootstrap_status(False)
            return combined_configs

    if allow_unconfigured:
        _cache_bootstrap_status(True)
        return []

    _cache_bootstrap_status(True)
    raise LLMNotConfiguredError(
        "No LLM providers are currently configured. Complete the setup wizard before running agents."
    )


def get_summarization_llm_config(
    *,
    agent: Any | None = None,
    agent_id: str | None = None,
) -> Tuple[str, dict]:
    """
    Get LiteLLM configuration specifically for summarization tasks.

    Uses the same provider priority as get_llm_config() but with
    temperature=0 for deterministic summarization.

    Returns:
        Tuple of (model_name, litellm_params)
    """
    # DB-only: pick primary config and adjust temperature for summarisation
    if agent_id is None and agent is not None:
        possible_id = getattr(agent, "id", None)
        if possible_id is not None:
            agent_id = str(possible_id)

    configs = get_llm_config_with_failover(
        agent_id=agent_id,
        token_count=0,
        agent=agent,
    )
    _provider_key, model, params_with_hints = configs[0]
    # Remove internal-only hints that shouldn't be passed to litellm
    supports_temperature = bool(params_with_hints.get("supports_temperature", True))
    params = {
        k: v for k, v in params_with_hints.items()
        if k not in ("supports_tool_choice", "use_parallel_tool_calls", "supports_vision", "supports_temperature")
    }

    # Default to deterministic temperature unless the endpoint already
    # specifies a requirement (e.g., GPT-5 must run at temperature=1).
    if not supports_temperature:
        params.pop("temperature", None)
    elif "temperature" not in params or params["temperature"] is None:
        params["temperature"] = 0

    if supports_temperature:
        _apply_required_temperature(model, params)
    else:
        params.pop("temperature", None)

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
