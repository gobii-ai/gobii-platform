import logging
from typing import Any, Optional

from django.db import DatabaseError

from api.agent.tools.runtime_execution_context import get_tool_execution_context
from api.models import PersistentAgent, PersistentAgentEnabledTool, PersistentAgentToolCall
from api.services.pipedream_apps import (
    PIPEDREAM_COMPONENT_OPTION_TOOLS,
    normalize_app_slug,
    pipedream_app_slug_for_tool_call,
    pipedream_app_slug_for_tool_name,
)
from api.services.pipedream_feature_flags import pipedream_google_sheets_guard_enabled
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

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


def _entry_targets_pipedream_google_sheets(entry: Any, params: Any) -> bool:
    return (
        entry.provider == "mcp"
        and str(entry.tool_server).strip().casefold() == PIPEDREAM_PROVIDER
        and _provider_app_slug(entry, params) == GOOGLE_SHEETS_INTEGRATION
    )


def _resolve_tool_entry(agent: PersistentAgent, tool_name: str) -> Any:
    # Imported lazily because tool_manager owns the execution boundary that
    # imports this guard.
    from api.agent.tools.tool_manager import resolve_tool_entry

    return resolve_tool_entry(agent, tool_name)


def _blocked_error(handoff_status: str) -> dict[str, Any]:
    from api.agent.system_skills.service import (
        GOOGLE_SHEETS_HANDOFF_EXPLICITLY_DISABLED,
        GOOGLE_SHEETS_HANDOFF_READY,
    )

    if handoff_status == GOOGLE_SHEETS_HANDOFF_READY:
        message = (
            "Google Sheets through Pipedream is disabled. The native Google Sheets skill is enabled; continue with "
            "it now. If Google Drive is disconnected, tell the requester to open /app/integrations, connect Google "
            "Drive, and select the spreadsheets to use. Do not retry Pipedream."
        )
        next_action = "continue_with_native_google_sheets"
    elif handoff_status == GOOGLE_SHEETS_HANDOFF_EXPLICITLY_DISABLED:
        message = (
            "Google Sheets through Pipedream is disabled, and the native Google Sheets skill was explicitly disabled "
            "for this agent. Do not retry Pipedream; tell the requester the native Google Sheets skill must be "
            "re-enabled before this work can continue."
        )
        next_action = "ask_user_to_enable_native_google_sheets"
    else:
        message = (
            "Google Sheets through Pipedream is disabled, and the native Google Sheets skill could not be prepared "
            "automatically. Do not retry Pipedream; use search_tools to enable Google Sheets, or tell the requester "
            "that native integration setup needs attention."
        )
        next_action = "enable_native_google_sheets"

    return {
        "status": "error",
        "error_code": DEPRECATED_PROVIDER_BLOCKED,
        "message": message,
        "provider": PIPEDREAM_PROVIDER,
        "integration": GOOGLE_SHEETS_INTEGRATION,
        "replacement": GOOGLE_SHEETS_REPLACEMENT,
        "handoff_status": handoff_status,
        "next_action": next_action,
        "setup_url": "/app/integrations",
        "retryable": False,
    }


def is_deprecated_provider_blocked_result(result: Any) -> bool:
    return (
        isinstance(result, dict)
        and result.get("status") == "error"
        and result.get("error_code") == DEPRECATED_PROVIDER_BLOCKED
    )


def filter_deprecated_provider_blocked_tool(
    agent: PersistentAgent,
    tool_definitions: Optional[list[dict]],
    blocked_tool_name: str,
) -> Optional[list[dict]]:
    """Keep deprecated Sheets actions out of the same-turn roster refresh."""
    if tool_definitions is None:
        return None

    tool_names = {
        definition["function"]["name"]
        for definition in tool_definitions
        if (
            isinstance(definition, dict)
            and isinstance(definition.get("function"), dict)
            and isinstance(definition["function"].get("name"), str)
        )
    }
    pipedream_tool_names = set(
        PersistentAgentEnabledTool.objects.filter(
            agent=agent,
            tool_full_name__in=tool_names,
            tool_server=PIPEDREAM_PROVIDER,
        ).values_list("tool_full_name", flat=True)
    )
    candidates = pipedream_tool_names | {
        tool_name
        for tool_name in tool_names
        if pipedream_app_slug_for_tool_name(tool_name) == GOOGLE_SHEETS_INTEGRATION
    }
    blocked_tool_names = {blocked_tool_name}
    for tool_name in candidates - blocked_tool_names:
        entry = _resolve_tool_entry(agent, tool_name)
        if entry is not None and _entry_targets_pipedream_google_sheets(entry, {}):
            blocked_tool_names.add(tool_name)

    return [
        definition
        for definition in tool_definitions
        if not (
            isinstance(definition, dict)
            and isinstance(definition.get("function"), dict)
            and definition["function"].get("name") in blocked_tool_names
        )
    ]


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


def _track_blocked_execution(
    agent: PersistentAgent,
    *,
    tool_name: str,
    app_slug: str,
    invocation_scope: str,
    handoff_status: str,
) -> None:
    properties = Analytics.with_org_properties(
        {
            "agent_id": str(agent.id),
            "tool_name": tool_name,
            "provider": PIPEDREAM_PROVIDER,
            "app_slug": app_slug,
            "integration": GOOGLE_SHEETS_INTEGRATION,
            "replacement": GOOGLE_SHEETS_REPLACEMENT,
            "invocation_scope": invocation_scope,
            "handoff_status": handoff_status,
            "error_code": DEPRECATED_PROVIDER_BLOCKED,
        },
        organization_id=str(agent.organization_id) if agent.organization_id else None,
    )
    try:
        Analytics.track_event(
            user_id=agent.user_id,
            event=AnalyticsEvent.PIPEDREAM_GOOGLE_SHEETS_EXECUTION_BLOCKED,
            source=AnalyticsSource.AGENT,
            properties=properties,
        )
    except Exception:
        # Analytics is diagnostic only and must never weaken the execution
        # guard or replace its actionable native handoff result.
        logger.warning(
            "Unable to emit deprecated-provider block analytics for agent %s.",
            agent.id,
            exc_info=True,
        )


def pipedream_google_sheets_blocked_error(
    agent: PersistentAgent,
    entry: Any,
    params: Any,
    *,
    blocked_call: Optional[bool] = None,
) -> Optional[dict[str, Any]]:
    should_block = (
        is_pipedream_google_sheets_blocked_call(entry, params)
        if blocked_call is None
        else blocked_call and is_pipedream_google_sheets_call(entry, params)
    )
    if not should_block:
        return None

    from api.agent.system_skills.service import (
        GOOGLE_SHEETS_HANDOFF_UNAVAILABLE,
        prepare_google_sheets_native_handoff,
    )

    try:
        handoff_status = prepare_google_sheets_native_handoff(agent)
    except DatabaseError:
        handoff_status = GOOGLE_SHEETS_HANDOFF_UNAVAILABLE
        logger.warning(
            "Unable to prepare native Google Sheets handoff for agent %s.",
            agent.id,
            exc_info=True,
        )

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
            "handoff_status": handoff_status,
        },
    )
    _track_blocked_execution(
        agent,
        tool_name=entry.full_name,
        app_slug=app_slug,
        invocation_scope=invocation_scope,
        handoff_status=handoff_status,
    )
    return _blocked_error(handoff_status)


def is_pipedream_google_sheets_blocked_call(entry: Any, params: Any) -> bool:
    return (
        is_pipedream_google_sheets_call(entry, params)
        and pipedream_google_sheets_guard_enabled()
    )


def is_pipedream_google_sheets_call(entry: Any, params: Any) -> bool:
    return _entry_targets_pipedream_google_sheets(entry, params)
