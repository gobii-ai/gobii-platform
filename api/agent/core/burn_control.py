"""Burn-rate control helpers for persistent agents."""

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Optional, Union
from uuid import UUID, uuid4

from django.utils import timezone as dj_timezone

from config import settings
from config.redis_client import get_redis_client
from .schedule_parser import ScheduleParser
from .budget import AgentBudgetManager, BudgetContext
from .llm_config import (
    get_agent_baseline_llm_tier,
    get_credit_multiplier_for_tier,
    get_next_lower_configured_tier,
    get_runtime_tier_override,
    set_runtime_tier_override,
)
from .prompt_context import get_agent_daily_credit_state
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from api.models import (
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentSystemStep,
    PersistentAgentStep,
)

logger = logging.getLogger(__name__)

BURN_RATE_COOLDOWN_SECONDS = int(getattr(settings, "BURN_RATE_COOLDOWN_SECONDS", 3600))
BURN_FOLLOW_UP_SKIP_WINDOW_SECONDS = int(
    getattr(settings, "BURN_FOLLOW_UP_SKIP_WINDOW_SECONDS", 2 * 60 * 60)
)
BURN_FOLLOW_UP_TTL_BUFFER_SECONDS = int(
    getattr(settings, "BURN_FOLLOW_UP_TTL_BUFFER_SECONDS", 600)
)
BURN_RATE_USER_INACTIVITY_MINUTES = int(
    getattr(settings, "BURN_RATE_USER_INACTIVITY_MINUTES", 60)
)


def _resolve_burn_rate_metrics(daily_state: Optional[dict]) -> tuple[Decimal, Decimal, Optional[int]] | None:
    """Normalize burn-rate metrics from daily state when threshold is exceeded."""

    if daily_state is None:
        return None

    burn_rate = daily_state.get("burn_rate_per_hour")
    burn_threshold = daily_state.get("burn_rate_threshold_per_hour")
    burn_window = daily_state.get("burn_rate_window_minutes")

    try:
        if (
            burn_rate is None
            or burn_threshold is None
            or burn_threshold <= Decimal("0")
            or burn_rate <= burn_threshold
        ):
            return None
    except Exception:
        logger.warning(
            "Error while evaluating burn-rate metrics from daily state: %s",
            daily_state,
            exc_info=True,
        )
        return None

    return burn_rate, burn_threshold, burn_window


def burn_cooldown_key(agent_id: Union[str, UUID]) -> str:
    """Return the Redis key used to mark an active burn-rate cooldown."""

    return f"agent-burn-cooldown:{agent_id}"


def burn_follow_up_key(agent_id: Union[str, UUID]) -> str:
    """Return the Redis key used to dedupe scheduled burn-rate follow-ups."""

    return f"agent-burn-followup:{agent_id}"


def _next_scheduled_run(agent: PersistentAgent, *, now=None):
    """Return the datetime of the next scheduled run for the agent, if known."""

    now = now or dj_timezone.now()
    schedule_str = getattr(agent, "schedule", None) or getattr(agent, "schedule_snapshot", None)
    if not schedule_str:
        return None

    try:
        schedule_obj = ScheduleParser.parse(schedule_str)
    except Exception:
        logger.warning("Failed to parse schedule for agent %s", agent.id, exc_info=True)
        return None

    if schedule_obj is None:
        return None

    try:
        eta = schedule_obj.remaining_estimate(now)
    except Exception:
        logger.warning("Failed to compute next scheduled run for agent %s", agent.id, exc_info=True)
        return None

    if eta is None:
        return None

    if isinstance(eta, (int, float)):
        eta = timedelta(seconds=eta)

    try:
        return now + eta
    except Exception:
        logger.warning("Failed to compute next scheduled datetime for agent %s", agent.id, exc_info=True)
        return None


def has_recent_user_message(agent_id: Union[str, UUID], *, window_minutes: int) -> bool:
    """Return True if the agent received a non-peer inbound message recently."""

    cutoff = dj_timezone.now() - timedelta(minutes=window_minutes)
    try:
        return PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            is_outbound=False,
            timestamp__gte=cutoff,
            conversation__is_peer_dm=False,
        ).exists()
    except Exception:
        logger.debug(
            "Failed to check recent user messages for agent %s", agent_id, exc_info=True
        )
        return False


def schedule_burn_follow_up(
    agent: PersistentAgent,
    cooldown_seconds: int,
    redis_client=None,
    follow_up_task=None,
) -> Optional[str]:
    """Schedule a delayed follow-up run to resume after a burn-rate pause."""

    now = dj_timezone.now()
    cooldown_ends_at = now + timedelta(seconds=max(1, int(cooldown_seconds)))
    next_run = _next_scheduled_run(agent, now=now)
    if next_run is not None:
        horizon = now + timedelta(seconds=BURN_FOLLOW_UP_SKIP_WINDOW_SECONDS)
        # Keep the optimization only when cron is expected shortly *after*
        # cooldown ends. If cron lands during cooldown, we still need follow-up.
        if cooldown_ends_at <= next_run <= horizon:
            logger.info(
                "Skipping burn-rate follow-up for agent %s: next cron/interval run at %s is after cooldown end %s and within skip window horizon %s.",
                agent.id,
                next_run,
                cooldown_ends_at,
                horizon,
            )
            return None

    try:
        from api.agent.core import event_processing as _event_processing  # type: ignore

        process_agent_events_task = (
            follow_up_task
            or getattr(_event_processing, "process_agent_events_task", None)
        )
        if process_agent_events_task is None:
            from ..tasks.process_events import process_agent_events_task  # noqa: WPS433
    except Exception:
        logger.exception("Failed to import process_agent_events_task for agent %s", agent.id)
        return None

    token = uuid4().hex
    ttl_seconds = max(1, cooldown_seconds + BURN_FOLLOW_UP_TTL_BUFFER_SECONDS)
    client = redis_client if redis_client is not None else get_redis_client()
    try:
        set_result = client.set(
            burn_follow_up_key(agent.id),
            token,
            ex=ttl_seconds,
        )
        logger.debug(
            "Burn follow-up token set result for agent %s: %s",
            agent.id,
            set_result,
        )
    except Exception:
        logger.debug(
            "Failed to persist burn follow-up token for agent %s", agent.id, exc_info=True
        )
        return None

    if not set_result:
        logger.debug(
            "Redis refused burn follow-up token set for agent %s; skipping follow-up schedule.",
            agent.id,
        )
        return None

    try:
        logger.info(
            "Scheduling burn-rate follow-up for agent %s via %s (countdown=%s, ttl=%s)",
            agent.id,
            getattr(process_agent_events_task, "__name__", type(process_agent_events_task)),
            cooldown_seconds,
            ttl_seconds,
        )
        process_agent_events_task.apply_async(
            args=[str(agent.id)],
            kwargs={"burn_follow_up_token": token},
            countdown=cooldown_seconds,
        )
    except Exception:
        logger.error(
            "Failed to schedule burn-rate follow-up for agent %s", agent.id, exc_info=True
        )
        return None

    return token


def pause_for_burn_rate(
    agent: PersistentAgent,
    *,
    burn_rate: Decimal,
    burn_threshold: Decimal,
    burn_window: Optional[int],
    budget_ctx: Optional[BudgetContext],
    span=None,
    redis_client=None,
    follow_up_task=None,
) -> None:
    """Record a burn-rate pause, set cooldown markers, and schedule a follow-up."""

    cooldown_seconds = max(1, int(BURN_RATE_COOLDOWN_SECONDS))
    redis_client = redis_client if redis_client is not None else get_redis_client()

    try:
        redis_client.set(
            burn_cooldown_key(agent.id),
            "1",
            ex=cooldown_seconds,
        )
    except Exception:
        logger.debug(
            "Failed to set burn-rate cooldown key for agent %s", agent.id, exc_info=True
        )

    follow_up_token = schedule_burn_follow_up(
        agent,
        cooldown_seconds,
        redis_client=redis_client,
        follow_up_task=follow_up_task,
    )

    window_text = f"{burn_window} minutes" if burn_window else "the recent window"
    cooldown_minutes = round(cooldown_seconds / 60, 2)
    description = (
        "Paused processing due to elevated burn rate without recent user input. "
        f"Current burn rate: {burn_rate} credits/hour over {window_text}; "
        f"threshold: {burn_threshold} credits/hour. "
        f"Will resume after cooldown (~{cooldown_minutes} minutes) or when triggered by new input."
    )
    step = PersistentAgentStep.objects.create(
        agent=agent,
        description=description,
    )
    PersistentAgentSystemStep.objects.create(
        step=step,
        code=PersistentAgentSystemStep.Code.BURN_RATE_COOLDOWN,
    )

    try:
        analytics_props: dict[str, str] = {
            "agent_id": str(agent.id),
            "agent_name": agent.name,
            "burn_rate_per_hour": str(burn_rate),
            "burn_rate_threshold_per_hour": str(burn_threshold),
        }
        if burn_window is not None:
            analytics_props["burn_rate_window_minutes"] = str(burn_window)
        props_with_org = Analytics.with_org_properties(
            analytics_props,
            organization=getattr(agent, "organization", None),
        )
        Analytics.track_event(
            user_id=getattr(getattr(agent, "user", None), "id", None),
            event=AnalyticsEvent.PERSISTENT_AGENT_BURN_RATE_LIMIT_REACHED,
            source=AnalyticsSource.AGENT,
            properties=props_with_org,
        )
    except Exception:
        logger.debug(
            "Failed to emit burn-rate limit analytics for agent %s", agent.id, exc_info=True
        )

    if span is not None:
        try:
            span.add_event("Burn-rate cooldown activated")
            span.set_attribute("burn_rate.cooldown_seconds", cooldown_seconds)
            span.set_attribute("burn_rate.value", float(burn_rate))
            span.set_attribute("burn_rate.threshold", float(burn_threshold))
            span.set_attribute("burn_rate.follow_up_token_present", bool(follow_up_token))
        except Exception:
            logger.debug("Failed to set burn-rate span attributes for agent %s", agent.id, exc_info=True)

    if budget_ctx is not None:
        try:
            AgentBudgetManager.close_cycle(
                agent_id=budget_ctx.agent_id,
                budget_id=budget_ctx.budget_id,
            )
            logger.info(
                "Closed budget cycle for agent %s after burn-rate pause.",
                agent.id,
            )
        except Exception:
            logger.debug(
                "Failed to close budget cycle for agent %s after burn pause.",
                agent.id,
                exc_info=True,
            )


def should_pause_for_burn_rate(
    agent: PersistentAgent,
    *,
    budget_ctx: Optional[BudgetContext],
    span=None,
    daily_state: Optional[dict] = None,
    redis_client=None,
    follow_up_task=None,
) -> bool:
    """Return True and trigger pause if burn rate exceeds threshold without user input."""

    if daily_state is None:
        try:
            daily_state = get_agent_daily_credit_state(agent)
        except Exception:
            logger.warning(
                "Failed to get daily credit state for agent %s; cannot check burn rate.",
                agent.id,
                exc_info=True,
            )
            return False
    if daily_state is None:
        logger.warning(
            "Daily credit state unavailable for agent %s; skipping burn-rate pause check.",
            agent.id,
        )
        return False
    burn_rate = daily_state.get("burn_rate_per_hour")
    burn_threshold = daily_state.get("burn_rate_threshold_per_hour")
    burn_window = daily_state.get("burn_rate_window_minutes")

    try:
        if (
            burn_rate is None
            or burn_threshold is None
            or burn_threshold <= Decimal("0")
            or burn_rate <= burn_threshold
        ):
            return False
    except Exception as exc:
        logger.warning(
            "Error during burn rate check for agent %s: %s. Skipping pause.",
            getattr(agent, "id", "unknown"),
            exc,
            exc_info=True,
        )
        return False

    # Do not pause if the agent recently engaged with the user.
    if has_recent_user_message(
        agent.id, window_minutes=BURN_RATE_USER_INACTIVITY_MINUTES
    ):
        return False

    # If a cooldown is already in place, do not schedule another.
    try:
        client = redis_client if redis_client is not None else get_redis_client()
        if client.get(burn_cooldown_key(agent.id)):
            return False
    except Exception:
        logger.warning(
            "Failed cooldown check for agent %s; proceeding as if no cooldown is active.",
            agent.id,
            exc_info=True,
        )

    if follow_up_task is not None:
        logger.debug("Burn-rate pause will schedule follow-up via override task for agent %s", agent.id)

    pause_for_burn_rate(
        agent,
        burn_rate=burn_rate,
        burn_threshold=burn_threshold,
        burn_window=burn_window,
        budget_ctx=budget_ctx,
        span=span,
        redis_client=redis_client,
        follow_up_task=follow_up_task,
    )
    return True


def maybe_step_down_runtime_tier_for_burn_rate(
    agent: PersistentAgent,
    *,
    daily_state: Optional[dict] = None,
    span=None,
) -> bool:
    """Lower the runtime tier for this run when burn is high but user input is recent."""

    if daily_state is None:
        try:
            daily_state = get_agent_daily_credit_state(agent)
        except Exception:
            logger.warning(
                "Failed to get daily credit state for runtime tier step-down on agent %s.",
                agent.id,
                exc_info=True,
            )
            return False

    metrics = _resolve_burn_rate_metrics(daily_state)
    if metrics is None:
        return False

    if not has_recent_user_message(
        agent.id,
        window_minutes=BURN_RATE_USER_INACTIVITY_MINUTES,
    ):
        return False

    if get_runtime_tier_override(agent) is not None:
        return False

    baseline_tier = get_agent_baseline_llm_tier(agent)
    runtime_tier = get_next_lower_configured_tier(baseline_tier)
    if runtime_tier == baseline_tier:
        return False

    set_runtime_tier_override(agent, runtime_tier)
    burn_rate, burn_threshold, burn_window = metrics
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
        except Exception:
            logger.debug(
                "Failed to set runtime tier step-down span attributes for agent %s",
                agent.id,
                exc_info=True,
            )

    logger.info(
        "Agent %s runtime tier stepped down from %s to %s due to burn rate %s > %s with recent user input.",
        agent.id,
        baseline_tier.value,
        runtime_tier.value,
        burn_rate,
        burn_threshold,
    )
    return True
