import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Any

from django.utils import timezone
from django.utils.formats import date_format

from api.models import PersistentAgentSystemStep
from api.services.daily_credit_limits import (
    STANDARD_TIER_DAILY_CREDIT_MAX,
    calculate_daily_credit_slider_bounds,
    get_agent_credit_multiplier,
)
from api.services.daily_credit_settings import get_daily_credit_settings_for_owner


logger = logging.getLogger(__name__)


def _percent(value: Decimal, total: Decimal | None) -> float | None:
    if total is None or total <= Decimal("0"):
        return None
    try:
        pct = float((value / total) * 100)
    except Exception:
        return None
    return min(pct, 100.0)


def get_daily_credit_slider_bounds(
    credit_settings,
    *,
    tier_multiplier: Decimal | None = None,
) -> dict[str, Decimal]:
    return calculate_daily_credit_slider_bounds(
        credit_settings,
        tier_multiplier=tier_multiplier,
    )


def build_agent_daily_credit_context(agent, owner=None) -> dict[str, Any]:
    if owner is None:
        owner = agent.organization or agent.user
    credit_settings = get_daily_credit_settings_for_owner(owner)
    tier_multiplier = get_agent_credit_multiplier(agent)
    slider_bounds = get_daily_credit_slider_bounds(credit_settings, tier_multiplier=tier_multiplier)
    blocked_today = False

    context = {
        "daily_credit_slider_min": slider_bounds["slider_min"],
        "daily_credit_slider_max": slider_bounds["slider_unlimited_value"],
        "daily_credit_slider_step": slider_bounds["slider_step"],
        "daily_credit_slider_limit_max": slider_bounds["slider_limit_max"],
    }

    try:
        today = timezone.localdate()
        try:
            blocked_today = PersistentAgentSystemStep.objects.filter(
                step__agent=agent,
                code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
                notes__in=["daily_credit_limit_mid_loop", "daily_credit_limit_exhausted"],
                step__created_at__date=today,
            ).exists()
        except Exception as exc:
            logger.warning(
                "Failed to check daily credit block status for agent %s: %s",
                getattr(agent, "id", None),
                exc,
                exc_info=True,
            )
        soft_target = agent.get_daily_credit_soft_target()
        hard_limit = agent.get_daily_credit_hard_limit()
        usage = agent.get_daily_credit_usage(usage_date=today)
        hard_remaining = agent.get_daily_credit_remaining(usage_date=today)
        soft_remaining = agent.get_daily_credit_soft_target_remaining(usage_date=today)
        unlimited = soft_target is None

        percent_used = _percent(usage, hard_limit)
        soft_percent_used = _percent(usage, soft_target)
        next_reset = (
            timezone.localtime(timezone.now()).replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            + timedelta(days=1)
        )

        slider_value = slider_bounds["slider_unlimited_value"]
        if soft_target is not None:
            slider_value = soft_target
            if slider_value < slider_bounds["slider_min"]:
                slider_value = slider_bounds["slider_min"]
            if slider_value > slider_bounds["slider_limit_max"]:
                slider_value = slider_bounds["slider_limit_max"]

        context.update(
            {
                "daily_credit_limit": soft_target,
                "daily_credit_soft_target": soft_target,
                "daily_credit_hard_limit": hard_limit,
                "daily_credit_usage": usage,
                "daily_credit_remaining": hard_remaining,
                "daily_credit_soft_remaining": soft_remaining,
                "daily_credit_unlimited": unlimited,
                "daily_credit_percent_used": percent_used,
                "daily_credit_soft_percent_used": soft_percent_used,
                "daily_credit_next_reset": next_reset,
                "daily_credit_low": (
                    not unlimited and hard_remaining is not None and hard_remaining < Decimal("1")
                ),
                "daily_credit_slider_value": slider_value,
            }
        )
    except Exception as exc:
        logger.error(
            "Failed to get daily credit usage for agent %s: %s",
            getattr(agent, "id", None),
            exc,
            exc_info=True,
        )
        context.update(
            {
                "daily_credit_limit": None,
                "daily_credit_soft_target": None,
                "daily_credit_hard_limit": None,
                "daily_credit_usage": Decimal("0"),
                "daily_credit_remaining": None,
                "daily_credit_soft_remaining": None,
                "daily_credit_unlimited": True,
                "daily_credit_percent_used": None,
                "daily_credit_soft_percent_used": None,
                "daily_credit_next_reset": None,
                "daily_credit_low": False,
                "daily_credit_slider_value": slider_bounds["slider_unlimited_value"],
            }
        )

    context["daily_credit_hard_blocked"] = blocked_today
    return context


def _decimal_to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _datetime_iso(value):
    if not value:
        return None
    localized = timezone.localtime(value)
    return localized.isoformat()


def _datetime_display(value, fmt: str):
    if not value:
        return None
    localized = timezone.localtime(value)
    return date_format(localized, fmt)


def serialize_daily_credit_payload(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "limit": _decimal_to_float(context.get("daily_credit_limit")),
        "hardLimit": _decimal_to_float(context.get("daily_credit_hard_limit")),
        "usage": _decimal_to_float(context.get("daily_credit_usage")) or 0.0,
        "remaining": _decimal_to_float(context.get("daily_credit_remaining")),
        "softRemaining": _decimal_to_float(context.get("daily_credit_soft_remaining")),
        "unlimited": bool(context.get("daily_credit_unlimited")),
        "percentUsed": _decimal_to_float(context.get("daily_credit_percent_used")),
        "softPercentUsed": _decimal_to_float(context.get("daily_credit_soft_percent_used")),
        "nextResetIso": _datetime_iso(context.get("daily_credit_next_reset")),
        "nextResetLabel": _datetime_display(context.get("daily_credit_next_reset"), "Y-m-d H:i T"),
        "low": bool(context.get("daily_credit_low")),
        "sliderMin": _decimal_to_float(context.get("daily_credit_slider_min")) or 0.0,
        "sliderMax": _decimal_to_float(context.get("daily_credit_slider_max")) or 0.0,
        "sliderLimitMax": _decimal_to_float(context.get("daily_credit_slider_limit_max")) or 0.0,
        "sliderStep": _decimal_to_float(context.get("daily_credit_slider_step")) or 1.0,
        "sliderValue": _decimal_to_float(context.get("daily_credit_slider_value"))
        or _decimal_to_float(context.get("daily_credit_slider_min"))
        or 0.0,
        "sliderEmptyValue": _decimal_to_float(context.get("daily_credit_slider_max")) or 0.0,
        "standardSliderLimit": _decimal_to_float(STANDARD_TIER_DAILY_CREDIT_MAX) or 0.0,
    }


def build_daily_credit_status(context: dict[str, Any]) -> dict[str, bool]:
    soft_remaining = context.get("daily_credit_soft_remaining")
    hard_remaining = context.get("daily_credit_remaining")
    unlimited = bool(context.get("daily_credit_unlimited"))
    soft_exceeded = soft_remaining is not None and soft_remaining <= Decimal("0")
    hard_reached = hard_remaining is not None and hard_remaining <= Decimal("0")
    hard_blocked = bool(context.get("daily_credit_hard_blocked"))
    if unlimited or (hard_remaining is not None and hard_remaining > Decimal("0")):
        hard_blocked = False
    return {
        "softTargetExceeded": soft_exceeded,
        "hardLimitReached": hard_reached,
        "hardLimitBlocked": hard_reached or hard_blocked,
    }


def parse_daily_credit_limit(
    payload: dict[str, Any],
    credit_settings,
    *,
    tier_multiplier: Decimal | None = None,
) -> tuple[int | None, str | None]:
    raw_limit = payload.get("daily_credit_limit", None)
    if raw_limit is None or (isinstance(raw_limit, str) and not raw_limit.strip()):
        return None, None

    try:
        parsed_limit = Decimal(str(raw_limit))
    except InvalidOperation:
        return None, "Enter a whole number for the daily credit soft target."

    if parsed_limit != parsed_limit.to_integral_value(rounding=ROUND_DOWN):
        return None, "Enter a whole number for the daily credit soft target."

    parsed_limit = parsed_limit.to_integral_value(rounding=ROUND_HALF_UP)
    if parsed_limit <= Decimal("0"):
        return None, None

    slider_bounds = get_daily_credit_slider_bounds(credit_settings, tier_multiplier=tier_multiplier)
    slider_min = slider_bounds["slider_min"]
    slider_max = slider_bounds["slider_limit_max"]
    if parsed_limit < slider_min:
        parsed_limit = slider_min
    if parsed_limit > slider_max:
        parsed_limit = slider_max

    return int(parsed_limit), None
