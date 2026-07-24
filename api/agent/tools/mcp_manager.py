"""
MCP (Model Context Protocol) tool management for persistent agents.

This module provides dynamic tool discovery, search, and enable/disable functionality
for MCP servers, allowing agents to intelligently select tools from a large ecosystem.

Pipedream (remote MCP) integration goals:
- Centralize headers + token handling
- Discover action tools with direct schemas so the full catalog is searchable
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
import sys
from time import monotonic
from collections import OrderedDict
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from urllib.parse import urlparse
from typing import Dict, Any, Iterable, List, Optional, Tuple
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, time, timedelta, UTC
from uuid import UUID

import requests

import httpx
from kombu.exceptions import OperationalError as KombuOperationalError
from fastmcp import Client
from fastmcp.client.transports import StdioTransport as FastMCPStdioTransport
from fastmcp.exceptions import ToolError
from mcp import ClientSession, StdioServerParameters
from mcp.types import Tool as MCPTool
from opentelemetry import trace
from django.conf import settings
from django.contrib.sites.models import Site
from django.db import DatabaseError
from django.db.models import Max
from django.urls import reverse

from api.services.system_settings import get_mcp_http_timeout_seconds, get_mcp_stdio_timeout_seconds
from django.utils import timezone

from .mcp_param_guards import MCPParamGuardRegistry
from .mcp_error_normalizers import MCPErrorNormalizerRegistry
from .mcp_result_adapters import MCPResultAdapterRegistry, mcp_result_owner_context
from ...models import MCPServerConfig, MCPServerOAuthCredential, PersistentAgent, PersistentAgentEnabledTool, PipedreamConnectSession
from ...proxy_selection import select_proxy_for_persistent_agent, select_proxy
from ...services.mcp_servers import agent_accessible_server_configs
from ...services.mcp_tool_discovery import schedule_mcp_tool_discovery
from ...services.sandbox_compute import SandboxComputeService, SandboxComputeUnavailable, sandbox_compute_enabled, sandbox_compute_enabled_for_agent
from ...services.mcp_tool_cache import (
    build_mcp_tool_cache_fingerprint,
    claim_mcp_catalog_refresh,
    get_cached_mcp_tool_definitions,
    invalidate_mcp_tool_cache,
    mcp_catalog_discovery_locks,
    release_mcp_catalog_refresh,
    set_cached_mcp_tool_definitions,
)
from ...services.mcp_oauth import (
    OAUTH_REFRESH_SAFETY_MARGIN,
    MCPOAuthStatus,
    ensure_mcp_oauth_credential,
)
from ...services.pipedream_apps import (
    get_effective_pipedream_app_slugs_for_agent,
    get_platform_pipedream_app_slugs,
    normalize_app_slugs,
    pipedream_app_slug_for_tool_name,
)

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")
_MCP_SYNC_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mcp-sync")

_proxy_url_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "mcp_http_proxy_url", default=None
)
_http_timeout_seconds_var: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar(
    "mcp_http_timeout_seconds", default=None
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


@contextlib.contextmanager
def _use_mcp_http_timeout(timeout_seconds: Optional[float]):
    """Bind the HTTP timeout before FastMCP enters its async transport setup."""
    if timeout_seconds is None:
        yield
        return
    token = _http_timeout_seconds_var.set(float(timeout_seconds))
    try:
        yield
    finally:
        _http_timeout_seconds_var.reset(token)


def _sandbox_mcp_fallback_enabled() -> bool:
    return bool(getattr(settings, "SANDBOX_COMPUTE_LOCAL_FALLBACK_MCP", True))


MCP_WILL_CONTINUE_TOOL_NAMES = {
    "search_engine_batch",
}

MCP_SESSION_DEATH_ERROR_SNIPPETS = (
    "connection closed",
    "server session was closed",
    "event loop is closed",
    "client failed to connect",
)
MCP_TOOL_SUCCESS_SENTINEL = "Tool executed successfully"

def _inject_will_continue_work_param(parameters: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(parameters, dict):
        return parameters
    if parameters.get("type") != "object":
        return parameters
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return parameters
    if "will_continue_work" in properties:
        return parameters

    updated_parameters = dict(parameters)
    updated_properties = dict(properties)
    updated_properties["will_continue_work"] = {
        "type": "boolean",
        "description": "REQUIRED. true = you'll take another action, false = you're done. Omitting this stops you for good—choose wisely.",
    }
    updated_parameters["properties"] = updated_properties
    # Add to required list
    existing_required = parameters.get("required", [])
    if isinstance(existing_required, list):
        updated_parameters["required"] = list(existing_required) + ["will_continue_work"]
    else:
        updated_parameters["required"] = ["will_continue_work"]
    return updated_parameters


def _extract_will_continue_work(
    params: Dict[str, Any],
) -> tuple[Dict[str, Any], Optional[bool]]:
    will_continue_work_raw = params.get("will_continue_work", None)
    if will_continue_work_raw is None:
        will_continue_work = None
    elif isinstance(will_continue_work_raw, bool):
        will_continue_work = will_continue_work_raw
    elif isinstance(will_continue_work_raw, str):
        will_continue_work = will_continue_work_raw.lower() == "true"
    else:
        will_continue_work = None

    if "will_continue_work" not in params:
        return params, will_continue_work

    sanitized_params = dict(params)
    sanitized_params.pop("will_continue_work", None)
    return sanitized_params, will_continue_work


def _build_jit_connect_url(agent_id: str, app_slug: str) -> str:
    """
    Build the just-in-time Pipedream connect URL that generates fresh auth links on demand.
    This avoids the 4-hour expiration issue with direct Pipedream links.
    """
    current_site = Site.objects.get_current()
    domain = current_site.domain.strip().rstrip('/')
    path = reverse('pipedream_jit_connect', kwargs={'agent_id': agent_id, 'app_slug': app_slug})
    return f"https://{domain}{path}"


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
    metadata: Dict[str, Any] = field(default_factory=dict)


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


@dataclass(frozen=True)
class PipedreamToolCacheContext:
    """Pipedream apps whose shared catalog shards should be composed."""

    effective_app_slugs: List[str]


@dataclass(frozen=True)
class SandboxToolCacheContext:
    """Agent-scoped discovery inputs for sandboxed stdio tool catalogs."""

    agent_cache_key: str


class GobiiStdioTransport(FastMCPStdioTransport):
    """Custom stdio transport that guarantees an errlog with a real fileno."""

    def __init__(
        self,
        command: str,
        args: List[str],
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        keep_alive: Optional[bool] = None,
    ):
        super().__init__(command=command, args=args, env=env, cwd=cwd, keep_alive=keep_alive)
        self._errlog_fallback = None

    def _resolve_errlog(self):
        for candidate in (getattr(sys, "__stderr__", None), sys.stderr):
            if candidate and hasattr(candidate, "fileno"):
                return candidate
        if self._errlog_fallback is None:
            self._errlog_fallback = open(os.devnull, "w")
        return self._errlog_fallback

    async def connect(self, **session_kwargs):
        if self._connect_task is not None:
            return

        errlog = self._resolve_errlog()

        async def _connect_task():
            from mcp.client.stdio import stdio_client

            try:
                async with contextlib.AsyncExitStack() as stack:
                    try:
                        server_params = StdioServerParameters(
                            command=self.command,
                            args=self.args,
                            env=self.env,
                            cwd=self.cwd,
                        )
                        transport = await stack.enter_async_context(
                            stdio_client(server_params, errlog=errlog)
                        )
                        read_stream, write_stream = transport
                        self._session = await stack.enter_async_context(
                            ClientSession(read_stream, write_stream, **session_kwargs)
                        )

                        logger.debug("Stdio transport connected")
                        self._ready_event.set()

                        await self._stop_event.wait()
                    finally:
                        self._session = None
                        logger.debug("Stdio transport disconnected")
            except Exception:
                self._ready_event.set()
                raise

        self._connect_task = asyncio.create_task(_connect_task())
        await self._ready_event.wait()

        if self._connect_task.done():
            exception = self._connect_task.exception()
            if exception is not None:
                raise exception

    async def disconnect(self):
        await super().disconnect()
        self._cleanup_errlog()

    async def close(self):
        await super().close()
        self._cleanup_errlog()

    def _cleanup_errlog(self):
        if self._errlog_fallback:
            try:
                self._errlog_fallback.close()
            finally:
                self._errlog_fallback = None


class MCPToolManager:
    """Manages MCP tool connections and provides search/enable/disable functionality."""

    PIPEDREAM_RUNTIME_NAME = "pipedream"
    PIPEDREAM_COMPONENT_OPTION_TOOLS = {"retrieve_options", "configure_component"}

    # Blacklisted tool patterns (glob-style patterns)
    # Tools matching these patterns will be excluded from discovery and execution
    TOOL_BLACKLIST = [
        "mcp_brightdata_search_engine",
        "mcp_brightdata_scrape_as_markdown",
        "mcp_brightdata_web_data_linkedin_person_profile",
        "mcp_brightdata_scraping_browser_*",  # Blacklist all scraping browser tools
        "mcp_brightdata_scrape_as_html", # usually results in huge result sets that we don't want
        "select_apps"
        # Add more blacklist patterns here as needed
    ]

    TOOL_CACHE_MAX_SLOTS = 128

    def __init__(self):
        self._clients: Dict[str, Client] = {}
        self._stdio_proxy_clients: Dict[str, Client] = {}
        self._server_cache: Dict[str, MCPServerRuntime] = {}
        self._tools_cache: OrderedDict[str, List[MCPToolInfo]] = OrderedDict()
        self._tool_cache_fingerprints: Dict[str, str] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._initialized = False
        self._last_refresh_marker: Optional[datetime] = None
        # Cached Pipedream token and expiry
        self._pd_access_token: Optional[str] = None
        self._pd_token_expiry: Optional[datetime] = None
        # Per‑agent Pipedream clients (unique connection per agent id)
        self._pd_agent_clients: Dict[str, Client] = {}
        self._httpx_client_factory = self._build_httpx_client_factory()
        self._pd_missing_credentials_logged = False
        self._param_guards = MCPParamGuardRegistry.default()
        self._error_normalizers = MCPErrorNormalizerRegistry.default()
        self._result_adapters = MCPResultAdapterRegistry.default()

    def _cache_tools(self, slot_key: str, tools: List[MCPToolInfo], fingerprint: str) -> None:
        self._tools_cache[slot_key] = tools
        self._touch_tools_cache(slot_key)
        self._tool_cache_fingerprints[slot_key] = fingerprint
        while len(self._tools_cache) > self.TOOL_CACHE_MAX_SLOTS:
            evicted, _tools = self._tools_cache.popitem(last=False)
            self._tool_cache_fingerprints.pop(evicted, None)

    def _touch_tools_cache(self, slot_key: str) -> None:
        if not isinstance(self._tools_cache, OrderedDict):
            self._tools_cache = OrderedDict(self._tools_cache)
        self._tools_cache.move_to_end(slot_key)

    def _ensure_event_loop(self) -> asyncio.AbstractEventLoop:
        """Ensure we have an event loop for async operations."""
        if self._loop is None or self._loop.is_closed():
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        return self._loop

    def _run_coroutine_isolated(self, coroutine):
        """Run a coroutine on a dedicated event loop for thread-safe execution."""
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coroutine)
        finally:
            # AnyIO's stdio transport owns task-group scoped async generators.
            # Forcing shutdown_asyncgens() here can close them from a different
            # task than they were entered in, which raises during parallel MCP teardown.
            asyncio.set_event_loop(None)
            loop.close()

    def _run_coroutine_sync(self, coroutine):
        """Run async MCP work from sync code, including async caller contexts."""
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        cached_loop_running = (
            self._loop is not None
            and not self._loop.is_closed()
            and self._loop.is_running()
        )
        if (running_loop and running_loop.is_running()) or cached_loop_running:
            # Python disallows nesting run_until_complete in a thread that is
            # already running an event loop, including one cached on the manager.
            context = contextvars.copy_context()
            return _MCP_SYNC_EXECUTOR.submit(context.run, self._run_coroutine_isolated, coroutine).result()

        loop = self._ensure_event_loop()
        return loop.run_until_complete(coroutine)

    def _close_client_sync(self, client: Optional[Client], *, context: str) -> None:
        """Close a FastMCP client from sync code."""
        if client is None:
            return
        try:
            self._run_coroutine_isolated(client.close())
        except Exception:
            logger.debug("Failed to close MCP client for %s", context, exc_info=True)

    def _discard_scoped_stdio_proxy_clients(self, prefix: str) -> None:
        for cache_key in [key for key in self._stdio_proxy_clients if key.startswith(prefix)]:
            client = self._stdio_proxy_clients.pop(cache_key, None)
            if client:
                self._close_client_sync(client, context=cache_key)

    def _normalize_stdio_proxy_url(self, proxy_url: Optional[str]) -> Optional[str]:
        raw_proxy_url = str(proxy_url or "").strip()
        if not raw_proxy_url:
            return None

        parsed = urlparse(raw_proxy_url)
        netloc = parsed.netloc or parsed.path
        if not netloc:
            return None
        return f"http://{netloc}"

    def _build_stdio_proxy_env(self, proxy_url: Optional[str]) -> Dict[str, str]:
        normalized_proxy_url = self._normalize_stdio_proxy_url(proxy_url)
        if not normalized_proxy_url:
            return {}

        proxy_env_keys = (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        )
        return {key: normalized_proxy_url for key in proxy_env_keys}

    def _get_scoped_stdio_proxy_client(
        self,
        runtime: MCPServerRuntime,
        *,
        scope_key: str,
        proxy_url: str,
    ) -> Client:
        normalized_proxy_url = self._normalize_stdio_proxy_url(proxy_url)
        if not normalized_proxy_url:
            raise ValueError("A proxy URL is required for scoped stdio clients.")

        cache_prefix = f"{runtime.config_id}:{scope_key}:"
        cache_key = f"{cache_prefix}{normalized_proxy_url}"
        cached_client = self._stdio_proxy_clients.get(cache_key)
        if cached_client:
            return cached_client

        self._discard_scoped_stdio_proxy_clients(cache_prefix)
        client = self._build_client_for_runtime(
            runtime,
            env_overrides=self._build_stdio_proxy_env(proxy_url),
        )
        self._stdio_proxy_clients[cache_key] = client
        return client

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

    def _apply_server_subset(self, configs, stale_ids: set[str], *, update_global_marker: bool) -> bool:
        refreshed_ids: set[str] = set()
        latest_seen: Optional[datetime] = None
        for cfg in configs:
            runtime = self._build_runtime_from_config(cfg)
            refreshed_ids.add(runtime.config_id)
            if cfg.updated_at and (latest_seen is None or cfg.updated_at > latest_seen):
                latest_seen = cfg.updated_at
            prior = self._server_cache.get(runtime.config_id)
            prior_oauth_updated = getattr(prior, "oauth_updated_at", None) if prior else None
            if prior and prior.updated_at == runtime.updated_at and prior_oauth_updated == runtime.oauth_updated_at:
                continue
            self._safe_register_runtime(runtime)
        for config_id in stale_ids - refreshed_ids:
            self._discard_client(config_id)
            self._server_cache.pop(config_id, None)
        if update_global_marker:
            self._initialized = True
            self._last_refresh_marker = max(
                filter(None, (self._last_refresh_marker, latest_seen or timezone.now()))
            )
        return True

    def _discard_client(self, config_id: str) -> None:
        client = self._clients.pop(config_id, None)
        if client:
            self._close_client_sync(client, context=config_id)
        self._discard_scoped_stdio_proxy_clients(f"{config_id}:")
        self._tools_cache.pop(config_id, None)
        self._tool_cache_fingerprints.pop(config_id, None)
        prefix = f"{config_id}:"
        for slot_key in [key for key in self._tools_cache if key.startswith(prefix)]:
            self._tools_cache.pop(slot_key, None)
            self._tool_cache_fingerprints.pop(slot_key, None)

    def _discard_execution_clients(self, config_id: str) -> None:
        client = self._clients.pop(config_id, None)
        if client:
            self._close_client_sync(client, context=config_id)
        self._discard_scoped_stdio_proxy_clients(f"{config_id}:")

    @staticmethod
    def _is_mcp_session_death_message(message: Any) -> bool:
        if not isinstance(message, str):
            return False
        lower = message.lower()
        return any(snippet in lower for snippet in MCP_SESSION_DEATH_ERROR_SNIPPETS)

    def _handle_mcp_session_death_response(
        self,
        runtime: Optional[MCPServerRuntime],
        response: Dict[str, Any],
        *,
        evict_client: bool,
    ) -> Dict[str, Any]:
        if not isinstance(response, dict) or response.get("status") != "error":
            return response
        if not self._is_mcp_session_death_message(str(response.get("message") or "")):
            return response

        if evict_client and runtime and self._is_stdio_runtime(runtime):
            logger.info(
                "Discarding MCP stdio execution client for %s after session closure: %s",
                runtime.name,
                response.get("message"),
            )
            self._discard_execution_clients(runtime.config_id)

        updated = dict(response)
        updated["retryable"] = True
        return updated

    def _update_refresh_marker(self, runtime: MCPServerRuntime) -> None:
        marker = runtime.updated_at or timezone.now()
        if self._last_refresh_marker is None or marker > self._last_refresh_marker:
            self._last_refresh_marker = marker

    def _sandbox_mcp_enabled(self, agent: Optional[PersistentAgent]) -> bool:
        """Return whether sandbox compute is available for this agent context."""
        if agent is None:
            return sandbox_compute_enabled()
        return sandbox_compute_enabled_for_agent(agent)

    @staticmethod
    def _is_stdio_runtime(runtime: Optional[MCPServerRuntime]) -> bool:
        if runtime is None:
            return False
        return bool(runtime.command) and not bool(runtime.url)

    def _runtime_requires_sandbox(self, runtime: Optional[MCPServerRuntime]) -> bool:
        return bool(
            runtime
            and runtime.scope != MCPServerConfig.Scope.PLATFORM
            and self._is_stdio_runtime(runtime)
        )

    def _sandbox_required_runtime_available(
        self,
        runtime: Optional[MCPServerRuntime],
        *,
        agent: Optional[PersistentAgent],
        force_local: bool = False,
    ) -> bool:
        if force_local or not self._runtime_requires_sandbox(runtime):
            return True
        if agent is None:
            return False
        return self._sandbox_mcp_enabled(agent)

    def _should_route_runtime_via_sandbox(
        self,
        runtime: Optional[MCPServerRuntime],
        *,
        agent: Optional[PersistentAgent],
    ) -> bool:
        return self._runtime_requires_sandbox(runtime) and self._sandbox_mcp_enabled(agent)

    def _ensure_runtime_registered(
        self,
        runtime: MCPServerRuntime,
        *,
        agent: Optional[PersistentAgent] = None,
        force_local: bool = False,
        require_client: bool = False,
        pipedream_context: Optional[PipedreamToolCacheContext] = None,
        sandbox_context: Optional[SandboxToolCacheContext] = None,
    ) -> bool:
        """Ensure the given runtime has an active client and cached tool list."""
        if not self._sandbox_required_runtime_available(
            runtime,
            agent=agent,
            force_local=force_local,
        ):
            logger.info(
                "Skipping non-platform STDIO MCP server %s because sandbox compute is unavailable",
                runtime.name,
            )
            return False

        config_id = runtime.config_id
        slot_key = self._tool_cache_slot_key(runtime, pipedream_context, sandbox_context)
        uses_per_agent_client = self._runtime_uses_per_agent_client(runtime)
        needs_shared_client = require_client and not uses_per_agent_client
        if slot_key in self._tools_cache:
            self._touch_tools_cache(slot_key)
            if pipedream_context is not None or sandbox_context is not None:
                cache_fingerprint = self._build_tool_cache_fingerprint(runtime, pipedream_context, sandbox_context)
                cached_fingerprint = self._tool_cache_fingerprints.get(slot_key)
                if cached_fingerprint and cached_fingerprint != cache_fingerprint:
                    self._tools_cache.pop(slot_key, None)
                    self._tool_cache_fingerprints.pop(slot_key, None)
                else:
                    if not require_client or config_id in self._clients:
                        return True
                    if uses_per_agent_client:
                        return self._runtime_per_agent_client_ready(runtime)
            else:
                if not require_client or config_id in self._clients:
                    return True
                if uses_per_agent_client:
                    return self._runtime_per_agent_client_ready(runtime)
        try:
            self._register_server(
                runtime,
                agent=agent,
                force_local=force_local or needs_shared_client,
                pipedream_context=pipedream_context,
                sandbox_context=sandbox_context,
            )
        except Exception:
            logger.exception("Failed to register MCP server %s", runtime.name)
            return False
        if slot_key not in self._tools_cache:
            return False
        if require_client and config_id not in self._clients:
            if uses_per_agent_client:
                return self._runtime_per_agent_client_ready(runtime)
            return False
        return True

    def _runtime_uses_per_agent_client(self, runtime: MCPServerRuntime) -> bool:
        """Return True when execution uses dedicated per-agent clients, not shared runtime clients."""
        return runtime.name == self.PIPEDREAM_RUNTIME_NAME

    def _runtime_per_agent_client_ready(self, runtime: MCPServerRuntime) -> bool:
        """Validate readiness for runtimes that establish clients per execution context."""
        if runtime.name == self.PIPEDREAM_RUNTIME_NAME:
            return bool(self._get_pipedream_access_token())
        return False

    def is_platform_brightdata_config(self, config_id: Optional[str]) -> bool:
        if not config_id:
            return False
        runtime = self._server_cache.get(str(config_id))
        return bool(
            runtime
            and runtime.name == "brightdata"
            and runtime.scope == MCPServerConfig.Scope.PLATFORM
        )

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
        invalidate_mcp_tool_cache(config_id)

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

    def discover_tools_for_server(
        self,
        config_id: str,
        *,
        agent: Optional[PersistentAgent] = None,
    ) -> bool:
        """Fetch tool definitions for a server and populate the cache."""
        if not config_id:
            return False

        try:
            cfg = (
                MCPServerConfig.objects.filter(id=config_id, is_active=True)
                .select_related("oauth_credential")
                .first()
            )
        except DatabaseError:
            logger.exception("Failed to load MCP server %s during discovery", config_id)
            return False

        if not cfg:
            return False

        runtime = self._build_runtime_from_config(cfg)
        if self._runtime_requires_sandbox(runtime) and not sandbox_compute_enabled_for_agent(agent):
            logger.warning(
                "Refusing local MCP discovery for non-platform STDIO server %s without an eligible agent",
                runtime.name,
            )
            return False

        sandbox_context = self._sandbox_cache_context_for_runtime(runtime, agent)
        try:
            self._register_server(
                runtime,
                agent=agent,
                force_local=True,
                prefer_cache=False,
                sandbox_context=sandbox_context,
            )
        except (
            ValueError,
            RuntimeError,
            OSError,
            TimeoutError,
            asyncio.TimeoutError,
            httpx.HTTPError,
            ToolError,
        ) as exc:
            logger.warning("MCP discovery failed for %s: %s", config_id, exc)
            return False
        return True

    def refresh_cached_catalog(
        self,
        config_id: str,
        app_slugs: Optional[Iterable[str]] = None,
    ) -> bool:
        """Refresh shared catalog shards after their soft freshness window."""
        config = (
            MCPServerConfig.objects.filter(id=config_id, is_active=True)
            .select_related("oauth_credential")
            .first()
        )
        if config is None:
            return False

        runtime = self._build_runtime_from_config(config)
        normalized_apps = normalize_app_slugs(app_slugs or [])
        pipedream_context = None
        if runtime.name == self.PIPEDREAM_RUNTIME_NAME:
            if not normalized_apps:
                return False
            pipedream_context = PipedreamToolCacheContext(effective_app_slugs=normalized_apps)

        fingerprints = (
            [self._pipedream_app_cache_fingerprint(runtime, app_slug) for app_slug in normalized_apps]
            if pipedream_context is not None
            else [self._build_tool_cache_fingerprint(runtime)]
        )
        if self._runtime_requires_sandbox(runtime):
            logger.warning(
                "Refusing host-local catalog refresh for non-platform STDIO MCP server %s",
                runtime.name,
            )
            for fingerprint in fingerprints:
                release_mcp_catalog_refresh(config_id, fingerprint)
            return False

        try:
            slot_key = self._tool_cache_slot_key(runtime, pipedream_context)
            self._tools_cache.pop(slot_key, None)
            self._tool_cache_fingerprints.pop(slot_key, None)
            self._register_server(
                runtime,
                force_local=True,
                prefer_cache=False,
                pipedream_context=pipedream_context,
            )
            return slot_key in self._tools_cache
        finally:
            for fingerprint in fingerprints:
                release_mcp_catalog_refresh(config_id, fingerprint)

    def test_server_tools(
        self,
        config_id: str,
        *,
        agent: Optional[PersistentAgent] = None,
    ) -> Tuple[bool, List[MCPToolInfo], Dict[str, str]]:
        """Run fresh tool discovery for a saved active server and return diagnostic details."""
        if not config_id:
            return False, [], {
                "phase": "load_config",
                "error_type": "missing_config_id",
                "message": "MCP server config id is required.",
            }

        try:
            cfg = (
                MCPServerConfig.objects.filter(id=config_id, is_active=True)
                .select_related("oauth_credential")
                .first()
            )
        except DatabaseError as exc:
            logger.exception("Failed to load MCP server %s during test discovery", config_id)
            return False, [], {
                "phase": "load_config",
                "error_type": exc.__class__.__name__,
                "message": "Failed to load MCP server config.",
            }

        if not cfg:
            return False, [], {
                "phase": "load_config",
                "error_type": "not_found",
                "message": "MCP server config is not active or does not exist.",
            }

        runtime = self._build_runtime_from_config(cfg)
        if self._runtime_requires_sandbox(runtime):
            return False, [], {
                "phase": "sandbox_discovery",
                "error_type": "sandbox_required",
                "message": "Non-platform STDIO MCP servers must be tested through sandbox compute.",
            }

        sandbox_context = self._sandbox_cache_context_for_runtime(runtime, agent)
        slot_key = self._tool_cache_slot_key(runtime, sandbox_context=sandbox_context)
        self._tools_cache.pop(slot_key, None)
        self._tool_cache_fingerprints.pop(slot_key, None)
        try:
            self._register_server(
                runtime,
                agent=agent,
                force_local=True,
                prefer_cache=False,
                sandbox_context=sandbox_context,
            )
        except (
            ValueError,
            RuntimeError,
            OSError,
            TimeoutError,
            asyncio.TimeoutError,
            httpx.HTTPError,
            ToolError,
        ) as exc:
            logger.warning("MCP test discovery failed for %s: %s", config_id, exc)
            return False, [], {
                "phase": "discover_tools",
                "error_type": exc.__class__.__name__,
                "message": str(exc),
            }

        if slot_key not in self._tools_cache:
            return False, [], {
                "phase": "discover_tools",
                "error_type": "discovery_not_completed",
                "message": "MCP server test did not complete tool discovery.",
            }
        return True, list(self._tools_cache.get(slot_key) or []), {}

    def remove_server(self, config_id: str) -> None:
        if not config_id:
            return
        self._discard_client(config_id)
        self._server_cache.pop(config_id, None)
        self._pd_agent_clients.clear()
        invalidate_mcp_tool_cache(config_id)

    def prewarm_pipedream_owner_cache(
        self,
        owner_scope: str,
        owner_id: str,
        *,
        app_slugs: Optional[Iterable[str]] = None,
    ) -> bool:
        if not owner_id:
            return False
        runtime = next(
            (
                cached_runtime
                for cached_runtime in self._server_cache.values()
                if cached_runtime.name == self.PIPEDREAM_RUNTIME_NAME
                and cached_runtime.scope == MCPServerConfig.Scope.PLATFORM
            ),
            None,
        )
        if runtime is None:
            config = (
                MCPServerConfig.objects.filter(
                    is_active=True,
                    scope=MCPServerConfig.Scope.PLATFORM,
                    name=self.PIPEDREAM_RUNTIME_NAME,
                )
                .select_related("oauth_credential")
                .first()
            )
            if config is None:
                return False
            runtime = self._build_runtime_from_config(config)
            self._server_cache[runtime.config_id] = runtime

        context = self._pipedream_cache_context_for_owner(owner_scope, owner_id, app_slugs=app_slugs)
        return self._ensure_runtime_registered(runtime, force_local=True, pipedream_context=context)

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
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
        )

    def _build_httpx_client_factory(self):
        def factory(
            headers: Optional[dict[str, str]] = None,
            timeout: Optional[httpx.Timeout] = None,
            auth: Optional[httpx.Auth] = None,
            follow_redirects: Optional[bool] = None,
            **extra_client_kwargs: Any,
        ) -> httpx.AsyncClient:
            default_timeout_seconds = _http_timeout_seconds_var.get()
            if default_timeout_seconds is None:
                default_timeout_seconds = settings.MCP_HTTP_REQUEST_TIMEOUT_SECONDS
            client_kwargs: Dict[str, Any] = {
                "headers": headers,
                "timeout": timeout or httpx.Timeout(default_timeout_seconds),
                "auth": auth,
                "trust_env": False,
            }
            if follow_redirects is not None:
                client_kwargs["follow_redirects"] = follow_redirects
            if extra_client_kwargs:
                client_kwargs.update(extra_client_kwargs)
            proxy_url = _proxy_url_var.get()
            if proxy_url:
                client_kwargs["proxy"] = proxy_url
            return httpx.AsyncClient(**client_kwargs)

        return factory

    def _ensure_runtime_oauth(
        self,
        server: MCPServerRuntime,
    ) -> tuple[MCPServerRuntime, Optional[Dict[str, Any]]]:
        if server.auth_method != MCPServerConfig.AuthMethod.OAUTH2:
            return server, None
        if server.oauth_access_token and (
            server.oauth_expires_at is None
            or server.oauth_expires_at > timezone.now() + OAUTH_REFRESH_SAFETY_MARGIN
        ):
            return server, None

        result = ensure_mcp_oauth_credential(server.config_id)
        credential = result.credential
        if result.status != MCPOAuthStatus.USABLE or credential is None:
            if (
                result.status == MCPOAuthStatus.TEMPORARILY_UNAVAILABLE
                and server.oauth_access_token
                and server.oauth_expires_at is not None
                and server.oauth_expires_at > timezone.now()
            ):
                logger.warning(
                    "Using still-valid OAuth token for MCP server %s while refresh is unavailable",
                    server.config_id,
                )
                return server, None
            message = result.message or "This MCP integration is unavailable."
            if result.status == MCPOAuthStatus.RECONNECT_REQUIRED:
                return server, {
                    "status": "action_required",
                    "result": message,
                    "message": message,
                }
            return server, {
                "status": "error",
                "message": message,
                "retryable": result.status == MCPOAuthStatus.TEMPORARILY_UNAVAILABLE,
            }

        access_token = (credential.access_token or "").strip() or None
        if (
            server.oauth_updated_at != credential.updated_at
            or server.oauth_access_token != access_token
        ):
            self._discard_client(server.config_id)
        server.oauth_access_token = access_token
        server.oauth_token_type = (credential.token_type or "").strip() or None
        server.oauth_expires_at = credential.expires_at
        server.oauth_updated_at = credential.updated_at
        self._server_cache[server.config_id] = server
        return server, None

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

    def _build_client_for_runtime(
        self,
        server: MCPServerRuntime,
        *,
        pipedream_context: Optional[PipedreamToolCacheContext] = None,
        env_overrides: Optional[Dict[str, str]] = None,
    ) -> Client:
        if server.name == self.PIPEDREAM_RUNTIME_NAME:
            raise ValueError("Pipedream clients require agent-scoped initialization")

        if server.url:
            from fastmcp.client.transports import StreamableHttpTransport

            headers: Dict[str, str] = dict(server.headers or {})
            auth_headers = self._build_auth_headers(server)
            if auth_headers:
                headers.update(auth_headers)
            transport = StreamableHttpTransport(
                url=server.url,
                headers=headers,
                httpx_client_factory=self._httpx_client_factory,
            )
        elif server.command:
            env = dict(server.env or {})
            if env_overrides:
                env.update(env_overrides)
            transport = GobiiStdioTransport(
                command=server.command,
                args=server.args or [],
                env=env,
            )
        else:
            raise ValueError(f"Server '{server.name}' must have either 'url' or 'command'")

        return Client(transport)

    def _select_discovery_proxy_url(self, server: MCPServerRuntime) -> Optional[str]:
        if not settings.ENABLE_PROXY_ROUTING:
            return None
        proxy_required = settings.GOBII_PROPRIETARY_MODE
        try:
            proxy = select_proxy(
                allow_no_proxy_in_debug=settings.DEBUG and not proxy_required,
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
        if not settings.ENABLE_PROXY_ROUTING:
            # Allow environments to opt out entirely (mainly for tests)
            return None, None

        proxy_required = settings.GOBII_PROPRIETARY_MODE
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

    def _get_timeout_for_runtime(self, runtime: Optional[MCPServerRuntime]) -> float:
        """Get the appropriate request timeout based on the runtime's transport."""
        is_http = bool(runtime and runtime.url)
        return get_mcp_http_timeout_seconds() if is_http else get_mcp_stdio_timeout_seconds()

    def _pipedream_cache_context_for_agent(self, agent: PersistentAgent) -> PipedreamToolCacheContext:
        return PipedreamToolCacheContext(
            effective_app_slugs=get_effective_pipedream_app_slugs_for_agent(agent),
        )

    def _pipedream_cache_context_for_owner(
        self,
        owner_scope: str,
        owner_id: str,
        *,
        app_slugs: Optional[Iterable[str]] = None,
    ) -> PipedreamToolCacheContext:
        platform_app_slugs = get_platform_pipedream_app_slugs()
        selected_app_slugs = normalize_app_slugs(app_slugs or [])
        return PipedreamToolCacheContext(
            effective_app_slugs=normalize_app_slugs([*platform_app_slugs, *selected_app_slugs]),
        )

    def _tool_cache_slot_key(
        self,
        server: MCPServerRuntime,
        pipedream_context: Optional[PipedreamToolCacheContext] = None,
        sandbox_context: Optional[SandboxToolCacheContext] = None,
    ) -> str:
        if server.name == self.PIPEDREAM_RUNTIME_NAME and pipedream_context is not None:
            app_fingerprint = build_mcp_tool_cache_fingerprint(
                {"apps": normalize_app_slugs(pipedream_context.effective_app_slugs)}
            )
            return f"{server.config_id}:pipedream:{app_fingerprint}"
        if sandbox_context is not None:
            return f"{server.config_id}:agent:{sandbox_context.agent_cache_key}"
        return server.config_id

    def _sandbox_cache_context_for_runtime(
        self,
        server: MCPServerRuntime,
        agent: Optional[PersistentAgent],
    ) -> Optional[SandboxToolCacheContext]:
        if not agent or not self._should_route_runtime_via_sandbox(server, agent=agent):
            return None
        if not self._is_stdio_runtime(server):
            return None
        return SandboxToolCacheContext(agent_cache_key=str(agent.id))

    def _effective_prefetch_apps(
        self,
        server: MCPServerRuntime,
        pipedream_context: Optional[PipedreamToolCacheContext] = None,
    ) -> List[str]:
        if server.name == self.PIPEDREAM_RUNTIME_NAME and pipedream_context is not None:
            return [slug for slug in pipedream_context.effective_app_slugs if slug]
        if server.prefetch_apps:
            return [s.strip() for s in server.prefetch_apps if s.strip()]
        if server.name == self.PIPEDREAM_RUNTIME_NAME:
            app_csv = getattr(settings, "PIPEDREAM_PREFETCH_APPS", "google_sheets,greenhouse")
            return [s.strip() for s in app_csv.split(",") if s.strip()]
        return []

    def _tool_cache_fingerprint_payload(
        self,
        server: MCPServerRuntime,
        pipedream_context: Optional[PipedreamToolCacheContext] = None,
        sandbox_context: Optional[SandboxToolCacheContext] = None,
    ) -> Dict[str, Any]:
        def _normalize_mapping(values: Dict[str, str]) -> Dict[str, str]:
            return {
                str(key): str(values[key])
                for key in sorted(values)
            }

        updated_at = server.updated_at.isoformat() if server.updated_at else ""
        prefetch_apps = self._effective_prefetch_apps(server, pipedream_context)

        return {
            "config_id": server.config_id,
            "name": server.name,
            "scope": server.scope,
            "url": server.url or "",
            "command": server.command or "",
            "args": [str(arg) for arg in (server.args or [])],
            "auth_method": server.auth_method,
            "updated_at": updated_at,
            "prefetch_apps": prefetch_apps,
            "sandbox_agent_cache_key": (
                sandbox_context.agent_cache_key
                if sandbox_context is not None and self._is_stdio_runtime(server)
                else ""
            ),
            "headers": _normalize_mapping(server.headers or {}),
            "env": _normalize_mapping(server.env or {}),
        }

    def _build_tool_cache_fingerprint(
        self,
        server: MCPServerRuntime,
        pipedream_context: Optional[PipedreamToolCacheContext] = None,
        sandbox_context: Optional[SandboxToolCacheContext] = None,
    ) -> str:
        payload = self._tool_cache_fingerprint_payload(server, pipedream_context, sandbox_context)
        return build_mcp_tool_cache_fingerprint(payload)

    def _pipedream_app_cache_fingerprint(
        self,
        server: MCPServerRuntime,
        app_slug: str,
    ) -> str:
        return self._build_tool_cache_fingerprint(
            server, PipedreamToolCacheContext(effective_app_slugs=[app_slug])
        )

    def _missing_pipedream_app_slugs(
        self,
        server: MCPServerRuntime,
        pipedream_context: PipedreamToolCacheContext,
    ) -> List[str]:
        return [
            app_slug
            for app_slug in normalize_app_slugs(pipedream_context.effective_app_slugs)
            if get_cached_mcp_tool_definitions(
                server.config_id,
                self._pipedream_app_cache_fingerprint(server, app_slug),
            ) is None
        ]

    def _serialize_tools_for_cache(self, tools: List["MCPToolInfo"]) -> List[Dict[str, Any]]:
        return [
            {
                "full_name": tool.full_name,
                "server_name": tool.server_name,
                "tool_name": tool.tool_name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in tools
        ]

    def _deserialize_tools_from_cache(
        self,
        server: MCPServerRuntime,
        cached: List[Dict[str, Any]],
    ) -> List["MCPToolInfo"]:
        tools: List[MCPToolInfo] = []
        for entry in cached:
            if not isinstance(entry, dict):
                continue
            full_name = entry.get("full_name")
            tool_name = entry.get("tool_name")
            server_name = entry.get("server_name")
            if not full_name or not tool_name or not server_name:
                continue
            tools.append(
                MCPToolInfo(
                    config_id=server.config_id,
                    full_name=full_name,
                    server_name=server_name,
                    tool_name=tool_name,
                    description=entry.get("description", ""),
                    parameters=entry.get("parameters") or {"type": "object", "properties": {}},
                )
            )
        return tools

    def _store_discovered_tools(
        self,
        server: MCPServerRuntime,
        tools: List["MCPToolInfo"],
        cache_fingerprint: str,
        pipedream_context: Optional[PipedreamToolCacheContext],
    ) -> None:
        if server.name != self.PIPEDREAM_RUNTIME_NAME or pipedream_context is None:
            set_cached_mcp_tool_definitions(
                server.config_id,
                cache_fingerprint,
                self._serialize_tools_for_cache(tools),
            )
            return

        app_slugs = normalize_app_slugs(pipedream_context.effective_app_slugs)
        tools_by_app: Dict[str, List[MCPToolInfo]] = {app_slug: [] for app_slug in app_slugs}
        unassigned = 0
        for tool in tools:
            app_slug = pipedream_app_slug_for_tool_name(tool.full_name)
            if app_slug in tools_by_app:
                tools_by_app[app_slug].append(tool)
            else:
                unassigned += 1
        if unassigned:
            logger.warning(
                "Skipped %d Pipedream tools that could not be assigned to a requested app shard",
                unassigned,
            )
        for app_slug in app_slugs:
            set_cached_mcp_tool_definitions(
                server.config_id,
                self._pipedream_app_cache_fingerprint(server, app_slug),
                self._serialize_tools_for_cache(tools_by_app[app_slug]),
            )

    def _load_cached_tools(
        self,
        server: MCPServerRuntime,
        cache_fingerprint: str,
        *,
        sandbox_mode: bool = False,
        pipedream_context: Optional[PipedreamToolCacheContext] = None,
        sandbox_context: Optional[SandboxToolCacheContext] = None,
    ) -> bool:
        started_at = monotonic()
        stale_fingerprints: list[tuple[str, str]] = []
        app_count = 0
        if server.name == self.PIPEDREAM_RUNTIME_NAME and pipedream_context is not None:
            cached_payload: list[Dict[str, Any]] = []
            app_slugs = normalize_app_slugs(pipedream_context.effective_app_slugs)
            app_count = len(app_slugs)
            for app_slug in app_slugs:
                app_fingerprint = self._pipedream_app_cache_fingerprint(server, app_slug)
                cached_entry = get_cached_mcp_tool_definitions(server.config_id, app_fingerprint)
                if cached_entry is None:
                    return False
                cached_payload.extend(cached_entry.tools)
                if cached_entry.is_stale:
                    stale_fingerprints.append((app_slug, app_fingerprint))
        else:
            cached_entry = get_cached_mcp_tool_definitions(server.config_id, cache_fingerprint)
            if cached_entry is None:
                return False
            cached_payload = cached_entry.tools
            if cached_entry.is_stale:
                stale_fingerprints.append(("", cache_fingerprint))

        cached_tools = self._deserialize_tools_from_cache(server, cached_payload)
        cached_tools = list({tool.full_name: tool for tool in cached_tools}.values())

        slot_key = self._tool_cache_slot_key(server, pipedream_context, sandbox_context)
        self._cache_tools(slot_key, cached_tools, cache_fingerprint)
        if sandbox_mode:
            client = self._clients.pop(server.config_id, None)
            if client:
                self._close_client_sync(client, context=server.config_id)
            self._discard_scoped_stdio_proxy_clients(f"{server.config_id}:")

        cache_state = "stale" if stale_fingerprints else "fresh"
        logger.info(
            "MCP catalog cache load: server=%s config=%s apps=%d tools=%d state=%s duration_ms=%d%s",
            server.name,
            server.config_id,
            app_count,
            len(cached_tools),
            cache_state,
            round((monotonic() - started_at) * 1000),
            " (sandbox)" if sandbox_mode else "",
        )
        if stale_fingerprints:
            self._schedule_catalog_refresh(server, stale_fingerprints)
        return True

    def _schedule_catalog_refresh(
        self,
        server: MCPServerRuntime,
        stale_fingerprints: list[tuple[str, str]],
    ) -> None:
        if self._runtime_requires_sandbox(server):
            logger.info(
                "Skipping host-local catalog refresh for non-platform STDIO MCP server %s",
                server.name,
            )
            return

        claimed = [
            (app_slug, fingerprint)
            for app_slug, fingerprint in stale_fingerprints
            if claim_mcp_catalog_refresh(server.config_id, fingerprint)
        ]
        if not claimed:
            return
        try:
            from api.tasks.mcp_catalogs import refresh_mcp_catalog

            refresh_mcp_catalog.delay(
                server.config_id,
                [app_slug for app_slug, _fingerprint in claimed if app_slug],
            )
        except (ImportError, RuntimeError, KombuOperationalError):
            logger.warning("Failed to schedule MCP catalog refresh for %s", server.config_id, exc_info=True)
            for _app_slug, fingerprint in claimed:
                release_mcp_catalog_refresh(server.config_id, fingerprint)

    def _compose_requested_pipedream_cache(
        self,
        server: MCPServerRuntime,
        requested_context: Optional[PipedreamToolCacheContext],
        discovery_context: Optional[PipedreamToolCacheContext],
        sandbox_context: Optional[SandboxToolCacheContext],
    ) -> None:
        if requested_context is None or requested_context == discovery_context:
            return
        requested_fingerprint = self._build_tool_cache_fingerprint(server, requested_context)
        self._load_cached_tools(
            server,
            requested_fingerprint,
            pipedream_context=requested_context,
            sandbox_context=sandbox_context,
        )

    def _register_server(
        self,
        server: MCPServerRuntime,
        *,
        agent: Optional[PersistentAgent] = None,
        force_local: bool = False,
        prefer_cache: bool = True,
        pipedream_context: Optional[PipedreamToolCacheContext] = None,
        sandbox_context: Optional[SandboxToolCacheContext] = None,
    ):
        """Register an MCP server and cache its tools."""

        if not self._sandbox_required_runtime_available(
            server,
            agent=agent,
            force_local=force_local,
        ):
            logger.warning(
                "Refusing local registration for non-platform STDIO MCP server %s because sandbox compute is unavailable",
                server.name,
            )
            return

        discovery_started_at = monotonic()
        requested_pipedream_context = pipedream_context
        sandbox_mode = self._should_route_runtime_via_sandbox(server, agent=agent) and not force_local
        cache_fingerprint = self._build_tool_cache_fingerprint(server, pipedream_context, sandbox_context)
        if prefer_cache and self._load_cached_tools(
            server,
            cache_fingerprint,
            sandbox_mode=sandbox_mode,
            pipedream_context=pipedream_context,
            sandbox_context=sandbox_context,
        ):
            if not force_local:
                return
            # Force-local execution requires an active local client even when tools are cached.
        if sandbox_mode:
            server, auth_error = self._ensure_runtime_oauth(server)
            if auth_error:
                logger.info(
                    "Skipping sandbox MCP discovery: server=%s config=%s auth_status=%s",
                    server.name,
                    server.config_id,
                    auth_error.get("status"),
                )
                return
            if not _sandbox_mcp_fallback_enabled():
                logger.info(
                    "No cached MCP tools for '%s' (%s); scheduling sandbox discovery",
                    server.name,
                    server.config_id,
                )
                schedule_mcp_tool_discovery(server.config_id, reason="cache_miss", agent=agent)
                if self._load_cached_tools(
                    server,
                    cache_fingerprint,
                    sandbox_mode=sandbox_mode,
                    pipedream_context=pipedream_context,
                    sandbox_context=sandbox_context,
                ):
                    return
                self._discard_client(server.config_id)
                return
            logger.info(
                "No cached MCP tools for '%s' (%s); falling back to local discovery",
                server.name,
                server.config_id,
            )

        if server.name == self.PIPEDREAM_RUNTIME_NAME and pipedream_context is not None:
            missing_app_slugs = self._missing_pipedream_app_slugs(server, pipedream_context)
            if missing_app_slugs:
                pipedream_context = PipedreamToolCacheContext(
                    effective_app_slugs=missing_app_slugs,
                )
                cache_fingerprint = self._build_tool_cache_fingerprint(server, pipedream_context)

        if server.name == self.PIPEDREAM_RUNTIME_NAME:
            # Check Pipedream credentials before attempting registration
            token = self._get_pipedream_access_token()
            if not token:
                if not self._pd_missing_credentials_logged:
                    logger.warning("Skipping Pipedream MCP registration: credentials missing.")
                    self._pd_missing_credentials_logged = True
                return

        if server.name == "brightdata":
            # Check BrightData credentials before attempting registration
            # Assuming the env var name is BRIGHTDATA_API_KEY or similar based on the error
            # The server runtime env might have it, or process env.
            # Checking process env as a safe default if runtime.env is unreliable for this check.
            if not os.environ.get("BRIGHTDATA_API_KEY") and not server.env.get("API_TOKEN"):
                 logger.warning("Skipping BrightData MCP registration: API_TOKEN/BRIGHTDATA_API_KEY missing.")
                 return

        server, auth_error = self._ensure_runtime_oauth(server)
        if auth_error:
            logger.info(
                "Skipping MCP network preparation: server=%s config=%s auth_status=%s",
                server.name,
                server.config_id,
                auth_error.get("status"),
            )
            return

        discovery_proxy_url = self._select_discovery_proxy_url(server)
        stdio_env_overrides = (
            self._build_stdio_proxy_env(discovery_proxy_url)
            if self._is_stdio_runtime(server)
            else None
        )

        if server.url:
            from fastmcp.client.transports import StreamableHttpTransport

            headers: Dict[str, str] = dict(server.headers or {})
            if server.name == self.PIPEDREAM_RUNTIME_NAME and server.scope == MCPServerConfig.Scope.PLATFORM:
                prefetch_apps = self._effective_prefetch_apps(server, pipedream_context)
                prefetch_csv = ",".join(prefetch_apps)
                headers = self._pd_build_headers(
                    app_slug=prefetch_csv,
                    external_user_id="gobii-discovery",
                    conversation_id="discovery",
                )
                logger.debug(
                    "Pipedream discovery initializing with app slug '%s'",
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
            transport = GobiiStdioTransport(
                command=server.command,
                args=server.args or [],
                env={**(server.env or {}), **(stdio_env_overrides or {})},
            )
        else:
            raise ValueError(f"Server '{server.name}' must have either 'url' or 'command'")

        client = Client(transport)
        self._clients[server.config_id] = client

        if prefer_cache and self._load_cached_tools(
            server,
            cache_fingerprint,
            pipedream_context=pipedream_context,
            sandbox_context=sandbox_context,
        ):
            self._compose_requested_pipedream_cache(
                server,
                requested_pipedream_context,
                pipedream_context,
                sandbox_context,
            )
            return

        if server.name == self.PIPEDREAM_RUNTIME_NAME and pipedream_context is not None:
            app_slugs = (
                self._missing_pipedream_app_slugs(server, pipedream_context)
                if prefer_cache
                else normalize_app_slugs(pipedream_context.effective_app_slugs)
            )
            if not app_slugs:
                self._compose_requested_pipedream_cache(
                    server, requested_pipedream_context, pipedream_context, sandbox_context
                )
                return
            pipedream_context = PipedreamToolCacheContext(effective_app_slugs=app_slugs)
            cache_fingerprint = self._build_tool_cache_fingerprint(server, pipedream_context)
            lock_fingerprints = [
                self._pipedream_app_cache_fingerprint(server, app_slug) for app_slug in app_slugs
            ]
        else:
            lock_fingerprints = [cache_fingerprint]

        timeout = self._get_timeout_for_runtime(server)
        with mcp_catalog_discovery_locks(
            server.config_id,
            lock_fingerprints,
            timeout=timeout,
        ) as acquired:
            # A lock winner may have published while this process waited.
            if prefer_cache and self._load_cached_tools(
                server,
                cache_fingerprint,
                pipedream_context=pipedream_context,
                sandbox_context=sandbox_context,
            ):
                self._compose_requested_pipedream_cache(
                    server,
                    requested_pipedream_context,
                    pipedream_context,
                    sandbox_context,
                )
                return
            if not acquired:
                logger.warning(
                    "Timed out waiting for MCP catalog discovery: server=%s config=%s",
                    server.name,
                    server.config_id,
                )
                return

            http_proxy_url = discovery_proxy_url if server.url else None
            http_timeout_seconds = get_mcp_http_timeout_seconds() if server.url else None
            with _use_mcp_http_timeout(http_timeout_seconds), _use_mcp_proxy(http_proxy_url):
                tools = self._run_coroutine_sync(
                    self._fetch_server_tools(client, server, pipedream_context=pipedream_context)
                )
            slot_key = self._tool_cache_slot_key(server, pipedream_context, sandbox_context)
            self._cache_tools(slot_key, tools, cache_fingerprint)
            self._store_discovered_tools(server, tools, cache_fingerprint, pipedream_context)

        self._compose_requested_pipedream_cache(
            server,
            requested_pipedream_context,
            pipedream_context,
            sandbox_context,
        )

        logger.info(
            "MCP catalog discovery: server=%s config=%s apps=%d tools=%d state=discovered duration_ms=%d",
            server.name,
            server.config_id,
            len(self._effective_prefetch_apps(server, pipedream_context)),
            len(tools),
            round((monotonic() - discovery_started_at) * 1000),
        )
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "MCP catalog tool names for %s (capped): %s",
                server.name,
                [tool.full_name for tool in tools[:25]],
            )

    async def _fetch_server_tools(
        self,
        client: Client,
        server: MCPServerRuntime,
        *,
        pipedream_context: Optional[PipedreamToolCacheContext] = None,
    ) -> List[MCPToolInfo]:
        """Fetch tools from an MCP server, filtering out blacklisted tools.

        For Pipedream, discover the effective tool catalog once using the
        preconfigured app set attached to the runtime headers.
        """
        tools: List[MCPToolInfo] = []
        async with client:
            if server.name != self.PIPEDREAM_RUNTIME_NAME:
                mcp_tools = await client.list_tools()
                tools.extend(self._convert_tools(server, mcp_tools))
            else:
                prefetch = self._effective_prefetch_apps(server, pipedream_context)
                try:
                    app_tools = await client.list_tools()
                    tools.extend(self._convert_tools(server, app_tools))
                except Exception as e:
                    logger.warning("Pipedream prefetch failed for app set %s: %s", prefetch, e)
        
        # Note: blacklist logging moved inside converter per-batch
        # Deduplicate by full tool name to avoid repeated entries across app slugs
        try:
            if tools:
                unique: Dict[str, MCPToolInfo] = {}
                for t in tools:
                    if t.full_name not in unique:
                        unique[t.full_name] = t
                if len(unique) != len(tools):
                    logger.debug(
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
            if server.name == self.PIPEDREAM_RUNTIME_NAME:
                full_name = tool.name
            else:
                full_name = f"mcp_{server.name}_{tool.name}"
            if self._is_tool_blacklisted(full_name):
                blacklisted_count += 1
                continue
            description = tool.description or f"{tool.name} from {server.display_name}"
            # Augment scrape tools with guidance to prefer http_request for data files
            if tool.name in ("scrape_as_markdown", "scrape_as_html"):
                description += " NOT for data files (.csv, .json, .xml, .txt, /api/) — use http_request instead."
            tools.append(
                MCPToolInfo(
                    config_id=server.config_id,
                    full_name=full_name,
                    server_name=server.name,
                    tool_name=tool.name,
                    description=description,
                    parameters=tool.inputSchema or {"type": "object", "properties": {}}
                )
            )
        if blacklisted_count:
            logger.debug(
                "Filtered out %d blacklisted tools from server '%s' (%s)",
                blacklisted_count,
                server.name,
                server.config_id,
            )
        return tools
    
    def get_tools_for_agent(
        self,
        agent: PersistentAgent,
        *,
        allowed_server_names: Optional[Iterable[str]] = None,
        allowed_config_ids: Optional[Iterable[str]] = None,
        pipedream_app_slugs: Optional[Iterable[str]] = None,
    ) -> List[MCPToolInfo]:
        """Return MCP tools that the given agent may access."""

        allowed_set = None
        allowed_config_set = None
        if allowed_server_names is not None:
            allowed_set = {
                str(name).lower()
                for name in allowed_server_names
                if isinstance(name, str) and str(name).strip()
            }
            if not allowed_set:
                return []
        if allowed_config_ids is not None:
            allowed_config_set = {
                str(config_id)
                for config_id in allowed_config_ids
                if config_id and str(config_id).strip()
            }
            if not allowed_config_set:
                return []

        if allowed_config_set is None and allowed_set is None:
            if ((not self._initialized) or self._needs_refresh()) and not self.initialize():
                return []

        configs = agent_accessible_server_configs(
            agent,
            allowed_config_ids=allowed_config_set,
            allowed_server_names=allowed_set,
        )
        desired_ids = {str(cfg.id) for cfg in configs}

        if not desired_ids:
            return []

        if allowed_config_set is not None or allowed_set is not None:
            if not self._apply_server_subset(configs, desired_ids, update_global_marker=False):
                return []

        missing_ids = [config_id for config_id in desired_ids if config_id not in self._server_cache]
        if missing_ids:
            if allowed_set is None and allowed_config_set is None:
                logger.info("Refreshing MCP server cache to include missing configs: %s", missing_ids)
                if not self.initialize(force=True):
                    return []
            else:
                logger.info(
                    "Skipping unavailable MCP servers for agent %s due to allowlist: %s",
                    getattr(agent, "id", None),
                    missing_ids,
                )
                desired_ids = {config_id for config_id in desired_ids if config_id in self._server_cache}
                if not desired_ids:
                    return []

        tools: List[MCPToolInfo] = []
        for config_id in desired_ids:
            runtime = self._server_cache.get(config_id)
            if not runtime:
                continue
            pipedream_context = None
            sandbox_context = None
            if runtime.name == self.PIPEDREAM_RUNTIME_NAME:
                pipedream_context = self._pipedream_cache_context_for_agent(agent)
                if pipedream_app_slugs is not None:
                    pipedream_context = PipedreamToolCacheContext(
                        effective_app_slugs=normalize_app_slugs(pipedream_app_slugs),
                    )
            else:
                sandbox_context = self._sandbox_cache_context_for_runtime(runtime, agent)
            if not self._ensure_runtime_registered(
                runtime,
                agent=agent,
                pipedream_context=pipedream_context,
                sandbox_context=sandbox_context,
            ):
                continue
            slot_key = self._tool_cache_slot_key(runtime, pipedream_context, sandbox_context)
            server_tools = self._tools_cache.get(slot_key)
            if server_tools:
                tools.extend(server_tools)
        return tools

    def find_tool_by_name(self, full_name: str) -> Optional[MCPToolInfo]:
        """Find a discovered MCP tool by its full name (exact match)."""
        for tools in self._tools_cache.values():
            for t in tools:
                if t.full_name == full_name:
                    return t
        return None

    @staticmethod
    def _backfill_enabled_tool_metadata(
        enabled_row: PersistentAgentEnabledTool,
        info: MCPToolInfo,
    ) -> None:
        update_fields: list[str] = []
        if enabled_row.tool_server != info.server_name:
            enabled_row.tool_server = info.server_name
            update_fields.append("tool_server")
        if enabled_row.tool_name != info.tool_name:
            enabled_row.tool_name = info.tool_name
            update_fields.append("tool_name")
        if str(enabled_row.server_config_id or "") != info.config_id:
            enabled_row.server_config_id = info.config_id
            update_fields.append("server_config")
        if not update_fields:
            return
        try:
            enabled_row.save(update_fields=update_fields)
        except DatabaseError:
            logger.exception("Failed to backfill MCP tool metadata for %s", info.full_name)

    def prepare_tool_for_agent(
        self,
        agent: PersistentAgent,
        tool_name: str,
        *,
        require_enabled: bool = True,
    ) -> Optional[MCPToolInfo]:
        """Load only the runtime/catalog shard needed to resolve one tool."""
        started_at = monotonic()
        try:
            enabled_row = (
                PersistentAgentEnabledTool.objects.filter(
                    agent=agent,
                    tool_full_name=tool_name,
                )
                .only("id", "tool_full_name", "tool_server", "tool_name", "server_config_id")
                .first()
            )
        except DatabaseError:
            logger.exception("Failed to load enabled tool metadata for agent %s", agent.id)
            return None
        if require_enabled and enabled_row is None:
            return None

        cached = self.find_tool_by_name(tool_name)
        cached_matches_row = bool(
            cached
            and enabled_row
            and enabled_row.server_config_id
            and str(enabled_row.server_config_id) == cached.config_id
        )
        if cached and cached_matches_row:
            cached_runtime = self._server_cache.get(cached.config_id)
            if cached_runtime and self._sandbox_required_runtime_available(
                cached_runtime,
                agent=agent,
            ):
                return cached

        allowed_config_ids: set[str] = set()
        allowed_server_names: set[str] = set()
        app_slugs: set[str] = set()
        if enabled_row and enabled_row.server_config_id:
            allowed_config_ids.add(str(enabled_row.server_config_id))
        if (
            not allowed_config_ids
            and enabled_row
            and enabled_row.tool_server not in {"", "builtin", "custom", "eval"}
        ):
            allowed_server_names.add(enabled_row.tool_server)

        app_slug = pipedream_app_slug_for_tool_name(tool_name)
        if app_slug:
            app_slugs.add(app_slug)
            if not allowed_config_ids:
                allowed_server_names.add(self.PIPEDREAM_RUNTIME_NAME)
        elif not allowed_config_ids and not allowed_server_names and tool_name.startswith("mcp_"):
            parts = tool_name.split("_", 2)
            if len(parts) == 3 and parts[1]:
                allowed_server_names.add(parts[1])

        if not allowed_config_ids and not allowed_server_names:
            return None

        tools: List[MCPToolInfo] = []
        if allowed_config_ids:
            tools.extend(
                self.get_tools_for_agent(
                    agent,
                    allowed_config_ids=allowed_config_ids,
                    pipedream_app_slugs=app_slugs or None,
                )
            )
        if allowed_server_names:
            seen = {tool.full_name for tool in tools}
            tools.extend(
                tool
                for tool in self.get_tools_for_agent(
                    agent,
                    allowed_server_names=allowed_server_names,
                    pipedream_app_slugs=app_slugs or None,
                )
                if tool.full_name not in seen
            )

        info = next((tool for tool in tools if tool.full_name == tool_name), None)
        if info is None:
            collapsed = tool_name.replace("_", "").lower()
            info = next(
                (
                    tool
                    for tool in tools
                    if tool.full_name.replace("_", "").lower() == collapsed
                ),
                None,
            )
        if info is None:
            candidate_runtimes = [
                runtime
                for runtime in self._server_cache.values()
                if (
                    runtime.config_id in allowed_config_ids
                    or runtime.name.lower() in allowed_server_names
                )
                and self._sandbox_required_runtime_available(runtime, agent=agent)
            ]
            if tool_name.startswith("mcp_"):
                parts = tool_name.split("_", 2)
                actual_name = enabled_row.tool_name if enabled_row else ""
                if len(parts) == 3:
                    actual_name = actual_name or parts[2]
                runtime = next(
                    (
                        candidate
                        for candidate in candidate_runtimes
                        if not allowed_server_names
                        or candidate.name.lower() in allowed_server_names
                    ),
                    None,
                )
                if runtime and actual_name:
                    info = MCPToolInfo(
                        config_id=runtime.config_id,
                        full_name=tool_name,
                        server_name=runtime.name,
                        tool_name=actual_name,
                        description=f"{actual_name} via {runtime.display_name}",
                        parameters={"type": "object", "properties": {}},
                    )
            elif app_slug:
                runtime = next(
                    (
                        candidate
                        for candidate in candidate_runtimes
                        if candidate.name == self.PIPEDREAM_RUNTIME_NAME
                    ),
                    None,
                )
                if runtime:
                    info = MCPToolInfo(
                        config_id=runtime.config_id,
                        full_name=tool_name,
                        server_name=runtime.name,
                        tool_name=(
                            enabled_row.tool_name
                            if enabled_row and enabled_row.tool_name
                            else tool_name
                        ),
                        description=f"{tool_name} via {runtime.display_name}",
                        parameters={"type": "object", "properties": {}},
                    )

        if info and enabled_row:
            self._backfill_enabled_tool_metadata(enabled_row, info)

        logger.info(
            "MCP targeted preparation: agent=%s tool=%s configs=%d servers=%d found=%s duration_ms=%d",
            agent.id,
            tool_name,
            len(allowed_config_ids),
            len(allowed_server_names),
            bool(info),
            round((monotonic() - started_at) * 1000),
        )
        return info
    
    def get_enabled_tools_definitions(self, agent: PersistentAgent) -> List[Dict[str, Any]]:
        """Get OpenAI-format tool definitions for enabled MCP tools."""
        enabled_rows = list(
            PersistentAgentEnabledTool.objects.filter(agent=agent)
            .only("tool_full_name", "tool_server", "tool_name", "server_config_id")
        )

        enabled_names: list[str] = []
        enabled_rows_by_name = {row.tool_full_name: row for row in enabled_rows}
        allowed_config_ids: set[str] = set()
        allowed_server_names: set[str] = set()
        pipedream_app_slugs: set[str] = set()
        for row in enabled_rows:
            tool_name = row.tool_full_name
            tool_server = (row.tool_server or "").strip()
            server_config_id = str(row.server_config_id) if row.server_config_id else ""
            app_slug = pipedream_app_slug_for_tool_name(tool_name)
            if app_slug:
                pipedream_app_slugs.add(app_slug)

            if server_config_id:
                enabled_names.append(tool_name)
                allowed_config_ids.add(server_config_id)
                continue

            if tool_server and tool_server not in {"builtin", "custom", "eval"}:
                enabled_names.append(tool_name)
                allowed_server_names.add(tool_server)
                continue

            if app_slug:
                enabled_names.append(tool_name)
                allowed_server_names.add(self.PIPEDREAM_RUNTIME_NAME)
                continue

            # Older MCP rows may not have denormalized metadata. Only infer the
            # server for canonical mcp_<server>_<tool> names; blank non-MCP rows
            # are builtins/custom tools and should not force broad discovery.
            if not tool_server and tool_name.startswith("mcp_"):
                parts = tool_name.split("_", 2)
                if len(parts) == 3 and parts[1]:
                    enabled_names.append(tool_name)
                    allowed_server_names.add(parts[1])

        if not enabled_names:
            return []

        definitions: List[Dict[str, Any]] = []
        enabled_set = set(enabled_names)
        tools = (
            self.get_tools_for_agent(
                agent,
                allowed_config_ids=allowed_config_ids,
                pipedream_app_slugs=pipedream_app_slugs or None,
            )
            if allowed_config_ids
            else []
        )
        if allowed_server_names:
            seen_tool_names = {tool.full_name for tool in tools}
            tools.extend(
                tool
                for tool in self.get_tools_for_agent(
                    agent,
                    allowed_server_names=allowed_server_names,
                    pipedream_app_slugs=pipedream_app_slugs or None,
                )
                if tool.full_name not in seen_tool_names
            )
        for tool_info in tools:
            if tool_info.full_name in enabled_set:
                self._backfill_enabled_tool_metadata(
                    enabled_rows_by_name[tool_info.full_name],
                    tool_info,
                )
                parameters = tool_info.parameters
                if tool_info.tool_name in MCP_WILL_CONTINUE_TOOL_NAMES:
                    parameters = _inject_will_continue_work_param(parameters)
                definitions.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool_info.full_name,
                            "description": tool_info.description,
                            "parameters": parameters,
                        },
                    }
                )

        return definitions

    @classmethod
    def _json_safe_mcp_data(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value

        if isinstance(value, (datetime, date, time)):
            return value.isoformat()

        if isinstance(value, (Decimal, UUID)):
            return str(value)

        if is_dataclass(value) and not isinstance(value, type):
            return cls._json_safe_mcp_data(asdict(value))

        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                return cls._json_safe_mcp_data(model_dump(mode="json"))
            except TypeError:
                return cls._json_safe_mcp_data(model_dump())

        dict_dump = getattr(value, "dict", None)
        if callable(dict_dump):
            return cls._json_safe_mcp_data(dict_dump())

        if isinstance(value, Mapping):
            return {
                str(key): cls._json_safe_mcp_data(item)
                for key, item in value.items()
            }

        if isinstance(value, (list, tuple, set)):
            return [cls._json_safe_mcp_data(item) for item in value]

        return value

    @classmethod
    def _extract_tool_result_content(cls, result: Any) -> Any:
        if isinstance(result, dict) and "result" in result:
            return result.get("result")
        if getattr(result, "data", None) is not None:
            return cls._json_safe_mcp_data(result.data)
        for block in getattr(result, "content", None) or []:
            if hasattr(block, "text"):
                return block.text
        return None

    @staticmethod
    def _tool_result_error_message(result: Any) -> Optional[str]:
        if isinstance(result, dict) and result.get("status") == "error":
            return str(result.get("message") or result.get("result") or "Unknown error")
        if not (hasattr(result, "is_error") and result.is_error):
            return None
        content = getattr(result, "content", None) or []
        if content:
            return str(getattr(content[0], "text", "Unknown error"))
        return "Unknown error"

    def _result_to_error_response(
        self,
        server_name: str,
        tool_name: str,
        result: Any,
    ) -> Optional[Dict[str, Any]]:
        message = self._tool_result_error_message(result)
        if message is None:
            return None
        normalized = self._error_normalizers.normalize(server_name, tool_name, message)
        if normalized is not None:
            return normalized
        return {"status": "error", "message": message}

    def _build_mcp_success_response(
        self,
        server_name: str,
        tool_name: str,
        content: Any,
        *,
        use_success_sentinel: bool = True,
    ) -> Dict[str, Any]:
        if use_success_sentinel:
            content = content or MCP_TOOL_SUCCESS_SENTINEL
        return {"status": "success", "result": content}

    def _finalize_mcp_result(
        self,
        server_name: str,
        tool_name: str,
        result: Any,
        *,
        use_success_sentinel: bool = True,
    ) -> Dict[str, Any]:
        error_response = self._result_to_error_response(server_name, tool_name, result)
        if error_response:
            return error_response

        content = self._extract_tool_result_content(result)
        return self._build_mcp_success_response(
            server_name,
            tool_name,
            content,
            use_success_sentinel=use_success_sentinel,
        )

    def _dispatch_sandbox_mcp_request(
        self,
        *,
        agent: PersistentAgent,
        info: MCPToolInfo,
        runtime: MCPServerRuntime,
        server_name: str,
        actual_tool_name: str,
        params: Dict[str, Any],
        full_tool_name: str,
    ) -> Tuple[Optional[Any], bool]:
        try:
            service = SandboxComputeService()
        except SandboxComputeUnavailable as exc:
            return {"status": "error", "message": str(exc)}, False

        def run_once(attempt_params: Dict[str, Any]) -> Any:
            return service.mcp_request(
                agent,
                runtime.config_id,
                actual_tool_name,
                attempt_params,
                full_tool_name=full_tool_name,
            )

        sandbox_result = run_once(params)
        if (
            isinstance(sandbox_result, dict)
            and sandbox_result.get("error_code") == "sandbox_unsupported_mcp"
            and _sandbox_mcp_fallback_enabled()
        ):
            logger.info("Sandbox MCP fallback enabled for %s; executing locally.", info.full_name)
            return None, True

        if isinstance(sandbox_result, dict):
            if sandbox_result.get("status") == "error":
                return sandbox_result, False
            if "result" in sandbox_result:
                adapted = self._adapt_tool_result(
                    server_name,
                    actual_tool_name,
                    sandbox_result.get("result"),
                )
                adapted_result = dict(sandbox_result)
                adapted_result["result"] = adapted
                return adapted_result, False
        return sandbox_result, False

    def execute_mcp_tool(
        self,
        agent: PersistentAgent,
        tool_name: str,
        params: Dict[str, Any],
        *,
        force_local: bool = False,
        tool_info: Optional[MCPToolInfo] = None,
    ) -> Dict[str, Any]:
        """Execute an MCP tool if it's enabled for the agent."""
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
        
        info = tool_info or self._resolve_tool_info(tool_name)
        if not info:
            return {"status": "error", "message": f"Unknown MCP tool: {tool_name}"}

        server_name = info.server_name
        actual_tool_name = info.tool_name
        runtime = self._server_cache.get(info.config_id)

        if not self._sandbox_required_runtime_available(
            runtime,
            agent=agent,
            force_local=force_local,
        ):
            return {
                "status": "error",
                "message": (
                    f"MCP server '{server_name}' requires sandbox compute, "
                    "which is not available for this agent"
                ),
            }

        if runtime:
            runtime, auth_error = self._ensure_runtime_oauth(runtime)
            if auth_error:
                return auth_error

        owner = getattr(agent, "organization", None) or getattr(agent, "user", None)

        params, will_continue_work = _extract_will_continue_work(params)

        param_error = self._param_guards.validate(server_name, actual_tool_name, params, owner)
        if param_error:
            return param_error

        sandbox_fallback = False
        sandbox_routed = self._should_route_runtime_via_sandbox(runtime, agent=agent) and not force_local
        if sandbox_routed:
            sandbox_result, sandbox_fallback = self._dispatch_sandbox_mcp_request(
                agent=agent,
                info=info,
                runtime=runtime,
                server_name=server_name,
                actual_tool_name=actual_tool_name,
                params=params,
                full_tool_name=tool_name,
            )
            if sandbox_result is not None:
                return sandbox_result

        if runtime and (not sandbox_routed or sandbox_fallback):
            local_force = force_local or sandbox_fallback
            if not self._ensure_runtime_registered(
                runtime,
                agent=agent,
                force_local=local_force,
                require_client=True,
            ):
                return {
                    "status": "error",
                    "message": f"MCP server '{server_name}' is not available",
                }

        proxy_url = None
        proxy_error: Optional[str] = None
        if runtime and (runtime.url or self._is_stdio_runtime(runtime)):
            proxy_url, proxy_error = self._select_agent_proxy_url(agent)
            if proxy_error:
                return {"status": "error", "message": proxy_error}

        if server_name == self.PIPEDREAM_RUNTIME_NAME:
            app_slug = self._pd_app_slug_for_tool_call(info.tool_name, params)
            if app_slug:
                try:
                    from ...services.pipedream_connections import PipedreamConnectionError, list_pipedream_connected_accounts

                    connected_accounts = list_pipedream_connected_accounts(agent, app_slug=app_slug)
                except PipedreamConnectionError as exc:
                    return {"status": "error", "message": str(exc)}
                if not connected_accounts:
                    jit_url = _build_jit_connect_url(str(agent.id), app_slug)
                    return {
                        "status": "action_required",
                        "result": f"Authorization required. Please connect your account via: {jit_url}",
                        "connect_url": jit_url,
                    }
            try:
                client = self._get_pipedream_agent_client(agent, app_slug=app_slug)
            except RuntimeError as exc:
                return {"status": "error", "message": str(exc)}
        else:
            client = None
            if runtime and self._is_stdio_runtime(runtime) and proxy_url:
                client = self._get_scoped_stdio_proxy_client(
                    runtime,
                    scope_key=f"agent:{agent.id}",
                    proxy_url=proxy_url,
                )
            else:
                client = self._clients.get(info.config_id)
            if not client:
                return {
                    "status": "error",
                    "message": f"MCP server '{info.server_name}' not available",
                }
        
        try:
            timeout_seconds = self._get_timeout_for_runtime(runtime)
            http_timeout_seconds = timeout_seconds if runtime and runtime.url else None
            with _use_mcp_http_timeout(http_timeout_seconds), _use_mcp_proxy(proxy_url):
                result = self._run_coroutine_sync(
                    self._execute_async(
                        client,
                        actual_tool_name,
                        params,
                        timeout_seconds=timeout_seconds,
                    )
                )
            with mcp_result_owner_context(owner):
                result = self._adapt_tool_result(server_name, actual_tool_name, result)
            
            response = self._finalize_mcp_result(server_name, actual_tool_name, result)
            response = self._handle_mcp_session_death_response(
                runtime,
                response,
                evict_client=True,
            )
            if response.get("status") == "error":
                return response
            content = response.get("result")

            # Detect Pipedream Connect Link responses and replace with our own Connect Link
            if server_name == self.PIPEDREAM_RUNTIME_NAME:
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
                            app_slug = self._pd_parse_tool(actual_tool_name)
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
                            jit_url = _build_jit_connect_url(str(agent.id), normalized_app or "")
                            return {
                                "status": "action_required",
                                "result": f"Authorization required. Please connect your account via: {jit_url}",
                                "connect_url": jit_url,
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

                        # Use JIT URL that generates fresh auth links on demand
                        jit_url = _build_jit_connect_url(str(agent.id), normalized_app or "")
                        logger.info(
                            "PD Connect: surfacing JIT connect link agent=%s app=%s",
                            str(agent.id), app_slug or ""
                        )
                        return {
                            "status": "action_required",
                            "result": f"Authorization required. Please connect your account via: {jit_url}",
                            "connect_url": jit_url,
                        }
                    except Exception:
                        logger.exception("PD Connect: failed to generate first-party link; falling back to JIT URL")
                        jit_url = _build_jit_connect_url(str(agent.id), (app_slug or "").strip())
                        return {
                            "status": "action_required",
                            "result": f"Authorization required. Please connect your account via: {jit_url}",
                            "connect_url": jit_url,
                        }

            if will_continue_work is False:
                response["auto_sleep_ok"] = True
            return response
            
        except Exception as e:
            logger.error(f"Failed to execute MCP tool {tool_name}: {e}")
            response = {
                "status": "error",
                "message": str(e),
            }
            return self._handle_mcp_session_death_response(
                runtime,
                response,
                evict_client=True,
            )

    def execute_mcp_tool_isolated(
        self,
        agent: PersistentAgent,
        tool_name: str,
        params: Dict[str, Any],
        *,
        tool_info: Optional[MCPToolInfo] = None,
    ) -> Dict[str, Any]:
        """Execute an MCP tool without shared loop/client state."""
        if self._is_tool_blacklisted(tool_name):
            return {
                "status": "error",
                "message": f"Tool '{tool_name}' is blacklisted and cannot be executed",
            }

        if not PersistentAgentEnabledTool.objects.filter(agent=agent, tool_full_name=tool_name).exists():
            return {
                "status": "error",
                "message": f"Tool '{tool_name}' is not enabled for this agent",
            }

        info = tool_info or self._resolve_tool_info(tool_name)
        if not info:
            return {"status": "error", "message": f"Unknown MCP tool: {tool_name}"}

        runtime = self._server_cache.get(info.config_id)
        if not runtime:
            return {"status": "error", "message": f"MCP server '{info.server_name}' is not available"}

        if self._runtime_requires_sandbox(runtime):
            return self.execute_mcp_tool(
                agent,
                tool_name,
                params,
                tool_info=info,
            )

        try:
            row, _ = PersistentAgentEnabledTool.objects.get_or_create(
                agent=agent,
                tool_full_name=tool_name,
            )
            row.last_used_at = datetime.now(UTC)
            row.usage_count = (row.usage_count or 0) + 1
            row.save(update_fields=["last_used_at", "usage_count"])
        except Exception:
            logger.exception("Failed to update isolated usage for tool %s", tool_name)

        runtime, auth_error = self._ensure_runtime_oauth(runtime)
        if auth_error:
            return auth_error

        owner = getattr(agent, "organization", None) or getattr(agent, "user", None)
        actual_tool_name = info.tool_name
        server_name = info.server_name
        params, will_continue_work = _extract_will_continue_work(params)

        param_error = self._param_guards.validate(server_name, actual_tool_name, params, owner)
        if param_error:
            return param_error

        proxy_url = None
        if runtime.url or self._is_stdio_runtime(runtime):
            proxy_url, proxy_error = self._select_agent_proxy_url(agent)
            if proxy_error:
                return {"status": "error", "message": proxy_error}

        try:
            client = self._build_client_for_runtime(
                runtime,
                env_overrides=self._build_stdio_proxy_env(proxy_url)
                if self._is_stdio_runtime(runtime)
                else None,
            )
            timeout_seconds = self._get_timeout_for_runtime(runtime)
            http_timeout_seconds = timeout_seconds if runtime.url else None
            with _use_mcp_http_timeout(http_timeout_seconds), _use_mcp_proxy(proxy_url):
                result = self._run_coroutine_isolated(
                    self._execute_async(
                        client,
                        actual_tool_name,
                        params,
                        timeout_seconds=timeout_seconds,
                    )
                )
            with mcp_result_owner_context(owner):
                result = self._adapt_tool_result(server_name, actual_tool_name, result)

            response = self._finalize_mcp_result(server_name, actual_tool_name, result)
            response = self._handle_mcp_session_death_response(
                runtime,
                response,
                evict_client=False,
            )
            if will_continue_work is False:
                response["auto_sleep_ok"] = True
            return response
        except Exception as exc:
            logger.error("Failed to execute isolated MCP tool %s: %s", tool_name, exc)
            response = {"status": "error", "message": str(exc)}
            return self._handle_mcp_session_death_response(
                runtime,
                response,
                evict_client=False,
            )
    
    def _adapt_tool_result(self, server_name: str, tool_name: str, result: Any):
        """Run the tool response through any registered adapters."""
        return self._result_adapters.adapt(server_name, tool_name, result)

    async def _execute_async(
        self,
        client: Client,
        tool_name: str,
        params: Dict[str, Any],
        *,
        timeout_seconds: float,
    ):
        """Execute a tool asynchronously."""
        async with client:
            # Timeout must be resolved before the async call to avoid sync ORM access in the event loop.
            try:
                return await asyncio.wait_for(
                    client.call_tool(tool_name, params),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                raise asyncio.TimeoutError(
                    f"MCP tool call timed out after {timeout_seconds}s"
                ) from exc
    
    def cleanup(self):
        """Clean up resources."""
        # Attempt to close per-agent Pipedream clients
        for c in self._pd_agent_clients.values():
            try:
                c.close()
            except Exception:
                pass
        self._pd_agent_clients.clear()
        self._discard_scoped_stdio_proxy_clients("")
        self._server_cache.clear()
        self._clients.clear()
        self._tools_cache.clear()
        self._tool_cache_fingerprints.clear()
        self._last_refresh_marker = None
        if self._loop and not self._loop.is_closed():
            self._loop.close()
        self._loop = None
        self._initialized = False

    def _resolve_tool_info(self, tool_name: str) -> Optional[MCPToolInfo]:
        """Resolve tool metadata from runtimes already loaded for this process."""

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

        runtime = next((r for r in self._server_cache.values() if r.name == self.PIPEDREAM_RUNTIME_NAME), None)
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

    def is_tool_blacklisted(self, tool_name: str) -> bool:
        """Expose blacklist checks for external managers."""
        return self._is_tool_blacklisted(tool_name)

    def get_pipedream_access_token(self) -> Optional[str]:
        """Expose the shared Pipedream token flow to other services."""
        return self._get_pipedream_access_token()

    def _get_pipedream_access_token(self) -> Optional[str]:
        """Acquire or refresh the Pipedream OAuth access token (cached)."""
        try:
            # Reuse cached token if valid for at least 2 minutes
            if self._pd_access_token and self._pd_token_expiry and datetime.now(UTC) < (self._pd_token_expiry - timedelta(minutes=2)):
                return self._pd_access_token

            client_id = getattr(settings, "PIPEDREAM_CLIENT_ID", "")
            client_secret = getattr(settings, "PIPEDREAM_CLIENT_SECRET", "")
            if not client_id or not client_secret:
                if not self._pd_missing_credentials_logged:
                    logger.warning(
                        "Pipedream MCP credentials missing; set PIPEDREAM_CLIENT_ID and PIPEDREAM_CLIENT_SECRET to enable remote tools."
                    )
                    self._pd_missing_credentials_logged = True
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
            if self._pd_missing_credentials_logged:
                self._pd_missing_credentials_logged = False
            return access_token
        except Exception as e:
            logger.error(f"Failed to obtain Pipedream access token: {e}")
            return None

    def _pd_build_headers(self, app_slug: Optional[str], external_user_id: str, conversation_id: str) -> Dict[str, str]:
        token = self._get_pipedream_access_token()
        if not token:
            raise RuntimeError(
                "Pipedream access token unavailable; set PIPEDREAM_CLIENT_ID/PIPEDREAM_CLIENT_SECRET and try again."
            )
        headers: Dict[str, str] = {
            "Authorization": f"Bearer {token}",
            "x-pd-project-id": getattr(settings, "PIPEDREAM_PROJECT_ID", ""),
            "x-pd-environment": getattr(settings, "PIPEDREAM_ENVIRONMENT", "development"),
            "x-pd-external-user-id": external_user_id,
            "x-pd-conversation-id": conversation_id,
            "x-pd-app-discovery": "true",
            "x-pd-tool-mode": "tools-only",
        }
        if app_slug:
            headers["x-pd-app-slug"] = app_slug
        return headers

    def _pd_parse_tool(self, tool_name: str) -> Optional[str]:
        """Infer app slug for a Pipedream action tool.

        Expected names look like '<app>-<action>', e.g., 'google_sheets-add-single-row'.
        """
        app = tool_name.split("-", 1)[0] if "-" in tool_name else None
        return app or None

    def _pd_app_slug_for_tool_call(self, tool_name: str, params: Dict[str, Any]) -> Optional[str]:
        app_slug = self._pd_parse_tool(tool_name)
        if app_slug:
            return app_slug

        if tool_name not in self.PIPEDREAM_COMPONENT_OPTION_TOOLS or not isinstance(params, dict):
            return None

        component_key = str(params.get("componentKey") or params.get("component_key") or "").strip()
        return self._pd_parse_tool(component_key)

    def _get_pipedream_agent_client(self, agent: PersistentAgent, app_slug: Optional[str]) -> Client:
        """Get or create a Pipedream client for an agent/app pair."""
        agent_key = str(agent.id)
        cache_key = f"{agent_key}:{app_slug or ''}"
        if cache_key in self._pd_agent_clients:
            client = self._pd_agent_clients[cache_key]
            # Ensure Authorization header is current
            if hasattr(client, "transport") and getattr(client.transport, "headers", None) is not None:
                token = self._get_pipedream_access_token()
                if not token:
                    raise RuntimeError(
                        "Pipedream access token unavailable; set PIPEDREAM_CLIENT_ID/PIPEDREAM_CLIENT_SECRET and try again."
                    )
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
                if srv.name == self.PIPEDREAM_RUNTIME_NAME and srv.config_id in accessible_ids
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


def execute_mcp_tool(
    agent: PersistentAgent,
    tool_name: str,
    params: Dict[str, Any],
    *,
    force_local: bool = False,
    tool_info: Optional[MCPToolInfo] = None,
) -> Dict[str, Any]:
    """Execute any enabled MCP tool via the shared manager."""
    tool_info = tool_info or _mcp_manager.prepare_tool_for_agent(
        agent,
        tool_name,
        require_enabled=True,
    )
    if tool_info is None:
        return {"status": "error", "message": f"Unknown MCP tool: {tool_name}"}
    return _mcp_manager.execute_mcp_tool(
        agent,
        tool_info.full_name,
        params,
        force_local=force_local,
        tool_info=tool_info,
    )


def execute_mcp_tool_isolated(
    agent: PersistentAgent,
    tool_name: str,
    params: Dict[str, Any],
    *,
    tool_info: Optional[MCPToolInfo] = None,
) -> Dict[str, Any]:
    """Execute any enabled MCP tool without shared runtime state."""
    tool_info = tool_info or _mcp_manager.prepare_tool_for_agent(
        agent,
        tool_name,
        require_enabled=True,
    )
    if tool_info is None:
        return {"status": "error", "message": f"Unknown MCP tool: {tool_name}"}
    return _mcp_manager.execute_mcp_tool_isolated(
        agent,
        tool_info.full_name,
        params,
        tool_info=tool_info,
    )


def get_mcp_manager() -> MCPToolManager:
    """Get the global MCP tool manager instance."""
    return _mcp_manager


def get_pipedream_access_token() -> Optional[str]:
    """Return the shared Pipedream API token, if configured."""
    return _mcp_manager.get_pipedream_access_token()
