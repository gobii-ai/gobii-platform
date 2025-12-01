"""Burn-rate control helpers for persistent agents."""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Optional, Union
from uuid import UUID, uuid4

from django.utils import timezone as dj_timezone

from config import settings
from config.redis_client import get_redis_client
from .budget import AgentBudgetManager, BudgetContext
from .prompt_context import get_agent_daily_credit_state
from api.models import (
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
)

logger = logging.getLogger(__name__)

BURN_RATE_COOLDOWN_SECONDS = int(getattr(settings, "BURN_RATE_COOLDOWN_SECONDS", 3600))
BURN_RATE_USER_INACTIVITY_MINUTES = int(
    getattr(settings, "BURN_RATE_USER_INACTIVITY_MINUTES", 60)
)


def burn_cooldown_key(agent_id: Union[str, UUID]) -> str:
    """Return the Redis key used to mark an active burn-rate cooldown."""

    return f"agent-burn-cooldown:{agent_id}"


def burn_follow_up_key(agent_id: Union[str, UUID]) -> str:
    """Return the Redis key used to dedupe scheduled burn-rate follow-ups."""

    return f"agent-burn-followup:{agent_id}"


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


def schedule_burn_follow_up(agent: PersistentAgent, cooldown_seconds: int) -> Optional[str]:
    """Schedule a delayed follow-up run to resume after a burn-rate pause."""

    try:
        from ..tasks.process_events import process_agent_events_task  # noqa: WPS433
    except Exception:
        logger.exception("Failed to import process_agent_events_task for agent %s", agent.id)
        return None

    token = uuid4().hex
    redis_client = get_redis_client()
    try:
        set_result = redis_client.set(
            burn_follow_up_key(agent.id),
            token,
            ex=cooldown_seconds,
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
) -> None:
    """Record a burn-rate pause, set cooldown markers, and schedule a follow-up."""

    cooldown_seconds = max(1, int(BURN_RATE_COOLDOWN_SECONDS))
    redis_client = get_redis_client()

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

    follow_up_token = schedule_burn_follow_up(agent, cooldown_seconds)

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
        code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        notes="burn_rate_cooldown",
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
    agent: PersistentAgent, *, budget_ctx: Optional[BudgetContext], span=None
) -> bool:
    """Return True and trigger pause if burn rate exceeds threshold without user input."""

    daily_state = get_agent_daily_credit_state(agent)
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
    except Exception:
        return False

    # Do not pause if the agent recently engaged with the user.
    if has_recent_user_message(
        agent.id, window_minutes=BURN_RATE_USER_INACTIVITY_MINUTES
    ):
        return False

    # If a cooldown is already in place, do not schedule another.
    try:
        if get_redis_client().get(burn_cooldown_key(agent.id)):
            return False
    except Exception:
        logger.debug(
            "Failed cooldown check for agent %s; proceeding cautiously.",
            agent.id,
            exc_info=True,
        )

    pause_for_burn_rate(
        agent,
        burn_rate=burn_rate,
        burn_threshold=burn_threshold,
        burn_window=burn_window,
        budget_ctx=budget_ctx,
        span=span,
    )
    return True
