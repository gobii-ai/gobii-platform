from typing import Any, Dict

from api.models import PersistentAgent
from api.services.agent_planning import complete_agent_planning


def get_end_planning_tool() -> Dict[str, Any]:
    """Return the planning-mode completion tool definition."""
    return {
        "type": "function",
        "function": {
            "name": "end_planning",
            "description": (
                "Complete planning mode once the user's need, scope, desired outcome, "
                "constraints, assumptions, and success criteria are clear in plain language. "
                "Call this before doing substantive task work; planning mode should not execute "
                "the actual task until this tool has been used."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "full_plan": {
                        "type": "string",
                        "description": "The full decision-complete plan to store as the agent charter before work begins.",
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

    try:
        updated_agent = complete_agent_planning(agent, full_plan)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    from console.agent_chat.signals import emit_agent_planning_state_update

    emit_agent_planning_state_update(updated_agent)

    return {
        "status": "ok",
        "message": "Planning completed.",
        "planning_state": PersistentAgent.PlanningState.COMPLETED,
    }
