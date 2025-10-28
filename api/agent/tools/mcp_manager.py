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
import contextlib
import contextvars
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC

import requests
import litellm  # re-exported for tests expecting to patch LiteLLM directly

import httpx
from fastmcp import Client
from mcp.types import Tool as MCPTool
from opentelemetry import trace
from django.conf import settings
from django.db.models import Max
from django.utils import timezone

from ...models import (
    MCPServerConfig,
    MCPServerOAuthCredential,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PipedreamConnectSession,
)
from ...proxy_selection import select_proxy_for_persistent_agent, select_proxy
from ...services.mcp_servers import agent_accessible_server_configs

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")

_proxy_url_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "mcp_http_proxy_url", default=None
)


@contextlib.contextmanager
def _use_mcp_proxy(proxy_url: Optional[str]):
    """Temporarily bind an HTTP proxy URL for MCP HTTP transports."""
    if proxy_url:
        token = _proxy_url_var.set(proxy_url)
        try:
            yield
        finally:
            _proxy_url_var.reset(token)
    else:
        yield


@dataclass
class MCPServerRuntime:
    """Runtime representation of an MCP server configuration."""

    config_id: str
    name: str
    display_name: str
    description: str
    command: Optional[str]
    args: List[str]
    url: Optional[str]
    auth_method: str
    env: Dict[str, str]
    headers: Dict[str, str]
    prefetch_apps: List[str]
    scope: str
    organization_id: Optional[str]
    user_id: Optional[str]
    updated_at: Optional[datetime]
    oauth_access_token: Optional[str] = field(default=None, repr=False)
    oauth_token_type: Optional[str] = None
    oauth_expires_at: Optional[datetime] = None
    oauth_updated_at: Optional[datetime] = None


@dataclass 
class MCPToolInfo:
    """Information about an MCP tool for search and display."""
    config_id: str
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

    # Buffer window before expiry where we will proactively refresh OAuth tokens
    OAUTH_REFRESH_SAFETY_MARGIN = timedelta(minutes=2)
    OAUTH_REFRESH_TIMEOUT_SECONDS = 15
    
    def __init__(self):
        self._clients: Dict[str, Client] = {}
        self._server_cache: Dict[str, MCPServerRuntime] = {}
        self._tools_cache: Dict[str, List[MCPToolInfo]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._initialized = False
        self._last_refresh_marker: Optional[datetime] = None
        # Cached Pipedream token and expiry
        self._pd_access_token: Optional[str] = None
        self._pd_token_expiry: Optional[datetime] = None
        # Per‑agent Pipedream clients (unique connection per agent id)
        self._pd_agent_clients: Dict[str, Client] = {}
        self._httpx_client_factory = self._build_httpx_client_factory()
        
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
    
    def initialize(self, force: bool = False) -> bool:
        """Initialize or refresh all configured MCP servers."""

        if not force and not self._needs_refresh():
            return self._initialized

        try:
            self._refresh_server_cache()
            self._initialized = True
            return True
        except Exception:
            logger.exception("Failed to refresh MCP server cache")
            self._initialized = False
            return False

    def _needs_refresh(self) -> bool:
        if not self._initialized:
            return True

        try:
            latest = (
                MCPServerConfig.objects.filter(is_active=True)
                .aggregate(latest=Max('updated_at'))
                .get('latest')
            )
        except Exception:
            logger.exception("Failed to determine MCP server freshness; forcing refresh")
            return True

        if latest is None:
            # No active servers; refresh only if cache is non-empty
            return bool(self._server_cache)

        if self._last_refresh_marker is None:
            return True

        return latest > self._last_refresh_marker

    def _refresh_server_cache(self) -> None:
        from django.utils import timezone

        configs = list(
            MCPServerConfig.objects.filter(is_active=True)
            .select_related("oauth_credential")
        )
        logger.info("Loaded %d active MCP server configs", len(configs))

        new_cache: Dict[str, MCPServerRuntime] = {}
        latest_seen: Optional[datetime] = None

        for cfg in configs:
            runtime = self._build_runtime_from_config(cfg)
            new_cache[runtime.config_id] = runtime

            if cfg.updated_at and (latest_seen is None or cfg.updated_at > latest_seen):
                latest_seen = cfg.updated_at

        existing_ids = set(self._server_cache.keys())
        current_ids = set(new_cache.keys())

        removed_ids = existing_ids - current_ids
        for config_id in removed_ids:
            self._discard_client(config_id)

        for config_id, runtime in new_cache.items():
            prior = self._server_cache.get(config_id)
            if not prior:
                continue
            prior_oauth_updated = getattr(prior, "oauth_updated_at", None)
            if prior.updated_at == runtime.updated_at and prior_oauth_updated == runtime.oauth_updated_at:
                continue
            logger.debug("Invalidating cached MCP runtime for %s due to updated configuration", runtime.name)
            self._discard_client(config_id)

        self._server_cache = new_cache
        self._last_refresh_marker = latest_seen or timezone.now()

    def _discard_client(self, config_id: str) -> None:
        client = self._clients.pop(config_id, None)
        if client:
            try:
                client.close()
            except Exception:
                logger.debug("Error closing MCP client for %s", config_id, exc_info=True)
        self._tools_cache.pop(config_id, None)
    def _update_refresh_marker(self, runtime: MCPServerRuntime) -> None:
        marker = runtime.updated_at or timezone.now()
        if self._last_refresh_marker is None or marker > self._last_refresh_marker:
            self._last_refresh_marker = marker

    def _ensure_runtime_registered(self, runtime: MCPServerRuntime) -> bool:
        """Ensure the given runtime has an active client and cached tool list."""
        config_id = runtime.config_id
        if config_id in self._clients and config_id in self._tools_cache:
            return True
        try:
            self._register_server(runtime)
        except Exception:
            logger.exception("Failed to register MCP server %s", runtime.name)
            return False
        return True

    def _safe_register_runtime(self, runtime: MCPServerRuntime) -> bool:
        try:
            self._register_server(runtime)
        except Exception:
            logger.exception("Failed to register MCP server %s", runtime.name)
            return False
        self._server_cache[runtime.config_id] = runtime
        self._update_refresh_marker(runtime)
        return True

    def refresh_server(self, config_id: str) -> None:
        if not config_id:
            return
        if not self._initialized:
            return

        existing_runtime = self._server_cache.get(config_id)
        self._discard_client(config_id)
        self._server_cache.pop(config_id, None)
        self._pd_agent_clients.clear()

        try:
            cfg = (
                MCPServerConfig.objects.filter(id=config_id, is_active=True)
                .select_related("oauth_credential")
                .first()
            )
        except Exception:
            logger.exception("Failed to load MCP server %s during refresh", config_id)
            if existing_runtime:
                self._safe_register_runtime(existing_runtime)
            return

        if not cfg:
            return

        runtime = self._build_runtime_from_config(cfg)
        if self._safe_register_runtime(runtime):
            return

        if existing_runtime:
            logger.warning(
                "Reverting to cached MCP server runtime for %s after refresh failure",
                config_id,
            )
            self._safe_register_runtime(existing_runtime)

    def remove_server(self, config_id: str) -> None:
        if not config_id:
            return
        self._discard_client(config_id)
        self._server_cache.pop(config_id, None)
        self._pd_agent_clients.clear()

    def _build_runtime_from_config(self, cfg: MCPServerConfig) -> MCPServerRuntime:
        env = dict(cfg.environment or {})
        headers = dict(cfg.headers or {})
        prefetch = list(cfg.prefetch_apps or [])
        metadata = cfg.metadata or {}
        oauth_access_token: Optional[str] = None
        oauth_token_type: Optional[str] = None
        oauth_expires_at: Optional[datetime] = None
        oauth_updated_at: Optional[datetime] = None

        try:
            credential = cfg.oauth_credential
        except MCPServerOAuthCredential.DoesNotExist:
            credential = None
        except Exception:
            logger.exception("Failed to load OAuth credential for MCP server %s", cfg.id)
            credential = None

        if credential:
            credential = self._maybe_refresh_oauth_credential(cfg, credential)
            token_value = (credential.access_token or "").strip()
            token_type_value = (credential.token_type or "").strip()
            oauth_access_token = token_value or None
            oauth_token_type = token_type_value or None
            oauth_expires_at = credential.expires_at
            oauth_updated_at = credential.updated_at

        fallback_map = metadata.get('env_fallback', {}) if isinstance(metadata, dict) else {}
        for key, env_var in fallback_map.items():
            if env.get(key):
                continue
            fallback_value = os.getenv(env_var, "")
            if fallback_value:
                env[key] = fallback_value

        return MCPServerRuntime(
            config_id=str(cfg.id),
            name=cfg.name,
            display_name=cfg.display_name,
            description=cfg.description,
            command=cfg.command or None,
            args=list(cfg.command_args or []),
            url=cfg.url or None,
            auth_method=cfg.auth_method,
            env=env,
            headers=headers,
            oauth_access_token=oauth_access_token,
            oauth_token_type=oauth_token_type,
            oauth_expires_at=oauth_expires_at,
            oauth_updated_at=oauth_updated_at,
            prefetch_apps=prefetch,
            scope=cfg.scope,
            organization_id=str(cfg.organization_id) if cfg.organization_id else None,
            user_id=str(cfg.user_id) if cfg.user_id else None,
            updated_at=cfg.updated_at,
        )

    def _maybe_refresh_oauth_credential(
        self,
        cfg: MCPServerConfig,
        credential: MCPServerOAuthCredential | None,
    ) -> MCPServerOAuthCredential | None:
        """Refresh an OAuth credential when the stored access token is expired or near expiry."""

        if not credential or cfg.auth_method != MCPServerConfig.AuthMethod.OAUTH2:
            return credential

        refresh_token = (credential.refresh_token or "").strip()
        if not refresh_token:
            return credential

        expires_at = credential.expires_at
        now = timezone.now()
        if expires_at and expires_at > now + self.OAUTH_REFRESH_SAFETY_MARGIN:
            return credential

        metadata = credential.metadata if isinstance(credential.metadata, dict) else {}
        cfg_metadata = cfg.metadata if isinstance(cfg.metadata, dict) else {}
        token_endpoint = (metadata.get("token_endpoint") or cfg_metadata.get("token_endpoint") or "").strip()
        if not token_endpoint:
            logger.warning(
                "OAuth credential for MCP server %s lacks a token endpoint; skipping refresh",
                cfg.id,
            )
            return credential

        request_data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        client_id = (credential.client_id or cfg_metadata.get("client_id") or "").strip()
        if client_id:
            request_data["client_id"] = client_id

        client_secret = (credential.client_secret or cfg_metadata.get("client_secret") or "").strip()
        if client_secret:
            request_data["client_secret"] = client_secret

        try:
            response = requests.post(
                token_endpoint,
                data=request_data,
                timeout=self.OAUTH_REFRESH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.error(
                "Failed to refresh OAuth token for MCP server %s: %s",
                cfg.id,
                exc,
            )
            return credential

        try:
            token_payload = response.json()
        except ValueError:
            logger.error(
                "Token refresh response for MCP server %s was not valid JSON",
                cfg.id,
            )
            return credential

        new_access_token = (token_payload.get("access_token") or "").strip()
        if not new_access_token:
            logger.error(
                "Token refresh for MCP server %s did not return an access token",
                cfg.id,
            )
            return credential

        update_fields = ["access_token_encrypted"]
        credential.access_token = new_access_token

        new_refresh_token = (token_payload.get("refresh_token") or "").strip()
        if new_refresh_token:
            credential.refresh_token = new_refresh_token
            update_fields.append("refresh_token_encrypted")

        new_id_token = (token_payload.get("id_token") or "").strip()
        if new_id_token:
            credential.id_token = new_id_token
            update_fields.append("id_token_encrypted")

        token_type = (token_payload.get("token_type") or "").strip()
        if token_type:
            credential.token_type = token_type
            update_fields.append("token_type")

        scope = (token_payload.get("scope") or "").strip()
        if scope:
            credential.scope = scope
            update_fields.append("scope")

        expires_in_raw = token_payload.get("expires_in")
        if expires_in_raw is not None:
            try:
                expires_seconds = int(expires_in_raw)
                credential.expires_at = now + timedelta(seconds=max(expires_seconds, 0))
            except (TypeError, ValueError):
                credential.expires_at = None
            update_fields.append("expires_at")

        metadata_update = dict(metadata)
        metadata_update["last_refresh_response"] = {
            key: value
            for key, value in token_payload.items()
            if key not in {"access_token", "refresh_token", "id_token"}
        }
        credential.metadata = metadata_update
        update_fields.append("metadata")

        credential.save(update_fields=list(dict.fromkeys(update_fields)))
        credential.refresh_from_db()
        logger.info(
            "Refreshed OAuth token for MCP server %s (credential updated at %s)",
            cfg.id,
            credential.updated_at,
        )
        return credential

    def _build_httpx_client_factory(self):
        def factory(
            headers: Optional[dict[str, str]] = None,
            timeout: Optional[httpx.Timeout] = None,
            auth: Optional[httpx.Auth] = None,
        ) -> httpx.AsyncClient:
            client_kwargs: Dict[str, Any] = {
                "headers": headers,
                "timeout": timeout or httpx.Timeout(5.0),
                "auth": auth,
                "trust_env": False,
            }
            proxy_url = _proxy_url_var.get()
            if proxy_url:
                client_kwargs["proxy"] = proxy_url
            return httpx.AsyncClient(**client_kwargs)

        return factory

    def _build_auth_headers(self, server: MCPServerRuntime) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if server.auth_method == MCPServerConfig.AuthMethod.OAUTH2:
            token_value = (server.oauth_access_token or "").strip()
            if not token_value:
                logger.info(
                    "MCP server '%s' is configured for OAuth 2.0 but no access token is stored",
                    server.name,
                )
                return headers
            token_type_raw = (server.oauth_token_type or "Bearer").strip() or "Bearer"
            token_type = "Bearer" if token_type_raw.lower() == "bearer" else token_type_raw
            headers["Authorization"] = f"{token_type} {token_value}"
        return headers

    def _select_discovery_proxy_url(self, server: MCPServerRuntime) -> Optional[str]:
        if not server.url:
            return None
        proxy_required = getattr(settings, "GOBII_PROPRIETARY_MODE", False)
        try:
            proxy = select_proxy(
                allow_no_proxy_in_debug=getattr(settings, "DEBUG", False) and not proxy_required,
                context_id=f"mcp_discovery_{server.config_id}",
            )
        except RuntimeError as exc:
            if proxy_required:
                logger.error(
                    "MCP discovery for %s (%s) requires a proxy but none are available: %s",
                    server.name,
                    server.config_id,
                    exc,
                )
                raise
            logger.warning(
                "MCP discovery for %s (%s) falling back to direct connection; proxy unavailable: %s",
                server.name,
                server.config_id,
                exc,
            )
            return None
        if proxy_required and proxy is None:
            logger.error(
                "MCP discovery for %s (%s) requires a proxy but none were selected.",
                server.name,
                server.config_id,
            )
            raise RuntimeError("Proxy required but unavailable for MCP discovery.")
        return proxy.proxy_url if proxy else None

    def _select_agent_proxy_url(self, agent: PersistentAgent) -> Tuple[Optional[str], Optional[str]]:
        if not getattr(settings, "ENABLE_PROXY_ROUTING", True):
            # Allow environments to opt out entirely (mainly for tests)
            return None, None

        proxy_required = getattr(settings, "GOBII_PROPRIETARY_MODE", False)
        try:
            proxy = select_proxy_for_persistent_agent(agent)
        except RuntimeError as exc:
            if proxy_required:
                logger.error("Proxy selection failed for agent %s and a proxy is required: %s", agent.id, exc)
                return None, "No proxy server available"
            logger.warning("Proxy selection failed for agent %s; continuing without proxy: %s", agent.id, exc)
            return None, None

        if proxy_required and not proxy:
            logger.error("Proxy required but unavailable for agent %s", agent.id)
            return None, "No proxy server available"

        return (proxy.proxy_url if proxy else None, None)
    
    def _register_server(self, server: MCPServerRuntime):
        """Register an MCP server and cache its tools."""

        if server.command and server.env.get("API_TOKEN") == "":
            raise ValueError(f"MCP server '{server.name}' missing API_TOKEN")

        if server.url:
            from fastmcp.client.transports import StreamableHttpTransport

            headers: Dict[str, str] = dict(server.headers or {})
            if server.name == "pipedream" and server.scope == MCPServerConfig.Scope.PLATFORM:
                prefetch_csv = ",".join(server.prefetch_apps) if server.prefetch_apps else getattr(
                    settings,
                    "PIPEDREAM_PREFETCH_APPS",
                    "google_sheets,greenhouse",
                )
                headers = self._pd_build_headers(
                    mode="sub-agent",
                    app_slug=prefetch_csv,
                    external_user_id="gobii-discovery",
                    conversation_id="discovery",
                )
                logger.info(
                    "Pipedream discovery initializing with app slug '%s' and sub-agent mode",
                    prefetch_csv,
                )

            else:
                auth_headers = self._build_auth_headers(server)
                if auth_headers:
                    headers.update(auth_headers)

            transport = StreamableHttpTransport(
                url=server.url,
                headers=headers,
                httpx_client_factory=self._httpx_client_factory,
            )
        elif server.command:
            from fastmcp.client.transports import StdioTransport

            transport = StdioTransport(
                command=server.command,
                args=server.args or [],
                env=server.env or {},
            )
        else:
            raise ValueError(f"Server '{server.name}' must have either 'url' or 'command'")

        client = Client(transport)
        self._clients[server.config_id] = client

        loop = self._ensure_event_loop()
        proxy_url = self._select_discovery_proxy_url(server)
        with _use_mcp_proxy(proxy_url):
            tools = loop.run_until_complete(self._fetch_server_tools(client, server))
        self._tools_cache[server.config_id] = tools

        logger.info(
            "Registered MCP server '%s' (%s) with %d tools",
            server.name,
            server.config_id,
            len(tools),
        )
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
                logger.exception(
                    "Failed while logging MCP tool list for server '%s' (%s)",
                    server.name,
                    server.config_id,
                )

    async def _fetch_server_tools(self, client: Client, server: MCPServerRuntime) -> List[MCPToolInfo]:
        """Fetch tools from an MCP server, filtering out blacklisted tools.

        For Pipedream, discover action tools per app slug in sub-agent mode.
        """
        tools: List[MCPToolInfo] = []
        async with client:
            if server.name != "pipedream":
                mcp_tools = await client.list_tools()
                tools.extend(self._convert_tools(server, mcp_tools))
            else:
                if server.prefetch_apps:
                    prefetch = [s.strip() for s in server.prefetch_apps if s.strip()]
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

    def _convert_tools(self, server: MCPServerRuntime, mcp_tools: List[MCPTool]) -> List[MCPToolInfo]:
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
                    config_id=server.config_id,
                    full_name=full_name,
                    server_name=server.name,
                    tool_name=tool.name,
                    description=tool.description or f"{tool.name} from {server.display_name}",
                    parameters=tool.inputSchema or {"type": "object", "properties": {}}
                )
            )
        if blacklisted_count:
            logger.info(
                "Filtered out %d blacklisted tools from server '%s' (%s)",
                blacklisted_count,
                server.name,
                server.config_id,
            )
        return tools
    
    def get_all_available_tools(self) -> List[MCPToolInfo]:
        """Get all available MCP tools from all servers."""
        if not self._initialized:
            self.initialize()
            
        all_tools = []
        for server_tools in self._tools_cache.values():
            all_tools.extend(server_tools)
        return all_tools

    def get_tools_for_agent(self, agent: PersistentAgent) -> List[MCPToolInfo]:
        """Return MCP tools that the given agent may access."""

        if not self.initialize():
            return []

        configs = agent_accessible_server_configs(agent)
        desired_ids = {str(cfg.id) for cfg in configs}

        if not desired_ids:
            return []

        missing_ids = [config_id for config_id in desired_ids if config_id not in self._server_cache]
        if missing_ids:
            logger.info("Refreshing MCP server cache to include missing configs: %s", missing_ids)
            if not self.initialize(force=True):
                return []

        tools: List[MCPToolInfo] = []
        for config_id in desired_ids:
            runtime = self._server_cache.get(config_id)
            if not runtime:
                continue
            if not self._ensure_runtime_registered(runtime):
                continue
            server_tools = self._tools_cache.get(config_id)
            if server_tools:
                tools.extend(server_tools)
        return tools

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
        for tool_info in self.get_tools_for_agent(agent):
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
        
        info = self._resolve_tool_info(tool_name)
        if not info:
            return {"status": "error", "message": f"Unknown MCP tool: {tool_name}"}

        server_name = info.server_name
        actual_tool_name = info.tool_name
        runtime = self._server_cache.get(info.config_id)
        if runtime and not self._ensure_runtime_registered(runtime):
            return {
                "status": "error",
                "message": f"MCP server '{server_name}' is not available",
            }

        proxy_url = None
        proxy_error: Optional[str] = None
        if runtime and runtime.url:
            proxy_url, proxy_error = self._select_agent_proxy_url(agent)
            if proxy_error:
                return {"status": "error", "message": proxy_error}

        if server_name == "pipedream":
            app_slug, mode = self._pd_parse_tool(info.tool_name)
            client = self._get_pipedream_agent_client(agent, app_slug=app_slug, mode=mode)
        else:
            client = self._clients.get(info.config_id)
            if not client:
                return {
                    "status": "error",
                    "message": f"MCP server '{info.server_name}' not available",
                }
        
        try:
            loop = self._ensure_event_loop()
            with _use_mcp_proxy(proxy_url):
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

                        # Create (or reuse) a first-party Connect session + link
                        from api.integrations.pipedream_connect import create_connect_session, EFFECTIVE_EXPIRATION_BUFFER_SECONDS

                        normalized_app = (app_slug or "").strip()

                        existing_session = (
                            PipedreamConnectSession.objects
                            .filter(
                                agent=agent,
                                app_slug=normalized_app,
                                status=PipedreamConnectSession.Status.PENDING,
                            )
                            .exclude(connect_link_url="")
                            .order_by("-created_at")
                            .first()
                        )

                        reused_url: Optional[str] = None
                        if existing_session is not None:
                            expires_at = existing_session.expires_at
                            now = datetime.now(UTC)
                            if not expires_at or expires_at > now + timedelta(seconds=EFFECTIVE_EXPIRATION_BUFFER_SECONDS):
                                reused_url = existing_session.connect_link_url
                                if (
                                    normalized_app
                                    and isinstance(reused_url, str)
                                    and "app=" not in reused_url
                                ):
                                    reused_url = (
                                        f"{reused_url}{'&' if '?' in reused_url else '?'}app={normalized_app}"
                                    )
                                logger.info(
                                    "PD Connect: reusing pending session id=%s app=%s agent=%s",
                                    getattr(existing_session, 'id', None),
                                    normalized_app,
                                    str(agent.id),
                                )
                            else:
                                existing_session.status = PipedreamConnectSession.Status.ERROR
                                existing_session.save(update_fields=["status", "updated_at"])
                                logger.info(
                                    "PD Connect: pending session expired session=%s app=%s agent=%s",
                                    getattr(existing_session, 'id', None),
                                    normalized_app,
                                    str(agent.id),
                                )

                        if reused_url:
                            return {
                                "status": "action_required",
                                "result": f"Authorization required. Please connect your account via: {reused_url}",
                                "connect_url": reused_url,
                            }

                        session, first_party_url = create_connect_session(agent, normalized_app)
                        logger.info(
                            "PD Connect: created session id=%s app=%s agent=%s",
                            getattr(session, 'id', None), normalized_app, str(agent.id)
                        )

                        if not first_party_url and isinstance(session, PipedreamConnectSession):
                            session_status = getattr(session, "status", None)
                            session_expiry = getattr(session, "expires_at", None)
                            if session_status == PipedreamConnectSession.Status.ERROR and session_expiry:
                                logger.warning(
                                    "PD Connect: refusing expired connect link session=%s app=%s expires_at=%s",
                                    getattr(session, 'id', None), app_slug or "", str(session_expiry)
                                )
                                return {
                                    "status": "action_required",
                                    "result": (
                                        "Authorization link expired before it could be delivered. "
                                        "Please ask me again to generate a new connect link."
                                    ),
                                }

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
        self._server_cache.clear()
        self._clients.clear()
        self._tools_cache.clear()
        self._last_refresh_marker = None
        if self._loop and not self._loop.is_closed():
            self._loop.close()
        self._loop = None
        self._initialized = False

    def _resolve_tool_info(self, tool_name: str) -> Optional[MCPToolInfo]:
        """Resolve tool metadata, refreshing cache on demand."""

        info = self.find_tool_by_name(tool_name)
        if info:
            return info

        if self.initialize(force=True):
            info = self.find_tool_by_name(tool_name)
            if info:
                return info

        if tool_name.startswith("mcp_"):
            parts = tool_name.split("_", 2)
            if len(parts) == 3:
                _, server_name, actual = parts
                runtime = next((r for r in self._server_cache.values() if r.name == server_name), None)
                if runtime:
                    return MCPToolInfo(
                        config_id=runtime.config_id,
                        full_name=tool_name,
                        server_name=server_name,
                        tool_name=actual,
                        description=f"{actual} via {runtime.display_name}",
                        parameters={"type": "object", "properties": {}},
                    )

        runtime = next((r for r in self._server_cache.values() if r.name == "pipedream"), None)
        if runtime and "-" in tool_name:
            return MCPToolInfo(
                config_id=runtime.config_id,
                full_name=tool_name,
                server_name=runtime.name,
                tool_name=tool_name,
                description=f"{tool_name} via {runtime.display_name}",
                parameters={"type": "object", "properties": {}},
            )

        return None

    def resolve_tool_info(self, tool_name: str) -> Optional[MCPToolInfo]:
        """Public wrapper to resolve tool metadata."""
        return self._resolve_tool_info(tool_name)

    def is_tool_blacklisted(self, tool_name: str) -> bool:
        """Expose blacklist checks for external managers."""
        return self._is_tool_blacklisted(tool_name)

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

        if not self.initialize():
            raise RuntimeError("Pipedream server configuration is unavailable")

        accessible_ids = {
            str(cfg.id) for cfg in agent_accessible_server_configs(agent)
        }
        runtime = next(
            (
                srv
                for srv in self._server_cache.values()
                if srv.name == "pipedream" and srv.config_id in accessible_ids
            ),
            None,
        )
        if runtime is None:
            logger.warning("Agent %s attempted to use pipedream without accessible server config", agent_key)
            raise RuntimeError("Pipedream MCP server is not accessible to this agent")
        if not runtime.url:
            logger.error("Pipedream runtime %s is missing URL", runtime.config_id)
            raise RuntimeError("Pipedream MCP server is misconfigured")

        from fastmcp.client.transports import StreamableHttpTransport
        headers = self._pd_build_headers(
            mode=mode,
            app_slug=app_slug,
            external_user_id=agent_key,
            conversation_id=agent_key,
        )
        transport = StreamableHttpTransport(
            url=runtime.url or "",
            headers=headers,
            httpx_client_factory=self._httpx_client_factory,
        )
        client = Client(transport)
        self._pd_agent_clients[cache_key] = client
        return client

    # Note: no longer need select_apps; discovery is driven by app slug headers.


# Global manager instance
_mcp_manager = MCPToolManager()


def execute_mcp_tool(agent: PersistentAgent, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute any enabled MCP tool via the shared manager."""
    if not _mcp_manager._initialized:
        _mcp_manager.initialize()
    return _mcp_manager.execute_mcp_tool(agent, tool_name, params)


def get_mcp_manager() -> MCPToolManager:
    """Get the global MCP tool manager instance."""
    return _mcp_manager


def cleanup_mcp_tools():
    """Clean up MCP tool resources."""
    _mcp_manager.cleanup()
