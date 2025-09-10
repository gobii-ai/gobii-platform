"""
MCP (Model Context Protocol) tool management for persistent agents.

This module provides dynamic tool discovery, search, and enable/disable functionality
for MCP servers, allowing agents to intelligently select tools from a large ecosystem.
"""

import json
import logging
import asyncio
import os
import fnmatch
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

import litellm
from fastmcp import Client
from mcp.types import Tool as MCPTool
from opentelemetry import trace
from django.conf import settings

from ...models import PersistentAgent
from ..core.llm_config import get_llm_config_with_failover

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")


@dataclass
class MCPServer:
    """Configuration for an MCP server."""
    name: str
    display_name: str
    description: str
    command: Optional[str] = None
    args: Optional[List[str]] = None
    url: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    enabled: bool = True


@dataclass 
class MCPToolInfo:
    """Information about an MCP tool for search and display."""
    full_name: str  # e.g., "mcp_brightdata_search_engine"
    server_name: str  # e.g., "brightdata"
    tool_name: str  # e.g., "search_engine"
    description: str
    parameters: Dict[str, Any]
    
    def to_search_dict(self) -> Dict[str, str]:
        """Convert to a dictionary for LLM search context."""
        return {
            "name": self.full_name,
            "server": self.server_name,
            "tool": self.tool_name,
            "description": self.description,
            "parameters": json.dumps(self.parameters) if self.parameters else "{}",
        }


class MCPToolManager:
    """Manages MCP tool connections and provides search/enable/disable functionality."""
    
    # Define available MCP servers
    AVAILABLE_SERVERS = [
        MCPServer(
            name="brightdata",
            display_name="Bright Data",
            description="Web scraping and data extraction tools with CAPTCHA bypass",
            command="npx",
            args=["@brightdata/mcp@2.4.3"],
            env={
                "API_TOKEN": os.getenv("BRIGHT_DATA_TOKEN", ""),
                "NPM_CONFIG_CACHE": os.getenv("NPM_CONFIG_CACHE", "/tmp/.npm"),
                "PRO_MODE": "true"
            },
            enabled=True
        ),
        # Future servers can be added here
    ]
    
    # Default MCP tools that should be enabled for all agents
    DEFAULT_ENABLED_TOOLS = [
        "mcp_brightdata_scrape_as_markdown",
        # Add more default tools here as needed
    ]
    
    # Blacklisted tool patterns (glob-style patterns)
    # Tools matching these patterns will be excluded from discovery and execution
    TOOL_BLACKLIST = [
        "mcp_brightdata_scraping_browser_*",  # Blacklist all scraping browser tools
        # Add more blacklist patterns here as needed
    ]
    
    def __init__(self):
        self._clients: Dict[str, Client] = {}
        self._tools_cache: Dict[str, List[MCPToolInfo]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._initialized = False
        
    def _ensure_event_loop(self) -> asyncio.AbstractEventLoop:
        """Ensure we have an event loop for async operations."""
        if self._loop is None or self._loop.is_closed():
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        return self._loop
    
    def _is_tool_blacklisted(self, tool_name: str) -> bool:
        """Check if a tool name matches any blacklist pattern."""
        for pattern in self.TOOL_BLACKLIST:
            if fnmatch.fnmatch(tool_name, pattern):
                logger.debug(f"Tool '{tool_name}' matches blacklist pattern '{pattern}'")
                return True
        return False
    
    def initialize(self) -> bool:
        """Initialize all configured MCP servers and cache their tools."""
        if self._initialized:
            return True
            
        success_count = 0
        for server in self.AVAILABLE_SERVERS:
            if not server.enabled:
                logger.info(f"MCP server '{server.name}' is disabled, skipping")
                continue
                
            # Check for required environment variables
            if server.env and server.env.get("API_TOKEN") == "":
                logger.warning(f"MCP server '{server.name}' missing API_TOKEN, skipping")
                continue
                
            try:
                self._register_server(server)
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to register MCP server '{server.name}': {e}")
        
        self._initialized = success_count > 0
        logger.info(f"Initialized {success_count}/{len(self.AVAILABLE_SERVERS)} MCP servers")
        return self._initialized
    
    def _register_server(self, server: MCPServer):
        """Register an MCP server and cache its tools."""
        # Use transport directly for better control and to avoid MCPConfig prefixing issues
        if server.url:
            # For HTTP/SSE servers
            from fastmcp.client.transports import StreamableHttpTransport
            transport = StreamableHttpTransport(url=server.url)
        elif server.command:
            # For stdio servers like npx
            from fastmcp.client.transports import StdioTransport
            transport = StdioTransport(
                command=server.command,
                args=server.args or [],
                env=server.env or {}
            )
        else:
            raise ValueError(f"Server '{server.name}' must have either 'url' or 'command'")
        
        # Create client with transport directly
        client = Client(transport)
        self._clients[server.name] = client
        
        # Fetch and cache tools
        loop = self._ensure_event_loop()
        tools = loop.run_until_complete(self._fetch_server_tools(client, server))
        self._tools_cache[server.name] = tools
        
        logger.info(f"Registered MCP server '{server.name}' with {len(tools)} tools")
    
    async def _fetch_server_tools(self, client: Client, server: MCPServer) -> List[MCPToolInfo]:
        """Fetch tools from an MCP server, filtering out blacklisted tools."""
        tools = []
        blacklisted_count = 0
        async with client:
            mcp_tools = await client.list_tools()
            
            for tool in mcp_tools:
                full_name = f"mcp_{server.name}_{tool.name}"
                
                # Check if tool is blacklisted
                if self._is_tool_blacklisted(full_name):
                    blacklisted_count += 1
                    logger.info(f"Skipping blacklisted tool: {full_name}")
                    continue
                
                tool_info = MCPToolInfo(
                    full_name=full_name,
                    server_name=server.name,
                    tool_name=tool.name,
                    description=tool.description or f"{tool.name} from {server.display_name}",
                    parameters=tool.inputSchema or {"type": "object", "properties": {}}
                )
                tools.append(tool_info)
        
        if blacklisted_count > 0:
            logger.info(f"Filtered out {blacklisted_count} blacklisted tools from server '{server.name}'")
        
        return tools
    
    def get_all_available_tools(self) -> List[MCPToolInfo]:
        """Get all available MCP tools from all servers."""
        if not self._initialized:
            self.initialize()
            
        all_tools = []
        for server_tools in self._tools_cache.values():
            all_tools.extend(server_tools)
        return all_tools
    
    def get_enabled_tools_definitions(self, agent: PersistentAgent) -> List[Dict[str, Any]]:
        """Get OpenAI-format tool definitions for enabled MCP tools."""
        if not self._initialized:
            self.initialize()
            
        enabled_names = agent.enabled_mcp_tools or []
        definitions = []
        
        for tool_info in self.get_all_available_tools():
            if tool_info.full_name in enabled_names:
                definition = {
                    "type": "function",
                    "function": {
                        "name": tool_info.full_name,
                        "description": tool_info.description,
                        "parameters": tool_info.parameters,
                    }
                }
                definitions.append(definition)
        
        return definitions
    
    def execute_mcp_tool(self, agent: PersistentAgent, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute an MCP tool if it's enabled for the agent."""
        import time
        
        # Check if tool is blacklisted
        if self._is_tool_blacklisted(tool_name):
            return {
                "status": "error",
                "message": f"Tool '{tool_name}' is blacklisted and cannot be executed"
            }
        
        # Check if tool is enabled
        if tool_name not in (agent.enabled_mcp_tools or []):
            return {
                "status": "error",
                "message": f"Tool '{tool_name}' is not enabled for this agent"
            }
        
        # Update usage timestamp
        tool_usage = dict(agent.mcp_tool_usage or {})
        tool_usage[tool_name] = time.time()
        agent.mcp_tool_usage = tool_usage
        agent.save(update_fields=['mcp_tool_usage'])
        
        # Parse tool name
        if not tool_name.startswith("mcp_"):
            return {
                "status": "error", 
                "message": f"Invalid MCP tool name format: {tool_name}"
            }
        
        parts = tool_name.split("_", 2)
        if len(parts) != 3:
            return {
                "status": "error",
                "message": f"Invalid MCP tool name format: {tool_name}"
            }
        
        _, server_name, actual_tool_name = parts
        
        if server_name not in self._clients:
            return {
                "status": "error",
                "message": f"MCP server '{server_name}' not available"
            }
        
        client = self._clients[server_name]
        
        try:
            loop = self._ensure_event_loop()
            result = loop.run_until_complete(self._execute_async(client, actual_tool_name, params))
            
            # Convert result to consistent format
            if hasattr(result, 'is_error') and result.is_error:
                return {
                    "status": "error",
                    "message": str(result.content[0].text if result.content else "Unknown error")
                }
            
            # Extract content
            content = None
            if result.data is not None:
                content = result.data
            elif result.content:
                for block in result.content:
                    if hasattr(block, 'text'):
                        content = block.text
                        break
            
            return {
                "status": "success",
                "result": content or "Tool executed successfully"
            }
            
        except Exception as e:
            logger.error(f"Failed to execute MCP tool {tool_name}: {e}")
            return {
                "status": "error",
                "message": str(e)
            }
    
    async def _execute_async(self, client: Client, tool_name: str, params: Dict[str, Any]):
        """Execute a tool asynchronously."""
        async with client:
            return await client.call_tool(tool_name, params)
    
    def cleanup(self):
        """Clean up resources."""
        self._clients.clear()
        self._tools_cache.clear()
        if self._loop and not self._loop.is_closed():
            self._loop.close()
        self._initialized = False


# Global manager instance
_mcp_manager = MCPToolManager()


@tracer.start_as_current_span("AGENT TOOL Search MCP Tools")
def search_mcp_tools(agent: PersistentAgent, query: str) -> Dict[str, Any]:
    """
    Search for relevant MCP tools using LLM.
    
    Returns a list of relevant tool names with descriptions and relevance notes.
    """
    span = trace.get_current_span()
    span.set_attribute("persistent_agent.id", str(agent.id))
    span.set_attribute("search.query", query)
    
    if not _mcp_manager._initialized:
        _mcp_manager.initialize()
    
    # Get all available tools
    all_tools = _mcp_manager.get_all_available_tools()
    
    if not all_tools:
        return {
            "status": "success",
            "tools": [],
            "message": "No MCP tools available"
        }
    
    # Prepare tool information for LLM
    tools_context = [tool.to_search_dict() for tool in all_tools]
    
    # Prepare the search prompt
    system_prompt = """You are a tool discovery assistant. Given a query about what the user wants to accomplish, 
search through the available MCP tools and return ONLY the most relevant tools that could help.

Return your response as a JSON array of relevant tools. Each tool should have:
- name: the full tool name (e.g., "mcp_brightdata_search_engine")
- relevance: a brief note about why this tool is relevant to the query

If no tools are relevant, return an empty array.

Example response:
[
  {"name": "mcp_brightdata_search_engine", "relevance": "Can search the web for current information"},
  {"name": "mcp_brightdata_scrape_as_markdown", "relevance": "Can extract content from specific web pages"}
]"""

    user_prompt = f"""Query: {query}

Available tools:
{json.dumps(tools_context, indent=2)}

Return the relevant tools as a JSON array:"""

    try:
        # Get LLM configuration with failover
        failover_configs = get_llm_config_with_failover()
        
        # Try each provider in order
        last_exc = None
        for i, (provider, model, params) in enumerate(failover_configs):
            try:
                logger.info(f"Searching MCP tools with provider {i+1}/{len(failover_configs)}: {provider}")
                
                response = litellm.completion(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    safety_identifier=getattr(agent.user, "id", None) if agent.user else None,
                    **params
                )
                
                # Parse the response
                content = response.choices[0].message.content
                result = json.loads(content)
                
                # Ensure it's a list
                if isinstance(result, dict) and "tools" in result:
                    result = result["tools"]
                elif not isinstance(result, list):
                    result = []
                
                # Validate tool names exist
                valid_tool_names = {tool.full_name for tool in all_tools}
                filtered_results = [
                    tool for tool in result 
                    if isinstance(tool, dict) and tool.get("name") in valid_tool_names
                ]
                
                return {
                    "status": "success",
                    "tools": filtered_results
                }
                
            except Exception as e:
                last_exc = e
                logger.warning(f"Provider {provider} failed for tool search: {e}")
                continue
        
        # All providers failed
        logger.error(f"All providers failed for tool search: {last_exc}")
        return {
            "status": "error",
            "message": "Failed to search tools",
            "tools": []
        }
        
    except Exception as e:
        logger.error(f"Failed to search MCP tools: {e}")
        return {
            "status": "error",
            "message": str(e),
            "tools": []
        }


def enable_mcp_tool(agent: PersistentAgent, tool_name: str) -> Dict[str, Any]:
    """Enable an MCP tool for the agent with LRU eviction if over limit."""
    import time
    
    MAX_MCP_TOOLS = 20  # Maximum number of MCP tools per agent
    
    if not _mcp_manager._initialized:
        _mcp_manager.initialize()
    
    # Check if tool is blacklisted
    if _mcp_manager._is_tool_blacklisted(tool_name):
        return {
            "status": "error",
            "message": f"Tool '{tool_name}' is blacklisted and cannot be enabled"
        }
    
    # Check if tool exists
    all_tools = _mcp_manager.get_all_available_tools()
    tool_exists = any(tool.full_name == tool_name for tool in all_tools)
    
    if not tool_exists:
        return {
            "status": "error",
            "message": f"Tool '{tool_name}' does not exist"
        }
    
    # Get current enabled tools and usage tracking
    enabled_tools = list(agent.enabled_mcp_tools or [])
    tool_usage = dict(agent.mcp_tool_usage or {})
    
    # Check if already enabled
    if tool_name in enabled_tools:
        # Update usage timestamp for already enabled tool
        tool_usage[tool_name] = time.time()
        agent.mcp_tool_usage = tool_usage
        agent.save(update_fields=['mcp_tool_usage'])
        
        return {
            "status": "success",
            "message": f"Tool '{tool_name}' is already enabled",
            "enabled": tool_name,
            "disabled": None
        }
    
    disabled_tool = None
    
    # Check if we need to evict a tool (LRU)
    if len(enabled_tools) >= MAX_MCP_TOOLS:
        # Find the least recently used tool
        # Filter tool_usage to only include currently enabled tools
        valid_usage = {t: tool_usage.get(t, 0) for t in enabled_tools}
        
        # Find the tool with the oldest timestamp (or 0 if never used)
        lru_tool = min(valid_usage, key=valid_usage.get)
        
        # Remove the LRU tool
        enabled_tools.remove(lru_tool)
        if lru_tool in tool_usage:
            del tool_usage[lru_tool]
        disabled_tool = lru_tool
        
        logger.info(f"Evicted LRU tool '{lru_tool}' to make room for '{tool_name}'")
    
    # Add the new tool
    enabled_tools.append(tool_name)
    tool_usage[tool_name] = time.time()
    
    # Save the updated state
    agent.enabled_mcp_tools = enabled_tools
    agent.mcp_tool_usage = tool_usage
    agent.save(update_fields=['enabled_mcp_tools', 'mcp_tool_usage'])
    
    logger.info(f"Enabled MCP tool '{tool_name}' for agent {agent.id}")
    
    result = {
        "status": "success",
        "message": f"Successfully enabled tool '{tool_name}'",
        "enabled": tool_name,
        "disabled": disabled_tool
    }
    
    if disabled_tool:
        result["message"] += f" (disabled '{disabled_tool}' due to 20 tool limit)"
    
    return result




def ensure_default_tools_enabled(agent: PersistentAgent) -> None:
    """Ensure default MCP tools are enabled for the agent."""
    if not _mcp_manager._initialized:
        _mcp_manager.initialize()
    
    # Get current enabled tools
    enabled_tools = set(agent.enabled_mcp_tools or [])
    
    # Check if any default tools are missing
    default_tools = set(MCPToolManager.DEFAULT_ENABLED_TOOLS)
    missing_tools = default_tools - enabled_tools
    
    if missing_tools:
        # Verify the tools actually exist and are not blacklisted
        all_tools = _mcp_manager.get_all_available_tools()
        available_tool_names = {tool.full_name for tool in all_tools}
        
        # Enable missing default tools that exist and are not blacklisted
        for tool_name in missing_tools:
            if _mcp_manager._is_tool_blacklisted(tool_name):
                logger.warning(f"Default MCP tool '{tool_name}' is blacklisted, skipping")
                continue
            if tool_name in available_tool_names:
                enable_mcp_tool(agent, tool_name)
                logger.info(f"Enabled default MCP tool '{tool_name}' for agent {agent.id}")
            else:
                logger.warning(f"Default MCP tool '{tool_name}' not found in available tools")


def get_mcp_manager() -> MCPToolManager:
    """Get the global MCP tool manager instance."""
    return _mcp_manager


def cleanup_mcp_tools():
    """Clean up MCP tool resources."""
    _mcp_manager.cleanup()
