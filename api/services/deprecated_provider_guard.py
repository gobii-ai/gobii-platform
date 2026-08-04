import logging
from typing import Any, Optional

from django.conf import settings
from django.db import DatabaseError

from api.agent.tools.runtime_execution_context import get_tool_execution_context
from api.models import PersistentAgent, PersistentAgentToolCall
from api.services.pipedream_apps import (
    PIPEDREAM_COMPONENT_OPTION_TOOLS,
    normalize_app_slug,
    pipedream_app_slug_for_tool_call,
)

logger = logging.getLogger(__name__)

DEPRECATED_PROVIDER_BLOCKED = "deprecated_provider_blocked"
PIPEDREAM_PROVIDER = "pipedream"
GOOGLE_SHEETS_INTEGRATION = "google_sheets"
GOOGLE_SHEETS_REPLACEMENT = "google_sheets_native"


def _provider_app_slug(entry: Any, params: Any) -> str:
    tool_name = entry.tool_name or entry.full_name
    if tool_name in PIPEDREAM_COMPONENT_OPTION_TOOLS:
        app_slug = pipedream_app_slug_for_tool_call(tool_name, params)
        if app_slug:
            return app_slug

    tool_info = entry.mcp_info
    if tool_info is not None:
        app_slug = normalize_app_slug(tool_info.app_slug)
        if app_slug:
            return app_slug
    return pipedream_app_slug_for_tool_call(tool_name, params)


def _blocked_error() -> dict[str, Any]:
    return {
        "status": "error",
        "error_code": DEPRECATED_PROVIDER_BLOCKED,
        "message": (
            "Google Sheets through Pipedream is disabled for this agent. "
            "Use the native Google Sheets integration."
        ),
        "provider": PIPEDREAM_PROVIDER,
        "integration": GOOGLE_SHEETS_INTEGRATION,
        "replacement": GOOGLE_SHEETS_REPLACEMENT,
        "retryable": False,
    }


def is_deprecated_provider_blocked_result(result: Any) -> bool:
    return (
        isinstance(result, dict)
        and result.get("status") == "error"
        and result.get("error_code") == DEPRECATED_PROVIDER_BLOCKED
    )


def _invocation_log_context() -> tuple[str, str, str]:
    execution_context = get_tool_execution_context()
    if execution_context is None or not execution_context.step_id:
        return "top_level", "", ""

    try:
        tool_call = (
            PersistentAgentToolCall.objects.select_related("parent_tool_call")
            .only("parent_tool_call_id", "parent_tool_call__tool_name")
            .filter(step_id=execution_context.step_id)
            .first()
        )
    except DatabaseError:
        logger.warning(
            "Unable to resolve deprecated-provider invocation context for step %s.",
            execution_context.step_id,
        )
        return "unknown", "", ""

    if tool_call is None or tool_call.parent_tool_call_id is None:
        return "top_level", "", ""
    return (
        "nested",
        str(tool_call.parent_tool_call_id),
        tool_call.parent_tool_call.tool_name,
    )


def pipedream_google_sheets_blocked_error(
    agent: PersistentAgent,
    entry: Any,
    params: Any,
) -> Optional[dict[str, Any]]:
    if not is_pipedream_google_sheets_blocked_call(entry, params):
        return None

    app_slug = _provider_app_slug(entry, params)
    invocation_scope, parent_tool_id, parent_tool_name = _invocation_log_context()
    logger.warning(
        "Blocked deprecated provider tool execution.",
        extra={
            "agent_id": str(agent.id),
            "tool_name": entry.full_name,
            "resolved_provider": PIPEDREAM_PROVIDER,
            "app_slug": app_slug,
            "invocation_scope": invocation_scope,
            "parent_tool_id": parent_tool_id,
            "parent_tool_name": parent_tool_name,
            "error_code": DEPRECATED_PROVIDER_BLOCKED,
        },
    )
    return _blocked_error()


def is_pipedream_google_sheets_blocked_call(entry: Any, params: Any) -> bool:
    if not settings.PIPEDREAM_GOOGLE_SHEETS_GUARD_ENABLED:
        return False
    if entry.provider != "mcp" or str(entry.tool_server).strip().casefold() != PIPEDREAM_PROVIDER:
        return False
    return _provider_app_slug(entry, params) == GOOGLE_SHEETS_INTEGRATION
