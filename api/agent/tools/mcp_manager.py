"""
MCP (Model Context Protocol) tool management for persistent agents.

This module provides dynamic tool discovery, search, and enable/disable functionality
for MCP servers, allowing agents to intelligently select tools from a large ecosystem.

Pipedream (remote MCP) integration goals:
- Centralize headers + token handling
- Discover action tools (sub-agent mode) so the full catalog is searchable
 - Enable only tools needed (40-cap enforced separately)
- Route execution automatically and surface Connect Links via action_required
"""

import json
import logging
import asyncio
import os
import fnmatch
import re
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC

import requests

import litellm
from fastmcp import Client
from mcp.types import Tool as MCPTool
from opentelemetry import trace
from django.conf import settings

from ...models import PersistentAgent, PersistentAgentEnabledTool
from ..core.llm_config import get_llm_config_with_failover

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")

# Maximum number of MCP tools that can be enabled per agent
MAX_MCP_TOOLS = 40


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
            # Prefer a pinned version for reproducibility; npx will use the preinstalled
            # global copy inside Docker, and fall back to on-the-fly install in local/dev.
            args=["-y", "@brightdata/mcp@2.5.0"],
            env={
                "API_TOKEN": os.getenv("BRIGHT_DATA_TOKEN", ""),
                "NPM_CONFIG_CACHE": os.getenv("NPM_CONFIG_CACHE", "/tmp/.npm"),
                # Enable full tool catalog (costs may apply per Bright Data docs)
                "PRO_MODE": "true",
                # Optional: set the MCP server's WEB_UNLOCKER_ZONE from one env var.
                # If BRIGHT_DATA_WEB_UNLOCKER_ZONE is empty/unset, the server default applies.
                **({"WEB_UNLOCKER_ZONE": os.getenv("BRIGHT_DATA_WEB_UNLOCKER_ZONE")} if os.getenv("BRIGHT_DATA_WEB_UNLOCKER_ZONE") else {}),
            },
            enabled=True
        ),
        # Pipedream remote MCP server (HTTP/SSE)
        MCPServer(
            name="pipedream",
            display_name="Pipedream",
            description="Access 2,800+ API integrations with per-user OAuth via Pipedream Connect",
            url="https://remote.mcp.pipedream.net",
            env={},
            headers={},
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
                # Require client credentials and project info from settings
                required = [
                    getattr(settings, "PIPEDREAM_CLIENT_ID", ""),
                    getattr(settings, "PIPEDREAM_CLIENT_SECRET", ""),
                    getattr(settings, "PIPEDREAM_PROJECT_ID", ""),
                    getattr(settings, "PIPEDREAM_ENVIRONMENT", ""),
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
            if server.name == "pipedream":
                # Build discovery headers in sub-agent mode with an initial app slug
                # Some servers expect an app slug present during the initial handshake.
                app_csv = getattr(settings, "PIPEDREAM_PREFETCH_APPS", "google_sheets,greenhouse")
                # first_slug is temporarily disabled as multi-prefetch seems to work; it's undocumented by Pipedream
                # but is shown in their examples. Leaving in case we have to revert
                # first_slug = next((s.strip() for s in app_csv.split(',') if s.strip()), "google_sheets")
                headers = self._pd_build_headers(
                    mode="sub-agent",
                    app_slug=app_csv,
                    external_user_id="gobii-discovery",
                    conversation_id="discovery",
                )
                logger.info(f"Pipedream discovery initializing with app slug '{app_csv}' and sub-agent mode")
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
        # Dump full tool list at INFO for observability
        if tools:
            try:
                for t in tools:
                    logger.info(
                        "MCP tool (server=%s): name=%s desc=%s params=%s",
                        server.name,
                        t.full_name,
                        (t.description or "").strip(),
                        json.dumps(t.parameters) if t.parameters else "{}",
                    )
            except Exception:
                # Never allow logging issues to break initialization
                logger.exception("Failed while logging MCP tool list for server '%s'", server.name)
    
    async def _fetch_server_tools(self, client: Client, server: MCPServer) -> List[MCPToolInfo]:
        """Fetch tools from an MCP server, filtering out blacklisted tools.

        For Pipedream, discover action tools per app slug in sub-agent mode.
        """
        tools = []
        blacklisted_count = 0
        async with client:
            if server.name != "pipedream":
                mcp_tools = await client.list_tools()
                tools.extend(self._convert_tools(server, mcp_tools))
            else:
                app_csv = getattr(settings, "PIPEDREAM_PREFETCH_APPS", "google_sheets,greenhouse")
                prefetch = [s.strip() for s in app_csv.split(",") if s.strip()]
                for app_slug in prefetch:
                    try:
                        if hasattr(client, "transport") and getattr(client.transport, "headers", None) is not None:
                            client.transport.headers["x-pd-app-slug"] = app_slug
                            client.transport.headers["x-pd-tool-mode"] = "sub-agent"
                        app_tools = await client.list_tools()
                        logger.info(
                            "Pipedream list_tools returned %d tools for app_slug='%s'",
                            len(app_tools or []),
                            app_slug,
                        )
                        # Log raw tool names from server response (best-effort)
                        try:
                            for t in app_tools or []:
                                name = getattr(t, "name", "<unnamed>")
                                desc = (getattr(t, "description", None) or "").strip()
                                logger.info("Pipedream raw tool: %s | %s", name, desc)
                        except Exception:
                            logger.exception("Error while logging raw Pipedream tools for '%s'", app_slug)
                        tools.extend(self._convert_tools(server, app_tools))
                    except Exception as e:
                        logger.warning(f"Pipedream prefetch for app '{app_slug}' failed: {e}")
        
        # Note: blacklist logging moved inside converter per-batch
        # Deduplicate by full tool name to avoid repeated entries across app slugs
        try:
            if tools:
                unique: Dict[str, MCPToolInfo] = {}
                for t in tools:
                    if t.full_name not in unique:
                        unique[t.full_name] = t
                if len(unique) != len(tools):
                    logger.info(
                        "Deduplicated tools for server '%s': %d -> %d",
                        server.name,
                        len(tools),
                        len(unique),
                    )
                tools = list(unique.values())
        except Exception:
            logger.exception("Failed while deduplicating tools for server '%s'", server.name)

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

        enabled_names = list(
            PersistentAgentEnabledTool.objects.filter(agent=agent)
            .values_list("tool_full_name", flat=True)
        )
        if not enabled_names:
            return []

        definitions: List[Dict[str, Any]] = []
        enabled_set = set(enabled_names)
        for tool_info in self.get_all_available_tools():
            if tool_info.full_name in enabled_set:
                definitions.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool_info.full_name,
                            "description": tool_info.description,
                            "parameters": tool_info.parameters,
                        },
                    }
                )

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
        if not PersistentAgentEnabledTool.objects.filter(agent=agent, tool_full_name=tool_name).exists():
            return {
                "status": "error",
                "message": f"Tool '{tool_name}' is not enabled for this agent"
            }
        
        # Update usage timestamp
        try:
            row, _ = PersistentAgentEnabledTool.objects.get_or_create(
                agent=agent, tool_full_name=tool_name
            )
            row.last_used_at = datetime.now(UTC)
            row.usage_count = (row.usage_count or 0) + 1
            row.save(update_fields=["last_used_at", "usage_count"])
        except Exception:
            logger.exception("Failed to update usage for tool %s", tool_name)
        
        # Resolve tool to server + actual tool name (supports unprefixed Pipedream tool names)
        resolved = self._resolve_tool(tool_name)
        if not resolved:
            return {"status": "error", "message": f"Unknown MCP tool: {tool_name}"}
        server_name, actual_tool_name = resolved
        
        # Ensure server availability (non-Pipedream servers)
        if server_name != "pipedream" and server_name not in self._clients:
            return {
                "status": "error",
                "message": f"MCP server '{server_name}' not available"
            }

        # Choose the right client
        if server_name == "pipedream":
            app_slug, mode = self._pd_parse_tool(actual_tool_name)
            client = self._get_pipedream_agent_client(agent, app_slug=app_slug, mode=mode)
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
            
            # Detect Pipedream Connect Link responses and replace with our own Connect Link
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
                    try:
                        logger.info(
                            "PD Connect: pass-through link detected for tool '%s' agent=%s",
                            actual_tool_name, str(agent.id)
                        )
                        # Determine app slug: prefer ?app= from server URL, else parse from tool name
                        app_slug = None
                        try:
                            from urllib.parse import urlparse, parse_qs
                            qs = parse_qs(urlparse(connect_url).query or "")
                            app_param = qs.get("app", [None])[0]
                            if isinstance(app_param, str) and app_param.strip():
                                app_slug = app_param.strip()
                                logger.info(
                                    "PD Connect: using app from server link app=%s",
                                    app_slug
                                )
                        except Exception:
                            app_slug = None
                        if not app_slug:
                            app_slug, _mode = self._pd_parse_tool(actual_tool_name)
                            logger.info(
                                "PD Connect: derived app from tool name tool=%s app=%s",
                                actual_tool_name, app_slug or ""
                            )

                        # Create a first‑party Connect session + link
                        from api.integrations.pipedream_connect import create_connect_session
                        session, first_party_url = create_connect_session(agent, app_slug or "")
                        logger.info(
                            "PD Connect: created session id=%s app=%s agent=%s",
                            getattr(session, 'id', None), app_slug or "", str(agent.id)
                        )
                        # Fall back to server‑provided URL if helper could not produce one
                        final_url = first_party_url or connect_url
                        logger.info(
                            "PD Connect: surfacing connect link agent=%s app=%s using_first_party=%s",
                            str(agent.id), app_slug or "", bool(first_party_url)
                        )
                        return {
                            "status": "action_required",
                            "result": f"Authorization required. Please connect your account via: {final_url}",
                            "connect_url": final_url,
                        }
                    except Exception:
                        logger.exception("PD Connect: failed to generate first-party link; falling back to server URL")
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

        - For prefixed servers (e.g., brightdata): 'mcp_{server}_{tool}'
        - For Pipedream: unprefixed and matches the actual tool name
        """
        # Exact match in the discovered catalog
        for _, tools in self._tools_cache.items():
            for t in tools:
                if t.full_name == tool_name:
                    return (t.server_name, t.tool_name)
        # Backward-compatible fallback
        if tool_name.startswith("mcp_"):
            parts = tool_name.split("_", 2)
            if len(parts) == 3:
                _, server_name, actual = parts
                return (server_name, actual)
        return None

    def _get_pipedream_access_token(self) -> Optional[str]:
        """Acquire or refresh the Pipedream OAuth access token (cached)."""
        try:
            # Reuse cached token if valid for at least 2 minutes
            if self._pd_access_token and self._pd_token_expiry and datetime.now(UTC) < (self._pd_token_expiry - timedelta(minutes=2)):
                return self._pd_access_token

            client_id = getattr(settings, "PIPEDREAM_CLIENT_ID", "")
            client_secret = getattr(settings, "PIPEDREAM_CLIENT_SECRET", "")
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
            self._pd_token_expiry = datetime.now(UTC) + timedelta(seconds=expires_in)
            return access_token
        except Exception as e:
            logger.error(f"Failed to obtain Pipedream access token: {e}")
            return None

    def _pd_build_headers(self, mode: str, app_slug: Optional[str], external_user_id: str, conversation_id: str) -> Dict[str, str]:
        token = self._get_pipedream_access_token() or ""
        headers: Dict[str, str] = {
            "Authorization": f"Bearer {token}",
            "x-pd-project-id": getattr(settings, "PIPEDREAM_PROJECT_ID", ""),
            "x-pd-environment": getattr(settings, "PIPEDREAM_ENVIRONMENT", "development"),
            "x-pd-external-user-id": external_user_id,
            "x-pd-conversation-id": conversation_id,
            "x-pd-app-discovery": "true",
            "x-pd-tool-mode": mode,
        }
        if app_slug:
            headers["x-pd-app-slug"] = app_slug
        return headers

    def _pd_parse_tool(self, tool_name: str) -> Tuple[Optional[str], str]:
        """Infer app slug for a Pipedream action tool and return sub-agent mode.

        Expected names look like '<app>-<action>', e.g., 'google_sheets-add-single-row'.
        """
        app = tool_name.split("-", 1)[0] if "-" in tool_name else None
        return (app or None, "sub-agent")

    def _get_pipedream_agent_client(self, agent: PersistentAgent, app_slug: Optional[str], mode: str) -> Client:
        """Get or create a Pipedream client for (agent, app_slug, mode)."""
        agent_key = str(agent.id)
        cache_key = f"{agent_key}:{app_slug or ''}:{mode}"
        if cache_key in self._pd_agent_clients:
            client = self._pd_agent_clients[cache_key]
            # Ensure Authorization header is current
            if hasattr(client, "transport") and getattr(client.transport, "headers", None) is not None:
                token = self._get_pipedream_access_token() or ""
                client.transport.headers["Authorization"] = f"Bearer {token}"
            return client

        from fastmcp.client.transports import StreamableHttpTransport
        server = next(s for s in self.AVAILABLE_SERVERS if s.name == "pipedream")
        headers = self._pd_build_headers(
            mode=mode,
            app_slug=app_slug,
            external_user_id=agent_key,
            conversation_id=agent_key,
        )
        transport = StreamableHttpTransport(url=server.url, headers=headers)
        client = Client(transport)
        self._pd_agent_clients[cache_key] = client
        return client

    # Note: no longer need select_apps; discovery is driven by app slug headers.


# Global manager instance
_mcp_manager = MCPToolManager()


@tracer.start_as_current_span("AGENT TOOL Search Tools")
def search_tools(agent: PersistentAgent, query: str) -> Dict[str, Any]:
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
    logger.info("search_tools: %d tools available across servers", len(all_tools))
    try:
        # Log the full set of discovered tool names (server/name)
        names = [f"{t.server_name}:{t.full_name}" for t in all_tools]
        logger.info("search_tools: available tool names: %s", ", ".join(names))
    except Exception:
        logger.exception("search_tools: failed to log available tool names")
    
    if not all_tools:
        return {
            "status": "success",
            "tools": [],
            "message": "No MCP tools available"
        }
    
    # Prepare concise, plain‑text tool catalog for the LLM (save tokens)
    def _strip_desc(text: str, limit: int = 180) -> str:
        if not text:
            return ""
        # Remove markdown links [text](url) → text
        text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\\1", text)
        # Remove bare URLs
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return (text[: limit].rstrip() + ("…" if len(text) > limit else ""))

    def _summarize_params(schema: Dict[str, Any], limit: int = 6) -> str:
        try:
            if not isinstance(schema, dict):
                return ""
            props = schema.get("properties") or {}
            if not isinstance(props, dict) or not props:
                return ""
            required = set(schema.get("required") or [])
            items = []
            for i, (k, v) in enumerate(props.items()):
                if i >= limit:
                    items.append(f"+{len(props) - limit} more")
                    break
                t = v.get("type") if isinstance(v, dict) else None
                t = t if isinstance(t, str) else "any"
                star = "*" if k in required else ""
                items.append(f"{k}{star}:{t}")
            return ", ".join(items)
        except Exception:
            return ""

    tools_lines: List[str] = []
    for tool in all_tools:
        desc = _strip_desc(tool.description or "")
        p = _summarize_params(tool.parameters or {})
        line = f"- {tool.full_name}: {desc}" if desc else f"- {tool.full_name}"
        if p:
            line += f" | params: {p}"
        tools_lines.append(line)

    # Log preview of the compact catalog
    try:
        preview = "\n".join(tools_lines[:5])
        logger.info(
            "search_tools: compact catalog prepared with %d entries; first few:\n%s",
            len(tools_lines),
            preview,
        )
        if len(tools_lines) > 5:
            logger.info("search_tools: (truncated catalog log; total entries=%d)", len(tools_lines))
    except Exception:
        logger.exception("search_tools: failed to log compact catalog preview")
    
    # Prepare the search prompt with an internal tool-call to enable_tools
    system_prompt = (
        "You are a concise tool discovery assistant. Given a user query and a list of available MCP tools "
        "(names, brief descriptions, and summarized parameters), you MUST select ALL relevant tools and then "
        "call the function enable_tools exactly once with the full tool names you selected. "
        "If no tools are relevant, do not call the function and reply briefly explaining that none are relevant."
    )

    user_prompt = (
        f"Query: {query}\n\n"
        "Available tools (names and brief details):\n"
        + "\n".join(tools_lines)
        + "\n\nSelect the relevant tools and call enable_tools once with their exact full names."
    )

    try:
        # Get LLM configuration with failover
        failover_configs = get_llm_config_with_failover()
        
        # Try each provider in order
        last_exc = None
        for i, (provider, model, params) in enumerate(failover_configs):
            try:
                logger.info(
                    "search_tools with provider %s/%s: provider=%s model=%s",
                    i + 1,
                    len(failover_configs),
                    provider,
                    model,
                )
                # Remove internal-only hints (not accepted by litellm)
                params = {k: v for k, v in params.items() if k not in ('supports_tool_choice', 'use_parallel_tool_calls')}

                enable_tools_def = {
                    "type": "function",
                    "function": {
                        "name": "enable_tools",
                        "description": (
                            "Enable multiple MCP tools in one call. Provide the exact full names "
                            "from the catalog above."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "tool_names": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "minItems": 1,
                                    "uniqueItems": True,
                                    "description": "List of full tool names to enable"
                                }
                            },
                            "required": ["tool_names"],
                        },
                    },
                }

                response = litellm.completion(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    tools=[enable_tools_def],
                    tool_choice="auto",
                    safety_identifier=str(agent.user.id if agent.user else ""),
                    **params,
                )

                msg = response.choices[0].message
                content_text = getattr(msg, "content", None) or ""

                # Collect tool names from any enable_tools tool-calls (single-turn, no loop)
                requested: List[str] = []
                tool_calls = getattr(msg, "tool_calls", None) or []
                for tc in tool_calls:
                    try:
                        if not tc:
                            continue
                        fn = getattr(tc, "function", None) or tc.get("function")
                        if not fn:
                            continue
                        fn_name = getattr(fn, "name", None) or fn.get("name")
                        if fn_name != "enable_tools":
                            continue
                        raw_args = getattr(fn, "arguments", None) or fn.get("arguments") or "{}"
                        args = json.loads(raw_args)
                        names = args.get("tool_names") or []
                        if isinstance(names, list):
                            for n in names:
                                if isinstance(n, str) and n not in requested:
                                    requested.append(n)
                    except Exception:
                        logger.exception("search_tools: failed to parse tool call; skipping one call")

                enabled_result = None
                if requested:
                    try:
                        enabled_result = enable_tools(agent, requested)
                    except Exception as e:
                        logger.error(f"search_tools: enable_tools failed: {e}")

                # Build final message + structured response
                message_lines: List[str] = []
                if content_text:
                    message_lines.append(content_text.strip())
                if enabled_result and enabled_result.get("status") == "success":
                    summary = []
                    if enabled_result.get("enabled"):
                        summary.append(f"Enabled: {', '.join(enabled_result['enabled'])}")
                    if enabled_result.get("already_enabled"):
                        summary.append(f"Already enabled: {', '.join(enabled_result['already_enabled'])}")
                    if enabled_result.get("evicted"):
                        summary.append(f"Evicted (LRU): {', '.join(enabled_result['evicted'])}")
                    if enabled_result.get("invalid"):
                        summary.append(f"Invalid: {', '.join(enabled_result['invalid'])}")
                    if summary:
                        message_lines.append("; ".join(summary))

                final = {
                    "status": "success",
                    "message": "\n".join([ln for ln in message_lines if ln]) or "",
                }
                if enabled_result and enabled_result.get("status") == "success":
                    final.update({
                        "enabled_tools": enabled_result.get("enabled", []),
                        "already_enabled": enabled_result.get("already_enabled", []),
                        "evicted": enabled_result.get("evicted", []),
                        "invalid": enabled_result.get("invalid", []),
                    })
                return final
                
            except Exception as e:
                last_exc = e
                logger.warning(f"Provider {provider} failed for tool search: {e}")
                continue
        
        # All providers failed
        logger.error(f"All providers failed for tool search: {last_exc}")
        return {
            "status": "error",
            "message": "Failed to search tools",
        }
        
    except Exception as e:
        logger.error(f"Failed to search tools: {e}")
        return {
            "status": "error",
            "message": str(e),
        }


def enable_tools(agent: PersistentAgent, tool_names: List[str]) -> Dict[str, Any]:
    """Enable multiple MCP tools for the agent with LRU eviction (cap=40).

    Blacklisted or non-existent tools are returned in `invalid` (no separate blacklist reporting).
    """
    import time

    # Use module-level MAX_MCP_TOOLS

    if not _mcp_manager._initialized:
        _mcp_manager.initialize()

    # Normalize and de-dupe
    requested: List[str] = []
    seen = set()
    for n in tool_names or []:
        if isinstance(n, str) and n not in seen:
            requested.append(n)
            seen.add(n)

    all_tools = _mcp_manager.get_all_available_tools()
    available = {t.full_name for t in all_tools}

    enabled: List[str] = []
    already_enabled: List[str] = []
    evicted: List[str] = []
    invalid: List[str] = []

    # Enable or mark already-enabled
    for name in requested:
        if name not in available or _mcp_manager._is_tool_blacklisted(name):
            invalid.append(name)
            continue

        try:
            row, created = PersistentAgentEnabledTool.objects.get_or_create(
                agent=agent, tool_full_name=name
            )
            if created:
                # Derive server/tool fields when possible
                resolved = _mcp_manager._resolve_tool(name)
                if resolved:
                    row.tool_server, row.tool_name = resolved
                    row.save(update_fields=["tool_server", "tool_name"])
                enabled.append(name)
            else:
                already_enabled.append(name)
        except Exception:
            logger.exception("Failed enabling tool %s", name)
            invalid.append(name)

    # Enforce LRU cap after all insertions
    total = PersistentAgentEnabledTool.objects.filter(agent=agent).count()
    if total > MAX_MCP_TOOLS:
        overflow = total - MAX_MCP_TOOLS
        # Oldest by (last_used_at NULLS FIRST, enabled_at ASC)
        from django.db.models import F
        oldest = (
            PersistentAgentEnabledTool.objects.filter(agent=agent)
            .order_by(F("last_used_at").asc(nulls_first=True), "enabled_at", "tool_full_name")
            [:overflow]
        )
        evicted_names = [o.tool_full_name for o in oldest]
        PersistentAgentEnabledTool.objects.filter(id__in=[o.id for o in oldest]).delete()
        evicted.extend(evicted_names)
        if evicted_names:
            logger.info(
                f"Evicted %d tool(s) for agent %s due to {MAX_MCP_TOOLS}-tool cap: %s",
                len(evicted_names), agent.id, ", ".join(evicted_names)
            )

    # Build message
    parts: List[str] = []
    if enabled:
        parts.append(f"Enabled: {', '.join(enabled)}")
    if already_enabled:
        parts.append(f"Already enabled: {', '.join(already_enabled)}")
    if evicted:
        parts.append(f"Evicted (LRU): {', '.join(evicted)}")
    if invalid:
        parts.append(f"Invalid: {', '.join(invalid)}")

    return {
        "status": "success",
        "message": "; ".join(parts),
        "enabled": enabled,
        "already_enabled": already_enabled,
        "evicted": evicted,
        "invalid": invalid,
    }


def enable_mcp_tool(agent: PersistentAgent, tool_name: str) -> Dict[str, Any]:
    """Enable an MCP tool for the agent with LRU eviction if over limit."""
    # Use module-level MAX_MCP_TOOLS

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

    # Already enabled?
    try:
        row = PersistentAgentEnabledTool.objects.filter(agent=agent, tool_full_name=tool_name).first()
        if row:
            # Touch usage to reflect interest
            row.last_used_at = datetime.now(UTC)
            row.usage_count = (row.usage_count or 0) + 1
            row.save(update_fields=["last_used_at", "usage_count"])
            return {
                "status": "success",
                "message": f"Tool '{tool_name}' is already enabled",
                "enabled": tool_name,
                "disabled": None,
            }
    except Exception:
        logger.exception("Error checking existing enabled tool %s", tool_name)

    # Enable new tool
    try:
        row = PersistentAgentEnabledTool.objects.create(agent=agent, tool_full_name=tool_name)
        resolved = _mcp_manager._resolve_tool(tool_name)
        if resolved:
            row.tool_server, row.tool_name = resolved
            row.save(update_fields=["tool_server", "tool_name"])
    except Exception as e:
        logger.error("Failed to create enabled tool %s: %s", tool_name, e)
        return {"status": "error", "message": str(e)}

    # Enforce cap
    total = PersistentAgentEnabledTool.objects.filter(agent=agent).count()
    disabled_tool = None
    if total > MAX_MCP_TOOLS:
        # Exclude the just-added tool from eviction so we always keep it
        from django.db.models import F
        oldest = (
            PersistentAgentEnabledTool.objects.filter(agent=agent)
            .exclude(tool_full_name=tool_name)
            .order_by(F("last_used_at").asc(nulls_first=True), "enabled_at", "tool_full_name")
            .first()
        )
        if oldest:
            disabled_tool = oldest.tool_full_name
            oldest.delete()
            logger.info("Evicted LRU tool '%s' to make room for '%s'", disabled_tool, tool_name)

    logger.info("Enabled MCP tool '%s' for agent %s", tool_name, agent.id)
    result = {
        "status": "success",
        "message": f"Successfully enabled tool '{tool_name}'",
        "enabled": tool_name,
        "disabled": disabled_tool,
    }
    if disabled_tool:
        result["message"] += f" (disabled '{disabled_tool}' due to {MAX_MCP_TOOLS} tool limit)"
    return result




def ensure_default_tools_enabled(agent: PersistentAgent) -> None:
    """Ensure default MCP tools are enabled for the agent."""
    if not _mcp_manager._initialized:
        _mcp_manager.initialize()
    
    # Get current enabled tools
    enabled_tools = set(
        PersistentAgentEnabledTool.objects.filter(agent=agent).values_list("tool_full_name", flat=True)
    )
    
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
