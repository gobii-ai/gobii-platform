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
from datetime import datetime, timedelta

import requests

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
    headers: Optional[Dict[str, str]] = None
    prefetch_apps: Optional[List[str]] = None
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
        # Pipedream remote MCP server (HTTP/SSE)
        MCPServer(
            name="pipedream",
            display_name="Pipedream",
            description="Access 2,800+ API integrations with per-user OAuth via Pipedream Connect",
            url="https://remote.mcp.pipedream.net",
            # Use env to pass required config; we validate on init.
            env={
                "PIPEDREAM_CLIENT_ID": os.getenv("PIPEDREAM_CLIENT_ID", ""),
                "PIPEDREAM_CLIENT_SECRET": os.getenv("PIPEDREAM_CLIENT_SECRET", ""),
                "PIPEDREAM_PROJECT_ID": os.getenv("PIPEDREAM_PROJECT_ID", ""),
                "PIPEDREAM_ENVIRONMENT": os.getenv("PIPEDREAM_ENVIRONMENT", "development"),
                # Optional comma-separated app slugs to prefetch tool catalogs for
                "PIPEDREAM_PREFETCH_APPS": os.getenv("PIPEDREAM_PREFETCH_APPS", "google_sheets"),
                # Default apps to auto-select per agent session
                "PIPEDREAM_DEFAULT_APPS": os.getenv("PIPEDREAM_DEFAULT_APPS", "google_sheets"),
            },
            headers={
                # Default to full-config so begin_configuration and dynamic props work during discovery
                "x-pd-tool-mode": "full-config",
                "x-pd-external-user-id": "gobii-discovery",  # overridden per-agent on calls
                "x-pd-conversation-id": "discovery",        # overridden per-agent
                # Let app discovery drive catalogs by default; specific app slugs set during prefetch
                "x-pd-app-discovery": "true",
            },
            enabled=True,

        ),
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
        "select_apps"
        # Add more blacklist patterns here as needed
    ]
    
    def __init__(self):
        self._clients: Dict[str, Client] = {}
        self._tools_cache: Dict[str, List[MCPToolInfo]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._initialized = False
        # Cached Pipedream token and expiry
        self._pd_access_token: Optional[str] = None
        self._pd_token_expiry: Optional[datetime] = None
        # Per‑agent Pipedream clients (unique connection per agent id)
        self._pd_agent_clients: Dict[str, Client] = {}
        
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
            # For stdio-based servers that commonly use an API_TOKEN env
            # (e.g., Bright Data) preserve generic check to maintain tests.
            if server.command and server.env and server.env.get("API_TOKEN") == "":
                logger.warning(f"MCP server '{server.name}' missing API_TOKEN, skipping")
                continue
            if server.name == "pipedream":
                # Require client credentials and project info
                env = server.env or {}
                required = [
                    env.get("PIPEDREAM_CLIENT_ID"),
                    env.get("PIPEDREAM_CLIENT_SECRET"),
                    env.get("PIPEDREAM_PROJECT_ID"),
                    env.get("PIPEDREAM_ENVIRONMENT"),
                ]
                if not all(required):
                    logger.warning(
                        "MCP server 'pipedream' missing required env (PIPEDREAM_CLIENT_ID/SECRET/PROJECT_ID/ENVIRONMENT), skipping"
                    )
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
            headers: Dict[str, str] = dict(server.headers or {})

            # Special handling for Pipedream: acquire token and set required headers
            if server.name == "pipedream":
                env = server.env or {}
                token = self._get_pipedream_access_token(env)
                if not token:
                    raise RuntimeError("Pipedream access token acquisition failed")

                # Base headers for Pipedream remote MCP
                headers.update({
                    "Authorization": f"Bearer {token}",
                    "x-pd-project-id": env.get("PIPEDREAM_PROJECT_ID", ""),
                    "x-pd-environment": env.get("PIPEDREAM_ENVIRONMENT", "development"),
                    # External user id is set per-call (agent.id); keep a discovery default for list_tools
                    "x-pd-external-user-id": env.get("PIPEDREAM_DISCOVERY_USER", "gobii-discovery"),
                    # Enable broad discovery so the catalog at least includes the discovery tool
                    "x-pd-app-discovery": "true",
                    # Full config exposes discovery and sub-agent capabilities
                    "x-pd-tool-mode": "full-config",
                    # Provide a stable conversation id for discovery/listing
                    "x-pd-conversation-id": env.get("PIPEDREAM_DISCOVERY_CONVERSATION", "discovery"),
                })

            transport = StreamableHttpTransport(url=server.url, headers=headers)
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
            # Always fetch the base tool list first
            mcp_tools = await client.list_tools()
            tools.extend(self._convert_tools(server, mcp_tools))

            # For Pipedream, optionally prefetch select app catalogs to expose app-specific tools
            if server.name == "pipedream":
                env = server.env or {}
                prefetch = [s.strip() for s in (env.get("PIPEDREAM_PREFETCH_APPS", "").split(",")) if s.strip()]
                for app_slug in prefetch:
                    try:
                        # Mutate header for app slug and refetch tools
                        if hasattr(client, "transport") and getattr(client.transport, "headers", None) is not None:
                            client.transport.headers["x-pd-app-slug"] = app_slug
                        app_tools = await client.list_tools()
                        tools.extend(self._convert_tools(server, app_tools))
                    except Exception as e:
                        logger.warning(f"Pipedream prefetch for app '{app_slug}' failed: {e}")
        
        # Note: blacklist logging moved inside converter per-batch
        
        return tools

    def _convert_tools(self, server: MCPServer, mcp_tools: List[MCPTool]) -> List[MCPToolInfo]:
        """Helper to convert MCP tool records to MCPToolInfo list with blacklist applied.

        For Pipedream, we intentionally DO NOT prefix tool names to avoid overly long names.
        For other servers, we keep the legacy prefix 'mcp_{server}_{tool}'.
        """
        tools: List[MCPToolInfo] = []
        blacklisted_count = 0
        for tool in mcp_tools:
            if server.name == "pipedream":
                full_name = tool.name
            else:
                full_name = f"mcp_{server.name}_{tool.name}"
            if self._is_tool_blacklisted(full_name):
                blacklisted_count += 1
                continue
            tools.append(
                MCPToolInfo(
                    full_name=full_name,
                    server_name=server.name,
                    tool_name=tool.name,
                    description=tool.description or f"{tool.name} from {server.display_name}",
                    parameters=tool.inputSchema or {"type": "object", "properties": {}}
                )
            )
        if blacklisted_count:
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

    def find_tool_by_name(self, full_name: str) -> Optional[MCPToolInfo]:
        """Find a discovered MCP tool by its full name (exact match)."""
        if not self._initialized:
            self.initialize()
        for tools in self._tools_cache.values():
            for t in tools:
                if t.full_name == full_name:
                    return t
        return None

    def has_tool(self, full_name: str) -> bool:
        """Return True if a discovered MCP tool with this full name exists."""
        return self.find_tool_by_name(full_name) is not None
    
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
        
        # Resolve tool to server + actual tool name (supports unprefixed Pipedream tool names)
        resolved = self._resolve_tool(tool_name)
        if not resolved:
            return {"status": "error", "message": f"Unknown MCP tool: {tool_name}"}
        server_name, actual_tool_name = resolved
        
        if server_name not in self._clients:
            return {
                "status": "error",
                "message": f"MCP server '{server_name}' not available"
            }
        
        # Choose the right client
        if server_name == "pipedream":
            client = self._get_pipedream_agent_client(agent)
        else:
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
            
            # Detect Pipedream Connect Link responses and surface clearly to the agent/user
            if server_name == "pipedream":
                connect_url = None
                if isinstance(content, dict):
                    # Heuristics: look for a URL containing the Connect Link path
                    for v in content.values():
                        if isinstance(v, str) and "pipedream.com/_static/connect.html" in v:
                            connect_url = v
                            break
                elif isinstance(content, str) and "pipedream.com/_static/connect.html" in content:
                    connect_url = content

                if connect_url:
                    return {
                        "status": "action_required",
                        "result": f"Authorization required. Please connect your account via: {connect_url}",
                        "connect_url": connect_url,
                    }

            return {"status": "success", "result": content or "Tool executed successfully"}
            
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
        # Attempt to close per-agent Pipedream clients
        for c in self._pd_agent_clients.values():
            try:
                c.close()
            except Exception:
                pass
        self._pd_agent_clients.clear()
        self._clients.clear()
        self._tools_cache.clear()
        if self._loop and not self._loop.is_closed():
            self._loop.close()
        self._initialized = False

    def _resolve_tool(self, tool_name: str) -> Optional[Tuple[str, str]]:
        """Resolve a tool's server and actual MCP tool name from the provided name.

        - For servers that prefix (e.g., brightdata), the provided name is 'mcp_{server}_{tool}'.
        - For Pipedream, provided names are unprefixed and match the actual tool name.
        """
        # Fast path: search the cached list for an exact full_name match
        for server, tools in self._tools_cache.items():
            for t in tools:
                if t.full_name == tool_name:
                    return (t.server_name, t.tool_name)
        # Backward-compatible fallback: parse legacy prefixed format
        if tool_name.startswith("mcp_"):
            parts = tool_name.split("_", 2)
            if len(parts) == 3:
                _, server_name, actual = parts
                return (server_name, actual)
        return None

    def _get_pipedream_access_token(self, env: Dict[str, str]) -> Optional[str]:
        """Acquire or refresh the Pipedream OAuth access token (cached)."""
        try:
            # Reuse cached token if valid for at least 2 minutes
            if self._pd_access_token and self._pd_token_expiry and datetime.utcnow() < (self._pd_token_expiry - timedelta(minutes=2)):
                return self._pd_access_token

            client_id = env.get("PIPEDREAM_CLIENT_ID", "")
            client_secret = env.get("PIPEDREAM_CLIENT_SECRET", "")
            if not client_id or not client_secret:
                return None

            resp = requests.post(
                "https://api.pipedream.com/v1/oauth/token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            access_token = data.get("access_token")
            expires_in = int(data.get("expires_in", 3600))
            if not access_token:
                return None
            self._pd_access_token = access_token
            self._pd_token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
            return access_token
        except Exception as e:
            logger.error(f"Failed to obtain Pipedream access token: {e}")
            return None

    def _get_pipedream_agent_client(self, agent: PersistentAgent) -> Client:
        """Get or create a unique Pipedream client for a given agent.

        Ensures per-agent isolation via x-pd-external-user-id (agent.id) and
        a dedicated conversation id (also agent.id by default).
        """
        agent_key = str(agent.id)
        if agent_key in self._pd_agent_clients:
            return self._pd_agent_clients[agent_key]

        server = next(s for s in self.AVAILABLE_SERVERS if s.name == "pipedream")
        env = server.env or {}
        token = self._get_pipedream_access_token(env) or ""

        from fastmcp.client.transports import StreamableHttpTransport
        headers: Dict[str, str] = {
            "Authorization": f"Bearer {token}",
            "x-pd-project-id": env.get("PIPEDREAM_PROJECT_ID", ""),
            "x-pd-environment": env.get("PIPEDREAM_ENVIRONMENT", "development"),
            "x-pd-external-user-id": agent_key,
            "x-pd-conversation-id": agent_key,
            "x-pd-app-discovery": "true",
            # Full-config is required for begin_configuration / configure_props / run_* flows
            "x-pd-tool-mode": "full-config",
            # Optionally set an app slug per agent/session as needed
            # "x-pd-app-slug": "google_sheets",
        }
        transport = StreamableHttpTransport(url=server.url, headers=headers)
        client = Client(transport)
        self._pd_agent_clients[agent_key] = client
        # Auto-select default apps for this agent session so catalogs are available
        apps_csv = (env.get("PIPEDREAM_DEFAULT_APPS") or "google_sheets").strip()
        app_list = [a.strip() for a in apps_csv.split(',') if a.strip()]
        if app_list:
            try:
                loop = self._ensure_event_loop()
                loop.run_until_complete(self._pipedream_auto_select_apps(client, app_list))
            except Exception as e:
                logger.info(f"Pipedream select_apps init failed for agent {agent_key}: {e}")
        return client

    async def _pipedream_auto_select_apps(self, client: Client, apps: List[str]):
        """Select default apps for a Pipedream client session.

        This is internal bootstrap and not exposed to the agent (blacklist still blocks agent).
        """
        async with client:
            await client.call_tool("select_apps", {"apps": apps})


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
                # Remove internal-only hints (not accepted by litellm)
                params = {k: v for k, v in params.items() if k != 'supports_tool_choice'}

                response = litellm.completion(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    safety_identifier=str(agent.user.id if agent.user else ""),
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
