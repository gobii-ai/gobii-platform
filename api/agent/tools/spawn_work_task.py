"""
Work task spawning tool for persistent agents.
"""

import logging
from typing import Dict, Any, Optional

from django.utils import timezone

from config.redis_client import get_redis_client
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from api.services.work_task_settings import get_work_task_settings
from api.agent.core.budget import get_current_context as get_budget_context, AgentBudgetManager
from api.agent.work_task_shared import WORK_TASK_ALLOWED_MCP_TOOLS
from api.models import PersistentAgent, WorkTask

logger = logging.getLogger(__name__)

_WORK_TASK_REDIS_TTL_SECONDS = 6 * 60 * 60


def _work_task_counter_key(agent_id: str) -> str:
    return f"pa:work_tasks:{agent_id}:active"


def get_spawn_work_task_tool(agent: Optional[PersistentAgent] = None) -> Dict[str, Any]:
    settings = get_work_task_settings()
    limit_bits = []
    if settings.max_active_tasks:
        limit_bits.append(f"Maximum {settings.max_active_tasks} active tasks at once.")
    if settings.max_tasks_per_day:
        limit_bits.append(f"Maximum {settings.max_tasks_per_day} work tasks per day.")
    if settings.max_steps:
        limit_bits.append(f"Maximum {settings.max_steps} steps per work task.")
    if not limit_bits:
        limit_bits.append("Task limits enforced per deployment settings.")
    limit_sentence = " ".join(limit_bits)

    return {
        "type": "function",
        "function": {
            "name": "spawn_work_task",
            "description": (
                "Spawn a new async work task for low-cost research using a stateless tool loop. "
                "Use this when you need to search or scrape via Bright Data tools. "
                "Provide a clear, self-contained query. "
                "The task runs outside the agent history and returns a summarized result with citations. "
                "If you have pending work tasks, you can sleep; event processing will re-trigger once all work tasks complete. "
                f"Allowed tools: {', '.join(WORK_TASK_ALLOWED_MCP_TOOLS)}. {limit_sentence}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Research request to run."},
                    "type": {
                        "type": "string",
                        "description": "Work task type.",
                        "enum": ["research"],
                    },
                },
                "required": ["query", "type"],
            },
        },
    }


def execute_spawn_work_task(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...tasks.work_agent_tasks import process_work_task

    query = (params or {}).get("query")
    if not query:
        return {"status": "error", "message": "Missing required parameter: query"}
    task_type = (params or {}).get("type")
    if not task_type:
        return {"status": "error", "message": "Missing required parameter: type"}
    if task_type != "research":
        return {"status": "error", "message": "Invalid work task type. Use 'research'."}

    settings = get_work_task_settings()

    max_steps = settings.max_steps
    allowed_tools = list(WORK_TASK_ALLOWED_MCP_TOOLS)

    # Active tasks limit (per agent)
    active_count = WorkTask.objects.filter(
        agent=agent,
        status__in=[WorkTask.StatusChoices.PENDING, WorkTask.StatusChoices.IN_PROGRESS],
    ).count()
    if settings.max_active_tasks and active_count >= settings.max_active_tasks:
        return {
            "status": "error",
            "message": (
                f"Maximum active work task limit reached ({settings.max_active_tasks}). "
                f"Currently have {active_count} active tasks."
            ),
        }

    # Daily limit (per agent)
    if settings.max_tasks_per_day:
        start_of_day = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        daily_count = WorkTask.objects.filter(agent=agent, created_at__gte=start_of_day).count()
        if daily_count >= settings.max_tasks_per_day:
            props = Analytics.with_org_properties(
                {
                    "agent_id": str(agent.id),
                    "daily_limit": settings.max_tasks_per_day,
                    "tasks_started_today": daily_count,
                },
                organization_id=str(agent.organization_id) if getattr(agent, "organization_id", None) else None,
            )
            try:
                Analytics.track_event(
                    agent.user_id,
                    AnalyticsEvent.PERSISTENT_AGENT_WORK_TASK_DAILY_LIMIT_REACHED,
                    AnalyticsSource.AGENT,
                    props,
                )
            except Exception:
                logger.debug("Failed to emit analytics for work task daily limit", exc_info=True)
            return {
                "status": "error",
                "message": (
                    f"Daily work task limit reached ({settings.max_tasks_per_day}). "
                    f"You have already started {daily_count} task(s) today."
                ),
            }

    budget_ctx = get_budget_context()
    next_depth = 1
    budget_id = None
    branch_id = None
    if budget_ctx is not None:
        budget_id = budget_ctx.budget_id
        branch_id = budget_ctx.branch_id
        current_depth = int(getattr(budget_ctx, "depth", 0))
        _, max_depth = AgentBudgetManager.get_limits(agent_id=str(agent.id))
        if current_depth >= max_depth:
            return {
                "status": "error",
                "message": "Recursion limit reached; cannot spawn additional work tasks.",
            }
        next_depth = current_depth + 1

    try:
        task = WorkTask.objects.create(
            agent=agent,
            user=agent.user,
            organization=getattr(agent, "organization", None),
            query=query,
            eval_run_id=getattr(budget_ctx, "eval_run_id", None),
        )

        try:
            if branch_id and budget_id:
                AgentBudgetManager.bump_branch_depth(
                    agent_id=str(agent.id),
                    branch_id=str(branch_id),
                    delta=+1,
                )
        except Exception:
            logger.warning(
                "Failed to increment outstanding work-task children for agent %s branch %s",
                agent.id,
                branch_id,
                exc_info=True,
            )

        try:
            redis_client = get_redis_client()
            if redis_client:
                key = _work_task_counter_key(str(agent.id))
                redis_client.incr(key)
                redis_client.expire(key, _WORK_TASK_REDIS_TTL_SECONDS)
        except Exception:
            logger.debug("Failed to increment work task counter", exc_info=True)

        props = Analytics.with_org_properties(
            {
                "agent_id": str(agent.id),
                "task_id": str(task.id),
                "model": None,
                "provider": None,
                "tool_count": len(allowed_tools),
            },
            organization=getattr(agent, "organization", None),
        )
        try:
            Analytics.track_event(
                agent.user_id,
                AnalyticsEvent.PERSISTENT_AGENT_WORK_TASK_CREATED,
                AnalyticsSource.AGENT,
                props,
            )
        except Exception:
            logger.debug("Failed to emit analytics for work task created", exc_info=True)

        process_work_task.delay(
            str(task.id),
            allowed_tools=allowed_tools,
            max_steps=max_steps,
            budget_id=budget_id,
            branch_id=branch_id,
            depth=next_depth,
        )

        return {
            "status": "pending",
            "task_id": str(task.id),
            "auto_sleep_ok": True,
        }
    except Exception as exc:
        logger.exception("Failed to create or enqueue WorkTask for agent %s", agent.id)
        return {"status": "error", "message": f"Failed to create or execute task: {exc}"}
