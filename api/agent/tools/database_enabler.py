"""
Tool for enabling the agent's SQLite database support.

Provides an `enable_database` function that ensures the builtin sqlite_batch tool
is enabled for the requesting agent.
"""

import logging
from typing import Any, Dict

from django.conf import settings

from ...models import PersistentAgent
from .tool_manager import enable_tools, is_sqlite_enabled_for_agent, SQLITE_TOOL_NAME

logger = logging.getLogger(__name__)


def execute_enable_database(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Enable the sqlite_batch tool for the agent."""
    logger.info("Agent %s requested enable_database", agent.id)

    # Check eligibility before enabling
    if not is_sqlite_enabled_for_agent(agent):
        message = "Database tool is not available for this deployment."
        if getattr(settings, "GOBII_PROPRIETARY_MODE", False):
            message = (
                "Database tool is not available on your current plan. "
                "Upgrade to a paid plan with max intelligence to access this feature."
            )
        return {
            "status": "error",
            "message": message,
        }

    try:
        result = enable_tools(agent, [SQLITE_TOOL_NAME])
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("enable_database failed for agent %s", agent.id)
        return {
            "status": "error",
            "message": f"Failed to enable sqlite_batch: {exc}",
        }

    enabled = set(result.get("enabled") or [])
    already_enabled = set(result.get("already_enabled") or [])

    if SQLITE_TOOL_NAME in enabled:
        message = "sqlite_batch has been enabled."
    elif SQLITE_TOOL_NAME in already_enabled:
        message = "sqlite_batch was already enabled."
    else:
        fallback = result.get("message") or "sqlite_batch could not be enabled."
        return {
            "status": "error",
            "message": fallback,
            "details": {
                key: value
                for key, value in result.items()
                if key not in {"status", "message"}
            },
        }

    response: Dict[str, Any] = {
        "status": "ok",
        "message": message,
        "tool_manager": {
            "enabled": list(result.get("enabled") or []),
            "already_enabled": list(result.get("already_enabled") or []),
            "evicted": list(result.get("evicted") or []),
            "invalid": list(result.get("invalid") or []),
        },
    }
    return response


def get_enable_database_tool() -> Dict[str, Any]:
    """Return the enable_database tool definition."""
    return {
        "type": "function",
        "function": {
            "name": "enable_database",
            "description": (
                "Enable the sqlite_batch tool so you can create, query, and maintain your SQLite memory. "
                "Call this before using sqlite_batch if it is not already enabled."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    }
