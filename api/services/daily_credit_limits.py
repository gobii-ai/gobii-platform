from decimal import Decimal, DivisionByZero, InvalidOperation, ROUND_HALF_UP
from typing import Any

# Baseline for standard tier; higher tiers scale this via multipliers.
STANDARD_TIER_DAILY_CREDIT_MAX = Decimal("20")
_DEFAULT_TIER_MULTIPLIER = Decimal("1")


def _coerce_multiplier(value: Any, fallback: Decimal = _DEFAULT_TIER_MULTIPLIER) -> Decimal:
    try:
        multiplier = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return fallback
    if multiplier <= Decimal("0"):
        return fallback
    return multiplier


def get_tier_credit_multiplier(tier: Any | None) -> Decimal:
    if tier is None:
        return _DEFAULT_TIER_MULTIPLIER
    return _coerce_multiplier(getattr(tier, "credit_multiplier", None))


def get_agent_credit_multiplier(agent: Any | None) -> Decimal:
    if agent is None:
        return _DEFAULT_TIER_MULTIPLIER
    return get_tier_credit_multiplier(getattr(agent, "preferred_llm_tier", None))


def get_tier_slider_limit_max(tier_multiplier: Decimal | None) -> Decimal:
    multiplier = _coerce_multiplier(tier_multiplier)
    return (STANDARD_TIER_DAILY_CREDIT_MAX * multiplier).to_integral_value(rounding=ROUND_HALF_UP)


def calculate_daily_credit_slider_bounds(
    credit_settings,
    *,
    tier_multiplier: Decimal | None = None,
) -> dict[str, Decimal]:
    slider_min = credit_settings.slider_min
    if slider_min < Decimal("1"):
        slider_min = Decimal("1")

    slider_step = credit_settings.slider_step
    if slider_step <= Decimal("0"):
        slider_step = Decimal("1")

    slider_limit_max = get_tier_slider_limit_max(tier_multiplier)
    if slider_limit_max < slider_min:
        slider_limit_max = slider_min

    slider_unlimited_value = slider_limit_max + slider_step

    return {
        "slider_min": slider_min,
        "slider_limit_max": slider_limit_max,
        "slider_step": slider_step,
        "slider_unlimited_value": slider_unlimited_value,
    }


def scale_daily_credit_limit_for_tier_change(
    limit: int | None,
    *,
    from_multiplier: Decimal | None,
    to_multiplier: Decimal | None,
    slider_min: Decimal,
    slider_max: Decimal,
) -> int | None:
    if limit is None:
        return None
    from_multiplier_value = _coerce_multiplier(from_multiplier)
    to_multiplier_value = _coerce_multiplier(to_multiplier)
    try:
        scaled = (
            Decimal(limit)
            * to_multiplier_value
            / from_multiplier_value
        ).to_integral_value(rounding=ROUND_HALF_UP)
    except (DivisionByZero, InvalidOperation):
        return limit
    if scaled <= Decimal("0"):
        return None
    if scaled > slider_max:
        return None
    if scaled < slider_min:
        scaled = slider_min
    return int(scaled)
