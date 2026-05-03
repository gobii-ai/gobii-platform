import logging
from types import SimpleNamespace
from typing import Any, Dict

from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.urls import NoReverseMatch, reverse

from api.agent.tools.sqlite_state import get_sqlite_db_path
from api.models import PersistentAgent
from api.services.agent_dashboards import (
    DashboardValidationError,
    create_or_update_dashboard,
)
from constants.feature_flags import AGENT_DASHBOARDS
from util.urls import append_context_query
from util.waffle_flags import is_waffle_flag_active

logger = logging.getLogger(__name__)

DASHBOARD_TOOL_NAME = "create_or_update_dashboard"


def is_dashboard_tool_available_for_agent(agent: PersistentAgent | None) -> bool:
    """Evaluate the rollout flag against the agent owner when no request exists."""
    if agent is None:
        return False

    request = SimpleNamespace(
        user=getattr(agent, "user", None),
        GET={},
        headers={},
        COOKIES={},
    )
    return is_waffle_flag_active(AGENT_DASHBOARDS, request, default=False)


def _dashboard_url(agent: PersistentAgent) -> str:
    try:
        path = reverse("agent_dashboards", kwargs={"pk": agent.id})
    except NoReverseMatch:
        logger.warning("Failed to reverse dashboard URL for agent %s", agent.id, exc_info=True)
        path = f"/console/agents/{agent.id}/dashboards/"
    org_id = str(agent.organization_id) if agent.organization_id else None
    return append_context_query(path, org_id)


def execute_create_or_update_dashboard(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Create or replace the agent's dashboard definition."""
    if not agent:
        return {"status": "error", "message": "Dashboard creation requires an agent."}

    try:
        dashboard = create_or_update_dashboard(
            agent,
            title=params.get("title"),
            description=params.get("description", ""),
            widgets=params.get("widgets"),
            db_path=get_sqlite_db_path(),
            created_by_agent=True,
        )
    except DashboardValidationError as exc:
        return {
            "status": "error",
            "message": str(exc),
            "hint": (
                "Create durable SQLite tables first, then call this tool with 1-8 widgets. "
                "Each widget SQL must be a single SELECT/WITH statement over durable, non-internal tables."
            ),
        }
    except ValidationError as exc:
        return {"status": "error", "message": "; ".join(exc.messages)}
    except DatabaseError as exc:
        logger.exception("Failed to save dashboard for agent %s", agent.id)
        return {"status": "error", "message": f"Failed to save dashboard: {exc}"}

    dashboard_url = _dashboard_url(agent)
    return {
        "status": "ok",
        "message": f"Dashboard '{dashboard.title}' is ready.",
        "dashboard_id": str(dashboard.id),
        "dashboard_url": dashboard_url,
        "widgets": dashboard.widgets.count(),
    }


def get_create_or_update_dashboard_tool() -> Dict[str, Any]:
    """Return the dashboard authoring tool definition."""
    return {
        "type": "function",
        "function": {
            "name": DASHBOARD_TOOL_NAME,
            "description": (
                "Create or replace this agent's simple business dashboard from durable SQLite tables. "
                "Use this only when the user asks for a dashboard or asks to update an existing dashboard. "
                "Do not use it for speculative summaries. Widgets are read-only and render in the console."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short dashboard title.",
                    },
                    "description": {
                        "type": "string",
                        "description": "One sentence explaining what this dashboard tracks.",
                    },
                    "widgets": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "description": "Ordered dashboard widgets backed by durable SQLite SELECT queries.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["metric", "table", "bar"],
                                    "description": "Widget rendering type.",
                                },
                                "title": {
                                    "type": "string",
                                    "description": "Widget title.",
                                },
                                "sql": {
                                    "type": "string",
                                    "description": (
                                        "A single SELECT or WITH query against durable agent-created tables. "
                                        "Do not query __internal tables."
                                    ),
                                },
                                "display_config": {
                                    "type": "object",
                                    "description": (
                                        "Optional display hints. For bar widgets, use x and y to name result columns."
                                    ),
                                    "additionalProperties": True,
                                },
                            },
                            "required": ["type", "title", "sql"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["title", "widgets"],
                "additionalProperties": False,
            },
        },
    }
