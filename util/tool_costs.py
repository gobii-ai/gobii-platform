from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Tuple

from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.db import OperationalError, ProgrammingError

_TOOL_COST_CACHE_KEY = "task_credit_costs:v1"
_TOOL_COST_CACHE_TTL_SECONDS = 300  # 5 minutes is enough for eventual consistency in workers


def clear_tool_credit_cost_cache() -> None:
    """Evict the cached tool credit cost mapping."""
    cache.delete(_TOOL_COST_CACHE_KEY)


def _normalize_tool_name(name: str | None) -> str:
    return (name or "").strip().lower()


def _get_models() -> Tuple[Any, Any]:
    """Return the TaskCreditConfig and ToolCreditCost models lazily."""
    TaskCreditConfig = apps.get_model("api", "TaskCreditConfig")
    ToolCreditCost = apps.get_model("api", "ToolCreditCost")
    return TaskCreditConfig, ToolCreditCost


def _coerce_decimal(value: Any, fallback: Decimal) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return fallback


def _fetch_tool_cost_configuration() -> Tuple[Decimal, Dict[str, Decimal]]:
    """
    Retrieve default and per-tool credit costs from the database.

    Falls back to settings values when tables are unavailable (e.g. during
    migrations or the very first deploy).
    """

    default_cost = getattr(settings, "CREDITS_PER_TASK")
    overrides: Dict[str, Decimal] = {}

    TaskCreditConfig, ToolCreditCost = _get_models()

    if TaskCreditConfig is None or ToolCreditCost is None:
        return default_cost, overrides

    try:
        config = TaskCreditConfig.objects.first()
        if config and config.default_task_cost is not None:
            default_cost = config.default_task_cost

        overrides = {
            _normalize_tool_name(entry.tool_name): entry.credit_cost
            for entry in ToolCreditCost.objects.all()
        }
    except (OperationalError, ProgrammingError):
        # Database tables may not exist yet
        raw_mapping: dict[str, Any] = getattr(settings, "TOOL_CREDIT_COSTS", {}) or {}
        overrides = {
            _normalize_tool_name(name): _coerce_decimal(value, default_cost)
            for name, value in raw_mapping.items()
        }

    return default_cost, overrides


def _get_tool_cost_config() -> Tuple[Decimal, Dict[str, Decimal]]:
    cached = cache.get(_TOOL_COST_CACHE_KEY)
    if cached is not None:
        return cached

    data = _fetch_tool_cost_configuration()
    cache.set(_TOOL_COST_CACHE_KEY, data, _TOOL_COST_CACHE_TTL_SECONDS)
    return data


def get_default_task_credit_cost() -> Decimal:
    default_cost, _ = _get_tool_cost_config()
    return default_cost


def get_tool_credit_cost(tool_name: str | None) -> Decimal:
    """Return the credit cost for the given tool name."""
    default_cost, overrides = _get_tool_cost_config()

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


def get_most_expensive_tool_cost() -> Decimal:
    """Return the largest configured tool credit cost, including the default."""
    default_cost: Decimal = getattr(settings, "CREDITS_PER_TASK")
    raw_mapping: dict[str, Any] = getattr(settings, "TOOL_CREDIT_COSTS", {}) or {}

    max_cost = default_cost
    for value in raw_mapping.values():
        try:
            candidate = value if isinstance(value, Decimal) else Decimal(str(value))
        except Exception:
            continue

        if candidate > max_cost:
            max_cost = candidate

    return max_cost
