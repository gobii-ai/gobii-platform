"""Shared static tool definitions for persistent agents."""

from typing import Dict, List, Optional, Set

from django.db.models import Q

from agents.services import AgentService
from api.models import AgentPeerLink, PersistentAgent

def _get_sleep_tool() -> Dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": "sleep_until_next_trigger",
            "description": "Pause the agent until the next external trigger (no further action this cycle).",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def get_static_tool_definitions(agent: Optional[PersistentAgent]) -> List[dict]:
    """Return static (always-present) tool definitions for an agent."""
    from .email_sender import get_send_email_tool
    from .request_contact_permission import get_request_contact_permission_tool
    from .search_tools import get_search_tools_tool
    from .secure_credentials_request import get_secure_credentials_request_tool
    from .sms_sender import get_send_sms_tool
    from .spawn_agent import get_spawn_agent_tool
    from .spawn_web_task import get_spawn_web_task_tool
    from .web_chat_sender import get_send_chat_tool
    from .webhook_sender import get_send_webhook_tool
    from .peer_dm import get_send_agent_message_tool

    static_tools: List[dict] = [
        _get_sleep_tool(),
        get_send_email_tool(),
        get_send_sms_tool(),
        get_send_chat_tool(),
        get_spawn_web_task_tool(agent),
        get_search_tools_tool(),
        get_request_contact_permission_tool(),
        get_secure_credentials_request_tool(),
    ]

    if not agent:
        return static_tools

    owner = agent.organization if agent.organization_id else agent.user
    spawn_capacity = max(int(AgentService.get_agents_available(owner)), 0)
    if spawn_capacity > 0:
        static_tools.append(get_spawn_agent_tool(agent, available_capacity=spawn_capacity))

    if agent.webhooks.exists():
        static_tools.append(get_send_webhook_tool())

    has_peer_links = AgentPeerLink.objects.filter(
        is_enabled=True,
    ).filter(
        Q(agent_a=agent) | Q(agent_b=agent)
    ).exists()
    if has_peer_links:
        static_tools.append(get_send_agent_message_tool())

    return static_tools


def get_static_tool_names(agent: Optional[PersistentAgent]) -> Set[str]:
    """Return function names for static tools currently available to an agent."""
    names: Set[str] = set()
    for tool in get_static_tool_definitions(agent):
        if not isinstance(tool, dict):
            continue
        function_block = tool.get("function")
        if not isinstance(function_block, dict):
            continue
        tool_name = function_block.get("name")
        if isinstance(tool_name, str) and tool_name:
            names.add(tool_name)
    return names
