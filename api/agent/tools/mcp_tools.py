"""
MCP tool management functions for persistent agents.

Provides search and enable functionality for dynamic MCP tool discovery with LRU eviction.
"""

import logging
from typing import Dict, Any

from opentelemetry import trace

from ...models import PersistentAgent
from .mcp_manager import (
    search_mcp_tools,
    enable_mcp_tool,
    get_mcp_manager,
)

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")


def get_search_tools_tool() -> Dict[str, Any]:
    """Return the search_tools tool definition for the LLM."""
    return {
        "type": "function",
        "function": {
            "name": "search_tools",
            "description": "Search for available MCP tools that could help with a specific task or query. Returns relevant tool names with descriptions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Description of what you want to accomplish or what kind of tools you're looking for",
                    }
                },
                "required": ["query"],
            },
        },
    }


def get_enable_tool_tool() -> Dict[str, Any]:
    """Return the enable_tool tool definition for the LLM."""
    return {
        "type": "function",
        "function": {
            "name": "enable_tool",
            "description": "Enable a specific MCP tool for use. Maximum of 20 MCP tools can be enabled at once. If the limit is reached, the least recently used tool will be automatically disabled. The tool must exist and be discovered through search_tools first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "description": "The full name of the tool to enable (e.g., 'mcp_brightdata_search_engine')",
                    }
                },
                "required": ["tool_name"],
            },
        },
    }


@tracer.start_as_current_span("AGENT TOOL Search Tools")
def execute_search_tools(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute the search_tools function to find relevant MCP tools.
    """
    span = trace.get_current_span()
    span.set_attribute("persistent_agent.id", str(agent.id))
    
    query = params.get("query")
    if not query:
        return {"status": "error", "message": "Missing required parameter: query"}
    
    span.set_attribute("search.query", query)
    logger.info(f"Agent {agent.id} searching for tools: {query}")
    
    result = search_mcp_tools(agent, query)
    
    # Format the result for the agent
    if result["status"] == "success":
        tools = result.get("tools", [])
        if tools:
            formatted_tools = "\n".join([
                f"- {tool['name']}: {tool.get('relevance', 'Relevant to your query')}"
                for tool in tools
            ])
            return {
                "status": "success",
                "message": f"Found {len(tools)} relevant tool(s):\n{formatted_tools}"
            }
        else:
            return {
                "status": "success",
                "message": "No relevant tools found for your query."
            }
    else:
        return result


@tracer.start_as_current_span("AGENT TOOL Enable Tool")
def execute_enable_tool(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute the enable_tool function to enable an MCP tool for the agent.
    Implements LRU eviction if the 20 tool limit is exceeded.
    """
    span = trace.get_current_span()
    span.set_attribute("persistent_agent.id", str(agent.id))
    
    tool_name = params.get("tool_name")
    if not tool_name:
        return {"status": "error", "message": "Missing required parameter: tool_name"}
    
    span.set_attribute("tool.name", tool_name)
    logger.info(f"Agent {agent.id} enabling tool: {tool_name}")
    
    result = enable_mcp_tool(agent, tool_name)
    
    # Format message to include any disabled tool info
    if result["status"] == "success":
        message = f"Enabled tool '{result['enabled']}'"
        if result.get("disabled"):
            message += f". Disabled '{result['disabled']}' (least recently used) to stay within 20 tool limit"
        result["message"] = message
    
    return result


@tracer.start_as_current_span("AGENT TOOL Execute MCP Tool")
def execute_mcp_tool(agent: PersistentAgent, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute any enabled MCP tool.
    
    This is the dispatcher for all MCP tool executions.
    """
    span = trace.get_current_span()
    span.set_attribute("persistent_agent.id", str(agent.id))
    span.set_attribute("mcp.tool_name", tool_name)
    
    logger.info(f"Agent {agent.id} executing MCP tool: {tool_name}")
    
    manager = get_mcp_manager()
    if not manager._initialized:
        manager.initialize()
    
    return manager.execute_mcp_tool(agent, tool_name, params)