"""
MCP tool management functions for persistent agents.

Provides search and enable functionality for dynamic MCP tool discovery with LRU eviction.
"""

import logging
from typing import Dict, Any

from opentelemetry import trace

from ...models import PersistentAgent
from .mcp_manager import (
    search_tools,  # renamed from search_mcp_tools
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
            "description": (
                "Search for available MCP tools relevant to a query. "
                "This call will automatically enable ALL relevant tools in one step (no separate enable calls). "
                "Returns a short summary and which tools were enabled."
            ),
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
    
    result = search_tools(agent, query)
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
