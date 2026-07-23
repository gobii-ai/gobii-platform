"""Unit tests for MCP tool management functionality."""

import asyncio
import atexit
import json
import os
import uuid
from datetime import datetime, timedelta, UTC
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock

import httpx
from django.test import TestCase, tag, override_settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from api.models import (
    AgentFsNode,
    PersistentAgent,
    BrowserUseAgent,
    ProxyServer,
    GlobalAgentSkill,
    GlobalAgentSkillCustomTool,
    PersistentAgentCustomTool,
    PersistentAgentEnabledTool,
    PersistentAgentSkill,
    PersistentAgentSystemSkillState,
    MCPServerConfig,
    MCPServerOAuthCredential,
    PersistentAgentMCPServer,
    PromptConfig,
    ToolConfig,
    UserBilling,
    LLMProvider,
    ImageGenerationModelEndpoint,
    ImageGenerationLLMTier,
    ImageGenerationTierEndpoint,
)
from api.agent.core.llm_config import AgentLLMTier
from tests.utils.llm_seed import get_intelligence_tier
from constants.plans import PlanNames
from api.agent.tools.mcp_manager import (
    MCPToolManager,
    MCPToolInfo,
    MCPServerRuntime,
    SandboxToolCacheContext,
    get_mcp_manager,
    execute_mcp_tool,
)
from api.agent.tools.tool_manager import (
    enable_tools,
    ensure_default_tools_enabled,
    get_enabled_tool_definitions,
    execute_enabled_tool,
    mark_tool_enabled_without_discovery,
)
from api.agent.tools.static_tools import get_static_tool_names
from api.agent.tools.custom_tool_names import CREATE_CUSTOM_TOOL_NAME, CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY
from api.agent.tools.tool_runtime import execute_runtime_tool_call
from api.agent.tools.search_tools import (
    execute_search_tools,
    get_search_tools_tool,
    search_tools,
)
from api.agent.system_skills.registry import SystemSkillDefinition
from api.agent.system_skills.image_generation import IMAGE_GENERATION_SYSTEM_SKILL_KEY
from api.services.prompt_settings import invalidate_prompt_settings_cache
from api.services.mcp_oauth import MCPOAuthResult, MCPOAuthStatus
from api.services.tool_settings import invalidate_tool_settings_cache
from tests.utils.llm_seed import seed_persistent_basic
from util.analytics import AnalyticsEvent


def _valid_custom_tool_source() -> bytes:
    return (
        b"from _gobii_ctx import main\n\n"
        b"def run(params, ctx):\n"
        b"    return {'ok': True}\n\n"
        b"if __name__ == '__main__':\n"
        b"    main(run)\n"
    )


def _default_fake_run_completion(*args, **kwargs):
    """Default stub to prevent real LLM calls during tests."""
    user_content = ""
    messages = kwargs.get("messages") or []
    if len(messages) >= 2:
        user_message = messages[1] or {}
        if isinstance(user_message, dict):
            user_content = user_message.get("content") or ""

    tool_names: list[str] = []
    in_tool_section = False
    for raw_line in user_content.splitlines():
        line = raw_line.strip()
        if line == "Available tools:":
            in_tool_section = True
            continue
        if line in {
            "Available agent skills:",
            "Available global skills:",
            "Available system skills:",
            "Available Pipedream apps:",
        }:
            in_tool_section = False
            continue
        if not in_tool_section or not line.startswith("- "):
            continue
        trimmed = line[2:].split("|", 1)[0].split(":", 1)[0].strip()
        if trimmed:
            tool_names.append(trimmed)

    message = MagicMock()
    if tool_names:
        message.content = "No MCP tools available.\n" + f"Enabled: {', '.join(tool_names)}"
        message.tool_calls = [
            {
                "type": "function",
                "function": {
                    "name": "enable_tools",
                    "arguments": json.dumps({"tool_names": tool_names}),
                },
            }
        ]
    else:
        message.content = "No MCP tools available."
        message.tool_calls = []
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


_RUN_COMPLETION_PATCHER = patch(
    "api.agent.tools.search_tools.run_completion",
    side_effect=_default_fake_run_completion,
)
RUN_COMPLETION_GUARD = _RUN_COMPLETION_PATCHER.start()
atexit.register(_RUN_COMPLETION_PATCHER.stop)


class _DummyMCPClient:
    """Stub client to prevent real MCP subprocess or HTTP calls in tests."""

    def __init__(self, transport):
        self.transport = transport

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def list_tools(self):
        # Return no tools by default; tests can patch manager caches as needed.
        return []

    def close(self):
        pass


class _DummyStdioTransport:
    """Stub transport to prevent subprocess execution."""

    def __init__(self, command, args=None, env=None):
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.headers: dict[str, str] = {}


class _DummyStreamableHttpTransport:
    """Stub transport to avoid real HTTP connections during discovery."""

    def __init__(self, url, headers=None, httpx_client_factory=None):
        self.url = url
        self.headers = dict(headers or {})
        self.httpx_client_factory = httpx_client_factory


_MCP_CLIENT_PATCHER = patch(
    "api.agent.tools.mcp_manager.Client",
    new=_DummyMCPClient,
)
_MCP_CLIENT_PATCHER.start()
atexit.register(_MCP_CLIENT_PATCHER.stop)

_STDIO_TRANSPORT_PATCHER = patch(
    "fastmcp.client.transports.StdioTransport",
    new=_DummyStdioTransport,
)
_STDIO_TRANSPORT_PATCHER.start()
atexit.register(_STDIO_TRANSPORT_PATCHER.stop)

_STREAM_TRANSPORT_PATCHER = patch(
    "fastmcp.client.transports.StreamableHttpTransport",
    new=_DummyStreamableHttpTransport,
)
_STREAM_TRANSPORT_PATCHER.start()
atexit.register(_STREAM_TRANSPORT_PATCHER.stop)


def create_test_browser_agent(user):
    """Helper to create BrowserUseAgent without triggering proxy selection."""
    with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
        return BrowserUseAgent.objects.create(user=user, name="test-browser-agent")


@tag("batch_mcp_tools")
class MCPToolInfoTests(TestCase):
    """Test MCPToolInfo data class."""
    
    def test_to_search_dict(self):
        """Test converting tool info to search dictionary."""
        tool_info = MCPToolInfo(
            "cfg",
            full_name="mcp_brightdata_scrape",
            server_name="brightdata",
            tool_name="scrape",
            description="Scrape web pages",
            parameters={"type": "object", "properties": {"url": {"type": "string"}}}
        )
        
        search_dict = tool_info.to_search_dict()
        
        self.assertEqual(search_dict["name"], "mcp_brightdata_scrape")
        self.assertEqual(search_dict["server"], "brightdata")
        self.assertEqual(search_dict["tool"], "scrape")
        self.assertEqual(search_dict["description"], "Scrape web pages")
        self.assertIn("url", search_dict["parameters"])


@tag("batch_mcp_tools")
class MCPToolManagerTests(TestCase):
    """Test MCPToolManager functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.manager = MCPToolManager()
        self.manager._initialized = False
        self.manager._clients.clear()
        self.manager._tools_cache.clear()
        self.server_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name=f"test-platform-{uuid.uuid4().hex[:8]}",
            display_name="Test Platform Server",
            description="",
            command="npx",
            command_args=[],
        )
        self.config_id = str(self.server_config.id)
        self.server_name = self.server_config.name
    
    def _setup_http_tool(self) -> PersistentAgent:
        """Register a simple HTTP MCP server and enable it for a new agent."""
        User = get_user_model()
        user = User.objects.create_user(username=f'http-{uuid.uuid4().hex[:8]}@example.com')
        browser_agent = create_test_browser_agent(user)
        agent = PersistentAgent.objects.create(
            user=user,
            name=f"http-agent-{uuid.uuid4().hex[:6]}",
            charter="HTTP",
            browser_use_agent=browser_agent,
        )

        http_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name=f"http-server-{uuid.uuid4().hex[:8]}",
            display_name="HTTP Server",
            description="",
            url="https://example.com/mcp",
        )
        runtime = MCPServerRuntime(
            config_id=str(http_config.id),
            name=http_config.name,
            display_name=http_config.display_name,
            description=http_config.description,
            command=None,
            args=[],
            url=http_config.url,
            auth_method=http_config.auth_method,
            env=http_config.environment or {},
            headers=http_config.headers or {},
            prefetch_apps=[],
            scope=http_config.scope,
            organization_id=None,
            user_id=None,
            updated_at=http_config.updated_at,
        )
        tool = MCPToolInfo(
            runtime.config_id,
            "http_tool",
            runtime.name,
            "http_tool",
            "HTTP tool",
            {},
        )

        self.manager._initialized = True
        self.manager._server_cache = {runtime.config_id: runtime}
        self.manager._clients = {runtime.config_id: MagicMock()}
        self.manager._tools_cache = {runtime.config_id: [tool]}

        PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name="http_tool",
            tool_server=runtime.name,
            tool_name=tool.tool_name,
            server_config=http_config,
        )

        return agent

    def _setup_stdio_tool(self) -> tuple[PersistentAgent, MCPServerRuntime, MCPToolInfo]:
        """Register a simple stdio MCP server and enable it for a new agent."""
        User = get_user_model()
        user = User.objects.create_user(username=f"stdio-{uuid.uuid4().hex[:8]}@example.com")
        browser_agent = create_test_browser_agent(user)
        agent = PersistentAgent.objects.create(
            user=user,
            name=f"stdio-agent-{uuid.uuid4().hex[:6]}",
            charter="STDIO",
            browser_use_agent=browser_agent,
        )

        stdio_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name=f"stdio-server-{uuid.uuid4().hex[:8]}",
            display_name="STDIO Server",
            description="",
            command="npx",
            command_args=["-y", "@dummy/server"],
        )
        runtime = MCPServerRuntime(
            config_id=str(stdio_config.id),
            name=stdio_config.name,
            display_name=stdio_config.display_name,
            description=stdio_config.description,
            command=stdio_config.command,
            args=list(stdio_config.command_args or []),
            url=None,
            auth_method=stdio_config.auth_method,
            env=stdio_config.environment or {},
            headers=stdio_config.headers or {},
            prefetch_apps=[],
            scope=stdio_config.scope,
            organization_id=None,
            user_id=None,
            updated_at=stdio_config.updated_at,
        )
        tool = MCPToolInfo(
            runtime.config_id,
            "stdio_tool",
            runtime.name,
            "stdio_tool",
            "STDIO tool",
            {},
        )

        self.manager._initialized = True
        self.manager._server_cache = {runtime.config_id: runtime}
        self.manager._clients = {runtime.config_id: MagicMock(name="shared-stdio-client")}
        self.manager._tools_cache = {runtime.config_id: [tool]}

        PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name="stdio_tool",
            tool_server=runtime.name,
            tool_name=tool.tool_name,
            server_config=stdio_config,
        )

        return agent, runtime, tool

    def test_build_stdio_proxy_env_rewrites_socks5_to_http_vars(self):
        proxy_env = self.manager._build_stdio_proxy_env("socks5://user:pass@proxy.internal:1080")

        expected_proxy_url = "http://user:pass@proxy.internal:1080"
        self.assertEqual(
            proxy_env,
            {
                "HTTP_PROXY": expected_proxy_url,
                "HTTPS_PROXY": expected_proxy_url,
                "ALL_PROXY": expected_proxy_url,
                "http_proxy": expected_proxy_url,
                "https_proxy": expected_proxy_url,
                "all_proxy": expected_proxy_url,
            },
        )

    @override_settings(ENABLE_PROXY_ROUTING=True)
    def test_select_agent_proxy_url_uses_browser_preference(self):
        """Agents with dedicated IPs should expose them to the MCP proxy selector."""
        User = get_user_model()
        user = User.objects.create_user(username='proxy-agent@example.com')
        browser_agent = create_test_browser_agent(user)
        proxy = ProxyServer.objects.create(
            name="Dedicated",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="dedicated.proxy",
            port=8080,
            username="user",
            password="pass",
            is_active=True,
        )
        browser_agent.preferred_proxy = proxy
        browser_agent.save(update_fields=["preferred_proxy"])
        agent = PersistentAgent.objects.create(
            user=user,
            name="proxy-agent",
            charter="Proxy",
            browser_use_agent=browser_agent,
        )

        with patch('api.agent.tools.mcp_manager.select_proxy_for_persistent_agent') as mock_select:
            def _side_effect(agent_obj, *args, **kwargs):
                self.assertEqual(agent_obj.preferred_proxy, proxy)
                return proxy

            mock_select.side_effect = _side_effect
            proxy_url, error = self.manager._select_agent_proxy_url(agent)

        mock_select.assert_called_once()
        self.assertEqual(proxy_url, proxy.proxy_url)
        self.assertIsNone(error)

    @override_settings(ENABLE_PROXY_ROUTING=True)
    def test_select_agent_proxy_url_supports_socks5_proxy(self):
        user = get_user_model().objects.create_user(username="socks-agent@example.com")
        browser_agent = create_test_browser_agent(user)
        proxy = ProxyServer.objects.create(
            name="SOCKS Dedicated",
            proxy_type=ProxyServer.ProxyType.SOCKS5,
            host="dedicated.proxy",
            port=1080,
            is_active=True,
        )
        browser_agent.preferred_proxy = proxy
        browser_agent.save(update_fields=["preferred_proxy"])
        agent = PersistentAgent.objects.create(
            user=user,
            name="socks-agent",
            charter="Proxy",
            browser_use_agent=browser_agent,
        )

        with patch("api.agent.tools.mcp_manager.select_proxy_for_persistent_agent", return_value=proxy):
            proxy_url, error = self.manager._select_agent_proxy_url(agent)

        self.assertEqual(proxy_url, proxy.proxy_url)
        self.assertIsNone(error)

    def test_httpx_client_factory_forwards_socks5_proxy(self):
        factory = self.manager._build_httpx_client_factory()

        with patch("api.agent.tools.mcp_manager.httpx.AsyncClient") as mock_async_client:
            from api.agent.tools.mcp_manager import _use_mcp_proxy

            with _use_mcp_proxy("socks5://proxy.internal:1080"):
                factory()

        self.assertEqual(mock_async_client.call_args.kwargs["proxy"], "socks5://proxy.internal:1080")

    def test_register_stdio_server_injects_proxy_env(self):
        runtime = MCPServerRuntime(
            config_id=str(uuid.uuid4()),
            name="stdio-discovery",
            display_name="STDIO Discovery",
            description="",
            command="npx",
            args=["-y", "@dummy/server"],
            url=None,
            auth_method=MCPServerConfig.AuthMethod.NONE,
            env={"API_TOKEN": "token-123"},
            headers={},
            prefetch_apps=[],
            scope=MCPServerConfig.Scope.PLATFORM,
            organization_id=None,
            user_id=None,
            updated_at=datetime.now(UTC),
        )
        manager = MCPToolManager()
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)

        async def _fake_fetch(*args, **kwargs):
            return []

        with patch.object(manager, "_ensure_event_loop", return_value=loop), patch.object(
            manager,
            "_select_discovery_proxy_url",
            return_value="socks5://user:pass@proxy.internal:1080",
        ), patch.object(manager, "_fetch_server_tools", new=_fake_fetch):
            manager._register_server(runtime, force_local=True)

        transport = manager._clients[runtime.config_id].transport
        self.assertEqual(transport.env["API_TOKEN"], "token-123")
        self.assertEqual(transport.env["HTTP_PROXY"], "http://user:pass@proxy.internal:1080")
        self.assertEqual(transport.env["HTTPS_PROXY"], "http://user:pass@proxy.internal:1080")
        self.assertEqual(transport.env["ALL_PROXY"], "http://user:pass@proxy.internal:1080")

    def test_register_http_server_includes_oauth_header(self):
        runtime = MCPServerRuntime(
            config_id=str(uuid.uuid4()),
            name="notion",
            display_name="Notion",
            description="",
            command=None,
            args=[],
            url="https://mcp.example.com/mcp",
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
            env={},
            headers={},
            oauth_access_token="token-123",
            oauth_token_type="Bearer",
            oauth_expires_at=datetime.now(UTC) + timedelta(hours=1),
            oauth_updated_at=datetime.now(UTC),
            prefetch_apps=[],
            scope=MCPServerConfig.Scope.USER,
            organization_id=None,
            user_id=str(uuid.uuid4()),
            updated_at=datetime.now(UTC),
        )
        manager = MCPToolManager()
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)

        async def _fake_fetch(*args, **kwargs):
            return []

        with patch("api.agent.tools.mcp_manager.sandbox_compute_enabled_for_agent", return_value=False), \
                patch.object(manager, "_ensure_event_loop", return_value=loop), \
                patch.object(manager, "_select_discovery_proxy_url", return_value=None), \
                patch.object(manager, "_fetch_server_tools", new=_fake_fetch):
            manager._register_server(runtime, force_local=True)
        transport = manager._clients[runtime.config_id].transport
        self.assertEqual(transport.headers.get("Authorization"), "Bearer token-123")

    @override_settings(SANDBOX_COMPUTE_LOCAL_FALLBACK_MCP=False)
    @patch("api.agent.tools.mcp_manager.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.agent.tools.mcp_manager.sandbox_compute_enabled", return_value=True)
    @patch("api.agent.tools.mcp_manager.schedule_mcp_tool_discovery")
    @patch("api.agent.tools.mcp_manager.get_cached_mcp_tool_definitions", return_value=None)
    def test_register_server_schedules_discovery_on_cache_miss(
        self,
        _mock_cache_get,
        mock_schedule,
        _mock_sandbox_enabled_for_agent,
        _mock_sandbox_enabled,
    ):
        agent = SimpleNamespace(id=str(uuid.uuid4()))
        runtime = MCPServerRuntime(
            config_id=str(uuid.uuid4()),
            name="cache-miss-server",
            display_name="Cache Miss Server",
            description="",
            command="npx",
            args=["-y", "@dummy/server"],
            url=None,
            auth_method=MCPServerConfig.AuthMethod.NONE,
            env={},
            headers={},
            prefetch_apps=[],
            scope=MCPServerConfig.Scope.USER,
            organization_id=None,
            user_id=str(uuid.uuid4()),
            updated_at=datetime.now(UTC),
        )

        with patch.object(self.manager, "_ensure_event_loop") as mock_loop_factory:
            self.manager._register_server(runtime, agent=agent)

        mock_schedule.assert_called_once_with(runtime.config_id, reason="cache_miss", agent=agent)
        mock_loop_factory.assert_not_called()
        self.assertNotIn(runtime.config_id, self.manager._clients)
        self.assertNotIn(runtime.config_id, self.manager._tools_cache)

    @override_settings(SANDBOX_COMPUTE_LOCAL_FALLBACK_MCP=False)
    @patch("api.agent.tools.mcp_manager.sandbox_compute_enabled", return_value=True)
    @patch("api.agent.tools.mcp_manager.schedule_mcp_tool_discovery")
    def test_register_http_server_skips_sandbox_discovery(
        self,
        mock_schedule,
        _mock_sandbox_enabled,
    ):
        runtime = MCPServerRuntime(
            config_id=str(uuid.uuid4()),
            name="http-only-server",
            display_name="HTTP Server",
            description="",
            command="",
            args=[],
            url="https://example.com/mcp",
            auth_method=MCPServerConfig.AuthMethod.NONE,
            env={},
            headers={},
            prefetch_apps=[],
            scope=MCPServerConfig.Scope.USER,
            organization_id=None,
            user_id=str(uuid.uuid4()),
            updated_at=datetime.now(UTC),
        )
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)

        async def _fake_fetch(*args, **kwargs):
            return []

        with patch.object(self.manager, "_ensure_event_loop", return_value=loop), patch.object(
            self.manager,
            "_select_discovery_proxy_url",
            return_value=None,
        ), patch.object(self.manager, "_fetch_server_tools", new=_fake_fetch):
            self.manager._register_server(runtime)

        mock_schedule.assert_not_called()
        self.assertIn(runtime.config_id, self.manager._clients)
        self.assertIn(runtime.config_id, self.manager._tools_cache)

    @override_settings(SANDBOX_COMPUTE_LOCAL_FALLBACK_MCP=False)
    @patch("api.agent.tools.mcp_manager.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.agent.tools.mcp_manager.schedule_mcp_tool_discovery")
    def test_register_server_loads_inline_discovery_cache_for_first_agent_lookup(
        self,
        mock_schedule,
        _mock_sandbox_enabled_for_agent,
    ):
        agent = SimpleNamespace(id=str(uuid.uuid4()))
        runtime = MCPServerRuntime(
            config_id=str(uuid.uuid4()),
            name="cache-warmed-server",
            display_name="Cache Warmed Server",
            description="",
            command="npx",
            args=["-y", "@dummy/server"],
            url=None,
            auth_method=MCPServerConfig.AuthMethod.NONE,
            env={},
            headers={},
            prefetch_apps=[],
            scope=MCPServerConfig.Scope.USER,
            organization_id=None,
            user_id=str(uuid.uuid4()),
            updated_at=datetime.now(UTC),
        )
        slot_key = self.manager._tool_cache_slot_key(
            runtime,
            sandbox_context=SandboxToolCacheContext(agent_cache_key=str(agent.id)),
        )

        load_attempts = {"count": 0}

        def _fake_load(*args, **kwargs):
            load_attempts["count"] += 1
            if load_attempts["count"] == 1:
                return False
            self.manager._tools_cache[slot_key] = []
            self.manager._tool_cache_fingerprints[slot_key] = "fingerprint"
            return True

        with patch.object(self.manager, "_load_cached_tools", side_effect=_fake_load), patch.object(
            self.manager,
            "_discard_client",
        ) as mock_discard_client:
            self.manager._register_server(
                runtime,
                agent=agent,
                sandbox_context=SandboxToolCacheContext(agent_cache_key=str(agent.id)),
            )

        mock_schedule.assert_called_once_with(runtime.config_id, reason="cache_miss", agent=agent)
        mock_discard_client.assert_not_called()
        self.assertEqual(load_attempts["count"], 2)
        self.assertIn(slot_key, self.manager._tools_cache)

    @patch("api.agent.tools.mcp_manager.sandbox_compute_enabled", return_value=False)
    def test_get_tools_for_agent_passes_agent_to_runtime_registration(self, _mock_sandbox_enabled):
        runtime = MCPServerRuntime(
            config_id=str(uuid.uuid4()),
            name="agent-aware-server",
            display_name="Agent-aware Server",
            description="",
            command="npx",
            args=["-y", "@dummy/server"],
            url=None,
            auth_method=MCPServerConfig.AuthMethod.NONE,
            env={},
            headers={},
            prefetch_apps=[],
            scope=MCPServerConfig.Scope.USER,
            organization_id=None,
            user_id=str(uuid.uuid4()),
            updated_at=datetime.now(UTC),
        )
        agent = SimpleNamespace(id=uuid.uuid4(), user_id=runtime.user_id)
        self.manager._initialized = True
        self.manager._server_cache = {runtime.config_id: runtime}

        observed_agents: list[Any] = []

        def _fake_register(
            _runtime,
            *,
            agent=None,
            force_local=False,
            prefer_cache=True,
            pipedream_context=None,
            sandbox_context=None,
        ):
            observed_agents.append(agent)
            self.manager._tools_cache[_runtime.config_id] = []

        with patch.object(self.manager, "_needs_refresh", return_value=False), patch(
            "api.agent.tools.mcp_manager.agent_accessible_server_configs",
            return_value=[SimpleNamespace(id=runtime.config_id)],
        ), patch.object(self.manager, "_register_server", side_effect=_fake_register):
            tools = self.manager.get_tools_for_agent(agent)

        self.assertEqual(tools, [])
        self.assertEqual(observed_agents, [agent])

    def test_get_tools_for_agent_refreshes_config_ids_and_server_names_independently(self):
        config_id = str(uuid.uuid4())
        named_config_id = str(uuid.uuid4())
        runtime_by_id = MCPServerRuntime(
            config_id=config_id,
            name="server-by-id",
            display_name="Server By ID",
            description="",
            command=None,
            args=[],
            url="https://example.com/by-id",
            auth_method=MCPServerConfig.AuthMethod.NONE,
            env={},
            headers={},
            prefetch_apps=[],
            scope=MCPServerConfig.Scope.PLATFORM,
            organization_id=None,
            user_id=None,
            updated_at=datetime.now(UTC),
        )
        runtime_by_name = MCPServerRuntime(
            config_id=named_config_id,
            name="server-by-name",
            display_name="Server By Name",
            description="",
            command=None,
            args=[],
            url="https://example.com/by-name",
            auth_method=MCPServerConfig.AuthMethod.NONE,
            env={},
            headers={},
            prefetch_apps=[],
            scope=MCPServerConfig.Scope.PLATFORM,
            organization_id=None,
            user_id=None,
            updated_at=datetime.now(UTC),
        )
        tool_by_id = MCPToolInfo(
            config_id,
            "mcp_server-by-id_tool",
            "server-by-id",
            "tool",
            "By ID",
            {},
        )
        tool_by_name = MCPToolInfo(
            named_config_id,
            "mcp_server-by-name_tool",
            "server-by-name",
            "tool",
            "By Name",
            {},
        )
        self.manager._initialized = True
        self.manager._tools_cache = {
            config_id: [tool_by_id],
            named_config_id: [tool_by_name],
        }

        def apply_subset(_configs, _stale_ids, *, update_global_marker):
            self.assertFalse(update_global_marker)
            self.manager._server_cache[config_id] = runtime_by_id
            self.manager._server_cache[named_config_id] = runtime_by_name
            return True

        agent = SimpleNamespace(id=uuid.uuid4())
        configs = [
            SimpleNamespace(id=config_id, name=runtime_by_id.name),
            SimpleNamespace(id=named_config_id, name=runtime_by_name.name),
        ]
        with patch(
            "api.agent.tools.mcp_manager.agent_accessible_server_configs",
            return_value=configs,
        ), patch.object(
            self.manager,
            "_apply_server_subset",
            side_effect=apply_subset,
        ) as mock_apply_subset, patch.object(
            self.manager,
            "_ensure_runtime_registered",
            return_value=True,
        ):
            tools = self.manager.get_tools_for_agent(
                agent,
                allowed_config_ids={config_id},
                allowed_server_names={runtime_by_name.name},
            )

        mock_apply_subset.assert_called_once_with(
            configs,
            {config_id, named_config_id},
            update_global_marker=False,
        )
        self.assertEqual(
            {tool.full_name for tool in tools},
            {tool_by_id.full_name, tool_by_name.full_name},
        )

    @patch("api.agent.tools.mcp_manager.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.agent.tools.mcp_manager.SandboxComputeService")
    def test_execute_mcp_tool_sandbox_path_does_not_require_local_registration(
        self,
        mock_service_cls,
        _mock_sandbox_enabled_for_agent,
    ):
        agent = SimpleNamespace(id=uuid.uuid4(), organization=None, user=None)
        runtime = MCPServerRuntime(
            config_id=str(uuid.uuid4()),
            name="sandbox-exec-server",
            display_name="Sandbox Exec Server",
            description="",
            command="npx",
            args=["-y", "@dummy/server"],
            url=None,
            auth_method=MCPServerConfig.AuthMethod.NONE,
            env={},
            headers={},
            prefetch_apps=[],
            scope=MCPServerConfig.Scope.USER,
            organization_id=None,
            user_id=str(uuid.uuid4()),
            updated_at=datetime.now(UTC),
        )
        tool = MCPToolInfo(
            config_id=runtime.config_id,
            full_name=f"mcp_{runtime.name}_ping",
            server_name=runtime.name,
            tool_name="ping",
            description="Ping",
            parameters={"type": "object", "properties": {}},
        )
        self.manager._initialized = True
        self.manager._server_cache = {runtime.config_id: runtime}
        self.manager._tools_cache = {runtime.config_id: [tool]}

        mock_service = MagicMock()
        mock_service.mcp_request.return_value = {"status": "ok", "result": {"pong": True}}
        mock_service_cls.return_value = mock_service
        enabled_qs = MagicMock()
        enabled_qs.exists.return_value = True
        usage_row = SimpleNamespace(last_used_at=None, usage_count=0, save=MagicMock())

        with patch(
            "api.agent.tools.mcp_manager.PersistentAgentEnabledTool.objects.filter",
            return_value=enabled_qs,
        ), patch(
            "api.agent.tools.mcp_manager.PersistentAgentEnabledTool.objects.get_or_create",
            return_value=(usage_row, False),
        ), patch.object(
            self.manager,
            "_ensure_runtime_registered",
            side_effect=AssertionError("local registration should not run before sandbox dispatch"),
        ), patch.object(
            self.manager,
            "_get_scoped_stdio_proxy_client",
            side_effect=AssertionError("stdio proxy client should not be created for sandbox-routed executions"),
        ):
            result = self.manager.execute_mcp_tool(agent, tool.full_name, {})

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("result"), {"pong": True})
        mock_service.mcp_request.assert_called_once()

    @patch("api.agent.tools.mcp_manager.sandbox_compute_enabled_for_agent", return_value=True)
    def test_execute_http_mcp_tool_skips_sandbox_routing(self, _mock_sandbox_enabled_for_agent):
        agent = SimpleNamespace(id=uuid.uuid4(), organization=None, user=None)
        runtime = MCPServerRuntime(
            config_id=str(uuid.uuid4()),
            name="http-exec-server",
            display_name="HTTP Exec Server",
            description="",
            command="",
            args=[],
            url="https://example.com/mcp",
            auth_method=MCPServerConfig.AuthMethod.NONE,
            env={},
            headers={},
            prefetch_apps=[],
            scope=MCPServerConfig.Scope.USER,
            organization_id=None,
            user_id=str(uuid.uuid4()),
            updated_at=datetime.now(UTC),
        )
        tool = MCPToolInfo(
            config_id=runtime.config_id,
            full_name=f"mcp_{runtime.name}_ping",
            server_name=runtime.name,
            tool_name="ping",
            description="Ping",
            parameters={"type": "object", "properties": {}},
        )
        self.manager._initialized = True
        self.manager._server_cache = {runtime.config_id: runtime}
        self.manager._tools_cache = {runtime.config_id: [tool]}
        self.manager._clients = {runtime.config_id: MagicMock()}

        enabled_qs = MagicMock()
        enabled_qs.exists.return_value = True
        usage_row = SimpleNamespace(last_used_at=None, usage_count=0, save=MagicMock())
        local_result = SimpleNamespace(data={"http": True}, content=[], is_error=False)
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)

        with patch(
            "api.agent.tools.mcp_manager.PersistentAgentEnabledTool.objects.filter",
            return_value=enabled_qs,
        ), patch(
            "api.agent.tools.mcp_manager.PersistentAgentEnabledTool.objects.get_or_create",
            return_value=(usage_row, False),
        ), patch.object(
            self.manager,
            "_ensure_runtime_registered",
            return_value=True,
        ), patch.object(
            self.manager,
            "_select_agent_proxy_url",
            return_value=(None, None),
        ), patch.object(
            self.manager,
            "_dispatch_sandbox_mcp_request",
            side_effect=AssertionError("HTTP MCP should not route through sandbox"),
        ), patch.object(
            self.manager,
            "_ensure_event_loop",
            return_value=loop,
        ), patch.object(
            self.manager,
            "_execute_async",
            new=AsyncMock(return_value=local_result),
        ), patch.object(
            self.manager,
            "_adapt_tool_result",
            return_value=local_result,
        ):
            result = self.manager.execute_mcp_tool(agent, tool.full_name, {})

        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("result"), {"http": True})

    @override_settings(SANDBOX_COMPUTE_LOCAL_FALLBACK_MCP=True)
    @patch("api.agent.tools.mcp_manager.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.agent.tools.mcp_manager.SandboxComputeService")
    def test_execute_mcp_tool_sandbox_unsupported_falls_back_to_local(
        self,
        mock_service_cls,
        _mock_sandbox_enabled_for_agent,
    ):
        agent = SimpleNamespace(id=uuid.uuid4(), organization=None, user=None)
        runtime = MCPServerRuntime(
            config_id=str(uuid.uuid4()),
            name="sandbox-fallback-server",
            display_name="Sandbox Fallback Server",
            description="",
            command="npx",
            args=["-y", "@dummy/server"],
            url=None,
            auth_method=MCPServerConfig.AuthMethod.NONE,
            env={},
            headers={},
            prefetch_apps=[],
            scope=MCPServerConfig.Scope.USER,
            organization_id=None,
            user_id=str(uuid.uuid4()),
            updated_at=datetime.now(UTC),
        )
        tool = MCPToolInfo(
            config_id=runtime.config_id,
            full_name=f"mcp_{runtime.name}_ping",
            server_name=runtime.name,
            tool_name="ping",
            description="Ping",
            parameters={"type": "object", "properties": {}},
        )
        self.manager._initialized = True
        self.manager._server_cache = {runtime.config_id: runtime}
        self.manager._tools_cache = {runtime.config_id: [tool]}
        self.manager._clients = {runtime.config_id: MagicMock()}

        mock_service = MagicMock()
        mock_service.mcp_request.return_value = {
            "status": "error",
            "error_code": "sandbox_unsupported_mcp",
            "message": "unsupported",
        }
        mock_service_cls.return_value = mock_service

        enabled_qs = MagicMock()
        enabled_qs.exists.return_value = True
        usage_row = SimpleNamespace(last_used_at=None, usage_count=0, save=MagicMock())
        local_result = SimpleNamespace(data={"local": True}, content=[], is_error=False)
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)

        with patch(
            "api.agent.tools.mcp_manager.PersistentAgentEnabledTool.objects.filter",
            return_value=enabled_qs,
        ), patch(
            "api.agent.tools.mcp_manager.PersistentAgentEnabledTool.objects.get_or_create",
            return_value=(usage_row, False),
        ), patch.object(
            self.manager,
            "_ensure_runtime_registered",
            return_value=True,
        ) as mock_ensure_registered, patch.object(
            self.manager,
            "_ensure_event_loop",
            return_value=loop,
        ), patch.object(
            self.manager,
            "_select_agent_proxy_url",
            return_value=(None, None),
        ), patch.object(
            self.manager,
            "_execute_async",
            new=AsyncMock(return_value=local_result),
        ), patch.object(
            self.manager,
            "_adapt_tool_result",
            return_value=local_result,
        ):
            result = self.manager.execute_mcp_tool(agent, tool.full_name, {})

        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("result"), {"local": True})
        mock_service.mcp_request.assert_called_once()
        mock_ensure_registered.assert_called_once_with(
            runtime,
            agent=agent,
            force_local=True,
            require_client=True,
        )

    @tag("batch_mcp_tools")
    @patch("api.services.mcp_oauth.requests.post")
    def test_runtime_oauth_preparation_refreshes_expired_token(self, mock_post):
        with patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery"):
            config = MCPServerConfig.objects.create(
                scope=MCPServerConfig.Scope.USER,
                user=get_user_model().objects.create_user(
                    username="oauth-user", email="oauth@example.com"
                ),
                name=f"notion-{uuid.uuid4().hex[:8]}",
                display_name="Notion",
                url="https://notion.example.com/mcp",
                auth_method=MCPServerConfig.AuthMethod.OAUTH2,
            )

            credential = MCPServerOAuthCredential.objects.create(
                server_config=config,
                user=config.user,
                client_id="client-123",
            )
            credential.client_secret = "secret-xyz"
            credential.access_token = "expired-token"
            credential.refresh_token = "refresh-123"
            credential.token_type = "Bearer"
            credential.expires_at = timezone.now() - timedelta(minutes=5)
            credential.metadata = {"token_endpoint": "https://notion.example.com/oauth/token"}
            credential.save()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "read:pages",
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        manager = MCPToolManager()
        with patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery"):
            runtime = manager._build_runtime_from_config(config)

        mock_post.assert_not_called()
        with patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery"), patch(
            "api.models.invalidate_mcp_tool_cache"
        ) as invalidate_catalog:
            runtime, auth_error = manager._ensure_runtime_oauth(runtime)

        mock_post.assert_called_once_with(
            "https://notion.example.com/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": "refresh-123",
                "client_id": "client-123",
                "client_secret": "secret-xyz",
            },
            timeout=15,
        )

        self.assertIsNone(auth_error)
        invalidate_catalog.assert_not_called()
        self.assertEqual(runtime.oauth_access_token, "new-access")
        self.assertEqual(runtime.oauth_token_type, "Bearer")
        self.assertGreater(runtime.oauth_expires_at, timezone.now())

        credential.refresh_from_db()
        self.assertEqual(credential.access_token, "new-access")
        self.assertEqual(credential.refresh_token, "new-refresh")
        self.assertEqual(credential.token_type, "Bearer")
        self.assertIn("last_refresh_response", credential.metadata)

    @tag("batch_mcp_tools")
    def test_runtime_oauth_uses_unexpired_token_during_database_failure(self):
        runtime = MCPServerRuntime(
            config_id=self.config_id,
            name=self.server_name,
            display_name="OAuth server",
            description="",
            command=None,
            args=[],
            url="https://example.com/mcp",
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
            env={},
            headers={},
            prefetch_apps=[],
            scope=MCPServerConfig.Scope.PLATFORM,
            organization_id=None,
            user_id=None,
            updated_at=timezone.now(),
            oauth_access_token="still-valid",
            oauth_expires_at=timezone.now() + timedelta(minutes=1),
        )
        unavailable = MCPOAuthResult(
            status=MCPOAuthStatus.TEMPORARILY_UNAVAILABLE,
            credential=None,
        )

        with patch(
            "api.agent.tools.mcp_manager.ensure_mcp_oauth_credential",
            return_value=unavailable,
        ):
            prepared, error = self.manager._ensure_runtime_oauth(runtime)

        self.assertIs(prepared, runtime)
        self.assertIsNone(error)

    @tag("batch_mcp_tools")
    def test_runtime_oauth_rejects_expired_token_during_database_failure(self):
        runtime = MCPServerRuntime(
            config_id=self.config_id,
            name=self.server_name,
            display_name="OAuth server",
            description="",
            command=None,
            args=[],
            url="https://example.com/mcp",
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
            env={},
            headers={},
            prefetch_apps=[],
            scope=MCPServerConfig.Scope.PLATFORM,
            organization_id=None,
            user_id=None,
            updated_at=timezone.now(),
            oauth_access_token="expired",
            oauth_expires_at=timezone.now() - timedelta(minutes=1),
        )
        unavailable = MCPOAuthResult(
            status=MCPOAuthStatus.TEMPORARILY_UNAVAILABLE,
            credential=None,
        )

        with patch(
            "api.agent.tools.mcp_manager.ensure_mcp_oauth_credential",
            return_value=unavailable,
        ):
            _prepared, error = self.manager._ensure_runtime_oauth(runtime)

        self.assertEqual(error["status"], "error")
        self.assertTrue(error["retryable"])

    @tag("batch_mcp_tools")
    @patch("api.services.mcp_oauth.requests.post")
    def test_build_runtime_skips_refresh_when_token_valid(self, mock_post):
        with patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery"):
            user = get_user_model().objects.create_user(
                username="fresh-user",
                email="fresh@example.com",
            )
            config = MCPServerConfig.objects.create(
                scope=MCPServerConfig.Scope.USER,
                user=user,
                name=f"fresh-notion-{uuid.uuid4().hex[:8]}",
                display_name="Notion Fresh",
                url="https://notion.example.com/mcp",
                auth_method=MCPServerConfig.AuthMethod.OAUTH2,
            )

            credential = MCPServerOAuthCredential.objects.create(
                server_config=config,
                user=user,
                client_id="client-789",
            )
            credential.client_secret = "secret-abc"
            credential.access_token = "valid-token"
            credential.refresh_token = "refresh-abc"
            credential.token_type = "Bearer"
            credential.expires_at = timezone.now() + timedelta(minutes=10)
            credential.metadata = {"token_endpoint": "https://notion.example.com/oauth/token"}
            credential.save()

        manager = MCPToolManager()
        runtime = manager._build_runtime_from_config(config)

        mock_post.assert_not_called()
        self.assertEqual(runtime.oauth_access_token, "valid-token")
        self.assertEqual(runtime.oauth_token_type, "Bearer")

    @tag("batch_mcp_tools")
    def test_cached_catalog_load_does_not_prepare_oauth(self):
        runtime = MCPServerRuntime(
            config_id=self.config_id,
            name=self.server_name,
            display_name="OAuth server",
            description="",
            command=None,
            args=[],
            url="https://example.com/mcp",
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
            env={},
            headers={},
            prefetch_apps=[],
            scope=MCPServerConfig.Scope.PLATFORM,
            organization_id=None,
            user_id=None,
            updated_at=timezone.now(),
            oauth_access_token="expired-token",
            oauth_expires_at=timezone.now() - timedelta(minutes=1),
        )

        with patch.object(self.manager, "_load_cached_tools", return_value=True), patch.object(
            self.manager,
            "_ensure_runtime_oauth",
        ) as mock_prepare_oauth:
            self.manager._register_server(runtime)

        mock_prepare_oauth.assert_not_called()

    @tag("batch_mcp_tools")
    @patch("api.services.mcp_oauth.requests.post")
    def test_targeted_config_load_ignores_unrelated_expired_oauth(self, mock_post):
        user = get_user_model().objects.create_user(username=f"targeted-{uuid.uuid4().hex[:8]}")
        agent = PersistentAgent.objects.create(
            user=user,
            name="Targeted agent",
            charter="Test",
            browser_use_agent=create_test_browser_agent(user),
        )
        with patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery"):
            unrelated = MCPServerConfig.objects.create(
                scope=MCPServerConfig.Scope.PLATFORM,
                name=f"unrelated-{uuid.uuid4().hex[:8]}",
                display_name="Unrelated OAuth",
                url="https://unrelated.example.com/mcp",
                auth_method=MCPServerConfig.AuthMethod.OAUTH2,
            )
            credential = MCPServerOAuthCredential.objects.create(
                server_config=unrelated,
                expires_at=timezone.now() - timedelta(minutes=1),
                metadata={"token_endpoint": "https://unrelated.example.com/oauth/token"},
            )
            credential.access_token = "expired"
            credential.refresh_token = "refresh"
            credential.save()

        manager = MCPToolManager()
        with patch.object(manager, "_build_runtime_from_config", wraps=manager._build_runtime_from_config) as build_runtime, patch.object(
            manager,
            "_register_server",
        ), patch.object(manager, "initialize") as initialize:
            manager.get_tools_for_agent(agent, allowed_config_ids={self.config_id})

        initialize.assert_not_called()
        self.assertEqual(build_runtime.call_count, 1)
        self.assertEqual(build_runtime.call_args.args[0].id, self.server_config.id)
        mock_post.assert_not_called()

    @tag("batch_mcp_tools")
    def test_targeted_preparation_does_not_reuse_same_named_tool_from_other_config(self):
        user = get_user_model().objects.create_user(username=f"scoped-{uuid.uuid4().hex[:8]}")
        agent = PersistentAgent.objects.create(
            user=user,
            name="Scoped agent",
            charter="Test",
            browser_use_agent=create_test_browser_agent(user),
        )
        other_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name=f"other-{uuid.uuid4().hex[:8]}",
            display_name="Other",
            command="other",
        )
        PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name="mcp_shared_lookup",
            tool_server=self.server_name,
            tool_name="lookup",
            server_config=self.server_config,
        )
        wrong = MCPToolInfo(
            str(other_config.id),
            "mcp_shared_lookup",
            other_config.name,
            "lookup",
            "Wrong",
            {},
        )
        expected = MCPToolInfo(
            self.config_id,
            "mcp_shared_lookup",
            self.server_name,
            "lookup",
            "Expected",
            {},
        )
        self.manager._server_cache[str(other_config.id)] = self.manager._build_runtime_from_config(
            other_config
        )
        self.manager._tools_cache[str(other_config.id)] = [wrong]

        with patch.object(
            self.manager,
            "get_tools_for_agent",
            return_value=[expected],
        ) as get_tools:
            result = self.manager.prepare_tool_for_agent(agent, expected.full_name)

        self.assertIs(result, expected)
        get_tools.assert_called_once_with(
            agent,
            allowed_config_ids={self.config_id},
            pipedream_app_slugs=None,
        )

    @tag("batch_mcp_tools")
    def test_resolution_does_not_reuse_cached_tool_from_inaccessible_config(self):
        user = get_user_model().objects.create_user(username=f"resolver-{uuid.uuid4().hex[:8]}")
        agent = PersistentAgent.objects.create(
            user=user,
            name="Resolver agent",
            charter="Test",
            browser_use_agent=create_test_browser_agent(user),
        )
        other_user = get_user_model().objects.create_user(username=f"other-{uuid.uuid4().hex[:8]}")
        other_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=other_user,
            name="shared",
            display_name="Inaccessible",
            command="other",
        )
        wrong = MCPToolInfo(
            str(other_config.id),
            "mcp_shared_lookup",
            other_config.name,
            "lookup",
            "Wrong",
            {},
        )
        expected = MCPToolInfo(
            self.config_id,
            wrong.full_name,
            self.server_name,
            "lookup",
            "Expected",
            {},
        )
        self.manager._server_cache[str(other_config.id)] = self.manager._build_runtime_from_config(
            other_config
        )
        self.manager._tools_cache[str(other_config.id)] = [wrong]

        with patch.object(self.manager, "get_tools_for_agent", return_value=[expected]) as get_tools:
            result = self.manager.prepare_tool_for_agent(
                agent,
                expected.full_name,
                require_enabled=False,
            )

        self.assertIs(result, expected)
        get_tools.assert_called_once_with(
            agent,
            allowed_server_names={"shared"},
            pipedream_app_slugs=None,
        )
        
    @patch('api.agent.tools.mcp_manager.asyncio.get_running_loop')
    def test_ensure_event_loop_reuses_existing(self, mock_get_loop):
        """Test that existing event loop is reused."""
        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        mock_get_loop.return_value = mock_loop
        
        result = self.manager._ensure_event_loop()
        
        self.assertEqual(result, mock_loop)
        mock_get_loop.assert_called_once()
        
    @patch('api.agent.tools.mcp_manager.asyncio.new_event_loop')
    @patch('api.agent.tools.mcp_manager.asyncio.set_event_loop')
    @patch('api.agent.tools.mcp_manager.asyncio.get_running_loop')
    def test_ensure_event_loop_creates_new(self, mock_get_loop, mock_set_loop, mock_new_loop):
        """Test that new event loop is created when needed."""
        mock_get_loop.side_effect = RuntimeError("No running loop")
        new_loop = MagicMock()
        mock_new_loop.return_value = new_loop
        
        result = self.manager._ensure_event_loop()
        
        self.assertEqual(result, new_loop)
        mock_new_loop.assert_called_once()
        mock_set_loop.assert_called_once_with(new_loop)
        
    def test_initialize_does_not_register_servers(self):
        """Ensure initialize() avoids contacting MCP servers during refresh."""
        with patch.object(self.manager, "_register_server") as mock_register:
            self.manager.initialize(force=True)
        mock_register.assert_not_called()

    def test_test_server_tools_fails_when_forced_discovery_does_not_refresh_cache(self):
        """Manual tests should not report stale cached tools after an early registration return."""
        runtime = self.manager._build_runtime_from_config(self.server_config)
        slot_key = self.manager._tool_cache_slot_key(runtime)
        stale_tool = MCPToolInfo(
            self.config_id,
            full_name="mcp_stale_lookup",
            server_name=self.server_name,
            tool_name="lookup",
            description="Stale tool",
            parameters={},
        )
        self.manager._tools_cache[slot_key] = [stale_tool]
        self.manager._tool_cache_fingerprints[slot_key] = "stale-fingerprint"

        with patch.object(self.manager, "_register_server", return_value=None):
            ok, tools, details = self.manager.test_server_tools(self.config_id)

        self.assertFalse(ok)
        self.assertEqual(tools, [])
        self.assertEqual(details["error_type"], "discovery_not_completed")
        self.assertNotIn(slot_key, self.manager._tools_cache)
        self.assertNotIn(slot_key, self.manager._tool_cache_fingerprints)

    def test_test_server_tools_accepts_fresh_empty_tool_discovery(self):
        """A successful discovery can legitimately expose zero tools."""
        runtime = self.manager._build_runtime_from_config(self.server_config)
        slot_key = self.manager._tool_cache_slot_key(runtime)

        def register_server(_runtime, **_kwargs):
            self.manager._tools_cache[slot_key] = []

        with patch.object(self.manager, "_register_server", side_effect=register_server):
            ok, tools, details = self.manager.test_server_tools(self.config_id)

        self.assertTrue(ok)
        self.assertEqual(tools, [])
        self.assertEqual(details, {})

    def test_discover_tools_for_server_handles_http_discovery_failure(self):
        request = httpx.Request("GET", "https://example.com/mcp")
        response = httpx.Response(401, request=request)
        error = httpx.HTTPStatusError("Unauthorized", request=request, response=response)

        with patch.object(self.manager, "_register_server", side_effect=error):
            self.assertFalse(self.manager.discover_tools_for_server(self.config_id))
        
    def test_get_tools_for_agent_registers_accessible_servers_only(self):
        """Ensure discovery runs only for servers the agent can access."""
        User = get_user_model()
        user = User.objects.create_user(username="lazy-agent@example.com")
        browser_agent = create_test_browser_agent(user)
        agent = PersistentAgent.objects.create(
            user=user,
            name="lazy-agent",
            charter="Test charter",
            browser_use_agent=browser_agent,
        )

        assigned_personal = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=user,
            name=f"assigned-{uuid.uuid4().hex[:8]}",
            display_name="Assigned Personal Server",
            description="",
            command="npx",
            command_args=[],
            auth_method=MCPServerConfig.AuthMethod.NONE,
        )
        PersistentAgentMCPServer.objects.create(agent=agent, server_config=assigned_personal)

        MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=user,
            name=f"unassigned-{uuid.uuid4().hex[:8]}",
            display_name="Unassigned Personal Server",
            description="",
            command="npx",
            command_args=[],
            auth_method=MCPServerConfig.AuthMethod.NONE,
        )

        self.manager.initialize(force=True)
        original_register = self.manager._register_server
        with patch.object(self.manager, "_register_server", wraps=original_register) as mock_register:
            tools = self.manager.get_tools_for_agent(agent)

        self.assertEqual(tools, [])
        registered_ids = {call.args[0].config_id for call in mock_register.call_args_list}
        expected_ids = {str(self.server_config.id), str(assigned_personal.id)}
        self.assertEqual(registered_ids, expected_ids)
        self.assertEqual(mock_register.call_count, len(expected_ids))
        
    @patch('api.agent.tools.mcp_manager.MCPToolManager.initialize')
    def test_get_enabled_tools_definitions(self, mock_init):
        """Test getting OpenAI-format tool definitions."""
        User = get_user_model()
        user = User.objects.create_user(username='test@example.com')
        browser_agent = create_test_browser_agent(user)
        agent = PersistentAgent.objects.create(
            user=user,
            name="test-agent",
            charter="Test",
            browser_use_agent=browser_agent,
        )
        tool1 = MCPToolInfo(
            self.config_id,
            "mcp_test_tool1",
            self.server_name,
            "tool1",
            "Test tool 1",
            {"type": "object", "properties": {}}
        )
        # Ensure global manager doesn't auto-initialize during enable
        from api.agent.tools import mcp_manager as mm
        mm._mcp_manager._initialized = True
        with patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent') as mock_all:
            mock_all.return_value = [tool1]
            enable_tools(agent, ["mcp_test_tool1"])
        self.manager._tools_cache = {self.config_id: [tool1]}
        self.manager._initialized = True
        
        with patch.object(self.manager, 'get_tools_for_agent', return_value=[tool1]) as mock_get_tools:
            definitions = self.manager.get_enabled_tools_definitions(agent)
        
        self.assertEqual(len(definitions), 1)
        self.assertEqual(definitions[0]["function"]["name"], "mcp_test_tool1")
        self.assertEqual(definitions[0]["function"]["description"], "Test tool 1")
        mock_init.assert_not_called()
        mock_get_tools.assert_called_once_with(
            agent,
            allowed_config_ids={self.config_id},
            pipedream_app_slugs=None,
        )

    @patch('api.agent.tools.mcp_manager.MCPToolManager.initialize')
    def test_get_enabled_tools_definitions_skips_static_tools_without_mcp_discovery(self, mock_init):
        """Static enabled tools should not force MCP server discovery."""
        User = get_user_model()
        user = User.objects.create_user(username='static-tools@example.com')
        browser_agent = create_test_browser_agent(user)
        agent = PersistentAgent.objects.create(
            user=user,
            name="static-agent",
            charter="Test",
            browser_use_agent=browser_agent,
        )
        mark_tool_enabled_without_discovery(agent, "send_chat_message")

        with patch.object(self.manager, 'get_tools_for_agent') as mock_get_tools:
            definitions = self.manager.get_enabled_tools_definitions(agent)

        self.assertEqual(definitions, [])
        mock_init.assert_not_called()
        mock_get_tools.assert_not_called()

    @tag("batch_mcp_tools")
    def test_get_enabled_tools_definitions_targets_and_backfills_legacy_pipedream_row(self):
        user = get_user_model().objects.create_user(username=f"legacy-pd-{uuid.uuid4().hex[:8]}")
        agent = PersistentAgent.objects.create(
            user=user,
            name="Legacy Pipedream agent",
            charter="Test",
            browser_use_agent=create_test_browser_agent(user),
        )
        row = PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name="notion-create-page",
        )
        tool = MCPToolInfo(
            self.config_id,
            row.tool_full_name,
            "pipedream",
            row.tool_full_name,
            "Create page",
            {"type": "object", "properties": {}},
        )

        with patch.object(
            self.manager,
            "get_tools_for_agent",
            return_value=[tool],
        ) as get_tools:
            definitions = self.manager.get_enabled_tools_definitions(agent)

        self.assertEqual(definitions[0]["function"]["name"], row.tool_full_name)
        get_tools.assert_called_once_with(
            agent,
            allowed_server_names={"pipedream"},
            pipedream_app_slugs={"notion"},
        )
        row.refresh_from_db()
        self.assertEqual(row.tool_server, "pipedream")
        self.assertEqual(row.server_config_id, self.server_config.id)
        
    @patch('api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop')
    @patch('api.agent.tools.mcp_manager.MCPToolManager._execute_async')
    def test_execute_mcp_tool_success(self, mock_execute, mock_ensure_loop):
        """Test successful MCP tool execution."""
        User = get_user_model()
        user = User.objects.create_user(username='test@example.com')
        browser_agent = create_test_browser_agent(user)
        agent = PersistentAgent.objects.create(
            user=user,
            name="test-agent",
            charter="Test",
            browser_use_agent=browser_agent,
        )
        tool1 = MCPToolInfo(self.config_id, "mcp_test_tool1", self.server_name, "tool1", "Test tool 1", {})
        runtime = MCPServerRuntime(
            config_id=self.config_id,
            name=self.server_name,
            display_name=self.server_config.display_name,
            description=self.server_config.description,
            command=self.server_config.command or None,
            args=list(self.server_config.command_args or []),
            url=self.server_config.url or None,
            auth_method=self.server_config.auth_method,
            env=self.server_config.environment or {},
            headers=self.server_config.headers or {},
            prefetch_apps=list(self.server_config.prefetch_apps or []),
            scope=self.server_config.scope,
            organization_id=str(self.server_config.organization_id) if self.server_config.organization_id else None,
            user_id=str(self.server_config.user_id) if self.server_config.user_id else None,
            updated_at=self.server_config.updated_at,
        )
        self.manager._server_cache = {self.config_id: runtime}
        self.manager._tools_cache = {self.config_id: [tool1]}
        self.manager._initialized = True
        with patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent') as mock_all:
            mock_all.return_value = [tool1]
            enable_tools(agent, ["mcp_test_tool1"])

        mock_client = MagicMock()
        self.manager._clients = {self.config_id: mock_client}
        # Populate tools cache since legacy name fallback is removed
        self.manager._tools_cache = {self.config_id: [tool1]}
        
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.data = "Success result"
        mock_result.content = []
        
        mock_loop = MagicMock()
        mock_loop.run_until_complete.return_value = mock_result
        mock_ensure_loop.return_value = mock_loop

        with patch.object(self.manager, "_select_agent_proxy_url", return_value=(None, None)):
            result = self.manager.execute_mcp_tool(agent, "mcp_test_tool1", {"param": "value"})
        
        self.assertEqual(result["status"], "success")

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    def test_execute_http_tool_uses_proxy(self):
        """Ensure HTTP-based MCP tools route through agent-selected proxy."""
        agent = self._setup_http_tool()

        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.data = "Success result"
        mock_result.content = []

        loop = MagicMock()
        loop.run_until_complete.return_value = mock_result

        captured: list[str] = []

        def fake_proxy_ctx(url):
            captured.append(url)
            return nullcontext()

        with patch.object(self.manager, "_select_agent_proxy_url", return_value=("http://proxy.example:8080", None)) as mock_select, \
             patch("api.agent.tools.mcp_manager._use_mcp_proxy", side_effect=fake_proxy_ctx) as mock_ctx, \
             patch.object(self.manager, "_execute_async", new=AsyncMock(return_value=mock_result)), \
             patch.object(self.manager, "_ensure_event_loop", return_value=loop):
            result = self.manager.execute_mcp_tool(agent, "http_tool", {"foo": "bar"})

        self.assertEqual(result["status"], "success")
        mock_select.assert_called_once()
        mock_ctx.assert_called()
        self.assertIn("http://proxy.example:8080", captured)
        self.assertEqual(result["result"], "Success result")
        
        # Check that usage was tracked
        row = PersistentAgentEnabledTool.objects.get(agent=agent, tool_full_name="http_tool")
        self.assertIsNotNone(row.last_used_at)

    @override_settings(GOBII_PROPRIETARY_MODE=False, ENABLE_PROXY_ROUTING=True)
    @patch('api.agent.tools.mcp_manager.select_proxy_for_persistent_agent')
    def test_execute_http_tool_without_proxy_logs_warning(self, mock_select_proxy):
        """Ensure HTTP tools continue without proxy when none available."""
        mock_select_proxy.side_effect = RuntimeError("No proxies configured")
        agent = self._setup_http_tool()

        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.data = "OK"
        mock_result.content = []

        loop = MagicMock()
        loop.run_until_complete.return_value = mock_result

        with patch.object(self.manager, "_execute_async", new=AsyncMock(return_value=mock_result)), \
             patch.object(self.manager, "_ensure_event_loop", return_value=loop), \
             self.assertLogs("api.agent.tools.mcp_manager", level="WARNING") as log_capture:
            result = self.manager.execute_mcp_tool(agent, "http_tool", {"foo": "bar"})

        self.assertEqual(result["status"], "success")
        self.assertTrue(
            any("continuing without proxy" in message for message in log_capture.output),
            f"Expected warning about proxy fallback, got: {log_capture.output}",
        )
        mock_select_proxy.assert_called_once()

    @override_settings(GOBII_PROPRIETARY_MODE=True, ENABLE_PROXY_ROUTING=True)
    @patch('api.agent.tools.mcp_manager.select_proxy_for_persistent_agent')
    def test_execute_http_tool_errors_when_proxy_required(self, mock_select_proxy):
        """Ensure HTTP tools fail gracefully when proxy required but unavailable."""
        mock_select_proxy.side_effect = RuntimeError("No proxies configured")
        agent = self._setup_http_tool()

        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.data = "OK"
        mock_result.content = []

        loop = MagicMock()
        loop.run_until_complete.return_value = mock_result

        with patch.object(self.manager, "_execute_async", new=AsyncMock(return_value=mock_result)), \
             patch.object(self.manager, "_ensure_event_loop", return_value=loop), \
             self.assertLogs("api.agent.tools.mcp_manager", level="ERROR") as log_capture:
            result = self.manager.execute_mcp_tool(agent, "http_tool", {"foo": "bar"})

        self.assertEqual(result["status"], "error")
        self.assertIn("No proxy server available", result["message"])
        self.assertTrue(
            any("requires a proxy" in message or "Proxy selection failed" in message for message in log_capture.output),
            f"Expected error log about proxy requirement, got: {log_capture.output}",
        )

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    def test_execute_stdio_tool_uses_scoped_proxy_client_cache_and_rebuilds_on_proxy_change(self):
        agent, runtime, _tool = self._setup_stdio_tool()
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)

        fake_result = SimpleNamespace(is_error=False, data={"ok": True}, content=[])
        executed_clients: list[Any] = []

        async def fake_execute_async(client, _tool_name, _params, timeout_seconds):
            executed_clients.append(client)
            return fake_result

        proxied_client_one = MagicMock(name="proxied-client-one")
        proxied_client_two = MagicMock(name="proxied-client-two")

        with patch.object(
            self.manager,
            "_select_agent_proxy_url",
            side_effect=[
                ("socks5://proxy-one.internal:1080", None),
                ("socks5://proxy-one.internal:1080", None),
                ("http://proxy-two.internal:8080", None),
            ],
        ), patch.object(
            self.manager,
            "_build_client_for_runtime",
            side_effect=[proxied_client_one, proxied_client_two],
        ) as mock_build_client, patch.object(
            self.manager,
            "_ensure_event_loop",
            return_value=loop,
        ), patch.object(
            self.manager,
            "_execute_async",
            side_effect=fake_execute_async,
        ), patch.object(
            self.manager,
            "_close_client_sync",
        ), patch.object(
            self.manager,
            "_adapt_tool_result",
            side_effect=lambda _server, _tool_name, result: result,
        ):
            first = self.manager.execute_mcp_tool(agent, "stdio_tool", {"foo": "bar"})
            second = self.manager.execute_mcp_tool(agent, "stdio_tool", {"foo": "bar"})
            third = self.manager.execute_mcp_tool(agent, "stdio_tool", {"foo": "bar"})

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")
        self.assertEqual(third["status"], "success")
        self.assertEqual(executed_clients, [proxied_client_one, proxied_client_one, proxied_client_two])
        self.assertEqual(mock_build_client.call_count, 2)
        self.assertEqual(
            mock_build_client.call_args_list[0].kwargs["env_overrides"]["HTTP_PROXY"],
            "http://proxy-one.internal:1080",
        )
        self.assertEqual(
            mock_build_client.call_args_list[1].kwargs["env_overrides"]["HTTP_PROXY"],
            "http://proxy-two.internal:8080",
        )
        self.assertEqual(
            list(self.manager._stdio_proxy_clients.keys()),
            [f"{runtime.config_id}:agent:{agent.id}:http://proxy-two.internal:8080"],
        )

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    def test_execute_stdio_tool_without_proxy_uses_shared_client(self):
        agent, runtime, _tool = self._setup_stdio_tool()
        shared_client = self.manager._clients[runtime.config_id]
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)

        fake_result = SimpleNamespace(is_error=False, data="shared", content=[])
        executed_clients: list[Any] = []

        async def fake_execute_async(client, _tool_name, _params, timeout_seconds):
            executed_clients.append(client)
            return fake_result

        with patch.object(
            self.manager,
            "_select_agent_proxy_url",
            return_value=(None, None),
        ), patch.object(
            self.manager,
            "_ensure_event_loop",
            return_value=loop,
        ), patch.object(
            self.manager,
            "_execute_async",
            side_effect=fake_execute_async,
        ), patch.object(
            self.manager,
            "_build_client_for_runtime",
        ) as mock_build_client, patch.object(
            self.manager,
            "_adapt_tool_result",
            side_effect=lambda _server, _tool_name, result: result,
        ):
            result = self.manager.execute_mcp_tool(agent, "stdio_tool", {"foo": "bar"})

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["result"], "shared")
        self.assertEqual(executed_clients, [shared_client])
        self.assertFalse(self.manager._stdio_proxy_clients)
        mock_build_client.assert_not_called()

    def test_execute_mcp_tool_not_enabled(self):
        """Test executing a tool that's not enabled."""
        User = get_user_model()
        user = User.objects.create_user(username='test@example.com')
        browser_agent = create_test_browser_agent(user)
        agent = PersistentAgent.objects.create(
            user=user,
            name="test-agent",
            charter="Test",
            browser_use_agent=browser_agent,
        )
        
        result = self.manager.execute_mcp_tool(agent, "mcp_test_tool1", {})
        
        self.assertEqual(result["status"], "error")
        self.assertIn("not enabled", result["message"])
        
    def test_cleanup(self):
        """Test cleanup releases resources."""
        self.manager._clients = {"test": MagicMock()}
        self.manager._stdio_proxy_clients = {"test:agent:1:http://proxy.internal:8080": MagicMock()}
        self.manager._tools_cache = {"test": []}
        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        self.manager._loop = mock_loop
        self.manager._initialized = True
        
        self.manager.cleanup()
        
        self.assertEqual(len(self.manager._clients), 0)
        self.assertEqual(len(self.manager._stdio_proxy_clients), 0)
        self.assertEqual(len(self.manager._tools_cache), 0)
        mock_loop.close.assert_called_once()
        self.assertFalse(self.manager._initialized)


@tag("batch_mcp_tools")
class MCPToolFunctionsTests(TestCase):
    """Test module-level MCP tool functions."""
    
    def setUp(self):
        """Set up test fixtures."""
        invalidate_prompt_settings_cache()
        self.addCleanup(invalidate_prompt_settings_cache)
        invalidate_tool_settings_cache()
        self.addCleanup(invalidate_tool_settings_cache)
        User = get_user_model()
        self.user = User.objects.create_user(username='test@example.com')
        self.browser_agent = create_test_browser_agent(self.user)
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="test-agent",
            charter="Test",
            browser_use_agent=self.browser_agent
        )
        self.server_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name=f"integration-platform-{uuid.uuid4().hex[:8]}",
            display_name="Integration Platform Server",
            description="",
            command="npx",
            command_args=[],
        )
        self.config_id = str(self.server_config.id)
        self.server_name = self.server_config.name
        self.server_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name=f"integration-platform-{uuid.uuid4().hex[:8]}",
            display_name="Integration Platform Server",
            description="",
            command="npx",
            command_args=[],
        )
        self.config_id = str(self.server_config.id)
        self.server_name = self.server_config.name
        self.server_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name=f"integration-platform-{uuid.uuid4().hex[:8]}",
            display_name="Integration Platform Server",
            description="",
            command="npx",
            command_args=[],
        )
        self.config_id = str(self.server_config.id)
        self.server_name = self.server_config.name
        self.server_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name=f"integration-platform-{uuid.uuid4().hex[:8]}",
            display_name="Integration Platform Server",
            description="",
            command="npx",
            command_args=[],
        )
        self.config_id = str(self.server_config.id)
        self.server_name = self.server_config.name
        self.server_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name=f"integration-platform-{uuid.uuid4().hex[:8]}",
            display_name="Integration Platform Server",
            description="",
            command="npx",
            command_args=[],
        )
        self.config_id = str(self.server_config.id)
        self.server_name = self.server_config.name
        self.server_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name=f"integration-platform-{uuid.uuid4().hex[:8]}",
            display_name="Integration Platform Server",
            description="",
            command="npx",
            command_args=[],
        )
        self.config_id = str(self.server_config.id)
        self.server_name = self.server_config.name
        # Ensure persistent LLM config exists for DB-only selection
        seed_persistent_basic(include_openrouter=False)

    def _set_tool_search_auto_enable_apps(self, enabled: bool) -> None:
        config, _ = ToolConfig.objects.get_or_create(plan_name=PlanNames.FREE)
        config.tool_search_auto_enable_apps = enabled
        config.save()
        invalidate_tool_settings_cache()

    def _pipedream_server(self, *, deprecated_apps=()):
        return MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="pipedream",
            display_name="Pipedream",
            url="https://remote.mcp.pipedream.net",
            metadata={"deprecated_apps": list(deprecated_apps)},
        )

    def _mock_search_tools_state(self, mock_get_config, mock_get_manager, mock_run_completion, *, tools=()):
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = list(tools)
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        msg = MagicMock()
        msg.content = "No relevant tools."
        setattr(msg, "tool_calls", [])
        choice = MagicMock()
        choice.message = msg
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

    def _completion_user_message(self, mock_run_completion):
        return mock_run_completion.call_args.kwargs["messages"][1]["content"]

    def _tool_def_names(self, definitions):
        return {
            entry.get("function", {}).get("name")
            for entry in definitions
            if isinstance(entry, dict)
        }

    def _seed_create_image_tier(self) -> None:
        provider = LLMProvider.objects.create(
            key=f"img-provider-{uuid.uuid4().hex[:6]}",
            display_name="Image Provider",
            enabled=True,
        )
        endpoint = ImageGenerationModelEndpoint.objects.create(
            key=f"img-endpoint-{uuid.uuid4().hex[:6]}",
            provider=provider,
            enabled=True,
            litellm_model="google/gemini-2.5-flash-image",
        )
        tier = ImageGenerationLLMTier.objects.create(order=1, description="Tier 1")
        ImageGenerationTierEndpoint.objects.create(
            tier=tier,
            endpoint=endpoint,
            weight=1.0,
        )
        
    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    def test_search_tools_calls_enable_tools(self, mock_get_config, mock_get_manager, mock_run_completion, mock_enable_batch):
        """search_tools should invoke internal enable_tools via a tool call."""
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = [
            MCPToolInfo(self.config_id, "mcp_brightdata_scrape", "brightdata", "scrape", "Scrape pages", {}),
            MCPToolInfo(self.config_id, "mcp_brightdata_search", "brightdata", "search", "Search web", {}),
        ]
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        # Mock a tool-call style response
        tool_call = {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "enable_tools",
                "arguments": json.dumps({"tool_names": [
                    "mcp_brightdata_scrape", "mcp_brightdata_search"
                ]}),
            },
        }
        message = MagicMock()
        message.content = "Enabling Bright Data scraping and search."
        # Support both dict-style and attr-style access depending on litellm
        setattr(message, 'tool_calls', [tool_call])
        choice = MagicMock()
        choice.message = message
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        mock_enable_batch.return_value = {
            "status": "success",
            "message": "Enabled: mcp_brightdata_scrape, mcp_brightdata_search",
            "enabled": ["mcp_brightdata_scrape", "mcp_brightdata_search"],
            "already_enabled": [],
            "evicted": [],
            "invalid": [],
        }

        result = search_tools(self.agent, "scrape web pages")
        self.assertEqual(result["status"], "success")
        self.assertIn("Enabled tools:", result["message"]) 
        self.assertEqual(result.get("tools", {}).get("enabled"), ["mcp_brightdata_scrape", "mcp_brightdata_search"]) 
        mock_enable_batch.assert_called_once()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    def test_search_tools_passes_agent_to_failover(self, mock_get_config, mock_get_manager, mock_run_completion, mock_enable_tools):
        """search_tools should pass agent context when fetching failover configs."""
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = [
            MCPToolInfo(self.config_id, "builtin_sample", "builtin", "sample", "Sample tool", {}),
        ]
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [
            (
                "openai",
                "openai/gpt-4o",
                {"temperature": 0.1},
            )
        ]

        msg = MagicMock()
        msg.content = "No tool calls"
        setattr(msg, "tool_calls", [])
        choice = MagicMock()
        choice.message = msg
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "test query")
        self.assertEqual(result["status"], "success")
        mock_get_config.assert_called_once()
        kwargs = mock_get_config.call_args.kwargs
        self.assertIs(kwargs.get("agent"), self.agent)
        self.assertNotIn("agent_id", kwargs)

    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.run_completion')
    def test_search_tools_drops_parallel_hint_from_params(self, mock_run_completion, mock_get_manager, mock_get_config):
        """search_tools should not forward internal 'use_parallel_tool_calls' hint to LiteLLM."""
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = [
            MCPToolInfo(self.config_id, "mcp_brightdata_scrape", "brightdata", "scrape", "Scrape pages", {}),
        ]
        mock_get_manager.return_value = mock_manager
        # Return a single config with both hints present
        mock_get_config.return_value = [
            (
                "openai",
                "openai/gpt-4o",
                {
                    "temperature": 0.1,
                    "supports_tool_choice": True,
                    "use_parallel_tool_calls": True,
                    "supports_vision": True,
                },
            )
        ]

        # Make litellm.completion return a minimal response
        mock_response = MagicMock()
        msg = MagicMock()
        msg.content = "No tools"
        setattr(msg, 'tool_calls', [])
        choice = MagicMock()
        choice.message = msg
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        # Call search_tools (module-level function)
        res = search_tools(self.agent, "anything")
        self.assertEqual(res["status"], "success")
        # Assert the forwarded kwargs do not contain the internal hint
        kwargs = mock_run_completion.call_args.kwargs
        self.assertNotIn('use_parallel_tool_calls', kwargs)
        self.assertNotIn('supports_vision', kwargs)
        self.assertNotIn('tool_choice', kwargs)

    @patch('api.agent.tools.search_tools.get_mcp_manager')
    def test_search_tools_no_tools(self, mock_get_manager):
        """search_tools when no tools are available returns a message."""
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        result = search_tools(self.agent, "any query")
        self.assertEqual(result["status"], "success")
        self.assertIn("No MCP tools available", result["message"])

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.search_tools.get_effective_pipedream_app_slugs_for_agent')
    @patch('api.agent.tools.search_tools.PipedreamCatalogService.search_apps')
    def test_search_tools_includes_builtin_catalog(
        self,
        mock_search_apps,
        mock_get_effective_pipedream_app_slugs_for_agent,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        """search_tools should include builtin tools when MCP catalog is empty."""
        mock_search_apps.return_value = []
        mock_get_effective_pipedream_app_slugs_for_agent.return_value = []
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        mock_response = MagicMock()
        msg = MagicMock()
        msg.content = "No relevant tools."
        setattr(msg, 'tool_calls', [])
        choice = MagicMock()
        choice.message = msg
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "anything")

        self.assertEqual(result["status"], "success")
        self.assertIn("No relevant tools", result.get("message", ""))
        mock_run_completion.assert_called_once()
        _args, kwargs = mock_run_completion.call_args
        user_message = kwargs["messages"][1]["content"]
        self.assertIn("sqlite_batch", user_message)
        self.assertIn("http_request", user_message)
        self.assertNotIn("create_image", user_message)
        self.assertNotIn(f"- {IMAGE_GENERATION_SYSTEM_SKILL_KEY}:", user_message)
        mock_search_apps.assert_not_called()
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent', return_value=True)
    def test_search_tools_includes_global_skill_prompt_section(
        self,
        _mock_sandbox_enabled,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        GlobalAgentSkill.objects.create(
            name="ops-report",
            description="Generate an ops report",
            tools=["sqlite_batch"],
            instructions="Collect the latest operational metrics and summarize them.",
        )
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        mock_response = MagicMock()
        msg = MagicMock()
        msg.content = "No relevant tools."
        setattr(msg, 'tool_calls', [])
        choice = MagicMock()
        choice.message = msg
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "anything")

        self.assertEqual(result["status"], "success")
        user_message = mock_run_completion.call_args.kwargs["messages"][1]["content"]
        self.assertIn("Available global skills:", user_message)
        self.assertIn("- ops-report | use when: Generate an ops report | enables: sqlite_batch | secrets: (none)", user_message)
        system_message = mock_run_completion.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Treat global skills as capability bundles", system_message)
        self.assertIn("small composable primitives", system_message)
        tool_defs = mock_run_completion.call_args.kwargs["tools"]
        self.assertEqual(
            [tool_def["function"]["name"] for tool_def in tool_defs],
            ["enable_tools", "enable_global_skills"],
        )
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    def test_search_tools_includes_agent_skill_prompt_section(
        self,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="customer-research",
            description="Research customer accounts",
            version=1,
            tools=["sqlite_batch"],
            instructions="Review local account data before summarizing.",
        )
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        mock_response = MagicMock()
        msg = MagicMock()
        msg.content = "No relevant tools."
        setattr(msg, "tool_calls", [])
        choice = MagicMock()
        choice.message = msg
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "account workflow")

        self.assertEqual(result["status"], "success")
        user_message = mock_run_completion.call_args.kwargs["messages"][1]["content"]
        self.assertIn("Available agent skills:", user_message)
        self.assertIn(
            "- customer-research | use when: Research customer accounts | enables: sqlite_batch | secrets: (none)",
            user_message,
        )
        system_message = mock_run_completion.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Treat agent skills as this agent's saved workflows", system_message)
        tool_defs = mock_run_completion.call_args.kwargs["tools"]
        self.assertEqual(
            [tool_def["function"]["name"] for tool_def in tool_defs],
            ["enable_tools", "enable_agent_skills"],
        )
        mock_enable_tools.assert_not_called()

    @tag("batch_mcp_tools")
    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    def test_search_tools_omits_agent_skills_requiring_tier_blacklisted_tools(
        self,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        tier = get_intelligence_tier("standard")
        tier.blacklisted_tools = ["sqlite_batch"]
        tier.save(update_fields=["blacklisted_tools"])
        self.agent.preferred_llm_tier = tier
        self.agent.save(update_fields=["preferred_llm_tier"])
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="customer-research",
            description="Research customer accounts",
            version=1,
            tools=["sqlite_batch"],
            instructions="Review local account data before summarizing.",
        )
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="web-review",
            description="Review web content",
            version=1,
            tools=["read_file"],
            instructions="Review saved files before summarizing.",
        )
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        mock_response = MagicMock()
        msg = MagicMock()
        msg.content = "No relevant tools."
        setattr(msg, "tool_calls", [])
        choice = MagicMock()
        choice.message = msg
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "customer research")

        self.assertEqual(result["status"], "success")
        user_message = mock_run_completion.call_args.kwargs["messages"][1]["content"]
        self.assertNotIn("customer-research", user_message)
        self.assertNotIn("sqlite_batch", user_message)
        self.assertIn("web-review", user_message)
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    def test_search_tools_refreshes_agent_skill_and_enables_required_tools(
        self,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        skill = PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="customer-research",
            description="Research customer accounts",
            version=1,
            tools=["sqlite_batch"],
            instructions="Review local account data before summarizing.",
        )
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]
        mock_enable_tools.return_value = {
            "status": "success",
            "enabled": ["sqlite_batch"],
            "already_enabled": [],
            "evicted": [],
            "invalid": [],
        }

        msg = MagicMock()
        msg.content = "Use the saved customer research skill."
        setattr(msg, "tool_calls", [
            {
                "type": "function",
                "function": {
                    "name": "enable_agent_skills",
                    "arguments": json.dumps({"skill_names": ["customer-research"]}),
                },
            }
        ])
        choice = MagicMock()
        choice.message = msg
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "customer research")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["agent_skills"]["enabled"], ["customer-research"])
        skill.refresh_from_db()
        self.assertIsNotNone(skill.last_used_at)
        self.assertEqual(skill.usage_count, 1)
        mock_enable_tools.assert_called_once_with(self.agent, ["sqlite_batch"])

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent', return_value=True)
    def test_search_tools_lists_bundled_custom_tools_and_required_secrets(
        self,
        _mock_sandbox_enabled,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        skill = GlobalAgentSkill.objects.create(
            name="ops-report",
            description="Generate an ops report",
            tools=["sqlite_batch"],
            secrets=[
                {
                    "name": "Ops API token",
                    "key": "OPS_API_TOKEN",
                    "secret_type": "env_var",
                    "description": "Used by the bundled sync tool.",
                }
            ],
            instructions="Collect the latest operational metrics and summarize them.",
        )
        GlobalAgentSkillCustomTool.objects.create(
            global_skill=skill,
            name="Ops Sync",
            tool_name="ops_sync",
            description="Sync metrics into SQLite.",
            source_file=SimpleUploadedFile(
                "ops_sync.py",
                _valid_custom_tool_source(),
                content_type="text/x-python",
            ),
            parameters_schema={"type": "object", "properties": {}},
            timeout_seconds=300,
        )
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        mock_response = MagicMock()
        msg = MagicMock()
        msg.content = "No relevant tools."
        setattr(msg, 'tool_calls', [])
        choice = MagicMock()
        choice.message = msg
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "anything")

        self.assertEqual(result["status"], "success")
        user_message = mock_run_completion.call_args.kwargs["messages"][1]["content"]
        self.assertIn("custom_ops_sync", user_message)
        self.assertIn("Ops API token [env_var:OPS_API_TOKEN]", user_message)
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    def test_search_tools_omits_incompatible_global_skills_from_prompt(
        self,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        GlobalAgentSkill.objects.create(
            name="missing-integration-skill",
            description="Requires an unavailable integration",
            tools=["mcp_missing_tool"],
            instructions="Use the unavailable integration.",
        )
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        mock_response = MagicMock()
        msg = MagicMock()
        msg.content = "No relevant tools."
        setattr(msg, 'tool_calls', [])
        choice = MagicMock()
        choice.message = msg
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "anything")

        self.assertEqual(result["status"], "success")
        user_message = mock_run_completion.call_args.kwargs["messages"][1]["content"]
        self.assertNotIn("Available global skills:", user_message)
        self.assertNotIn("missing-integration-skill", user_message)
        tool_defs = mock_run_completion.call_args.kwargs["tools"]
        self.assertEqual([tool_def["function"]["name"] for tool_def in tool_defs], ["enable_tools"])
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    def test_search_tools_includes_system_skill_prompt_section_for_matching_query(
        self,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        skill = SystemSkillDefinition(
            skill_key="meta_ads_platform",
            name="Meta Ads Platform",
            search_summary="Monitor and report on Meta ads data.",
            tool_names=("meta_ads",),
            enables=("live Meta ads reporting", "campaign health checks"),
            use_when=("monitor Meta ads", "check Meta campaign performance"),
            query_aliases=("meta ads", "facebook ads"),
        )
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        mock_response = MagicMock()
        msg = MagicMock()
        msg.content = "No relevant tools."
        setattr(msg, 'tool_calls', [])
        choice = MagicMock()
        choice.message = msg
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        hidden_builtin = {
            "definition": lambda: {
                "type": "function",
                "function": {
                    "name": "meta_ads",
                    "description": "Read Meta ads data.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            "search_hidden": True,
        }

        with (
            patch.dict(
                "api.agent.system_skills.registry.SYSTEM_SKILL_REGISTRY",
                {"meta_ads_platform": skill},
                clear=True,
            ),
            patch.dict(
                "api.agent.tools.tool_manager.BUILTIN_TOOL_REGISTRY",
                {"meta_ads": hidden_builtin},
                clear=False,
            ),
        ):
            result = search_tools(self.agent, "monitor my meta ads")

        self.assertEqual(result["status"], "success")
        user_message = mock_run_completion.call_args.kwargs["messages"][1]["content"]
        self.assertIn("Available system skills:", user_message)
        self.assertIn(
            "- meta_ads_platform: Monitor and report on Meta ads data. | use when: monitor Meta ads, check Meta campaign performance | enables: live Meta ads reporting, campaign health checks | tools: meta_ads",
            user_message,
        )
        self.assertNotIn("Available tools:\n- meta_ads: Read Meta ads data.", user_message)
        system_message = mock_run_completion.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Treat system skills as capability bundles", system_message)
        tool_defs = mock_run_completion.call_args.kwargs["tools"]
        self.assertEqual(
            [tool_def["function"]["name"] for tool_def in tool_defs],
            ["enable_tools", "enable_system_skills"],
        )
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    def test_search_tools_omits_system_skill_prompt_section_for_non_matching_query(
        self,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        skill = SystemSkillDefinition(
            skill_key="meta_ads_platform",
            name="Meta Ads Platform",
            search_summary="Monitor and report on Meta ads data.",
            tool_names=("meta_ads",),
            enables=("live Meta ads reporting",),
            use_when=("monitor Meta ads",),
            query_aliases=("meta ads", "facebook ads"),
        )
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        mock_response = MagicMock()
        msg = MagicMock()
        msg.content = "No relevant tools."
        setattr(msg, 'tool_calls', [])
        choice = MagicMock()
        choice.message = msg
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        hidden_builtin = {
            "definition": lambda: {
                "type": "function",
                "function": {
                    "name": "meta_ads",
                    "description": "Read Meta ads data.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            "search_hidden": True,
        }

        with (
            patch.dict(
                "api.agent.system_skills.registry.SYSTEM_SKILL_REGISTRY",
                {"meta_ads_platform": skill},
                clear=True,
            ),
            patch.dict(
                "api.agent.tools.tool_manager.BUILTIN_TOOL_REGISTRY",
                {"meta_ads": hidden_builtin},
                clear=False,
            ),
        ):
            result = search_tools(self.agent, "read some local files")

        self.assertEqual(result["status"], "success")
        user_message = mock_run_completion.call_args.kwargs["messages"][1]["content"]
        self.assertNotIn("Available system skills:", user_message)
        tool_defs = mock_run_completion.call_args.kwargs["tools"]
        self.assertEqual([tool_def["function"]["name"] for tool_def in tool_defs], ["enable_tools"])
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.search_tools._fallback_builtin_selection')
    def test_search_tools_enables_system_skill_hidden_builtin(
        self,
        mock_fallback_builtin_selection,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
    ):
        skill = SystemSkillDefinition(
            skill_key="meta_ads_platform",
            name="Meta Ads Platform",
            search_summary="Monitor and report on Meta ads data.",
            tool_names=("meta_ads",),
            enables=("live Meta ads reporting",),
            use_when=("monitor Meta ads",),
            query_aliases=("meta ads", "facebook ads"),
        )
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]
        mock_fallback_builtin_selection.return_value = ["read_file"]

        msg = MagicMock()
        msg.content = "Enable the Meta Ads skill."
        setattr(msg, "tool_calls", [
            {
                "type": "function",
                "function": {
                    "name": "enable_system_skills",
                    "arguments": json.dumps({"skill_keys": ["meta_ads_platform"]}),
                },
            }
        ])
        choice = MagicMock()
        choice.message = msg
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        hidden_builtin = {
            "definition": lambda: {
                "type": "function",
                "function": {
                    "name": "meta_ads",
                    "description": "Read Meta ads data.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            "executor": lambda _agent, _params: {"status": "ok"},
            "search_hidden": True,
        }

        with (
            patch.dict(
                "api.agent.system_skills.registry.SYSTEM_SKILL_REGISTRY",
                {"meta_ads_platform": skill},
                clear=True,
            ),
            patch.dict(
                "api.agent.tools.tool_manager.BUILTIN_TOOL_REGISTRY",
                {"meta_ads": hidden_builtin},
                clear=False,
            ),
        ):
            result = search_tools(self.agent, "monitor my meta ads")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["system_skills"]["enabled"], ["meta_ads_platform"])
        self.assertEqual(result["system_skills"]["already_enabled"], [])
        self.assertEqual(result["system_skills"]["invalid"], [])
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(agent=self.agent, tool_full_name="meta_ads").exists()
        )
        mock_fallback_builtin_selection.assert_not_called()

    @patch("api.agent.tools.tool_manager.sandbox_compute_enabled_for_agent", return_value=True)
    def test_create_custom_tool_is_not_static_by_default(self, _mock_sandbox):
        names = get_static_tool_names(self.agent)

        self.assertNotIn(CREATE_CUSTOM_TOOL_NAME, names)

    @patch("api.agent.tools.tool_manager.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.agent.tools.search_tools.run_completion")
    @patch("api.agent.tools.search_tools.get_mcp_manager")
    @patch("api.agent.tools.search_tools.get_llm_config_with_failover")
    def test_search_tools_enables_custom_tool_development_system_skill(
        self,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        _mock_sandbox,
    ):
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        msg = MagicMock()
        msg.content = "Enable the custom tool development skill."
        setattr(msg, "tool_calls", [
            {
                "type": "function",
                "function": {
                    "name": "enable_system_skills",
                    "arguments": json.dumps({"skill_keys": [CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY]}),
                },
            }
        ])
        choice = MagicMock()
        choice.message = msg
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "create a custom tool for bulk api fanout")

        self.assertEqual(result["status"], "success")
        user_message = mock_run_completion.call_args.kwargs["messages"][1]["content"]
        self.assertIn("Available system skills:", user_message)
        self.assertIn(f"- {CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY}:", user_message)
        self.assertEqual(result["system_skills"]["enabled"], [CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY])
        self.assertEqual(result["system_skills"]["invalid"], [])
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent,
                tool_full_name=CREATE_CUSTOM_TOOL_NAME,
            ).exists()
        )

    @patch("api.agent.tools.tool_manager.sandbox_compute_enabled_for_agent", return_value=False)
    @patch("api.agent.tools.search_tools.run_completion")
    @patch("api.agent.tools.search_tools.get_mcp_manager")
    @patch("api.agent.tools.search_tools.get_llm_config_with_failover")
    def test_search_tools_omits_custom_tool_development_when_create_custom_tool_unavailable(
        self,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        _mock_sandbox,
    ):
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        msg = MagicMock()
        msg.content = "No relevant tools."
        setattr(msg, "tool_calls", [])
        choice = MagicMock()
        choice.message = msg
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "create a custom tool for bulk api fanout")

        self.assertEqual(result["status"], "success")
        user_message = mock_run_completion.call_args.kwargs["messages"][1]["content"]
        self.assertNotIn(CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY, user_message)

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent', return_value=True)
    def test_search_tools_enables_global_skill_and_imports_agent_copy(
        self,
        _mock_sandbox_enabled,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        skill = GlobalAgentSkill.objects.create(
            name="ops-report",
            description="Generate an ops report",
            tools=["sqlite_batch"],
            secrets=[
                {
                    "name": "Ops API token",
                    "key": "OPS_API_TOKEN",
                    "secret_type": "env_var",
                    "description": "Used by the bundled sync tool.",
                }
            ],
            instructions="Collect the latest operational metrics and summarize them.",
        )
        GlobalAgentSkillCustomTool.objects.create(
            global_skill=skill,
            name="Ops Sync",
            tool_name="ops_sync",
            description="Sync metrics into SQLite.",
            source_file=SimpleUploadedFile(
                "ops_sync.py",
                _valid_custom_tool_source(),
                content_type="text/x-python",
            ),
            parameters_schema={"type": "object", "properties": {}},
            timeout_seconds=300,
        )
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        msg = MagicMock()
        msg.content = "Enable the ops report skill."
        setattr(msg, "tool_calls", [
            {
                "type": "function",
                "function": {
                    "name": "enable_global_skills",
                    "arguments": json.dumps({"skill_names": ["ops-report"]}),
                },
            }
        ])
        choice = MagicMock()
        choice.message = msg
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        with patch("util.analytics.Analytics.track_event") as mock_track_event:
            result = search_tools(self.agent, "generate recurring ops reports")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["skills"]["enabled"], ["ops-report"])
        self.assertEqual(result["skills"]["already_enabled"], [])
        self.assertEqual(result["skills"]["conflicts"], [])
        imported = PersistentAgentSkill.objects.get(agent=self.agent, name="ops-report")
        self.assertEqual(imported.global_skill, skill)
        self.assertEqual(imported.version, 1)
        self.assertEqual(imported.tools, ["sqlite_batch", "custom_ops_sync"])
        self.assertEqual(
            imported.secrets,
            [
                {
                    "name": "Ops API token",
                    "key": "OPS_API_TOKEN",
                    "secret_type": "env_var",
                    "description": "Used by the bundled sync tool.",
                }
            ],
        )
        custom_tool = PersistentAgentCustomTool.objects.get(agent=self.agent, tool_name="custom_ops_sync")
        self.assertEqual(custom_tool.source_path, "/tools/global_skills/ops-report/custom_ops_sync.py")
        self.assertTrue(AgentFsNode.objects.filter(path=custom_tool.source_path).exists())
        matching_calls = [
            call for call in mock_track_event.call_args_list
            if call.kwargs.get("event") == AnalyticsEvent.PERSISTENT_AGENT_GLOBAL_SKILL_IMPORTED
        ]
        self.assertEqual(len(matching_calls), 1)
        kwargs = matching_calls[0].kwargs
        self.assertEqual(kwargs["event"], AnalyticsEvent.PERSISTENT_AGENT_GLOBAL_SKILL_IMPORTED)
        self.assertEqual(kwargs["user_id"], self.agent.user_id)
        self.assertEqual(kwargs["properties"]["agent_id"], str(self.agent.id))
        self.assertEqual(kwargs["properties"]["skill_name"], "ops-report")
        self.assertEqual(kwargs["properties"]["skill_version"], 1)
        self.assertEqual(kwargs["properties"]["skill_origin"], "global_import")
        self.assertEqual(kwargs["properties"]["global_skill_id"], str(skill.id))
        self.assertEqual(kwargs["properties"]["global_skill_name"], "ops-report")
        self.assertEqual(kwargs["properties"]["tool_ids"], ["sqlite_batch", "custom_ops_sync"])
        self.assertFalse(kwargs["properties"]["organization"])
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent', return_value=True)
    def test_search_tools_global_skill_bundled_custom_tool_conflict_does_not_overwrite_local_tool(
        self,
        _mock_sandbox_enabled,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        skill = GlobalAgentSkill.objects.create(
            name="ops-report",
            description="Generate an ops report",
            tools=["sqlite_batch"],
            instructions="Collect the latest operational metrics and summarize them.",
        )
        GlobalAgentSkillCustomTool.objects.create(
            global_skill=skill,
            name="Ops Sync",
            tool_name="ops_sync",
            description="Sync metrics into SQLite.",
            source_file=SimpleUploadedFile(
                "ops_sync.py",
                _valid_custom_tool_source(),
                content_type="text/x-python",
            ),
            parameters_schema={"type": "object", "properties": {}},
            timeout_seconds=300,
        )
        PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Local Ops Sync",
            tool_name="custom_ops_sync",
            description="Local tool that should win.",
            source_path="/tools/local_ops_sync.py",
            parameters_schema={"type": "object", "properties": {}},
        )
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        msg = MagicMock()
        msg.content = "Enable the ops report skill."
        setattr(msg, "tool_calls", [
            {
                "type": "function",
                "function": {
                    "name": "enable_global_skills",
                    "arguments": json.dumps({"skill_names": ["ops-report"]}),
                },
            }
        ])
        choice = MagicMock()
        choice.message = msg
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "generate recurring ops reports")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["skills"]["enabled"], [])
        self.assertEqual(result["skills"]["conflicts"], ["ops-report"])
        self.assertFalse(PersistentAgentSkill.objects.filter(agent=self.agent, global_skill=skill).exists())
        local_tool = PersistentAgentCustomTool.objects.get(agent=self.agent, tool_name="custom_ops_sync")
        self.assertEqual(local_tool.source_path, "/tools/local_ops_sync.py")
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.search_tools._fallback_builtin_selection')
    @patch('api.agent.tools.search_tools.enable_global_skills')
    def test_search_tools_global_skill_selection_skips_builtin_fallback(
        self,
        mock_enable_global_skills,
        mock_fallback_builtin_selection,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        GlobalAgentSkill.objects.create(
            name="ops-report",
            description="Generate an ops report",
            tools=["sqlite_batch"],
            instructions="Collect the latest operational metrics and summarize them.",
        )
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]
        mock_enable_global_skills.return_value = {
            "status": "success",
            "enabled": ["ops-report"],
            "already_enabled": [],
            "conflicts": [],
        }
        mock_fallback_builtin_selection.return_value = ["read_file"]

        msg = MagicMock()
        msg.content = "Enable the ops report skill."
        setattr(msg, "tool_calls", [
            {
                "type": "function",
                "function": {
                    "name": "enable_global_skills",
                    "arguments": json.dumps({"skill_names": ["ops-report"]}),
                },
            }
        ])
        choice = MagicMock()
        choice.message = msg
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "generate recurring ops reports")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["skills"]["enabled"], ["ops-report"])
        mock_enable_global_skills.assert_called_once()
        mock_fallback_builtin_selection.assert_not_called()
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.search_tools._fallback_builtin_selection')
    def test_search_tools_exact_global_skill_name_fallback_preempts_builtin_http(
        self,
        mock_fallback_builtin_selection,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        skill = GlobalAgentSkill.objects.create(
            name="reports-http-skill",
            description="Fetch reports through the bundled HTTP workflow",
            tools=["http_request"],
            instructions="Use the report API flow before answering.",
        )
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]
        mock_fallback_builtin_selection.return_value = ["http_request"]

        msg = MagicMock()
        msg.content = ""
        setattr(msg, "tool_calls", [])
        choice = MagicMock()
        choice.message = msg
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "reports-http-skill")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["skills"]["enabled"], ["reports-http-skill"])
        self.assertEqual(result["skills"]["already_enabled"], [])
        self.assertTrue(
            PersistentAgentSkill.objects.filter(agent=self.agent, global_skill=skill).exists()
        )
        mock_fallback_builtin_selection.assert_not_called()
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    def test_search_tools_global_skill_enablement_is_idempotent(
        self,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        skill = GlobalAgentSkill.objects.create(
            name="ops-report",
            description="Generate an ops report",
            tools=["sqlite_batch"],
            instructions="Collect the latest operational metrics and summarize them.",
        )
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            global_skill=skill,
            name="ops-report",
            description=skill.description,
            version=1,
            tools=["sqlite_batch"],
            instructions=skill.instructions,
        )
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        msg = MagicMock()
        msg.content = "Enable the ops report skill."
        setattr(msg, "tool_calls", [
            {
                "type": "function",
                "function": {
                    "name": "enable_global_skills",
                    "arguments": json.dumps({"skill_names": ["ops-report"]}),
                },
            }
        ])
        choice = MagicMock()
        choice.message = msg
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        with patch("util.analytics.Analytics.track_event") as mock_track_event:
            result = search_tools(self.agent, "generate recurring ops reports")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["skills"]["enabled"], [])
        self.assertEqual(result["skills"]["already_enabled"], ["ops-report"])
        self.assertEqual(
            PersistentAgentSkill.objects.filter(agent=self.agent, global_skill=skill).count(),
            1,
        )
        mock_track_event.assert_not_called()
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    def test_search_tools_global_skill_same_name_is_reported_as_already_enabled(
        self,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        skill = GlobalAgentSkill.objects.create(
            name="ops-report",
            description="Generate an ops report",
            tools=["sqlite_batch"],
            instructions="Collect the latest operational metrics and summarize them.",
        )
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="ops-report",
            description="Locally customized skill",
            version=1,
            tools=["sqlite_batch"],
            instructions="Use a local variant instead.",
        )
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        msg = MagicMock()
        msg.content = "Enable the ops report skill."
        setattr(msg, "tool_calls", [
            {
                "type": "function",
                "function": {
                    "name": "enable_global_skills",
                    "arguments": json.dumps({"skill_names": ["ops-report"]}),
                },
            }
        ])
        choice = MagicMock()
        choice.message = msg
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        with patch("util.analytics.Analytics.track_event") as mock_track_event:
            result = search_tools(self.agent, "generate recurring ops reports")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["skills"]["enabled"], [])
        self.assertEqual(result["skills"]["already_enabled"], ["ops-report"])
        self.assertEqual(result["skills"]["conflicts"], [])
        self.assertFalse(PersistentAgentSkill.objects.filter(agent=self.agent, global_skill=skill).exists())
        mock_track_event.assert_not_called()
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.tool_manager.get_enabled_tool_limit', return_value=1)
    def test_search_tools_global_skill_enablement_preserves_required_tools(
        self,
        _mock_limit,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        GlobalAgentSkill.objects.create(
            name="file-review",
            description="Read files before summarizing them",
            tools=["read_file"],
            instructions="Open the requested file before summarizing it.",
        )
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="create_chart",
        )
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        msg = MagicMock()
        msg.content = "Enable the file review skill."
        setattr(msg, "tool_calls", [
            {
                "type": "function",
                "function": {
                    "name": "enable_global_skills",
                    "arguments": json.dumps({"skill_names": ["file-review"]}),
                },
            }
        ])
        choice = MagicMock()
        choice.message = msg
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "review files consistently")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["skills"]["enabled"], ["file-review"])
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(agent=self.agent, tool_full_name="read_file").exists()
        )
        self.assertFalse(
            PersistentAgentEnabledTool.objects.filter(agent=self.agent, tool_full_name="create_chart").exists()
        )
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.search_tools.get_effective_pipedream_app_slugs_for_agent')
    @patch('api.agent.tools.search_tools.PipedreamCatalogService.search_apps')
    @patch('api.agent.tools.search_tools._has_active_pipedream_runtime', return_value=True)
    def test_search_tools_includes_pipedream_app_prompt_section(
        self,
        _mock_has_active_pipedream_runtime,
        mock_search_apps,
        mock_get_effective_pipedream_app_slugs_for_agent,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        self._set_tool_search_auto_enable_apps(True)

        mock_search_apps.return_value = [
            SimpleNamespace(slug="slack", name="Slack"),
            SimpleNamespace(slug="trello", name="Trello"),
        ]
        mock_get_effective_pipedream_app_slugs_for_agent.return_value = ["slack"]
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        mock_response = MagicMock()
        msg = MagicMock()
        msg.content = "No relevant tools."
        setattr(msg, 'tool_calls', [])
        choice = MagicMock()
        choice.message = msg
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "anything")

        self.assertEqual(result["status"], "success")
        mock_search_apps.assert_called_once_with("anything", limit=20)
        user_message = mock_run_completion.call_args.kwargs["messages"][1]["content"]
        self.assertIn("Available Pipedream apps:", user_message)
        self.assertIn("- slack | Slack [enabled]", user_message)
        self.assertIn("- trello | Trello [not enabled]", user_message)
        self.assertNotIn("asana", user_message)
        tool_defs = mock_run_completion.call_args.kwargs["tools"]
        self.assertEqual([tool_def["function"]["name"] for tool_def in tool_defs], ["enable_tools", "enable_apps"])
        mock_enable_tools.assert_not_called()

    @patch("api.services.pipedream_apps.get_connected_pipedream_app_slugs_for_agent", return_value=set())
    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.search_tools.get_effective_pipedream_app_slugs_for_agent')
    @patch('api.agent.tools.search_tools.PipedreamCatalogService.search_apps')
    @patch('api.agent.tools.search_tools._has_active_pipedream_runtime', return_value=True)
    def test_search_tools_filters_deprecated_pipedream_apps_by_connection(
        self,
        _mock_has_active_pipedream_runtime,
        mock_search_apps,
        mock_get_effective_pipedream_app_slugs_for_agent,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
        mock_connected_app_slugs,
    ):
        self._pipedream_server(deprecated_apps=["slack"])
        mock_search_apps.return_value = [
            SimpleNamespace(slug="slack", name="Slack"),
            SimpleNamespace(slug="trello", name="Trello"),
        ]
        self._mock_search_tools_state(mock_get_config, mock_get_manager, mock_run_completion)

        cases = (
            (set(), False),
            ({"slack"}, True),
        )
        for connected_slugs, shows_slack in cases:
            with self.subTest(connected_slugs=connected_slugs):
                mock_connected_app_slugs.return_value = connected_slugs
                mock_get_effective_pipedream_app_slugs_for_agent.return_value = list(connected_slugs)

                result = search_tools(self.agent, "apps")

                self.assertEqual(result["status"], "success")
                user_message = self._completion_user_message(mock_run_completion)
                self.assertIn("Available Pipedream apps:", user_message)
                self.assertIn("- trello | Trello [not enabled]", user_message)
                if shows_slack:
                    self.assertIn("- slack | Slack [enabled]", user_message)
                else:
                    self.assertNotIn("- slack | Slack", user_message)
        mock_enable_tools.assert_not_called()

    @patch("api.services.pipedream_apps.get_connected_pipedream_app_slugs_for_agent", return_value=set())
    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.search_tools._has_active_pipedream_runtime', return_value=False)
    def test_search_tools_hides_deprecated_pipedream_tools_when_unconnected(
        self,
        _mock_has_active_pipedream_runtime,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
        mock_connected_app_slugs,
    ):
        pipedream_server = self._pipedream_server(deprecated_apps=["slack"])
        self._mock_search_tools_state(
            mock_get_config,
            mock_get_manager,
            mock_run_completion,
            tools=[
                MCPToolInfo(
                    str(pipedream_server.id),
                    "slack-send-message",
                    "pipedream",
                    "slack-send-message",
                    "Send Slack message",
                    {},
                ),
                MCPToolInfo(
                    str(pipedream_server.id),
                    "trello-create-card",
                    "pipedream",
                    "trello-create-card",
                    "Create card",
                    {},
                ),
            ],
        )

        result = search_tools(self.agent, "send updates")

        self.assertEqual(result["status"], "success")
        user_message = self._completion_user_message(mock_run_completion)
        self.assertNotIn("- slack-send-message", user_message)
        self.assertIn("- trello-create-card", user_message)
        mock_enable_tools.assert_not_called()

        mock_connected_app_slugs.return_value = {"slack"}
        result = search_tools(self.agent, "send updates")

        self.assertEqual(result["status"], "success")
        connected_user_message = self._completion_user_message(mock_run_completion)
        self.assertIn("- slack-send-message", connected_user_message)
        self.assertIn("- trello-create-card", connected_user_message)

    @override_settings(PUBLIC_SITE_URL="https://gobii.ai")
    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.search_tools.get_effective_pipedream_app_slugs_for_agent')
    @patch('api.agent.tools.search_tools.PipedreamCatalogService.search_apps')
    @patch('api.agent.tools.search_tools._has_active_pipedream_runtime', return_value=True)
    def test_search_tools_guides_manual_app_enablement_when_auto_enablement_disabled(
        self,
        _mock_has_active_pipedream_runtime,
        mock_search_apps,
        mock_get_effective_pipedream_app_slugs_for_agent,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        self._set_tool_search_auto_enable_apps(False)

        mock_search_apps.return_value = [
            SimpleNamespace(slug="slack", name="Slack"),
        ]
        mock_get_effective_pipedream_app_slugs_for_agent.return_value = []
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        mock_response = MagicMock()
        msg = MagicMock()
        msg.content = "Tell the user to enable Slack first."
        setattr(msg, 'tool_calls', [])
        choice = MagicMock()
        choice.message = msg
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "post to slack")

        self.assertEqual(result["status"], "success")
        system_message = mock_run_completion.call_args.kwargs["messages"][0]["content"]
        user_message = mock_run_completion.call_args.kwargs["messages"][1]["content"]
        self.assertIn("Do not call enable_apps", system_message)
        self.assertIn("Automatic Pipedream app enablement is disabled.", system_message)
        self.assertIn('go to "Add Apps" here: https://gobii.ai/app/integrations', system_message)
        self.assertIn('https://gobii.ai/app/integrations', user_message)
        tool_defs = mock_run_completion.call_args.kwargs["tools"]
        self.assertEqual([tool_def["function"]["name"] for tool_def in tool_defs], ["enable_tools"])
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_pipedream_apps_for_agent')
    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.search_tools.get_effective_pipedream_app_slugs_for_agent')
    @patch('api.agent.tools.search_tools.PipedreamCatalogService.search_apps')
    @patch('api.agent.tools.search_tools._has_active_pipedream_runtime', return_value=True)
    def test_search_tools_enable_apps_returns_guidance_and_skips_enable_tools(
        self,
        _mock_has_active_pipedream_runtime,
        mock_search_apps,
        mock_get_effective_pipedream_app_slugs_for_agent,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
        mock_enable_pipedream_apps_for_agent,
    ):
        self._set_tool_search_auto_enable_apps(True)

        mock_search_apps.return_value = [
            SimpleNamespace(slug="slack", name="Slack"),
        ]
        mock_get_effective_pipedream_app_slugs_for_agent.return_value = []
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = [
            MCPToolInfo(self.config_id, "mcp_brightdata_search", "brightdata", "search", "Search web", {}),
        ]
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        tool_calls = [
            {
                "type": "function",
                "function": {
                    "name": "enable_apps",
                    "arguments": json.dumps({"app_slugs": ["slack"]}),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "enable_tools",
                    "arguments": json.dumps({"tool_names": ["mcp_brightdata_search"]}),
                },
            },
        ]
        msg = MagicMock()
        msg.content = "Enable Slack first."
        setattr(msg, "tool_calls", tool_calls)
        choice = MagicMock()
        choice.message = msg
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        mock_enable_pipedream_apps_for_agent.return_value = {
            "status": "success",
            "enabled": ["slack"],
            "already_enabled": [],
            "invalid": [],
            "selected": ["slack"],
            "effective_apps": ["google_sheets", "slack"],
        }

        result = search_tools(self.agent, "post to slack")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["apps"]["enabled"], ["slack"])
        self.assertEqual(result["apps"]["already_enabled"], [])
        self.assertEqual(result["apps"]["invalid"], [])
        self.assertEqual(result["apps"]["effective_apps"], ["google_sheets", "slack"])
        self.assertIn("Run search_tools again", result["message"])
        mock_enable_pipedream_apps_for_agent.assert_called_once_with(
            self.agent,
            ["slack"],
            available_app_slugs=["slack"],
        )
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.search_tools.get_effective_pipedream_app_slugs_for_agent')
    @patch('api.agent.tools.search_tools.PipedreamCatalogService.search_apps')
    @patch('api.agent.tools.search_tools._has_active_pipedream_runtime', return_value=True)
    def test_search_tools_tool_path_still_works_when_app_catalog_available(
        self,
        _mock_has_active_pipedream_runtime,
        mock_search_apps,
        mock_get_effective_pipedream_app_slugs_for_agent,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        mock_search_apps.return_value = [
            SimpleNamespace(slug="slack", name="Slack"),
        ]
        mock_get_effective_pipedream_app_slugs_for_agent.return_value = []
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = [
            MCPToolInfo(self.config_id, "mcp_brightdata_search", "brightdata", "search", "Search web", {}),
        ]
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        msg = MagicMock()
        msg.content = "Enable the search tool."
        setattr(msg, "tool_calls", [
            {
                "type": "function",
                "function": {
                    "name": "enable_tools",
                    "arguments": json.dumps({"tool_names": ["mcp_brightdata_search"]}),
                },
            }
        ])
        choice = MagicMock()
        choice.message = msg
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response
        mock_enable_tools.return_value = {
            "status": "success",
            "enabled": ["mcp_brightdata_search"],
            "already_enabled": [],
            "evicted": [],
            "invalid": [],
        }

        result = search_tools(self.agent, "search the web")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["tools"]["enabled"], ["mcp_brightdata_search"])
        mock_enable_tools.assert_called_once_with(self.agent, ["mcp_brightdata_search"])

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.search_tools.PipedreamCatalogService.search_apps')
    @patch('api.agent.tools.search_tools._has_active_pipedream_runtime', return_value=True)
    def test_search_tools_omits_app_section_when_catalog_lookup_fails(
        self,
        _mock_has_active_pipedream_runtime,
        mock_search_apps,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        from api.services.pipedream_apps import PipedreamCatalogError

        mock_search_apps.side_effect = PipedreamCatalogError("boom")
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        mock_response = MagicMock()
        msg = MagicMock()
        msg.content = "No relevant tools."
        setattr(msg, 'tool_calls', [])
        choice = MagicMock()
        choice.message = msg
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "anything")

        self.assertEqual(result["status"], "success")
        user_message = mock_run_completion.call_args.kwargs["messages"][1]["content"]
        self.assertNotIn("Available Pipedream apps:", user_message)
        tool_defs = mock_run_completion.call_args.kwargs["tools"]
        self.assertEqual([tool_def["function"]["name"] for tool_def in tool_defs], ["enable_tools"])
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    @patch('api.agent.tools.search_tools.get_effective_pipedream_app_slugs_for_agent')
    @patch('api.agent.tools.search_tools.PipedreamCatalogService.search_apps')
    @patch('api.agent.tools.search_tools._has_active_pipedream_runtime', return_value=True)
    def test_search_tools_omits_enable_apps_when_query_shortlist_empty(
        self,
        _mock_has_active_pipedream_runtime,
        mock_search_apps,
        mock_get_effective_pipedream_app_slugs_for_agent,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        mock_search_apps.return_value = []
        mock_get_effective_pipedream_app_slugs_for_agent.return_value = ["slack"]
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        mock_response = MagicMock()
        msg = MagicMock()
        msg.content = "No relevant tools."
        setattr(msg, 'tool_calls', [])
        choice = MagicMock()
        choice.message = msg
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "very specific unknown app")

        self.assertEqual(result["status"], "success")
        user_message = mock_run_completion.call_args.kwargs["messages"][1]["content"]
        self.assertNotIn("Available Pipedream apps:", user_message)
        tool_defs = mock_run_completion.call_args.kwargs["tools"]
        self.assertEqual([tool_def["function"]["name"] for tool_def in tool_defs], ["enable_tools"])
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    def test_search_tools_includes_create_image_when_configured(
        self,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        """search_tools should include create_image once image tiers are configured."""
        self._seed_create_image_tier()

        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        mock_response = MagicMock()
        msg = MagicMock()
        msg.content = "No relevant tools."
        setattr(msg, 'tool_calls', [])
        choice = MagicMock()
        choice.message = msg
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "anything")
        self.assertEqual(result["status"], "success")
        mock_run_completion.assert_called_once()
        _args, kwargs = mock_run_completion.call_args
        user_message = kwargs["messages"][1]["content"]
        self.assertIn("create_image", user_message)
        mock_enable_tools.assert_not_called()

    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    def test_search_tools_includes_image_generation_system_skill_when_configured(
        self,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
    ):
        self._seed_create_image_tier()

        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        mock_response = MagicMock()
        msg = MagicMock()
        msg.content = "No relevant tools."
        setattr(msg, 'tool_calls', [])
        choice = MagicMock()
        choice.message = msg
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "generate a logo image")

        self.assertEqual(result["status"], "success")
        user_message = mock_run_completion.call_args.kwargs["messages"][1]["content"]
        self.assertIn("Available system skills:", user_message)
        self.assertIn(f"- {IMAGE_GENERATION_SYSTEM_SKILL_KEY}:", user_message)
        self.assertIn("create_image", user_message)
        system_message = mock_run_completion.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Treat system skills as capability bundles", system_message)
        mock_enable_tools.assert_not_called()

    def test_enable_create_image_enables_image_generation_system_skill(self):
        self._seed_create_image_tier()

        result = enable_tools(self.agent, ["create_image"])

        self.assertEqual(result["status"], "success")
        self.assertIn("create_image", result["enabled"])
        state = PersistentAgentSystemSkillState.objects.get(
            agent=self.agent,
            skill_key=IMAGE_GENERATION_SYSTEM_SKILL_KEY,
        )
        self.assertTrue(state.is_enabled)

    def test_loading_existing_create_image_backfills_missing_system_skill_state(self):
        self._seed_create_image_tier()
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="create_image",
            tool_server="builtin",
            tool_name="create_image",
        )

        definitions = get_enabled_tool_definitions(self.agent)

        self.assertIn("create_image", self._tool_def_names(definitions))
        self.assertTrue(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key=IMAGE_GENERATION_SYSTEM_SKILL_KEY,
                is_enabled=True,
            ).exists()
        )

    def test_loading_existing_create_image_preserves_disabled_system_skill_state(self):
        self._seed_create_image_tier()
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="create_image",
            tool_server="builtin",
            tool_name="create_image",
        )
        state = PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key=IMAGE_GENERATION_SYSTEM_SKILL_KEY,
            is_enabled=False,
        )

        definitions = get_enabled_tool_definitions(self.agent)
        state.refresh_from_db()

        self.assertIn("create_image", self._tool_def_names(definitions))
        self.assertFalse(state.is_enabled)

    @patch('api.agent.tools.tool_manager.sandbox_compute_enabled_for_agent', return_value=False)
    @patch('api.agent.tools.search_tools.enable_tools')
    @patch('api.agent.tools.search_tools.run_completion')
    @patch('api.agent.tools.search_tools.get_mcp_manager')
    @patch('api.agent.tools.search_tools.get_llm_config_with_failover')
    def test_search_tools_catalog_omits_sandbox_only_builtins(
        self,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
        _mock_sandbox_enabled,
    ):
        """search_tools should not advertise sandbox-only builtins when unavailable."""
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        mock_response = MagicMock()
        msg = MagicMock()
        msg.content = "No relevant tools."
        setattr(msg, 'tool_calls', [])
        choice = MagicMock()
        choice.message = msg
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "anything")
        self.assertEqual(result["status"], "success")
        mock_run_completion.assert_called_once()
        _args, kwargs = mock_run_completion.call_args
        user_message = kwargs["messages"][1]["content"]
        self.assertNotIn("python_exec", user_message)
        self.assertNotIn("run_command", user_message)
        mock_enable_tools.assert_not_called()
        
    @tag("batch_mcp_tools")
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_mark_tool_enabled_without_discovery_skips_discovery(self, mock_get_manager):
        result = mark_tool_enabled_without_discovery(self.agent, "mcp_test_tool")

        self.assertEqual(result["status"], "success")
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent, tool_full_name="mcp_test_tool"
            ).exists()
        )
        mock_get_manager.assert_not_called()

    @tag("batch_mcp_tools")
    @patch("api.agent.tools.tool_manager.get_enabled_tool_limit", return_value=1)
    def test_mark_tool_enabled_without_discovery_evicts_oldest(self, mock_limit):
        mark_tool_enabled_without_discovery(self.agent, "mcp_old_tool")
        old_row = PersistentAgentEnabledTool.objects.get(
            agent=self.agent, tool_full_name="mcp_old_tool"
        )
        old_row.last_used_at = timezone.now() - timedelta(days=1)
        old_row.save(update_fields=["last_used_at"])

        result = mark_tool_enabled_without_discovery(self.agent, "mcp_new_tool")

        self.assertEqual(result["status"], "success")
        self.assertIn("mcp_new_tool", result["message"])
        names = set(
            PersistentAgentEnabledTool.objects.filter(agent=self.agent).values_list(
                "tool_full_name", flat=True
            )
        )
        self.assertIn("mcp_new_tool", names)
        self.assertNotIn("mcp_old_tool", names)

    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent', return_value=[])
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    def test_enable_tools_includes_sqlite(self, mock_init, mock_get_tools):
        """Enable tools should handle built-in sqlite tool."""
        result = enable_tools(self.agent, ["sqlite_batch"])
        self.assertEqual(result["status"], "success")
        self.assertIn("sqlite_batch", result["enabled"])
        row = PersistentAgentEnabledTool.objects.get(agent=self.agent, tool_full_name="sqlite_batch")
        self.assertEqual(row.tool_server, "builtin")
        self.assertEqual(row.tool_name, "sqlite_batch")

    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent', return_value=[])
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    def test_get_enabled_tool_definitions_includes_sqlite(self, mock_init, mock_get_tools):
        """Enabled tool definitions include sqlite_batch when enabled."""
        # Set up paid account with max intelligence (required for sqlite access)
        billing, _ = UserBilling.objects.get_or_create(user=self.user)
        billing.subscription = PlanNames.STARTUP
        billing.save(update_fields=["subscription"])
        self.agent.preferred_llm_tier = get_intelligence_tier("max")
        self.agent.save(update_fields=["preferred_llm_tier"])

        enable_tools(self.agent, ["sqlite_batch"])
        definitions = get_enabled_tool_definitions(self.agent)
        names = {
            entry.get("function", {}).get("name")
            for entry in definitions
            if isinstance(entry, dict)
        }
        self.assertIn("sqlite_batch", names)
        
    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent', return_value=[])
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    def test_enable_tools_includes_http_request(self, mock_init, mock_get_tools):
        """Enable tools should handle built-in http_request tool."""
        result = enable_tools(self.agent, ["http_request"])
        self.assertEqual(result["status"], "success")
        self.assertIn("http_request", result["enabled"])
        row = PersistentAgentEnabledTool.objects.get(agent=self.agent, tool_full_name="http_request")
        self.assertEqual(row.tool_server, "builtin")
        self.assertEqual(row.tool_name, "http_request")

    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent', return_value=[])
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    def test_get_enabled_tool_definitions_includes_http_request(self, mock_init, mock_get_tools):
        """Enabled tool definitions include http_request when enabled."""
        enable_tools(self.agent, ["http_request"])
        definitions = get_enabled_tool_definitions(self.agent)
        names = {
            entry.get("function", {}).get("name")
            for entry in definitions
            if isinstance(entry, dict)
        }
        self.assertIn("http_request", names)

    @tag("batch_mcp_tools")
    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent', return_value=[])
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    def test_tier_blacklisted_builtin_cannot_be_enabled(self, mock_init, mock_get_tools):
        """Tier blacklists remove builtins from the enableable catalog."""
        tier = get_intelligence_tier("standard")
        tier.blacklisted_tools = ["http_request"]
        tier.save(update_fields=["blacklisted_tools"])
        self.agent.preferred_llm_tier = tier
        self.agent.save(update_fields=["preferred_llm_tier"])

        result = enable_tools(self.agent, ["http_request"])

        self.assertEqual(result["status"], "success")
        self.assertIn("http_request", result["invalid"])
        self.assertFalse(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent,
                tool_full_name="http_request",
            ).exists()
        )

    @tag("batch_mcp_tools")
    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent', return_value=[])
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    def test_tier_blacklisted_enabled_builtin_is_hidden_and_not_executed(self, mock_init, mock_get_tools):
        """Previously enabled rows do not bypass the intelligence-tier blacklist."""
        tier = get_intelligence_tier("standard")
        tier.blacklisted_tools = ["http_request"]
        tier.save(update_fields=["blacklisted_tools"])
        self.agent.preferred_llm_tier = tier
        self.agent.save(update_fields=["preferred_llm_tier"])
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="http_request",
            tool_server="builtin",
            tool_name="http_request",
        )

        definitions = get_enabled_tool_definitions(self.agent)
        names = {
            entry.get("function", {}).get("name")
            for entry in definitions
            if isinstance(entry, dict)
        }
        result = execute_enabled_tool(self.agent, "http_request", {"url": "https://example.com"})

        self.assertNotIn("http_request", names)
        self.assertEqual(result["status"], "error")
        self.assertIn("not available", result["message"])

    @tag("batch_mcp_tools")
    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_tier_blacklisted_static_tool_is_hidden_and_rejected(self):
        """Static tools also respect intelligence-tier blacklists."""
        tier = get_intelligence_tier("standard")
        tier.blacklisted_tools = ["search_tools"]
        tier.save(update_fields=["blacklisted_tools"])
        self.agent.preferred_llm_tier = tier
        self.agent.save(update_fields=["preferred_llm_tier"])

        names = get_static_tool_names(self.agent)
        result, updated_tools = execute_runtime_tool_call(
            self.agent,
            tool_name="search_tools",
            exec_params={"query": "anything"},
        )

        self.assertNotIn("search_tools", names)
        self.assertIsNone(updated_tools)
        self.assertEqual(result["status"], "error")
        self.assertIn("intelligence tier", result["message"])

    @tag("batch_mcp_tools")
    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent')
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    def test_tier_blacklisted_mcp_tool_cannot_be_enabled(self, mock_init, mock_get_tools):
        """Tier blacklists reject MCP tools even when the server exposes them."""
        tier = get_intelligence_tier("standard")
        tier.blacklisted_tools = ["mcp_test_tool"]
        tier.save(update_fields=["blacklisted_tools"])
        self.agent.preferred_llm_tier = tier
        self.agent.save(update_fields=["preferred_llm_tier"])
        mock_get_tools.return_value = [
            MCPToolInfo(self.config_id, "mcp_test_tool", self.server_name, "tool", "Test tool", {})
        ]

        result = enable_tools(self.agent, ["mcp_test_tool"])

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["invalid"], ["mcp_test_tool"])
        self.assertFalse(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent,
                tool_full_name="mcp_test_tool",
            ).exists()
        )

    @patch("api.agent.tools.tool_manager.is_custom_tools_available_for_agent", return_value=False)
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_get_enabled_tool_definitions_sanitizes_mcp_array_items(
        self,
        mock_get_manager,
        _mock_custom_available,
    ):
        """Enabled MCP tool definitions include required array item schemas."""
        mock_manager = MagicMock()
        mock_manager.get_enabled_tools_definitions.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "mcp_analytics-db_pg_execute_sql",
                    "description": "Execute SQL",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "sql": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string"},
                                    "params": {"type": "array"},
                                },
                            },
                        },
                    },
                },
            }
        ]
        mock_get_manager.return_value = mock_manager

        definitions = get_enabled_tool_definitions(self.agent)
        tool_def = definitions[0]

        self.assertEqual(
            tool_def["function"]["parameters"]["properties"]["sql"]["properties"]["params"]["items"],
            {"type": "string"},
        )

    @tag("batch_mcp_tools")
    @patch("api.services.pipedream_apps.get_connected_pipedream_app_slugs_for_agent", return_value=set())
    @patch("api.agent.tools.tool_manager.is_custom_tools_available_for_agent", return_value=False)
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_get_enabled_tool_definitions_hides_unconnected_deprecated_pipedream_tools(
        self,
        mock_get_manager,
        _mock_custom_available,
        mock_connected_app_slugs,
    ):
        pipedream_server = self._pipedream_server(deprecated_apps=["slack"])
        for tool_name in ("slack-send-message", "trello-create-card"):
            PersistentAgentEnabledTool.objects.create(
                agent=self.agent,
                tool_full_name=tool_name,
                tool_server="pipedream",
                tool_name=tool_name,
                server_config=pipedream_server,
            )
        mock_manager = MagicMock()
        mock_manager.get_enabled_tools_definitions.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "slack-send-message",
                    "description": "Send Slack message",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "trello-create-card",
                    "description": "Create card",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]
        mock_get_manager.return_value = mock_manager

        definitions = get_enabled_tool_definitions(self.agent)
        names = self._tool_def_names(definitions)

        self.assertNotIn("slack-send-message", names)
        self.assertIn("trello-create-card", names)
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent,
                tool_full_name="slack-send-message",
            ).exists()
        )

        mock_connected_app_slugs.return_value = {"slack"}
        connected_definitions = get_enabled_tool_definitions(self.agent)
        connected_names = self._tool_def_names(connected_definitions)
        self.assertIn("slack-send-message", connected_names)

@tag("batch_mcp_tools")
class MCPToolExecutorsTests(TestCase):
    """Test tool executor functions."""
    
    def setUp(self):
        """Set up test fixtures."""
        User = get_user_model()
        self.user = User.objects.create_user(username='test@example.com')
        self.browser_agent = create_test_browser_agent(self.user)
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="test-agent",
            charter="Test",
            browser_use_agent=self.browser_agent
        )
        
    def test_get_search_tools_tool_definition(self):
        """Test search_tools tool definition."""
        tool_def = get_search_tools_tool()
        
        self.assertEqual(tool_def["function"]["name"], "search_tools")
        self.assertIn("query", tool_def["function"]["parameters"]["properties"])
        self.assertNotIn("will_continue_work", tool_def["function"]["parameters"]["properties"])
        self.assertEqual(tool_def["function"]["parameters"]["required"], ["query"])
        description = tool_def["function"]["description"]
        self.assertIn("no enabled tool clearly fits", description)
        self.assertIn("do not rediscover", description)
        self.assertNotIn("NOT for web search", description)
        
    @patch('api.agent.tools.search_tools.search_tools')
    def test_execute_search_tools(self, mock_search):
        """Test executing search_tools function returns pass-through result."""
        mock_search.return_value = {
            "status": "success",
            "message": "Enabled: mcp_tool_a",
            "tools": {
                "enabled": ["mcp_tool_a"],
                "already_enabled": [],
                "evicted": [],
                "invalid": [],
            },
        }
        result = execute_search_tools(self.agent, {"query": "test query"})
        self.assertEqual(result["status"], "success")
        self.assertIn("Enabled: mcp_tool_a", result["message"]) 
        mock_search.assert_called_once_with(self.agent, "test query")

    @patch('api.agent.tools.search_tools.search_tools')
    def test_execute_search_tools_ignores_stale_will_continue_work_false(self, mock_search):
        """search_tools should never opt into auto-sleep."""
        mock_search.return_value = {
            "status": "success",
            "message": "Enabled: mcp_tool_a",
            "tools": {"enabled": ["mcp_tool_a"], "already_enabled": []},
        }
        result = execute_search_tools(self.agent, {"query": "test query", "will_continue_work": False})
        self.assertEqual(result["status"], "success")
        self.assertNotIn("auto_sleep_ok", result)
        
    def test_execute_search_tools_missing_query(self):
        """Test search_tools with missing query."""
        result = execute_search_tools(self.agent, {})
        
        self.assertEqual(result["status"], "error")
        self.assertIn("Missing required parameter: query", result["message"])
        
    # enable_tool is no longer exposed to the main agent; auto-enabling is handled inside search_tools
        
    @patch('api.agent.tools.mcp_manager._mcp_manager')
    def test_execute_mcp_tool(self, mock_manager):
        """Test executing an MCP tool."""
        tool = MCPToolInfo(
            str(uuid.uuid4()),
            "mcp_test_tool",
            "test-server",
            "tool",
            "Test tool",
            {},
        )
        mock_manager._initialized = True
        mock_manager.prepare_tool_for_agent.return_value = tool
        mock_manager.execute_mcp_tool.return_value = {
            "status": "success",
            "result": "Tool executed"
        }
        
        result = execute_mcp_tool(self.agent, "mcp_test_tool", {"param": "value"})
        
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["result"], "Tool executed")
        mock_manager.execute_mcp_tool.assert_called_once_with(
            self.agent,
            "mcp_test_tool",
            {"param": "value"},
            force_local=False,
            tool_info=tool,
        )


@tag("batch_mcp_tools")
class MCPToolIntegrationTests(TestCase):
    """Integration tests for MCP tool system."""
    
    def setUp(self):
        """Set up test fixtures."""
        User = get_user_model()
        self.user = User.objects.create_user(username='test@example.com')
        self.browser_agent = create_test_browser_agent(self.user)
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="test-agent",
            charter="Test",
            browser_use_agent=self.browser_agent
        )
        self.server_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name=f"integration-platform-{uuid.uuid4().hex[:8]}",
            display_name="Integration Platform Server",
            description="",
            command="npx",
            command_args=[],
        )
        self.config_id = str(self.server_config.id)
        self.server_name = self.server_config.name

    def _discord_send_tool_info(self) -> MCPToolInfo:
        return MCPToolInfo(
            self.config_id,
            "discord-send-message",
            "pipedream",
            "discord-send-message",
            "Send Discord message",
            {
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "message": {"type": "string"},
                    "avatarURL": {"type": "string"},
                    "username": {"type": "string"},
                    "includeSentViaPipedream": {"type": "boolean"},
                },
            },
        )

    def _pipedream_runtime(self, config: MCPServerConfig) -> MCPServerRuntime:
        return MCPServerRuntime(
            config_id=str(config.id),
            name=config.name,
            display_name=config.display_name,
            description=config.description,
            command=config.command or None,
            args=list(config.command_args or []),
            url=config.url or "",
            auth_method=config.auth_method,
            env=config.environment or {},
            headers=config.headers or {},
            prefetch_apps=list(config.prefetch_apps or []),
            scope=config.scope,
            organization_id=str(config.organization_id) if config.organization_id else None,
            user_id=str(config.user_id) if config.user_id else None,
            updated_at=config.updated_at,
        )
        
    @patch('api.agent.tools.tool_manager._get_manager')
    @patch('api.agent.tools.tool_manager.execute_mcp_tool')
    def test_execute_enabled_tool_auto_enables_mcp(self, mock_execute, mock_get_manager):
        """Tool execution should auto-enable MCP tools when missing."""
        mock_manager = MagicMock()
        mock_manager.get_tools_for_agent.return_value = [
            MCPToolInfo(self.config_id, "mcp_test_tool", self.server_name, "tool", "Test", {})
        ]
        mock_manager.prepare_tool_for_agent.return_value = mock_manager.get_tools_for_agent.return_value[0]
        mock_manager.is_tool_blacklisted.return_value = False
        mock_get_manager.return_value = mock_manager

        mock_execute.return_value = {"status": "success", "result": "ok"}

        self.assertFalse(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent, tool_full_name="mcp_test_tool"
            ).exists()
        )
        result = execute_enabled_tool(self.agent, "mcp_test_tool", {"foo": "bar"})

        self.assertEqual(result["status"], "success")
        mock_execute.assert_called_once_with(
            self.agent,
            "mcp_test_tool",
            {"foo": "bar"},
            tool_info=mock_manager.prepare_tool_for_agent.return_value,
        )
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent, tool_full_name="mcp_test_tool"
            ).exists()
        )

    @patch("api.agent.tools.tool_manager.get_mcp_manager")
    def test_get_manager_does_not_trigger_global_initialization(self, mock_get_manager):
        from api.agent.tools import tool_manager

        manager = MagicMock()
        mock_get_manager.return_value = manager

        self.assertIs(tool_manager._get_manager(), manager)
        manager.initialize.assert_not_called()

    def test_mcp_execution_wrappers_reuse_prepared_tool_info(self):
        from api.agent.tools import mcp_manager

        agent = SimpleNamespace(id=uuid.uuid4())
        tool = MCPToolInfo(
            self.config_id,
            "mcp_test_tool",
            self.server_name,
            "tool",
            "Test",
            {},
        )
        alias = "mcp_testtool"
        with patch.object(mcp_manager, "_mcp_manager") as manager:
            manager.prepare_tool_for_agent.return_value = tool
            manager.execute_mcp_tool.return_value = {"status": "success"}
            manager.execute_mcp_tool_isolated.return_value = {"status": "success"}

            self.assertEqual(
                mcp_manager.execute_mcp_tool(
                    agent,
                    alias,
                    {},
                    tool_info=tool,
                ),
                {"status": "success"},
            )
            self.assertEqual(
                mcp_manager.execute_mcp_tool_isolated(
                    agent,
                    alias,
                    {},
                    tool_info=tool,
                ),
                {"status": "success"},
            )

        manager.initialize.assert_not_called()
        manager.prepare_tool_for_agent.assert_not_called()
        manager.execute_mcp_tool.assert_called_once_with(
            agent,
            tool.full_name,
            {},
            force_local=False,
            tool_info=tool,
        )
        manager.execute_mcp_tool_isolated.assert_called_once_with(
            agent,
            tool.full_name,
            {},
            tool_info=tool,
        )

    @patch('api.agent.tools.tool_manager._get_manager')
    @patch('api.agent.tools.tool_manager.execute_mcp_tool')
    def test_execute_enabled_tool_decodes_pipedream_unicode_escapes(self, mock_execute, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.get_tools_for_agent.return_value = [
            MCPToolInfo(
                self.config_id,
                "notion-create-page",
                "pipedream",
                "notion-create-page",
                "Create Notion page",
                {},
            )
        ]
        mock_manager.prepare_tool_for_agent.return_value = mock_manager.get_tools_for_agent.return_value[0]
        mock_manager.is_tool_blacklisted.return_value = False
        mock_get_manager.return_value = mock_manager
        mock_execute.return_value = {"status": "success", "result": "ok"}

        escaped_message = "Look \\ud83d\\udd75\\ufe0f\\u200d\\u2642\\ufe0f\\u2728"

        result = execute_enabled_tool(
            self.agent,
            "notion-create-page",
            {
                "title": escaped_message,
                "properties": {"body": escaped_message},
                "items": [escaped_message],
            },
        )

        self.assertEqual(result["status"], "success")
        executed_params = mock_execute.call_args.args[2]
        expected_message = "Look \U0001f575\ufe0f\u200d\u2642\ufe0f\u2728"
        self.assertEqual(executed_params["title"], expected_message)
        self.assertEqual(executed_params["properties"]["body"], expected_message)
        self.assertEqual(executed_params["items"], [expected_message])

    @patch('api.agent.tools.tool_manager._get_manager')
    @patch('api.agent.tools.tool_manager.execute_mcp_tool')
    def test_execute_enabled_tool_leaves_pipedream_discord_send_params_unchanged(self, mock_execute, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.get_tools_for_agent.return_value = [self._discord_send_tool_info()]
        mock_manager.prepare_tool_for_agent.return_value = mock_manager.get_tools_for_agent.return_value[0]
        mock_manager.is_tool_blacklisted.return_value = False
        mock_get_manager.return_value = mock_manager
        mock_execute.return_value = {"status": "complete", "result": "ok"}

        result = execute_enabled_tool(
            self.agent,
            "discord-send-message",
            {
                "channel": "1492138162066034751",
                "message": "hello",
            },
        )

        self.assertEqual(result["status"], "complete")
        executed_params = mock_execute.call_args.args[2]
        self.assertEqual(executed_params["channel"], "1492138162066034751")
        self.assertEqual(executed_params["message"], "hello")
        self.assertNotIn("avatarURL", executed_params)
        self.assertNotIn("username", executed_params)
        self.assertNotIn("includeSentViaPipedream", executed_params)

    @patch('api.agent.tools.tool_manager._get_manager')
    @patch('api.agent.tools.tool_manager.execute_mcp_tool')
    def test_execute_enabled_tool_preserves_discord_send_overrides(self, mock_execute, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.get_tools_for_agent.return_value = [self._discord_send_tool_info()]
        mock_manager.prepare_tool_for_agent.return_value = mock_manager.get_tools_for_agent.return_value[0]
        mock_manager.is_tool_blacklisted.return_value = False
        mock_get_manager.return_value = mock_manager
        mock_execute.return_value = {"status": "complete", "result": "ok"}

        result = execute_enabled_tool(
            self.agent,
            "discord-send-message",
            {
                "channel": "1492138162066034751",
                "message": "hello",
                "avatarURL": "https://example.com/avatar.png",
                "username": "Custom Bot",
                "includeSentViaPipedream": True,
            },
        )

        self.assertEqual(result["status"], "complete")
        executed_params = mock_execute.call_args.args[2]
        self.assertEqual(executed_params["avatarURL"], "https://example.com/avatar.png")
        self.assertEqual(executed_params["username"], "Custom Bot")
        self.assertIs(executed_params["includeSentViaPipedream"], True)

    def test_execute_mcp_tool_infers_pipedream_app_slug_from_component_key(self):
        config, _created = MCPServerConfig.objects.update_or_create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="pipedream",
            defaults={
                "display_name": "Pipedream",
                "description": "Pipedream remote MCP",
                "command": "",
                "command_args": [],
                "url": "https://remote.mcp.pipedream.net",
                "prefetch_apps": [],
                "metadata": {},
                "is_active": True,
            },
        )
        runtime = self._pipedream_runtime(config)
        manager = MCPToolManager()
        manager._initialized = True
        manager._server_cache = {runtime.config_id: runtime}
        manager._tools_cache = {
            runtime.config_id: [
                MCPToolInfo(
                    runtime.config_id,
                    "retrieve_options",
                    "pipedream",
                    "retrieve_options",
                    "Retrieve component options",
                    {
                        "type": "object",
                        "properties": {
                            "componentKey": {"type": "string"},
                            "propName": {"type": "string"},
                            "configuredProps": {"type": "object"},
                        },
                    },
                )
            ]
        }
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="retrieve_options",
            tool_server="pipedream",
            tool_name="retrieve_options",
            server_config=config,
        )
        client = MagicMock()

        with (
            patch.object(manager, "_ensure_runtime_registered", return_value=True),
            patch.object(manager, "_select_agent_proxy_url", return_value=(None, None)),
            patch("api.services.pipedream_connections.list_pipedream_connected_accounts", return_value=[object()]),
            patch.object(manager, "_get_pipedream_agent_client", return_value=client) as mock_get_client,
            patch.object(
                manager,
                "_execute_async",
                new=MagicMock(return_value={"result": {"ok": True}}),
            ) as mock_execute,
            patch.object(manager, "_run_coroutine_sync", side_effect=lambda value: value),
        ):
            result = manager.execute_mcp_tool(
                self.agent,
                "retrieve_options",
                {
                    "componentKey": "discord-send-message",
                    "propName": "channel",
                },
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["result"], {"ok": True})
        self.assertEqual(mock_get_client.call_args.kwargs["app_slug"], "discord")
        mock_execute.assert_called_once()

    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent', return_value=[])
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    def test_builtin_execution_updates_usage(self, mock_init, mock_get_tools):
        """Executing a builtin tool should record usage to avoid premature eviction."""
        from api.agent.tools import tool_manager as tm

        # Set up paid account with max intelligence (required for sqlite access)
        billing, _ = UserBilling.objects.get_or_create(user=self.user)
        billing.subscription = PlanNames.STARTUP
        billing.save(update_fields=["subscription"])
        self.agent.preferred_llm_tier = get_intelligence_tier("max")
        self.agent.save(update_fields=["preferred_llm_tier"])

        mock_sqlite = MagicMock(return_value={"status": "ok"})
        original_executor = tm.BUILTIN_TOOL_REGISTRY["sqlite_batch"]["executor"]
        try:
            tm.BUILTIN_TOOL_REGISTRY["sqlite_batch"]["executor"] = mock_sqlite

            enable_tools(self.agent, ["sqlite_batch"])
            row = PersistentAgentEnabledTool.objects.get(
                agent=self.agent,
                tool_full_name="sqlite_batch",
            )
            self.assertIsNone(row.last_used_at)
            self.assertEqual(row.usage_count, 0)

            result = execute_enabled_tool(self.agent, "sqlite_batch", {"sql": "select 1"})
        finally:
            tm.BUILTIN_TOOL_REGISTRY["sqlite_batch"]["executor"] = original_executor

        self.assertEqual(result["status"], "ok")
        mock_sqlite.assert_called_once_with(self.agent, {"sql": "select 1"})
        row.refresh_from_db()
        self.assertIsNotNone(row.last_used_at)
        self.assertEqual(row.usage_count, 1)

    @patch('api.agent.tools.tool_manager._get_manager')
    def test_execute_enabled_tool_auto_enables_builtin(self, mock_get_manager):
        """Tool execution should auto-enable builtin tools when missing."""
        from api.agent.tools import tool_manager as tm

        mock_manager = MagicMock()
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager

        mock_executor = MagicMock(return_value={"status": "ok"})
        original_executor = tm.BUILTIN_TOOL_REGISTRY["read_file"]["executor"]
        try:
            tm.BUILTIN_TOOL_REGISTRY["read_file"]["executor"] = mock_executor
            result = execute_enabled_tool(self.agent, "read_file", {"path": "notes.md"})
        finally:
            tm.BUILTIN_TOOL_REGISTRY["read_file"]["executor"] = original_executor

        self.assertEqual(result["status"], "ok")
        mock_executor.assert_called_once_with(self.agent, {"path": "notes.md"})
        row = PersistentAgentEnabledTool.objects.get(agent=self.agent, tool_full_name="read_file")
        self.assertIsNotNone(row.last_used_at)
        self.assertEqual(row.usage_count, 1)

    def test_enable_tools_batch_with_lru(self):
        """Batch enabling enforces cap and evicts LRU as needed."""
        User = get_user_model()
        user = User.objects.create_user(username='batch@example.com')
        browser_agent = create_test_browser_agent(user)
        agent = PersistentAgent.objects.create(
            user=user,
            name="batch-agent",
            charter="Test",
            browser_use_agent=browser_agent,
        )

        # Populate the global cache used by enable_tools
        from api.agent.tools import mcp_manager as mm
        mm._mcp_manager._initialized = True
        tools = [MCPToolInfo(self.config_id, f"mcp_t{i}", self.server_name, f"t{i}", f"Tool {i}", {}) for i in range(45)]
        mm._mcp_manager._tools_cache = {self.config_id: tools}

        with patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent', return_value=tools):
            # Pre-fill 38 tools so a batch of 5 causes 3 evictions
            pre = [f"mcp_t{i}" for i in range(38)]
            for i, name in enumerate(pre):
                mark_tool_enabled_without_discovery(agent, name)
                # Stagger usage to influence eviction
                row = PersistentAgentEnabledTool.objects.get(agent=agent, tool_full_name=name)
                from django.utils import timezone
                row.last_used_at = timezone.now()
                row.save(update_fields=["last_used_at"])

            result = enable_tools(agent, [f"mcp_t{i}" for i in range(38, 43)])  # 5 new

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["enabled"]), 5)
        self.assertEqual(len(result["evicted"]), 3)
        agent.refresh_from_db()
        self.assertEqual(PersistentAgentEnabledTool.objects.filter(agent=agent).count(), 40)

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent')
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    def test_enable_tools_uses_prompt_config_limit(self, mock_init, mock_get_tools):
        """Tiered prompt configuration controls the enabled tool cap."""

        config, _ = PromptConfig.objects.get_or_create(singleton_id=1)
        original_limit = config.standard_enabled_tool_limit

        def _restore_prompt_limit():
            config.standard_enabled_tool_limit = original_limit
            config.save()
            invalidate_prompt_settings_cache()

        self.addCleanup(_restore_prompt_limit)

        config.standard_enabled_tool_limit = 5
        config.save()
        invalidate_prompt_settings_cache()

        tools = [
            MCPToolInfo(self.config_id, f"mcp_conf_{i}", self.server_name, f"t{i}", f"Tool {i}", {})
            for i in range(10)
        ]
        mock_get_tools.return_value = tools

        pre_enabled = [f"mcp_conf_{i}" for i in range(4)]
        for name in pre_enabled:
            mark_tool_enabled_without_discovery(self.agent, name)

        result = enable_tools(self.agent, [f"mcp_conf_{i}" for i in range(4, 7)])

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["enabled"]), 3)
        self.assertEqual(len(result["evicted"]), 2)
        self.assertEqual(
            PersistentAgentEnabledTool.objects.filter(agent=self.agent).count(),
            config.standard_enabled_tool_limit,
        )


@tag("batch_mcp_tools")
class ToolNameNormalizationTests(TestCase):
    """Test MCP tool name normalization and fuzzy matching."""

    def setUp(self):
        """Set up test fixtures."""
        User = get_user_model()
        self.user = User.objects.create_user(username=f'norm-{uuid.uuid4().hex[:8]}@example.com')
        with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
            browser_agent = BrowserUseAgent.objects.create(user=self.user, name="test-norm-agent")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name=f"norm-agent-{uuid.uuid4().hex[:6]}",
            charter="Test normalization",
            browser_use_agent=browser_agent,
        )
        self.server_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="brightdata",
            display_name="Bright Data",
            description="",
            command="npx",
            command_args=[],
        )
        self.config_id = str(self.server_config.id)

    def test_normalize_mcp_tool_name_bright_data_variation(self):
        """Test normalizing mcp_bright_data_* to mcp_brightdata_*."""
        from api.agent.tools.tool_manager import _normalize_mcp_tool_name, ToolCatalogEntry

        catalog = {
            "mcp_brightdata_search_engine_batch": ToolCatalogEntry(
                provider="mcp",
                full_name="mcp_brightdata_search_engine_batch",
                description="Batch search",
                parameters={},
                tool_server="brightdata",
                tool_name="search_engine_batch",
                server_config_id=self.config_id,
            )
        }

        # Test the variation with underscore in server name
        result = _normalize_mcp_tool_name("mcp_bright_data_search_engine_batch", catalog)
        self.assertEqual(result, "mcp_brightdata_search_engine_batch")

    def test_normalize_mcp_tool_name_no_match_returns_none(self):
        """If no matching tool in catalog, normalization returns None."""
        from api.agent.tools.tool_manager import _normalize_mcp_tool_name, ToolCatalogEntry

        catalog = {
            "mcp_other_tool": ToolCatalogEntry(
                provider="mcp",
                full_name="mcp_other_tool",
                description="Other",
                parameters={},
                tool_server="other",
                tool_name="tool",
                server_config_id=self.config_id,
            )
        }

        # No matching tool - normalization should return None
        result = _normalize_mcp_tool_name("mcp_brightdata_scrape", catalog)
        self.assertIsNone(result)

    def test_normalize_mcp_tool_name_collapsed_match(self):
        """Test matching when collapsing all underscores."""
        from api.agent.tools.tool_manager import _normalize_mcp_tool_name, ToolCatalogEntry

        catalog = {
            "mcp_my_server_tool": ToolCatalogEntry(
                provider="mcp",
                full_name="mcp_my_server_tool",
                description="Tool",
                parameters={},
                tool_server="my_server",
                tool_name="tool",
                server_config_id=self.config_id,
            )
        }

        # Test collapsed match (all underscores removed)
        result = _normalize_mcp_tool_name("mcp_myserver_tool", catalog)
        self.assertEqual(result, "mcp_my_server_tool")

    def test_normalize_mcp_tool_name_non_mcp_returns_none(self):
        """Non-MCP tool names should return None."""
        from api.agent.tools.tool_manager import _normalize_mcp_tool_name

        result = _normalize_mcp_tool_name("some_other_tool", {})
        self.assertIsNone(result)

    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent')
    @patch('api.agent.tools.mcp_manager._mcp_manager._initialized', True)
    def test_resolve_tool_entry_normalizes_tool_name(self, mock_get_tools):
        """resolve_tool_entry should find tools even with underscore variations."""
        from api.agent.tools.tool_manager import resolve_tool_entry

        mock_get_tools.return_value = [
            MCPToolInfo(
                self.config_id,
                "mcp_brightdata_search_engine_batch",
                "brightdata",
                "search_engine_batch",
                "Batch search",
                {"type": "object", "properties": {}},
            ),
        ]

        # Look for the tool with underscore variation
        entry = resolve_tool_entry(self.agent, "mcp_bright_data_search_engine_batch")

        self.assertIsNotNone(entry)
        self.assertEqual(entry.full_name, "mcp_brightdata_search_engine_batch")

    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent')
    @patch('api.agent.tools.mcp_manager._mcp_manager._initialized', True)
    def test_execute_enabled_tool_uses_resolved_name(self, mock_get_tools):
        """execute_enabled_tool should use resolved (normalized) name for execution."""
        from api.agent.tools.tool_manager import execute_enabled_tool

        mock_get_tools.return_value = [
            MCPToolInfo(
                self.config_id,
                "mcp_brightdata_search_engine_batch",
                "brightdata",
                "search_engine_batch",
                "Batch search",
                {"type": "object", "properties": {"queries": {"type": "array"}}},
            ),
        ]

        # Pre-enable the tool with the correct name
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="mcp_brightdata_search_engine_batch",
            tool_server="brightdata",
            tool_name="search_engine_batch",
            server_config=self.server_config,
        )

        # Execute with the wrong (underscore variation) name
        with patch('api.agent.tools.tool_manager.execute_mcp_tool') as mock_execute:
            mock_execute.return_value = {"status": "success", "data": "result"}

            result = execute_enabled_tool(
                self.agent,
                "mcp_bright_data_search_engine_batch",
                {"queries": [{"query": "example"}]}
            )

            # Should have called execute_mcp_tool with the CORRECT (normalized) name
            mock_execute.assert_called_once()
            call_args = mock_execute.call_args
            self.assertEqual(call_args[0][1], "mcp_brightdata_search_engine_batch")
            self.assertEqual(result["status"], "success")


@tag("batch_mcp_tools")
class MCPIsolatedExecutionTests(TestCase):
    def setUp(self):
        self.manager = MCPToolManager()
        self.manager._initialized = True
        User = get_user_model()
        self.user = User.objects.create_user(username=f"isolated-{uuid.uuid4().hex[:8]}@example.com")
        browser_agent = create_test_browser_agent(self.user)
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="isolated-agent",
            charter="test isolated mcp execution",
            browser_use_agent=browser_agent,
        )
        self.server_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name=f"brightdata-{uuid.uuid4().hex[:8]}",
            display_name="BrightData",
            description="",
            url="https://example.com/mcp",
        )
        self.config_id = str(self.server_config.id)
        self.runtime = MCPServerRuntime(
            config_id=self.config_id,
            name=self.server_config.name,
            display_name=self.server_config.display_name,
            description=self.server_config.description,
            command=None,
            args=[],
            url=self.server_config.url,
            auth_method=self.server_config.auth_method,
            env=self.server_config.environment or {},
            headers=self.server_config.headers or {},
            prefetch_apps=[],
            scope=self.server_config.scope,
            organization_id=None,
            user_id=None,
            updated_at=self.server_config.updated_at,
        )
        self.tool_info = MCPToolInfo(
            self.config_id,
            "mcp_brightdata_search_engine_batch",
            self.server_config.name,
            "search_engine_batch",
            "Search the web in a batch",
            {"type": "object", "properties": {"queries": {"type": "array"}}},
        )
        self.manager._server_cache[self.config_id] = self.runtime
        self.manager._tools_cache["test-slot"] = [self.tool_info]
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name=self.tool_info.full_name,
            tool_server=self.tool_info.server_name,
            tool_name=self.tool_info.tool_name,
            server_config=self.server_config,
        )

    def test_execute_mcp_tool_isolated_does_not_reuse_shared_loop_or_client_cache(self):
        shared_client = MagicMock(name="shared-client")
        shared_loop = object()
        self.manager._clients[self.config_id] = shared_client
        self.manager._loop = shared_loop
        isolated_client = MagicMock(name="isolated-client")
        fake_result = SimpleNamespace(is_error=False, data={"ok": True}, content=None)

        def run_coroutine_isolated(coroutine):
            try:
                return fake_result
            finally:
                coroutine.close()

        with patch.object(self.manager, "_select_agent_proxy_url", return_value=(None, None)), patch.object(
            self.manager,
            "_build_client_for_runtime",
            return_value=isolated_client,
        ) as mock_build, patch.object(
            self.manager,
            "_run_coroutine_isolated",
            side_effect=run_coroutine_isolated,
        ) as mock_run, patch.object(self.manager, "_adapt_tool_result", side_effect=lambda _server, _tool, result: result):
            result = self.manager.execute_mcp_tool_isolated(
                self.agent,
                self.tool_info.full_name,
                {"queries": [{"query": "openai"}]},
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["result"], {"ok": True})
        self.assertIs(self.manager._clients[self.config_id], shared_client)
        self.assertIs(self.manager._loop, shared_loop)
        mock_build.assert_called_once_with(self.runtime, env_overrides=None)
        mock_run.assert_called_once()
        isolated_client.close.assert_not_called()

    def test_execute_mcp_tool_isolated_strips_will_continue_work_before_validation_and_execution(self):
        fake_result = SimpleNamespace(is_error=False, data={"ok": True}, content=None)

        async def fake_execute_async(_client, _tool_name, params, timeout_seconds):
            self.assertEqual(params, {"queries": [{"query": "openai"}]})
            self.assertEqual(timeout_seconds, self.manager._get_timeout_for_runtime(self.runtime))
            return fake_result

        with patch.object(self.manager, "_select_agent_proxy_url", return_value=(None, None)), patch.object(
            self.manager,
            "_build_client_for_runtime",
            return_value=MagicMock(name="isolated-client"),
        ), patch.object(
            self.manager._param_guards,
            "validate",
            return_value=None,
        ) as mock_validate, patch.object(
            self.manager,
            "_execute_async",
            side_effect=fake_execute_async,
        ) as mock_execute_async, patch.object(
            self.manager,
            "_run_coroutine_isolated",
            side_effect=asyncio.run,
        ), patch.object(
            self.manager,
            "_adapt_tool_result",
            side_effect=lambda _server, _tool, result: result,
        ):
            result = self.manager.execute_mcp_tool_isolated(
                self.agent,
                self.tool_info.full_name,
                {"queries": [{"query": "openai"}], "will_continue_work": False},
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["result"], {"ok": True})
        self.assertTrue(result["auto_sleep_ok"])
        mock_validate.assert_called_once_with(
            self.server_config.name,
            self.tool_info.tool_name,
            {"queries": [{"query": "openai"}]},
            self.agent.user,
        )
        self.assertEqual(mock_execute_async.call_count, 1)

    def test_execute_mcp_tool_isolated_injects_stdio_proxy_env(self):
        self.runtime.command = "npx"
        self.runtime.args = ["-y", "@dummy/server"]
        self.runtime.url = None
        self.runtime.env = {"API_TOKEN": "token-123"}
        self.manager._server_cache[self.config_id] = self.runtime

        fake_result = SimpleNamespace(is_error=False, data={"ok": True}, content=None)

        async def fake_execute_async(_client, _tool_name, params, timeout_seconds):
            return fake_result

        with patch.object(
            self.manager,
            "_select_agent_proxy_url",
            return_value=("socks5://user:pass@proxy.internal:1080", None),
        ), patch.object(
            self.manager,
            "_build_client_for_runtime",
            return_value=MagicMock(name="isolated-client"),
        ) as mock_build, patch.object(
            self.manager,
            "_execute_async",
            side_effect=fake_execute_async,
        ), patch.object(
            self.manager,
            "_run_coroutine_isolated",
            side_effect=asyncio.run,
        ), patch.object(
            self.manager,
            "_adapt_tool_result",
            side_effect=lambda _server, _tool, result: result,
        ):
            result = self.manager.execute_mcp_tool_isolated(
                self.agent,
                self.tool_info.full_name,
                {"queries": [{"query": "openai"}]},
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            mock_build.call_args.kwargs["env_overrides"]["HTTP_PROXY"],
            "http://user:pass@proxy.internal:1080",
        )
        self.assertEqual(
            mock_build.call_args.kwargs["env_overrides"]["ALL_PROXY"],
            "http://user:pass@proxy.internal:1080",
        )
