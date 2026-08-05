import logging
from typing import Any, Dict, Optional

from api.models import PersistentAgent
from api.services.deprecated_provider_guard import (
    filter_deprecated_provider_blocked_tool,
    is_deprecated_provider_blocked_result,
    is_pipedream_google_sheets_blocked_call,
)
from api.services.tool_blacklist import is_tool_blacklisted_for_agent, tool_blacklist_error

from .apply_patch import execute_apply_patch
from .charter_updater import execute_update_charter
from .custom_tools import execute_create_custom_tool
from .custom_tool_names import CREATE_CUSTOM_TOOL_NAME
from .email_sender import execute_send_email
from .peer_dm import execute_send_agent_message
from .mcp_sender import execute_send_mcp_message
from .planning import execute_end_planning
from .request_contact_permission import execute_request_contact_permission
from .request_human_input import execute_request_human_input
from .schedule_updater import execute_update_schedule
from .search_tools import execute_search_tools
from .secure_credentials_request import execute_secure_credentials_request
from .sms_sender import execute_send_sms
from .spawn_web_task import execute_spawn_web_task
from . import tool_manager as tool_manager_service
from .tool_manager import ToolCatalogEntry, execute_enabled_tool
from .web_chat_sender import execute_send_chat_message


logger = logging.getLogger(__name__)


def _refresh_agent_tools(agent: PersistentAgent) -> Optional[list[dict]]:
    from ..core.prompt_context import get_agent_tools

    return get_agent_tools(agent)


def _refresh_agent_tools_after_deprecated_provider_handoff(
    agent: PersistentAgent,
    result: Any,
    blocked_tool_name: str,
    resolved_entry: Optional[ToolCatalogEntry],
    exec_params: Dict[str, Any],
) -> Optional[list[dict]]:
    if not (
        is_deprecated_provider_blocked_result(result)
        and resolved_entry is not None
        and is_pipedream_google_sheets_blocked_call(resolved_entry, exec_params)
    ):
        return None
    try:
        return filter_deprecated_provider_blocked_tool(
            agent,
            _refresh_agent_tools(agent),
            blocked_tool_name,
        )
    except Exception:
        # Preserve the actionable block if this best-effort roster refresh
        # fails; the next prompt build can retry it from persisted state.
        logger.exception(
            "Agent %s: nested native Sheets handoff tool refresh failed; preserving the blocked result.",
            agent.id,
        )
        return None


def execute_runtime_tool_call(
    agent: PersistentAgent,
    *,
    tool_name: str,
    exec_params: Dict[str, Any],
    isolated_mcp: bool = False,
) -> tuple[Any, Optional[list[dict]]]:
    updated_tools: Optional[list[dict]] = None

    if is_tool_blacklisted_for_agent(agent, tool_name):
        return tool_blacklist_error(tool_name), updated_tools

    if isolated_mcp:
        resolved_entry = tool_manager_service.resolve_tool_entry(agent, tool_name)
        result = execute_enabled_tool(
            agent,
            tool_name,
            exec_params,
            isolated_mcp=True,
            resolved_entry=resolved_entry,
        )
        updated_tools = _refresh_agent_tools_after_deprecated_provider_handoff(
            agent,
            result,
            tool_name,
            resolved_entry,
            exec_params,
        )
        return result, updated_tools
    if tool_name == "spawn_web_task":
        return execute_spawn_web_task(agent, exec_params), updated_tools
    if tool_name == "send_email":
        return execute_send_email(agent, exec_params), updated_tools
    if tool_name == "send_sms":
        return execute_send_sms(agent, exec_params), updated_tools
    if tool_name == "send_chat_message":
        return execute_send_chat_message(agent, exec_params), updated_tools
    if tool_name == "send_mcp_message":
        return execute_send_mcp_message(agent, exec_params), updated_tools
    if tool_name == "send_agent_message":
        return execute_send_agent_message(agent, exec_params), updated_tools
    if tool_name == "update_schedule":
        return execute_update_schedule(agent, exec_params), updated_tools
    if tool_name == "update_charter":
        return execute_update_charter(agent, exec_params), updated_tools
    if tool_name == "secure_credentials_request":
        return execute_secure_credentials_request(agent, exec_params), updated_tools
    if tool_name == "request_contact_permission":
        return execute_request_contact_permission(agent, exec_params), updated_tools
    if tool_name == "request_human_input":
        return execute_request_human_input(agent, exec_params), updated_tools
    if tool_name == "search_tools":
        result = execute_search_tools(agent, exec_params)
        updated_tools = _refresh_agent_tools(agent)
        return result, updated_tools
    if tool_name == CREATE_CUSTOM_TOOL_NAME:
        result = execute_create_custom_tool(agent, exec_params)
        updated_tools = _refresh_agent_tools(agent)
        return result, updated_tools
    if tool_name == "apply_patch":
        return execute_apply_patch(agent, exec_params), updated_tools
    if tool_name == "end_planning":
        result = execute_end_planning(agent, exec_params)
        updated_tools = _refresh_agent_tools(agent)
        return result, updated_tools

    resolved_entry = tool_manager_service.resolve_tool_entry(agent, tool_name)
    result = execute_enabled_tool(
        agent,
        tool_name,
        exec_params,
        resolved_entry=resolved_entry,
    )
    updated_tools = _refresh_agent_tools_after_deprecated_provider_handoff(
        agent,
        result,
        tool_name,
        resolved_entry,
        exec_params,
    )
    return result, updated_tools
