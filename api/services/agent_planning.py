import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from api.agent.avatar import maybe_schedule_agent_avatar
from api.agent.short_description import (
    maybe_schedule_mini_description,
    maybe_schedule_short_description,
)
from api.agent.tags import maybe_schedule_agent_tags
from api.evals.execution import get_current_eval_routing_profile
from api.models import PersistentAgent, PersistentAgentHumanInputRequest

logger = logging.getLogger(__name__)
PLANNING_TIMEOUT_SYSTEM_MESSAGE_MARKER = "Planning Timeout Auto-Complete"


def _schedule_charter_metadata(agent: PersistentAgent) -> None:
    routing_profile = get_current_eval_routing_profile()
    routing_profile_id = str(routing_profile.id) if routing_profile else None
    maybe_schedule_short_description(agent, routing_profile_id=routing_profile_id)
    maybe_schedule_mini_description(agent, routing_profile_id=routing_profile_id)
    maybe_schedule_agent_tags(agent, routing_profile_id=routing_profile_id)
    maybe_schedule_agent_avatar(agent, routing_profile_id=routing_profile_id)


def is_planning_timeout_expired(agent: PersistentAgent, *, now=None) -> bool:
    if agent.planning_state != PersistentAgent.PlanningState.PLANNING:
        return False

    timeout_seconds = int(settings.PERSISTENT_AGENT_PLANNING_TIMEOUT_SECONDS)
    if timeout_seconds <= 0:
        return False

    started_at = agent.created_at
    if started_at is None:
        return False

    stamp = now or timezone.now()
    return stamp >= started_at + timedelta(seconds=timeout_seconds)


def build_planning_timeout_directive(agent: PersistentAgent) -> str | None:
    if not is_planning_timeout_expired(agent):
        return None
    return (
        f"{PLANNING_TIMEOUT_SYSTEM_MESSAGE_MARKER}: "
        "This agent has been in Planning Mode for more than 1 hour. "
        "Do not ask another planning question unless the task is impossible to scope from existing context. "
        "Call end_planning(full_plan=...) now with the best decision-complete plan you can infer from the "
        "conversation, explicitly noting reasonable assumptions, then continue with the work after planning ends."
    )


def schedule_planning_timeout_processing(agent: PersistentAgent) -> None:
    if agent.planning_state != PersistentAgent.PlanningState.PLANNING:
        return

    timeout_seconds = int(settings.PERSISTENT_AGENT_PLANNING_TIMEOUT_SECONDS)
    if timeout_seconds <= 0:
        return
    if settings.CELERY_TASK_ALWAYS_EAGER and timeout_seconds > 0:
        logger.info(
            "Skipping delayed planning-timeout scheduling in eager mode for agent %s.",
            agent.id,
        )
        return

    from api.agent.tasks import process_planning_timeout_task

    transaction.on_commit(
        lambda: process_planning_timeout_task.apply_async(
            args=[str(agent.id)],
            countdown=timeout_seconds,
        )
    )
    logger.info(
        "Scheduled planning-timeout processing for agent %s in %s seconds.",
        agent.id,
        timeout_seconds,
    )


def complete_agent_planning(agent: PersistentAgent, full_plan: str) -> PersistentAgent:
    """Finalize planning mode and promote the accepted plan into the charter."""
    normalized_plan = (full_plan or "").strip()
    if not normalized_plan:
        raise ValueError("full_plan is required")

    with transaction.atomic():
        locked = PersistentAgent.objects.select_for_update().get(pk=agent.pk)
        if locked.planning_state != PersistentAgent.PlanningState.PLANNING:
            raise ValueError("Agent is not in planning mode")

        locked.planning_state = PersistentAgent.PlanningState.COMPLETED
        locked.planning_plan = normalized_plan
        locked.charter = normalized_plan
        locked.planning_completed_at = timezone.now()
        locked.save(
            update_fields=[
                "planning_state",
                "planning_plan",
                "planning_completed_at",
                "charter",
                "updated_at",
            ]
        )

    agent.refresh_from_db()
    _schedule_charter_metadata(agent)
    return agent


def skip_agent_planning(agent: PersistentAgent) -> tuple[PersistentAgent, int]:
    """Skip planning mode without changing the current charter."""
    cancelled_count = 0
    with transaction.atomic():
        locked = PersistentAgent.objects.select_for_update().get(pk=agent.pk)
        if locked.planning_state == PersistentAgent.PlanningState.PLANNING:
            locked.planning_state = PersistentAgent.PlanningState.SKIPPED
            locked.save(update_fields=["planning_state", "updated_at"])
            cancelled_count = PersistentAgentHumanInputRequest.objects.filter(
                agent=locked,
                status=PersistentAgentHumanInputRequest.Status.PENDING,
            ).update(
                status=PersistentAgentHumanInputRequest.Status.CANCELLED,
                updated_at=timezone.now(),
            )

    agent.refresh_from_db()
    return agent, cancelled_count
