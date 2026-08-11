import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from django.db import DatabaseError

from api.agent.system_skills.defaults import (
    APOLLO_NATIVE_SYSTEM_SKILL_KEY,
    GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY,
)
from api.agent.tools.runtime_execution_context import get_tool_execution_context
from api.models import PersistentAgent, PersistentAgentEnabledTool, PersistentAgentToolCall
from api.services.pipedream_apps import (
    PIPEDREAM_COMPONENT_OPTION_TOOLS,
    normalize_app_slug,
    pipedream_app_slug_for_tool_call,
)
from constants.feature_flags import PIPEDREAM_APOLLO_GUARD, PIPEDREAM_GOOGLE_SHEETS_GUARD
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.waffle_flags import is_waffle_switch_active

logger = logging.getLogger(__name__)

DEPRECATED_PROVIDER_BLOCKED = "deprecated_provider_blocked"
PIPEDREAM_PROVIDER = "pipedream"
GOOGLE_SHEETS_INTEGRATION = "google_sheets"
APOLLO_INTEGRATION = "apollo"


@dataclass(frozen=True)
class DeprecatedPipedreamIntegration:
    key: str
    display_name: str
    pipedream_app_slugs: frozenset[str]
    native_skill_key: str
    switch_name: str
    setup_url: str
    ready_message: str
    analytics_event: AnalyticsEvent


DEPRECATED_PIPEDREAM_INTEGRATIONS = {
    GOOGLE_SHEETS_INTEGRATION: DeprecatedPipedreamIntegration(
        key=GOOGLE_SHEETS_INTEGRATION,
        display_name="Google Sheets",
        pipedream_app_slugs=frozenset({"google_sheets"}),
        native_skill_key=GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY,
        switch_name=PIPEDREAM_GOOGLE_SHEETS_GUARD,
        setup_url="/app/integrations",
        ready_message=(
            "Google Sheets through Pipedream is disabled. The native Google Sheets skill is enabled; continue "
            "with it now. If Google Drive is disconnected, tell the requester to open /app/integrations, "
            "connect Google Drive, and select the spreadsheets to use. Do not retry Pipedream."
        ),
        analytics_event=AnalyticsEvent.PIPEDREAM_GOOGLE_SHEETS_EXECUTION_BLOCKED,
    ),
    APOLLO_INTEGRATION: DeprecatedPipedreamIntegration(
        key=APOLLO_INTEGRATION,
        display_name="Apollo",
        pipedream_app_slugs=frozenset({"apollo_io", "apollo_io_oauth"}),
        native_skill_key=APOLLO_NATIVE_SYSTEM_SKILL_KEY,
        switch_name=PIPEDREAM_APOLLO_GUARD,
        setup_url="/app/integrations?provider=apollo",
        ready_message=(
            "Apollo through Pipedream is disabled. The native Apollo skill is enabled; continue with it now "
            "using http_request. If Apollo is disconnected, tell the requester to open "
            "/app/integrations?provider=apollo and connect Apollo. Do not retry Pipedream."
        ),
        analytics_event=AnalyticsEvent.PIPEDREAM_APOLLO_EXECUTION_BLOCKED,
    ),
}


def deprecated_pipedream_guard_enabled(integration: DeprecatedPipedreamIntegration) -> bool:
    return is_waffle_switch_active(integration.switch_name, default=False)


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


def _integration_for_app_slug(app_slug: str) -> Optional[DeprecatedPipedreamIntegration]:
    normalized_slug = normalize_app_slug(app_slug)
    for integration in DEPRECATED_PIPEDREAM_INTEGRATIONS.values():
        if normalized_slug in integration.pipedream_app_slugs:
            return integration
    return None


def _integration_for_tool_name(
    tool_name: str,
    params: Any,
) -> Optional[DeprecatedPipedreamIntegration]:
    return _integration_for_app_slug(pipedream_app_slug_for_tool_call(tool_name, params))


def _integration_for_entry(
    entry: Any,
    params: Any,
) -> Optional[DeprecatedPipedreamIntegration]:
    if (
        entry.provider != "mcp"
        or str(entry.tool_server).strip().casefold() != PIPEDREAM_PROVIDER
    ):
        return None
    return _integration_for_app_slug(_provider_app_slug(entry, params))


def match_deprecated_pipedream_integration(
    tool_name: str,
    params: Any,
    *,
    entry: Any = None,
) -> Optional[DeprecatedPipedreamIntegration]:
    integration = (
        _integration_for_entry(entry, params)
        if entry is not None
        else _integration_for_tool_name(tool_name, params)
    )
    return integration if integration is not None and deprecated_pipedream_guard_enabled(integration) else None


def _resolve_tool_entry(agent: PersistentAgent, tool_name: str) -> Any:
    # Imported lazily because tool_manager owns the execution boundary that
    # imports this guard.
    from api.agent.tools.tool_manager import resolve_tool_entry

    return resolve_tool_entry(agent, tool_name)


def _blocked_error(
    integration: DeprecatedPipedreamIntegration,
    handoff_status: str,
) -> dict[str, Any]:
    from api.agent.system_skills.service import (
        NATIVE_INTEGRATION_HANDOFF_EXPLICITLY_DISABLED,
        NATIVE_INTEGRATION_HANDOFF_READY,
    )

    if handoff_status == NATIVE_INTEGRATION_HANDOFF_READY:
        message = integration.ready_message
        next_action = f"continue_with_native_{integration.key}"
    elif handoff_status == NATIVE_INTEGRATION_HANDOFF_EXPLICITLY_DISABLED:
        message = (
            f"{integration.display_name} through Pipedream is disabled, and the native "
            f"{integration.display_name} skill was explicitly disabled for this agent. Do not retry Pipedream; "
            f"tell the requester the native {integration.display_name} skill must be re-enabled before this work "
            "can continue."
        )
        next_action = f"ask_user_to_enable_native_{integration.key}"
    else:
        message = (
            f"{integration.display_name} through Pipedream is disabled, and the native "
            f"{integration.display_name} skill could not be prepared automatically. Do not retry Pipedream; use "
            f"search_tools to enable {integration.display_name}, or tell the requester that native integration "
            "setup needs attention."
        )
        next_action = f"enable_native_{integration.key}"

    return {
        "status": "error",
        "error_code": DEPRECATED_PROVIDER_BLOCKED,
        "message": message,
        "provider": PIPEDREAM_PROVIDER,
        "integration": integration.key,
        "replacement": integration.native_skill_key,
        "handoff_status": handoff_status,
        "next_action": next_action,
        "setup_url": integration.setup_url,
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
    blocked_integration: DeprecatedPipedreamIntegration,
) -> Optional[list[dict]]:
    """Keep deprecated provider actions out of the same-turn roster refresh."""
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
        if _integration_for_tool_name(tool_name, {}) == blocked_integration
    }
    blocked_tool_names = {blocked_tool_name}
    for tool_name in candidates - blocked_tool_names:
        entry = _resolve_tool_entry(agent, tool_name)
        detected_integration = _integration_for_entry(entry, {}) if entry is not None else None
        if detected_integration == blocked_integration:
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


def refresh_tools_after_deprecated_provider_handoff(
    agent: PersistentAgent,
    result: Any,
    blocked_tool_name: str,
    blocked_integration: Optional[DeprecatedPipedreamIntegration],
    refresh_tools: Callable[[PersistentAgent], Optional[list[dict]]],
) -> Optional[list[dict]]:
    if not (is_deprecated_provider_blocked_result(result) and blocked_integration):
        return None
    try:
        return filter_deprecated_provider_blocked_tool(
            agent,
            refresh_tools(agent),
            blocked_tool_name,
            blocked_integration,
        )
    except Exception:
        # The actionable block remains valid if this best-effort roster refresh fails.
        logger.exception(
            "Agent %s: native integration handoff tool refresh failed; preserving the blocked result.",
            agent.id,
        )
        return None


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
    integration: DeprecatedPipedreamIntegration,
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
            "integration": integration.key,
            "replacement": integration.native_skill_key,
            "invocation_scope": invocation_scope,
            "handoff_status": handoff_status,
            "error_code": DEPRECATED_PROVIDER_BLOCKED,
        },
        organization_id=str(agent.organization_id) if agent.organization_id else None,
    )
    try:
        Analytics.track_event(
            user_id=agent.user_id,
            event=integration.analytics_event,
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


def deprecated_pipedream_blocked_error(
    agent: PersistentAgent,
    tool_name: str,
    params: Any,
    *,
    entry: Any = None,
    blocked_integration: Optional[DeprecatedPipedreamIntegration] = None,
) -> Optional[dict[str, Any]]:
    detected_integration = (
        _integration_for_entry(entry, params)
        if entry is not None
        else _integration_for_tool_name(tool_name, params)
    )
    integration = blocked_integration or detected_integration
    if integration is None or detected_integration != integration:
        return None
    # Callers that classify before billing pass the decision through so a
    # mid-call switch change cannot make execution and credit handling differ.
    if blocked_integration is None and not deprecated_pipedream_guard_enabled(integration):
        return None

    from api.agent.system_skills.service import (
        NATIVE_INTEGRATION_HANDOFF_UNAVAILABLE,
        prepare_native_integration_handoff,
    )

    try:
        handoff_status = prepare_native_integration_handoff(
            agent,
            integration.native_skill_key,
        )
    except DatabaseError:
        handoff_status = NATIVE_INTEGRATION_HANDOFF_UNAVAILABLE
        logger.warning(
            "Unable to prepare native %s handoff for agent %s.",
            integration.display_name,
            agent.id,
            exc_info=True,
        )

    app_slug = (
        _provider_app_slug(entry, params)
        if entry is not None
        else pipedream_app_slug_for_tool_call(tool_name, params)
    )
    invocation_scope, parent_tool_id, parent_tool_name = _invocation_log_context()
    logger.warning(
        "Blocked deprecated provider tool execution.",
        extra={
            "agent_id": str(agent.id),
            "tool_name": tool_name,
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
        integration=integration,
        tool_name=tool_name,
        app_slug=app_slug,
        invocation_scope=invocation_scope,
        handoff_status=handoff_status,
    )
    return _blocked_error(integration, handoff_status)
