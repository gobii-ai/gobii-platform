"""Unit tests for MCP tool management functionality."""

import asyncio
import atexit
import json
import time
import uuid
from datetime import datetime, timedelta, UTC
from contextlib import nullcontext
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
from django.test import TestCase, tag, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from api.models import (
    PersistentAgent,
    BrowserUseAgent,
    ProxyServer,
    PersistentAgentEnabledTool,
    MCPServerConfig,
    MCPServerOAuthCredential,
    PersistentAgentMCPServer,
    PromptConfig,
    UserBilling,
)
from api.agent.core.llm_config import AgentLLMTier
from tests.utils.llm_seed import get_intelligence_tier
from constants.plans import PlanNames
from api.agent.tools.mcp_manager import (
    MCPToolManager,
    MCPToolInfo,
    MCPServerRuntime,
    get_mcp_manager,
    execute_mcp_tool,
)
from api.agent.tools.tool_manager import (
    enable_mcp_tool,
    enable_tools,
    ensure_default_tools_enabled,
    get_enabled_tool_definitions,
    execute_enabled_tool,
    mark_tool_enabled_without_discovery,
)
from api.agent.tools.search_tools import (
    execute_search_tools,
    get_search_tools_tool,
    search_tools,
)
from api.services.prompt_settings import invalidate_prompt_settings_cache
from tests.utils.llm_seed import seed_persistent_basic


def _default_fake_run_completion(*args, **kwargs):
    """Default stub to prevent real LLM calls during tests."""
    user_content = ""
    messages = kwargs.get("messages") or []
    if len(messages) >= 2:
        user_message = messages[1] or {}
        if isinstance(user_message, dict):
            user_content = user_message.get("content") or ""

    tool_names: list[str] = []
    for line in user_content.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        # Keep portion before any separator (":" or "|")
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

        with patch("api.agent.tools.mcp_manager._sandbox_enabled", return_value=False), \
                patch.object(manager, "_ensure_event_loop", return_value=loop), \
                patch.object(manager, "_select_discovery_proxy_url", return_value=None), \
                patch.object(manager, "_fetch_server_tools", new=_fake_fetch):
            manager._register_server(runtime)
        transport = manager._clients[runtime.config_id].transport
        self.assertEqual(transport.headers.get("Authorization"), "Bearer token-123")

    @tag("batch_mcp_tools")
    @patch("api.agent.tools.mcp_manager.requests.post")
    def test_build_runtime_refreshes_expired_oauth_token(self, mock_post):
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
        with patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery"):
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
        runtime = manager._build_runtime_from_config(config)

        mock_post.assert_called_once_with(
            "https://notion.example.com/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": "refresh-123",
                "client_id": "client-123",
                "client_secret": "secret-xyz",
            },
            timeout=manager.OAUTH_REFRESH_TIMEOUT_SECONDS,
        )

        self.assertEqual(runtime.oauth_access_token, "new-access")
        self.assertEqual(runtime.oauth_token_type, "Bearer")
        self.assertGreater(runtime.oauth_expires_at, timezone.now())

        credential.refresh_from_db()
        self.assertEqual(credential.access_token, "new-access")
        self.assertEqual(credential.refresh_token, "new-refresh")
        self.assertEqual(credential.token_type, "Bearer")
        self.assertIn("last_refresh_response", credential.metadata)

    @tag("batch_mcp_tools")
    @patch("api.agent.tools.mcp_manager.requests.post")
    def test_build_runtime_skips_refresh_when_token_valid(self, mock_post):
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
        
    def test_default_enabled_tools_defined(self):
        """Test that default enabled tools list is defined."""
        self.assertIn("mcp_brightdata_scrape_as_markdown", MCPToolManager.DEFAULT_ENABLED_TOOLS)
        self.assertIsInstance(MCPToolManager.DEFAULT_ENABLED_TOOLS, list)
        
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
    def test_get_all_available_tools(self, mock_init):
        """Test getting all available tools from cache."""
        tool1 = MCPToolInfo(self.config_id, "mcp_test_tool1", self.server_name, "tool1", "Test tool 1", {})
        tool2 = MCPToolInfo(self.config_id, "mcp_test_tool2", self.server_name, "tool2", "Test tool 2", {})
        self.manager._tools_cache = {self.config_id: [tool1, tool2]}
        self.manager._initialized = True
        
        tools = self.manager.get_all_available_tools()
        
        self.assertEqual(len(tools), 2)
        self.assertIn(tool1, tools)
        self.assertIn(tool2, tools)
        mock_init.assert_not_called()  # Should not call initialize since _initialized is True
        
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
        # Enable via API to populate table
        from api.agent.tools.tool_manager import enable_mcp_tool
        # Ensure global manager doesn't auto-initialize during enable
        from api.agent.tools import mcp_manager as mm
        mm._mcp_manager._initialized = True
        with patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent') as mock_all:
            mock_all.return_value = [tool1]
            enable_mcp_tool(agent, "mcp_test_tool1")
        self.manager._tools_cache = {self.config_id: [tool1]}
        self.manager._initialized = True
        
        with patch.object(self.manager, 'get_tools_for_agent', return_value=[tool1]):
            definitions = self.manager.get_enabled_tools_definitions(agent)
        
        self.assertEqual(len(definitions), 1)
        self.assertEqual(definitions[0]["function"]["name"], "mcp_test_tool1")
        self.assertEqual(definitions[0]["function"]["description"], "Test tool 1")
        
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
        # Mark enabled in table
        from api.agent.tools.tool_manager import enable_mcp_tool
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
            enable_mcp_tool(agent, "mcp_test_tool1")

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

    @override_settings(GOBII_PROPRIETARY_MODE=False)
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

    @override_settings(GOBII_PROPRIETARY_MODE=True)
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
        self.manager._tools_cache = {"test": []}
        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        self.manager._loop = mock_loop
        self.manager._initialized = True
        
        self.manager.cleanup()
        
        self.assertEqual(len(self.manager._clients), 0)
        self.assertEqual(len(self.manager._tools_cache), 0)
        mock_loop.close.assert_called_once()
        self.assertFalse(self.manager._initialized)


@tag("batch_mcp_tools")
class MCPToolFunctionsTests(TestCase):
    """Test module-level MCP tool functions."""
    
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
        self.assertIn("Enabled:", result["message"]) 
        self.assertEqual(result.get("enabled_tools"), ["mcp_brightdata_scrape", "mcp_brightdata_search"]) 
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
    def test_search_tools_includes_builtin_catalog(self, mock_get_config, mock_get_manager, mock_run_completion, mock_enable_tools):
        """search_tools should include builtin tools when MCP catalog is empty."""
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
        mock_enable_tools.assert_not_called()
        
    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent')
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    def test_enable_mcp_tool_success(self, mock_init, mock_get_tools):
        """Test successfully enabling an MCP tool."""
        mock_get_tools.return_value = [
            MCPToolInfo(self.config_id, "mcp_test_tool", self.server_name, "tool", "Test tool", {})
        ]
        
        result = enable_mcp_tool(self.agent, "mcp_test_tool")
        
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["enabled"], "mcp_test_tool")
        self.assertIsNone(result["disabled"])
        
        names = set(PersistentAgentEnabledTool.objects.filter(agent=self.agent).values_list("tool_full_name", flat=True))
        self.assertIn("mcp_test_tool", names)
        
    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent')
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    def test_enable_mcp_tool_already_enabled(self, mock_init, mock_get_tools):
        """Test enabling a tool that's already enabled."""
        mock_get_tools.return_value = [
            MCPToolInfo(self.config_id, "mcp_test_tool", self.server_name, "tool", "Test tool", {})
        ]
        
        # Pre-enable and set an older last_used_at
        enable_mcp_tool(self.agent, "mcp_test_tool")
        row = PersistentAgentEnabledTool.objects.get(agent=self.agent, tool_full_name="mcp_test_tool")
        from django.utils import timezone
        row.last_used_at = timezone.now() - timezone.timedelta(seconds=100)
        row.save(update_fields=["last_used_at"])
        
        result = enable_mcp_tool(self.agent, "mcp_test_tool")
        
        self.assertEqual(result["status"], "success")
        self.assertIn("already enabled", result["message"])
        
        # Check usage timestamp was updated
        row.refresh_from_db()
        from django.utils import timezone
        self.assertGreater(row.last_used_at, timezone.now() - timezone.timedelta(seconds=10))

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

    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent')
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    def test_enable_mcp_tool_with_lru_eviction(self, mock_init, mock_get_tools):
        """Test LRU eviction when enabling beyond limit."""
        # Create 41 tools (one more than the new 40 limit)
        tools = [
            MCPToolInfo(self.config_id, f"mcp_test_tool{i}", self.server_name, f"tool{i}", f"Test tool {i}", {})
            for i in range(41)
        ]
        mock_get_tools.return_value = tools
        
        # Enable 40 tools with different timestamps
        for i in range(40):
            enable_mcp_tool(self.agent, f"mcp_test_tool{i}")
            row = PersistentAgentEnabledTool.objects.get(agent=self.agent, tool_full_name=f"mcp_test_tool{i}")
            from django.utils import timezone
            row.last_used_at = timezone.now() - timezone.timedelta(seconds=(40 - i))
            row.save(update_fields=["last_used_at"])
        
        # Enable the 41st tool, should evict tool0 (oldest)
        result = enable_mcp_tool(self.agent, "mcp_test_tool40")
        
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["enabled"], "mcp_test_tool40")
        self.assertEqual(result["disabled"], "mcp_test_tool0")
        
        names = set(PersistentAgentEnabledTool.objects.filter(agent=self.agent).values_list("tool_full_name", flat=True))
        self.assertNotIn("mcp_test_tool0", names)
        self.assertIn("mcp_test_tool40", names)
        self.assertEqual(len(names), 40)
        
    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent')
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    def test_enable_mcp_tool_nonexistent(self, mock_init, mock_get_tools):
        """Test enabling a non-existent tool."""
        mock_get_tools.return_value = []
        
        result = enable_mcp_tool(self.agent, "mcp_nonexistent")
        
        self.assertEqual(result["status"], "error")
        self.assertIn("does not exist", result["message"])

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
        
    @patch('api.agent.tools.tool_manager.enable_mcp_tool')
    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent')
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    def test_ensure_default_tools_enabled(self, mock_init, mock_get_tools, mock_enable):
        """Test ensuring default tools are enabled."""
        mock_get_tools.return_value = [
            MCPToolInfo(self.config_id, "mcp_brightdata_scrape_as_markdown", "brightdata", "scrape_as_markdown", "Scrape", {})
        ]
        
        ensure_default_tools_enabled(self.agent)
        
        mock_enable.assert_called_once_with(self.agent, "mcp_brightdata_scrape_as_markdown")
        
    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent')
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    @patch('api.agent.tools.tool_manager.enable_mcp_tool')
    def test_ensure_default_tools_already_enabled(self, mock_enable, mock_init, mock_get_tools):
        """Test ensuring defaults when already enabled."""
        # Pre-enable default tool directly in table
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent, tool_full_name="mcp_brightdata_scrape_as_markdown"
        )
        mock_get_tools.return_value = [
            MCPToolInfo(self.config_id, "mcp_brightdata_scrape_as_markdown", "brightdata", "scrape_as_markdown", "Scrape", {})
        ]
        
        ensure_default_tools_enabled(self.agent)
        
        mock_enable.assert_not_called()


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
        self.assertIn("will_continue_work", tool_def["function"]["parameters"]["properties"])
        self.assertIn("query", tool_def["function"]["parameters"]["required"])
        
    @patch('api.agent.tools.search_tools.search_tools')
    def test_execute_search_tools(self, mock_search):
        """Test executing search_tools function returns pass-through result."""
        mock_search.return_value = {
            "status": "success",
            "message": "Enabled: mcp_tool_a",
            "enabled_tools": ["mcp_tool_a"],
            "already_enabled": [],
            "evicted": [],
            "invalid": []
        }
        result = execute_search_tools(self.agent, {"query": "test query"})
        self.assertEqual(result["status"], "success")
        self.assertIn("Enabled: mcp_tool_a", result["message"]) 
        mock_search.assert_called_once_with(self.agent, "test query")

    @patch('api.agent.tools.search_tools.search_tools')
    def test_execute_search_tools_auto_sleep(self, mock_search):
        """search_tools should opt into auto-sleep when will_continue_work is False."""
        mock_search.return_value = {
            "status": "success",
            "message": "Enabled: mcp_tool_a",
            "enabled_tools": ["mcp_tool_a"],
        }
        result = execute_search_tools(self.agent, {"query": "test query", "will_continue_work": False})
        self.assertEqual(result["status"], "success")
        self.assertTrue(result.get("auto_sleep_ok"))
        
    def test_execute_search_tools_missing_query(self):
        """Test search_tools with missing query."""
        result = execute_search_tools(self.agent, {})
        
        self.assertEqual(result["status"], "error")
        self.assertIn("Missing required parameter: query", result["message"])
        
    # enable_tool is no longer exposed to the main agent; auto-enabling is handled inside search_tools
        
    @patch('api.agent.tools.mcp_manager._mcp_manager')
    def test_execute_mcp_tool(self, mock_manager):
        """Test executing an MCP tool."""
        mock_manager._initialized = True
        mock_manager.execute_mcp_tool.return_value = {
            "status": "success",
            "result": "Tool executed"
        }
        
        result = execute_mcp_tool(self.agent, "mcp_test_tool", {"param": "value"})
        
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["result"], "Tool executed")
        mock_manager.execute_mcp_tool.assert_called_once_with(
            self.agent, "mcp_test_tool", {"param": "value"}, force_local=False
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
        
    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent')
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    def test_lru_eviction_workflow(self, mock_init, mock_get_tools):
        """Test complete LRU eviction workflow."""
        # Create exactly 40 tools
        tools = [
            MCPToolInfo(f"cfg-bulk-{i}", f"mcp_test_tool{i}", self.server_name, f"tool{i}", f"Test tool {i}", {})
            for i in range(41)
        ]
        mock_get_tools.return_value = tools
        
        # Enable 40 tools
        for i in range(40):
            result = enable_mcp_tool(self.agent, f"mcp_test_tool{i}")
            self.assertEqual(result["status"], "success")
            time.sleep(0.01)  # Small delay to ensure different timestamps
            
        self.assertEqual(
            PersistentAgentEnabledTool.objects.filter(agent=self.agent).count(), 40
        )
        
        # Use tool10 to make it more recent
        row10 = PersistentAgentEnabledTool.objects.get(agent=self.agent, tool_full_name="mcp_test_tool10")
        from django.utils import timezone
        row10.last_used_at = timezone.now()
        row10.save(update_fields=["last_used_at"])
        
        # Enable tool40, should evict tool0 (not tool10 since we just used it)
        result = enable_mcp_tool(self.agent, "mcp_test_tool40")
        
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["disabled"], "mcp_test_tool0")
        
        enabled_now = set(
            PersistentAgentEnabledTool.objects.filter(agent=self.agent).values_list("tool_full_name", flat=True)
        )
        self.assertIn("mcp_test_tool10", enabled_now)
        self.assertNotIn("mcp_test_tool0", enabled_now)
        self.assertIn("mcp_test_tool40", enabled_now)
        
    @patch('api.agent.tools.tool_manager.enable_mcp_tool')
    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent')
    @patch('api.agent.tools.mcp_manager._mcp_manager.initialize')
    def test_default_tools_initialization(self, mock_init, mock_get_tools, mock_enable):
        """Test that default tools are properly initialized."""
        default_tool = "mcp_brightdata_scrape_as_markdown"
        mock_get_tools.return_value = [
            MCPToolInfo(self.config_id, default_tool, "brightdata", "scrape_as_markdown", "Scrape", {})
        ]
        
        # Ensure defaults are enabled
        ensure_default_tools_enabled(self.agent)
        
        # Should enable the default tool
        mock_enable.assert_called_once_with(self.agent, default_tool)
        
    def test_tool_usage_tracking(self):
        """Test that tool usage is properly tracked."""
        with patch('api.agent.tools.mcp_manager._mcp_manager.initialize') as mock_init:
            with patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent') as mock_get_tools:
                mock_get_tools.return_value = [
                    MCPToolInfo(self.config_id, "mcp_test_tool", self.server_name, "tool", "Test", {})
                ]
                # Enable a tool (initial enable may not set last_used_at)
                enable_mcp_tool(self.agent, "mcp_test_tool")
                row = PersistentAgentEnabledTool.objects.get(agent=self.agent, tool_full_name="mcp_test_tool")
                first_time = row.last_used_at  # may be None on initial enable

                # Wait and re-enable (should set/update last_used_at)
                time.sleep(0.1)
                enable_mcp_tool(self.agent, "mcp_test_tool")
                row.refresh_from_db()
                second_time = row.last_used_at
                self.assertIsNotNone(second_time)
                if first_time is not None:
                    self.assertGreater(second_time, first_time)

    @patch('api.agent.tools.tool_manager._get_manager')
    @patch('api.agent.tools.tool_manager.execute_mcp_tool')
    def test_execute_enabled_tool_auto_enables_mcp(self, mock_execute, mock_get_manager):
        """Tool execution should auto-enable MCP tools when missing."""
        mock_manager = MagicMock()
        mock_manager.get_tools_for_agent.return_value = [
            MCPToolInfo(self.config_id, "mcp_test_tool", self.server_name, "tool", "Test", {})
        ]
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
        mock_execute.assert_called_once_with(self.agent, "mcp_test_tool", {"foo": "bar"})
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent, tool_full_name="mcp_test_tool"
            ).exists()
        )

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
                enable_mcp_tool(agent, name)
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
            enable_mcp_tool(self.agent, name)

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
            "mcp_brightdata_scrape_as_markdown": ToolCatalogEntry(
                provider="mcp",
                full_name="mcp_brightdata_scrape_as_markdown",
                description="Scrape",
                parameters={},
                tool_server="brightdata",
                tool_name="scrape_as_markdown",
                server_config_id=self.config_id,
            )
        }

        # Test the variation with underscore in server name
        result = _normalize_mcp_tool_name("mcp_bright_data_scrape_as_markdown", catalog)
        self.assertEqual(result, "mcp_brightdata_scrape_as_markdown")

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
                "mcp_brightdata_scrape_as_markdown",
                "brightdata",
                "scrape_as_markdown",
                "Scrape pages",
                {"type": "object", "properties": {}},
            ),
        ]

        # Look for the tool with underscore variation
        entry = resolve_tool_entry(self.agent, "mcp_bright_data_scrape_as_markdown")

        self.assertIsNotNone(entry)
        self.assertEqual(entry.full_name, "mcp_brightdata_scrape_as_markdown")

    @patch('api.agent.tools.mcp_manager._mcp_manager.get_tools_for_agent')
    @patch('api.agent.tools.mcp_manager._mcp_manager._initialized', True)
    def test_execute_enabled_tool_uses_resolved_name(self, mock_get_tools):
        """execute_enabled_tool should use resolved (normalized) name for execution."""
        from api.agent.tools.tool_manager import execute_enabled_tool

        mock_get_tools.return_value = [
            MCPToolInfo(
                self.config_id,
                "mcp_brightdata_scrape_as_markdown",
                "brightdata",
                "scrape_as_markdown",
                "Scrape pages",
                {"type": "object", "properties": {"url": {"type": "string"}}},
            ),
        ]

        # Pre-enable the tool with the correct name
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="mcp_brightdata_scrape_as_markdown",
            tool_server="brightdata",
            tool_name="scrape_as_markdown",
            server_config=self.server_config,
        )

        # Execute with the wrong (underscore variation) name
        with patch('api.agent.tools.tool_manager.execute_mcp_tool') as mock_execute:
            mock_execute.return_value = {"status": "success", "data": "result"}

            result = execute_enabled_tool(
                self.agent,
                "mcp_bright_data_scrape_as_markdown",
                {"url": "https://example.com"}
            )

            # Should have called execute_mcp_tool with the CORRECT (normalized) name
            mock_execute.assert_called_once()
            call_args = mock_execute.call_args
            self.assertEqual(call_args[0][1], "mcp_brightdata_scrape_as_markdown")
            self.assertEqual(result["status"], "success")
