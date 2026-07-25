from django.db import transaction
from django.utils import timezone

from api.models import PersistentAgent, PersistentAgentHumanInputRequest


def complete_agent_planning(agent: PersistentAgent, full_plan: str) -> PersistentAgent:
    """Safely finish a legacy planning session without changing durable config."""
    normalized_plan = (full_plan or "").strip()
    if not normalized_plan:
        raise ValueError("full_plan is required")

    with transaction.atomic():
        locked = PersistentAgent.objects.select_for_update().get(pk=agent.pk)
        if locked.planning_state != PersistentAgent.PlanningState.PLANNING:
            agent.refresh_from_db()
            return agent

        locked.planning_state = PersistentAgent.PlanningState.COMPLETED
        locked.planning_plan = normalized_plan
        locked.planning_completed_at = timezone.now()
        locked.save(
            update_fields=[
                "planning_state",
                "planning_plan",
                "planning_completed_at",
                "updated_at",
            ]
        )

    agent.refresh_from_db()
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
