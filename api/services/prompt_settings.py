from dataclasses import dataclass
from typing import Optional

from django.core.cache import cache
from django.conf import settings


DEFAULT_STANDARD_PROMPT_TOKEN_BUDGET = 120000
DEFAULT_PREMIUM_PROMPT_TOKEN_BUDGET = 120000
DEFAULT_MAX_PROMPT_TOKEN_BUDGET = 120000
DEFAULT_STANDARD_MESSAGE_HISTORY_LIMIT = 15
DEFAULT_PREMIUM_MESSAGE_HISTORY_LIMIT = 20
DEFAULT_MAX_MESSAGE_HISTORY_LIMIT = 20
DEFAULT_STANDARD_TOOL_CALL_HISTORY_LIMIT = 15
DEFAULT_PREMIUM_TOOL_CALL_HISTORY_LIMIT = 20
DEFAULT_MAX_TOOL_CALL_HISTORY_LIMIT = 20
DEFAULT_STANDARD_ENABLED_TOOL_LIMIT = 40
DEFAULT_PREMIUM_ENABLED_TOOL_LIMIT = 40
DEFAULT_MAX_ENABLED_TOOL_LIMIT = 40
DEFAULT_UNIFIED_HISTORY_LIMIT = getattr(settings, "PA_RAW_MSG_LIMIT", 20) + getattr(settings, "PA_RAW_STEP_LIMIT", 100)
DEFAULT_UNIFIED_HISTORY_HYSTERESIS = getattr(settings, "PA_RAW_MSG_LIMIT", 20)

_CACHE_KEY = "prompt_settings:v3"
_CACHE_TTL_SECONDS = 300


@dataclass(frozen=True)
class PromptSettings:
    standard_prompt_token_budget: int
    premium_prompt_token_budget: int
    max_prompt_token_budget: int
    standard_message_history_limit: int
    premium_message_history_limit: int
    max_message_history_limit: int
    standard_tool_call_history_limit: int
    premium_tool_call_history_limit: int
    max_tool_call_history_limit: int
    standard_enabled_tool_limit: int
    premium_enabled_tool_limit: int
    max_enabled_tool_limit: int
    standard_unified_history_limit: int
    premium_unified_history_limit: int
    max_unified_history_limit: int
    standard_unified_history_hysteresis: int
    premium_unified_history_hysteresis: int
    max_unified_history_hysteresis: int


def _serialise(config) -> dict:
    return {
        "standard_prompt_token_budget": config.standard_prompt_token_budget,
        "premium_prompt_token_budget": config.premium_prompt_token_budget,
        "max_prompt_token_budget": config.max_prompt_token_budget,
        "standard_message_history_limit": config.standard_message_history_limit,
        "premium_message_history_limit": config.premium_message_history_limit,
        "max_message_history_limit": config.max_message_history_limit,
        "standard_tool_call_history_limit": config.standard_tool_call_history_limit,
        "premium_tool_call_history_limit": config.premium_tool_call_history_limit,
        "max_tool_call_history_limit": config.max_tool_call_history_limit,
        "standard_enabled_tool_limit": config.standard_enabled_tool_limit,
        "premium_enabled_tool_limit": config.premium_enabled_tool_limit,
        "max_enabled_tool_limit": config.max_enabled_tool_limit,
        "standard_unified_history_limit": config.standard_unified_history_limit,
        "premium_unified_history_limit": config.premium_unified_history_limit,
        "max_unified_history_limit": config.max_unified_history_limit,
        "standard_unified_history_hysteresis": config.standard_unified_history_hysteresis,
        "premium_unified_history_hysteresis": config.premium_unified_history_hysteresis,
        "max_unified_history_hysteresis": config.max_unified_history_hysteresis,
    }


def _get_prompt_config_model():
    from api.models import PromptConfig

    return PromptConfig


def get_prompt_settings() -> PromptSettings:
    cached: Optional[dict] = cache.get(_CACHE_KEY)
    if cached:
        return PromptSettings(**cached)

    PromptConfig = _get_prompt_config_model()
    config = PromptConfig.objects.order_by("singleton_id").first()
    if config is None:
        config = PromptConfig.objects.create(
            standard_prompt_token_budget=DEFAULT_STANDARD_PROMPT_TOKEN_BUDGET,
            premium_prompt_token_budget=DEFAULT_PREMIUM_PROMPT_TOKEN_BUDGET,
            max_prompt_token_budget=DEFAULT_MAX_PROMPT_TOKEN_BUDGET,
            standard_message_history_limit=DEFAULT_STANDARD_MESSAGE_HISTORY_LIMIT,
            premium_message_history_limit=DEFAULT_PREMIUM_MESSAGE_HISTORY_LIMIT,
            max_message_history_limit=DEFAULT_MAX_MESSAGE_HISTORY_LIMIT,
            standard_tool_call_history_limit=DEFAULT_STANDARD_TOOL_CALL_HISTORY_LIMIT,
            premium_tool_call_history_limit=DEFAULT_PREMIUM_TOOL_CALL_HISTORY_LIMIT,
            max_tool_call_history_limit=DEFAULT_MAX_TOOL_CALL_HISTORY_LIMIT,
            standard_enabled_tool_limit=DEFAULT_STANDARD_ENABLED_TOOL_LIMIT,
            premium_enabled_tool_limit=DEFAULT_PREMIUM_ENABLED_TOOL_LIMIT,
            max_enabled_tool_limit=DEFAULT_MAX_ENABLED_TOOL_LIMIT,
            standard_unified_history_limit=DEFAULT_UNIFIED_HISTORY_LIMIT,
            premium_unified_history_limit=DEFAULT_UNIFIED_HISTORY_LIMIT,
            max_unified_history_limit=DEFAULT_UNIFIED_HISTORY_LIMIT,
            standard_unified_history_hysteresis=DEFAULT_UNIFIED_HISTORY_HYSTERESIS,
            premium_unified_history_hysteresis=DEFAULT_UNIFIED_HISTORY_HYSTERESIS,
            max_unified_history_hysteresis=DEFAULT_UNIFIED_HISTORY_HYSTERESIS,
        )

    data = _serialise(config)
    cache.set(_CACHE_KEY, data, _CACHE_TTL_SECONDS)
    return PromptSettings(**data)


def invalidate_prompt_settings_cache() -> None:
    cache.delete(_CACHE_KEY)
