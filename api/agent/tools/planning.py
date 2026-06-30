from typing import Any, Dict

from api.models import PersistentAgent
from api.services.agent_planning import complete_agent_planning
from api.services.agent_credit_forecasts import persist_agent_credit_forecast, serialize_credit_forecast


def get_end_planning_tool() -> Dict[str, Any]:
    """Return the planning-mode completion tool definition."""
    return {
        "type": "function",
        "function": {
            "name": "end_planning",
            "description": (
                "Complete planning mode once the user's need, scope, desired outcome, "
                "constraints, assumptions, and success criteria are clear in plain language. "
                "For clear one-off research, factual answers, or execute-now requests, call this before "
                "search_tools, web/search tools, or result delivery."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "full_plan": {
                        "type": "string",
                        "description": "The full decision-complete plan to store as the agent charter before work begins.",
                    },
                    "schedule": {
                        "type": ["string", "null"],
                        "description": (
                            "Optional final cron-like schedule, @daily, or @every interval to apply before cost estimation. "
                            "Use null or omit for one-time/unscheduled work."
                        ),
                    },
                },
                "required": ["full_plan"],
            },
        },
    }


def execute_end_planning(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the end_planning tool for a persistent agent."""
    full_plan = params.get("full_plan")
    if not isinstance(full_plan, str) or not full_plan.strip():
        return {"status": "error", "message": "Missing or invalid required parameter: full_plan"}
    schedule_provided = "schedule" in params
    schedule = params.get("schedule")
    if schedule_provided and schedule is not None and not isinstance(schedule, str):
        return {"status": "error", "message": "Invalid parameter: schedule must be a string or null"}

    try:
        updated_agent = complete_agent_planning(
            agent,
            full_plan,
            schedule_provided=schedule_provided,
            schedule=schedule,
        )
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    forecast = persist_agent_credit_forecast(updated_agent)

    from console.agent_chat.signals import (
        emit_agent_credit_forecast_timeline_event,
        emit_agent_planning_state_update,
        emit_agent_usage_update,
    )

    emit_agent_credit_forecast_timeline_event(updated_agent)
    emit_agent_planning_state_update(updated_agent)
    emit_agent_usage_update(updated_agent)

    return {
        "status": "ok",
        "message": "Planning completed.",
        "planning_state": PersistentAgent.PlanningState.COMPLETED,
        "schedule": updated_agent.schedule,
        "credit_forecast": serialize_credit_forecast(forecast),
    }
