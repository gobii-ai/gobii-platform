"""
Charter updater tool for persistent agents.

This module provides functionality for agents to update their own charter/instructions.
"""

import logging
from typing import Dict, Any

from ...models import PersistentAgent
from ..short_description import (
    maybe_schedule_mini_description,
    maybe_schedule_short_description,
)
from ..tags import maybe_schedule_agent_tags
from api.evals.execution import get_current_eval_routing_profile

logger = logging.getLogger(__name__)



def _should_continue_work(params: Dict[str, Any]) -> bool:
    """Return True if the agent indicates more work right after this charter update."""
    raw = params.get("will_continue_work")
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        return normalized in {"1", "true", "yes"}
    return bool(raw)

def get_update_charter_tool() -> Dict[str, Any]:
    """Return the update_charter tool definition for the LLM."""
    return {
        "type": "function",
        "function": {
            "name": "update_charter",
            "description": "Updates the agent's charter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "new_charter": {"type": "string", "description": "New charter text."},
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "Set true if you still have work or kanban cards to complete. Omit or set false to STOP when: all kanban cards are done AND report sent.",
                    },
                },
                "required": ["new_charter"],
            },
        },
    }


def execute_update_charter(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the update_charter tool for a persistent agent."""
    new_charter = params.get("new_charter")
    will_continue = _should_continue_work(params)
    if not new_charter or not isinstance(new_charter, str):
        return {"status": "error", "message": "Missing or invalid required parameter: new_charter"}

    # Log charter update attempt
    old_charter_preview = agent.charter[:100] + "..." if len(agent.charter) > 100 else agent.charter
    new_charter_preview = new_charter[:100] + "..." if len(new_charter) > 100 else new_charter
    logger.info(
        "Agent %s updating charter from '%s' to '%s'",
        agent.id, old_charter_preview, new_charter_preview
    )

    try:
        agent.charter = new_charter.strip()
        agent.save(update_fields=["charter"])

        # Extract routing profile ID for metadata tasks
        routing_profile = get_current_eval_routing_profile()
        routing_profile_id = str(routing_profile.id) if routing_profile else None

        maybe_schedule_short_description(agent, routing_profile_id=routing_profile_id)
        maybe_schedule_mini_description(agent, routing_profile_id=routing_profile_id)
        maybe_schedule_agent_tags(agent, routing_profile_id=routing_profile_id)
        return {
            "status": "ok",
            "message": "Charter updated successfully.",
            "auto_sleep_ok": not will_continue,
        }
    except Exception as e:
        logger.exception("Failed to update charter for agent %s", agent.id)
        return {"status": "error", "message": f"Failed to update charter: {e}"} 
