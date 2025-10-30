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

logger = logging.getLogger(__name__)


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
                },
                "required": ["new_charter"],
            },
        },
    }


def execute_update_charter(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the update_charter tool for a persistent agent."""
    new_charter = params.get("new_charter")
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
        maybe_schedule_short_description(agent)
        maybe_schedule_mini_description(agent)
        return {
            "status": "ok",
            "message": "Charter updated successfully.",
            "auto_sleep_ok": True,
        }
    except Exception as e:
        logger.exception("Failed to update charter for agent %s", agent.id)
        return {"status": "error", "message": f"Failed to update charter: {e}"} 
