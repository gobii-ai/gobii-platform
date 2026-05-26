"""Burn-rate control helpers for persistent agents."""

from enum import Enum
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

from .budget import BudgetContext
from .llm_config import (
    AgentLLMTier,
    get_agent_baseline_llm_tier,
    get_credit_multiplier_for_tier,
    get_runtime_tier_override,
    set_runtime_tier_override,
)
from .prompt_context import get_agent_daily_credit_state
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from api.models import PersistentAgent

logger = logging.getLogger(__name__)


class BurnRateAction(str, Enum):
    NONE = "none"
    STEPPED_DOWN = "stepped_down"


def _decimal_metric(value) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _resolve_burn_rate_metrics(
    daily_state: Optional[dict],
) -> tuple[Decimal, Decimal, Optional[int], Optional[Decimal], Optional[Decimal]] | None:
    """Normalize burn-rate metrics from daily state when threshold is exceeded."""

    if daily_state is None:
        return None

    burn_rate = _decimal_metric(daily_state.get("burn_rate_per_hour"))
    burn_threshold = _decimal_metric(daily_state.get("burn_rate_threshold_per_hour"))
    burn_window = daily_state.get("burn_rate_window_minutes")
    burn_24h_total = _decimal_metric(daily_state.get("burn_rate_24h_total"))
    burn_24h_threshold = _decimal_metric(daily_state.get("burn_rate_threshold_24h"))

    if (
        burn_rate is None
        or burn_threshold is None
        or burn_threshold <= Decimal("0")
        or burn_rate <= burn_threshold
    ):
        return None

    if burn_24h_threshold is not None and burn_24h_threshold > Decimal("0"):
        if burn_24h_total is None or burn_24h_total <= burn_24h_threshold:
            logger.debug(
                "Burn-rate hourly threshold exceeded but 24h burn gate not met: total=%s threshold=%s.",
                burn_24h_total,
                burn_24h_threshold,
            )
            return None

    try:
        burn_window = int(burn_window) if burn_window is not None else None
    except (TypeError, ValueError):
        logger.debug("Invalid burn-rate window from daily state: %s", burn_window)
        burn_window = None

    return burn_rate, burn_threshold, burn_window, burn_24h_total, burn_24h_threshold


def _burn_24h_analytics_props(
    *,
    burn_24h_total: Optional[Decimal],
    burn_24h_threshold: Optional[Decimal],
) -> dict[str, str]:
    if burn_24h_total is None and burn_24h_threshold is None:
        return {}
    props: dict[str, str] = {}
    if burn_24h_total is not None:
        props["burn_rate_24h_total"] = str(burn_24h_total)
    if burn_24h_threshold is not None:
        props["burn_rate_threshold_24h"] = str(burn_24h_threshold)
    return props


def _set_burn_24h_span_attributes(
    span,
    *,
    burn_24h_total: Optional[Decimal],
    burn_24h_threshold: Optional[Decimal],
) -> None:
    if span is None:
        return
    try:
        if burn_24h_total is not None:
            span.set_attribute("burn_rate.24h_total", float(burn_24h_total))
        if burn_24h_threshold is not None:
            span.set_attribute("burn_rate.24h_threshold", float(burn_24h_threshold))
    except (TypeError, ValueError, OverflowError):
        logger.debug("Failed to set burn-rate 24h span attributes.", exc_info=True)
        return None


def _step_down_runtime_tier(
    agent: PersistentAgent,
    *,
    burn_rate: Decimal,
    burn_threshold: Decimal,
    burn_window: Optional[int],
    burn_24h_total: Optional[Decimal] = None,
    burn_24h_threshold: Optional[Decimal] = None,
    span=None,
) -> bool:
    """Apply a runtime tier downgrade to the lowest tier for this processing run."""

    if get_runtime_tier_override(agent) is not None:
        return False

    baseline_tier = get_agent_baseline_llm_tier(agent)
    runtime_tier = AgentLLMTier.STANDARD
    if runtime_tier == baseline_tier:
        return False

    set_runtime_tier_override(agent, runtime_tier)
    baseline_multiplier = get_credit_multiplier_for_tier(baseline_tier)
    runtime_multiplier = get_credit_multiplier_for_tier(runtime_tier)

    try:
        analytics_props: dict[str, str] = {
            "agent_id": str(agent.id),
            "agent_name": agent.name,
            "baseline_tier": baseline_tier.value,
            "runtime_tier": runtime_tier.value,
            "baseline_multiplier": str(baseline_multiplier),
            "runtime_multiplier": str(runtime_multiplier),
            "burn_rate_per_hour": str(burn_rate),
            "burn_rate_threshold_per_hour": str(burn_threshold),
        }
        analytics_props.update(
            _burn_24h_analytics_props(
                burn_24h_total=burn_24h_total,
                burn_24h_threshold=burn_24h_threshold,
            )
        )
        if burn_window is not None:
            analytics_props["burn_rate_window_minutes"] = str(burn_window)
        props_with_org = Analytics.with_org_properties(
            analytics_props,
            organization=getattr(agent, "organization", None),
        )
        Analytics.track_event(
            user_id=getattr(getattr(agent, "user", None), "id", None),
            event=AnalyticsEvent.PERSISTENT_AGENT_BURN_RATE_RUNTIME_TIER_STEPPED_DOWN,
            source=AnalyticsSource.AGENT,
            properties=props_with_org,
        )
    except Exception:
        logger.debug(
            "Failed to emit runtime tier step-down analytics for agent %s",
            agent.id,
            exc_info=True,
        )

    if span is not None:
        try:
            span.add_event("Burn-rate runtime tier step-down activated")
            span.set_attribute("burn_rate.runtime_tier_step_down", True)
            span.set_attribute("burn_rate.runtime_tier_from", baseline_tier.value)
            span.set_attribute("burn_rate.runtime_tier_to", runtime_tier.value)
            span.set_attribute("burn_rate.value", float(burn_rate))
            span.set_attribute("burn_rate.threshold", float(burn_threshold))
            _set_burn_24h_span_attributes(
                span,
                burn_24h_total=burn_24h_total,
                burn_24h_threshold=burn_24h_threshold,
            )
        except Exception:
            logger.debug(
                "Failed to set runtime tier step-down span attributes for agent %s",
                agent.id,
                exc_info=True,
            )

    logger.info(
        "Agent %s runtime tier stepped down from %s to %s due to burn rate %s > %s.",
        agent.id,
        baseline_tier.value,
        runtime_tier.value,
        burn_rate,
        burn_threshold,
    )
    return True


def handle_burn_rate_limit(
    agent: PersistentAgent,
    *,
    budget_ctx: Optional[BudgetContext],
    span=None,
    daily_state: Optional[dict] = None,
    redis_client=None,
    follow_up_task=None,
) -> BurnRateAction:
    """Apply burn-rate controls and return the action taken."""

    if daily_state is None:
        try:
            daily_state = get_agent_daily_credit_state(agent)
        except Exception:
            logger.warning(
                "Failed to get daily credit state for agent %s; cannot check burn rate.",
                agent.id,
                exc_info=True,
            )
            return BurnRateAction.NONE
    if daily_state is None:
        logger.warning(
            "Daily credit state unavailable for agent %s; skipping burn-rate check.",
            agent.id,
        )
        return BurnRateAction.NONE

    metrics = _resolve_burn_rate_metrics(daily_state)
    if metrics is None:
        return BurnRateAction.NONE
    burn_rate, burn_threshold, burn_window, burn_24h_total, burn_24h_threshold = metrics

    if _step_down_runtime_tier(
        agent,
        burn_rate=burn_rate,
        burn_threshold=burn_threshold,
        burn_window=burn_window,
        burn_24h_total=burn_24h_total,
        burn_24h_threshold=burn_24h_threshold,
        span=span,
    ):
        return BurnRateAction.STEPPED_DOWN
    return BurnRateAction.NONE


def should_pause_for_burn_rate(
    agent: PersistentAgent,
    *,
    budget_ctx: Optional[BudgetContext],
    span=None,
    daily_state: Optional[dict] = None,
    redis_client=None,
    follow_up_task=None,
) -> bool:
    """Backwards-compatible helper; burn-rate pauses are no longer applied."""

    handle_burn_rate_limit(
        agent,
        budget_ctx=budget_ctx,
        span=span,
        daily_state=daily_state,
        redis_client=redis_client,
        follow_up_task=follow_up_task,
    )
    return False


def maybe_step_down_runtime_tier_for_burn_rate(
    agent: PersistentAgent,
    *,
    daily_state: Optional[dict] = None,
    span=None,
) -> bool:
    """Backwards-compatible step-down helper built on top of unified burn control."""

    return handle_burn_rate_limit(
        agent,
        budget_ctx=None,
        span=span,
        daily_state=daily_state,
    ) == BurnRateAction.STEPPED_DOWN
