import logging
from typing import Optional

from redis.exceptions import RedisError

from api.agent.core.budget import AgentBudgetManager
from api.models import PersistentAgent
from api.services.owner_execution_pause import is_owner_execution_paused, resolve_agent_owner


logger = logging.getLogger(__name__)


def enqueue_agent_background_follow_up(
    agent: PersistentAgent,
    *,
    budget_id: str = "",
    branch_id: str = "",
    depth: Optional[int] = None,
    eval_run_id=None,
) -> bool:
    owner = resolve_agent_owner(agent)
    if owner is not None and is_owner_execution_paused(owner):
        return False

    from api.agent.tasks.process_events import process_agent_events_task

    agent_id = str(agent.id)
    budget_is_active = False
    if budget_id:
        try:
            budget_is_active = (
                AgentBudgetManager.get_cycle_status(agent_id=agent_id) == "active"
                and AgentBudgetManager.get_active_budget_id(agent_id=agent_id) == budget_id
            )
        except (RuntimeError, RedisError):
            logger.warning(
                "Unable to inspect background task budget; using a fresh cycle",
                extra={"agent_id": agent_id},
                exc_info=True,
            )

    kwargs = {"eval_run_id": str(eval_run_id) if eval_run_id else None}
    if budget_is_active:
        kwargs.update(
            budget_id=budget_id,
            branch_id=branch_id or None,
            depth=max((depth or 1) - 1, 0),
        )
    process_agent_events_task.delay(agent_id, **kwargs)
    return True
