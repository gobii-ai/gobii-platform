"""Shared static tool definitions for persistent agents."""

from typing import Dict, List, Optional, Set

from django.db.models import Q

from api.models import AgentPeerLink, PersistentAgent
from api.services.sandbox_compute import sandbox_compute_enabled_for_agent
from api.services.tool_blacklist import get_agent_tool_blacklist
from .custom_tool_names import CREATE_CUSTOM_TOOL_NAME

PLANNING_MODE_DISABLED_TOOL_NAMES = frozenset({
    CREATE_CUSTOM_TOOL_NAME,
    "file_str_replace",
    "request_contact_permission",
    "spawn_agent",
    "spawn_web_task",
    "update_plan",
})


def planning_mode_disallows_tool(agent: Optional[PersistentAgent], tool_name: str) -> bool:
    return (
        bool(agent)
        and agent.planning_state == PersistentAgent.PlanningState.PLANNING
        and tool_name in PLANNING_MODE_DISABLED_TOOL_NAMES
    )


def _get_tool_name(tool: dict) -> Optional[str]:
    function_block = tool.get("function")
    if not isinstance(function_block, dict):
        return None
    tool_name = function_block.get("name")
    return tool_name if isinstance(tool_name, str) and tool_name else None


def _filter_planning_mode_tools(agent: PersistentAgent, tools: List[dict]) -> List[dict]:
    if agent.planning_state != PersistentAgent.PlanningState.PLANNING:
        return tools
    return [
        tool for tool in tools
        if (_get_tool_name(tool) not in PLANNING_MODE_DISABLED_TOOL_NAMES)
    ]


def _filter_tier_blacklisted_tools(agent: PersistentAgent, tools: List[dict]) -> List[dict]:
    blacklisted_tools = get_agent_tool_blacklist(agent)
    if not blacklisted_tools:
        return tools
    return [
        tool for tool in tools
        if (_get_tool_name(tool) not in blacklisted_tools)
    ]


def _get_sleep_tool() -> Dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": "sleep_until_next_trigger",
            "description": "Pause the agent until the next external trigger (no further action this cycle). You will wake on new user input or background task completion events.",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def get_static_tool_definitions(agent: Optional[PersistentAgent]) -> List[dict]:
    """Return static (always-present) tool definitions for an agent."""
    from .custom_tools import get_create_custom_tool_tool
    from .email_sender import get_send_email_tool
    from .file_str_replace import get_file_str_replace_tool
    from .planning import get_end_planning_tool
    from .request_human_input import get_request_human_input_tool
    from .request_contact_permission import get_request_contact_permission_tool
    from .search_tools import get_search_tools_tool
    from .secure_credentials_request import get_secure_credentials_request_tool
    from .sms_sender import get_send_sms_tool
    from .spawn_web_task import get_spawn_web_task_tool
    from .web_chat_sender import get_send_chat_tool
    from .webhook_sender import get_send_webhook_tool
    from .peer_dm import get_send_agent_message_tool
    from .plan import get_update_plan_tool

    static_tools: List[dict] = [
        _get_sleep_tool(),
        get_update_plan_tool(),
        get_send_email_tool(),
    ]
    if not agent or not agent.sms_disabled:
        static_tools.append(get_send_sms_tool())
    static_tools.extend([
        get_send_chat_tool(),
        get_spawn_web_task_tool(agent),
        get_search_tools_tool(),
        get_request_human_input_tool(),
        get_request_contact_permission_tool(),
        get_secure_credentials_request_tool(),
    ])

    if not agent:
        return static_tools

    if agent.planning_state == PersistentAgent.PlanningState.PLANNING:
        static_tools.append(get_end_planning_tool())

    static_tools.append(get_file_str_replace_tool())

    if sandbox_compute_enabled_for_agent(agent):
        static_tools.append(get_create_custom_tool_tool())

    if agent.webhooks.exists():
        static_tools.append(get_send_webhook_tool())

    has_peer_links = AgentPeerLink.objects.filter(
        is_enabled=True,
    ).filter(
        Q(agent_a=agent) | Q(agent_b=agent)
    ).exists()
    if has_peer_links:
        static_tools.append(get_send_agent_message_tool())

    return _filter_tier_blacklisted_tools(agent, _filter_planning_mode_tools(agent, static_tools))


def get_static_tool_names(agent: Optional[PersistentAgent]) -> Set[str]:
    """Return function names for static tools currently available to an agent."""
    names: Set[str] = set()
    for tool in get_static_tool_definitions(agent):
        tool_name = _get_tool_name(tool)
        if tool_name:
            names.add(tool_name)
    return names
