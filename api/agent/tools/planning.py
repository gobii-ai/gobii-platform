from typing import Any, Dict

from api.models import PersistentAgent
from api.services.agent_planning import complete_agent_planning


def execute_end_planning(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle an in-flight legacy completion without changing durable config."""
    full_plan = params.get("full_plan")
    if not isinstance(full_plan, str) or not full_plan.strip():
        return {"status": "error", "message": "Missing or invalid required parameter: full_plan"}

    updated_agent = complete_agent_planning(agent, full_plan)

    from console.agent_chat.signals import emit_agent_planning_state_update

    emit_agent_planning_state_update(updated_agent)

    return {
        "status": "ok",
        "message": "Legacy planning record completed; durable configuration was unchanged.",
        "planning_state": updated_agent.planning_state,
    }
