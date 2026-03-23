from typing import Any, Dict, Optional

from api.models import PersistentAgent

from .charter_updater import execute_update_charter
from .custom_tools import execute_create_custom_tool
from .database_enabler import execute_enable_database
from .email_sender import execute_send_email
from .file_str_replace import execute_file_str_replace
from .peer_dm import execute_send_agent_message
from .request_contact_permission import execute_request_contact_permission
from .request_human_input import execute_request_human_input
from .schedule_updater import execute_update_schedule
from .search_tools import execute_search_tools
from .secure_credentials_request import execute_secure_credentials_request
from .sms_sender import execute_send_sms
from .spawn_agent import execute_spawn_agent
from .spawn_web_task import execute_spawn_web_task
from .tool_manager import execute_enabled_tool
from .web_chat_sender import execute_send_chat_message
from .webhook_sender import execute_send_webhook_event


def _refresh_agent_tools(agent: PersistentAgent) -> Optional[list[dict]]:
    from ..core.prompt_context import get_agent_tools

    return get_agent_tools(agent)


def execute_runtime_tool_call(
    agent: PersistentAgent,
    *,
    tool_name: str,
    exec_params: Dict[str, Any],
    isolated_mcp: bool = False,
) -> tuple[Any, Optional[list[dict]]]:
    updated_tools: Optional[list[dict]] = None

    if isolated_mcp:
        return execute_enabled_tool(agent, tool_name, exec_params, isolated_mcp=True), updated_tools
    if tool_name == "spawn_web_task":
        return execute_spawn_web_task(agent, exec_params), updated_tools
    if tool_name == "send_email":
        return execute_send_email(agent, exec_params), updated_tools
    if tool_name == "send_sms":
        return execute_send_sms(agent, exec_params), updated_tools
    if tool_name == "send_chat_message":
        return execute_send_chat_message(agent, exec_params), updated_tools
    if tool_name == "send_agent_message":
        return execute_send_agent_message(agent, exec_params), updated_tools
    if tool_name == "send_webhook_event":
        return execute_send_webhook_event(agent, exec_params), updated_tools
    if tool_name == "update_schedule":
        return execute_update_schedule(agent, exec_params), updated_tools
    if tool_name == "update_charter":
        return execute_update_charter(agent, exec_params), updated_tools
    if tool_name == "secure_credentials_request":
        return execute_secure_credentials_request(agent, exec_params), updated_tools
    if tool_name == "enable_database":
        result = execute_enable_database(agent, exec_params)
        updated_tools = _refresh_agent_tools(agent)
        return result, updated_tools
    if tool_name == "request_contact_permission":
        return execute_request_contact_permission(agent, exec_params), updated_tools
    if tool_name == "request_human_input":
        return execute_request_human_input(agent, exec_params), updated_tools
    if tool_name == "spawn_agent":
        return execute_spawn_agent(agent, exec_params), updated_tools
    if tool_name == "search_tools":
        result = execute_search_tools(agent, exec_params)
        updated_tools = _refresh_agent_tools(agent)
        return result, updated_tools
    if tool_name == "create_custom_tool":
        result = execute_create_custom_tool(agent, exec_params)
        updated_tools = _refresh_agent_tools(agent)
        return result, updated_tools
    if tool_name == "file_str_replace":
        return execute_file_str_replace(agent, exec_params), updated_tools

    return execute_enabled_tool(agent, tool_name, exec_params), updated_tools
