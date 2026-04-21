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


def _schedule_charter_metadata(agent: PersistentAgent) -> None:
    routing_profile = get_current_eval_routing_profile()
    routing_profile_id = str(routing_profile.id) if routing_profile else None
    maybe_schedule_short_description(agent, routing_profile_id=routing_profile_id)
    maybe_schedule_mini_description(agent, routing_profile_id=routing_profile_id)
    maybe_schedule_agent_tags(agent, routing_profile_id=routing_profile_id)
    maybe_schedule_agent_avatar(agent, routing_profile_id=routing_profile_id)


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
