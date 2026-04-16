from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Set, Tuple

from django.apps import apps
from django.db import models as django_models
from django.conf import settings
from django.core.cache import cache
from django.db import OperationalError, ProgrammingError

_TOOL_COST_CACHE_KEY = "task_credit_costs:v2"
_TOOL_COST_CACHE_TTL_SECONDS = 300  # 5 minutes is enough for eventual consistency in workers

_CHANNEL_TOOL_NAMES: Dict[str, str] = {
    "email": "send_email",
    "sms": "send_sms",
    "web": "send_chat_message",
}


def clear_tool_credit_cost_cache() -> None:
    """Evict the cached tool credit cost mapping."""
    cache.delete(_TOOL_COST_CACHE_KEY)


def _normalize_tool_name(name: str | None) -> str:
    return (name or "").strip().lower()


def _get_models() -> Tuple[Any, Any]:
    """Return the TaskCreditConfig and ToolCreditCost models lazily."""
    TaskCreditConfig = apps.get_model("api", "TaskCreditConfig")
    ToolCreditCost = apps.get_model("api", "ToolCreditCost")

    # Tests often patch `apps.get_model` with MagicMocks, which lack the
    # Django model metadata we rely on. Treat those mock values as missing so
    # downstream callers fall back to the default settings-based configuration
    # rather than caching unpicklable mock objects.
    if not isinstance(TaskCreditConfig, type) or not issubclass(TaskCreditConfig, django_models.Model):
        TaskCreditConfig = None
    if not isinstance(ToolCreditCost, type) or not issubclass(ToolCreditCost, django_models.Model):
        ToolCreditCost = None
    return TaskCreditConfig, ToolCreditCost


def _coerce_decimal(value: Any, fallback: Decimal) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return fallback


def _fetch_tool_cost_configuration() -> Tuple[Decimal, Dict[str, Decimal], Set[str]]:
    """
    Retrieve default and per-tool credit costs from the database.

    Falls back to settings values when tables are unavailable (e.g. during
    migrations or the very first deploy).

    Returns a 3-tuple of (default_cost, overrides, tier_exempt_tools).
    """

    default_cost = getattr(settings, "CREDITS_PER_TASK")
    overrides: Dict[str, Decimal] = {}
    tier_exempt_tools: Set[str] = set()

    TaskCreditConfig, ToolCreditCost = _get_models()

    if TaskCreditConfig is None or ToolCreditCost is None:
        return default_cost, overrides, tier_exempt_tools

    try:
        config = TaskCreditConfig.objects.first()
        if config and config.default_task_cost is not None:
            default_cost = config.default_task_cost

        for entry in ToolCreditCost.objects.all():
            key = _normalize_tool_name(entry.tool_name)
            overrides[key] = entry.credit_cost
            if entry.tier_exempt:
                tier_exempt_tools.add(key)
    except (OperationalError, ProgrammingError):
        # Database tables may not exist yet
        raw_mapping: dict[str, Any] = getattr(settings, "TOOL_CREDIT_COSTS", {}) or {}
        overrides = {
            _normalize_tool_name(name): _coerce_decimal(value, default_cost)
            for name, value in raw_mapping.items()
        }

    return default_cost, overrides, tier_exempt_tools


def _get_tool_cost_config() -> Tuple[Decimal, Dict[str, Decimal], Set[str]]:
    cached = cache.get(_TOOL_COST_CACHE_KEY)
    if cached is not None:
        return cached

    data = _fetch_tool_cost_configuration()
    cache.set(_TOOL_COST_CACHE_KEY, data, _TOOL_COST_CACHE_TTL_SECONDS)
    return data


def get_tool_cost_overview() -> Tuple[Decimal, Dict[str, Decimal]]:
    """
    Return the default cost and a mapping of tool-specific overrides.

    The mapping keys are normalized lowercase tool names.
    """
    default_cost, overrides, _exempt = _get_tool_cost_config()
    return default_cost, overrides.copy()


def get_default_task_credit_cost() -> Decimal:
    default_cost, _overrides, _exempt = _get_tool_cost_config()
    return default_cost


def get_tool_credit_cost(tool_name: str | None) -> Decimal:
    """Return the credit cost for the given tool name."""
    default_cost, overrides, _exempt = _get_tool_cost_config()

    key = _normalize_tool_name(tool_name)
    if not key:
        return default_cost

    if key in overrides:
        try:
            value = overrides[key]
            return value if isinstance(value, Decimal) else _coerce_decimal(value, default_cost)
        except Exception:
            return default_cost

    return default_cost


def is_tool_tier_exempt(tool_name: str | None) -> bool:
    """Return True if the tool is exempt from the tier credit multiplier."""
    _default_cost, _overrides, tier_exempt_tools = _get_tool_cost_config()
    key = _normalize_tool_name(tool_name)
    return key in tier_exempt_tools


def get_most_expensive_tool_cost() -> Decimal:
    """Return the largest configured tool credit cost, including the default."""
    default_cost, overrides, _exempt = _get_tool_cost_config()

    max_cost = default_cost
    for value in overrides.values():
        try:
            candidate = value if isinstance(value, Decimal) else _coerce_decimal(value, default_cost)
        except Exception:
            continue

        if candidate > max_cost:
            max_cost = candidate

    return max_cost


def get_tool_credit_cost_for_channel(channel: str) -> Decimal:
    """Return the credit cost associated with an outbound communication channel."""

    # TextChoices values behave like strings, but also expose ``.value``. Prefer the
    # explicit value when available to avoid stringifying to ``CommsChannel.EMAIL``.
    raw_value = getattr(channel, "value", channel)
    normalized_channel = (
        raw_value.strip().lower() if isinstance(raw_value, str) else ""
    )

    tool_name = _CHANNEL_TOOL_NAMES.get(normalized_channel)
    return get_tool_credit_cost(tool_name)
