from dataclasses import dataclass
from typing import Optional

from django.core.cache import cache


DEFAULT_STANDARD_PROMPT_TOKEN_BUDGET = 120000
DEFAULT_PREMIUM_PROMPT_TOKEN_BUDGET = 120000
DEFAULT_STANDARD_MESSAGE_HISTORY_LIMIT = 15
DEFAULT_PREMIUM_MESSAGE_HISTORY_LIMIT = 20
DEFAULT_STANDARD_TOOL_CALL_HISTORY_LIMIT = 15
DEFAULT_PREMIUM_TOOL_CALL_HISTORY_LIMIT = 20

_CACHE_KEY = "prompt_settings:v1"
_CACHE_TTL_SECONDS = 300


@dataclass(frozen=True)
class PromptSettings:
    standard_prompt_token_budget: int
    premium_prompt_token_budget: int
    standard_message_history_limit: int
    premium_message_history_limit: int
    standard_tool_call_history_limit: int
    premium_tool_call_history_limit: int


def _coalesce(value: Optional[int], fallback: int) -> int:
    return fallback if value is None else value


def _serialise(config) -> dict:
    return {
        "standard_prompt_token_budget": _coalesce(
            config.standard_prompt_token_budget,
            DEFAULT_STANDARD_PROMPT_TOKEN_BUDGET,
        ),
        "premium_prompt_token_budget": _coalesce(
            config.premium_prompt_token_budget,
            DEFAULT_PREMIUM_PROMPT_TOKEN_BUDGET,
        ),
        "standard_message_history_limit": _coalesce(
            config.standard_message_history_limit,
            DEFAULT_STANDARD_MESSAGE_HISTORY_LIMIT,
        ),
        "premium_message_history_limit": _coalesce(
            config.premium_message_history_limit,
            DEFAULT_PREMIUM_MESSAGE_HISTORY_LIMIT,
        ),
        "standard_tool_call_history_limit": _coalesce(
            config.standard_tool_call_history_limit,
            DEFAULT_STANDARD_TOOL_CALL_HISTORY_LIMIT,
        ),
        "premium_tool_call_history_limit": _coalesce(
            config.premium_tool_call_history_limit,
            DEFAULT_PREMIUM_TOOL_CALL_HISTORY_LIMIT,
        ),
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
            standard_message_history_limit=DEFAULT_STANDARD_MESSAGE_HISTORY_LIMIT,
            premium_message_history_limit=DEFAULT_PREMIUM_MESSAGE_HISTORY_LIMIT,
            standard_tool_call_history_limit=DEFAULT_STANDARD_TOOL_CALL_HISTORY_LIMIT,
            premium_tool_call_history_limit=DEFAULT_PREMIUM_TOOL_CALL_HISTORY_LIMIT,
        )

    data = _serialise(config)
    cache.set(_CACHE_KEY, data, _CACHE_TTL_SECONDS)
    return PromptSettings(**data)


def invalidate_prompt_settings_cache() -> None:
    cache.delete(_CACHE_KEY)
