from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.conf import settings

def _normalize_tool_name(name: str | None) -> str:
    return (name or "").strip().lower()

def get_tool_credit_cost(tool_name: str | None) -> Decimal:
    """
    Return the credit cost for a tool name using settings.TOOL_CREDIT_COSTS,
    falling back to settings.CREDITS_PER_TASK when no override exists.

    Tool lookup is case-insensitive. Values in TOOL_CREDIT_COSTS may be Decimal,
    int, float, or str accepted by Decimal(); they're coerced to Decimal.
    """
    default_cost: Decimal = getattr(settings, "CREDITS_PER_TASK")
    raw_mapping: dict[str, Any] = getattr(settings, "TOOL_CREDIT_COSTS", {}) or {}
    # Normalize mapping keys to lowercase for case-insensitive lookups
    mapping = { _normalize_tool_name(k): v for k, v in raw_mapping.items() }

    key = _normalize_tool_name(tool_name)
    if not key:
        return default_cost

    if key in mapping:
        val = mapping[key]
        try:
            return val if isinstance(val, Decimal) else Decimal(str(val))
        except Exception:
            # Fallback to default on invalid config values
            return default_cost

    return default_cost
