import logging
from typing import Any, Dict

from api.agent.core.variables import VariableResolutionError, materialize_variable_value
from api.models import PersistentAgent, PersistentAgentVariable

logger = logging.getLogger(__name__)


def get_var_lookup_tool() -> dict:
    """Definition for the var_lookup tool."""
    return {
        "type": "function",
        "function": {
            "name": "var_lookup",
            "description": "Fetch a stored variable by name for this agent. Use this instead of asking the user to resend data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Variable name (without the leading $).",
                    },
                },
                "required": ["name"],
            },
        },
    }


def execute_var_lookup(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Retrieve a variable for this agent."""
    name = (params.get("name") or params.get("variable_name") or "").lstrip("$").strip()

    if not name:
        return {"status": "error", "message": "Variable name is required"}

    variable = PersistentAgentVariable.objects.filter(agent=agent, name=name).first()
    if not variable:
        return {"status": "not_found", "message": f"Variable ${name} not found"}

    try:
        value = materialize_variable_value(variable)
    except VariableResolutionError as exc:
        return {"status": "error", "message": str(exc)}
    except Exception:
        logger.exception("Failed to materialize variable %s for agent %s", name, agent.id)
        return {"status": "error", "message": "Unexpected error reading variable"}

    return {
        "status": "ok",
        "value": value,
    }
