"""Unit tests for MCP tool management functionality."""

import atexit
import json
import time
import uuid
from contextlib import nullcontext
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
from django.test import TestCase, tag, override_settings
from django.contrib.auth import get_user_model

from api.models import PersistentAgent, BrowserUseAgent, PersistentAgentEnabledTool, MCPServerConfig
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
)
from api.agent.tools.search_tools import (
    execute_search_tools,
    get_search_tools_tool,
    search_tools,
)
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

    @patch('api.agent.tools.search_tools._search_sqlite_tools')
    @patch('api.agent.tools.search_tools._search_mcp_tools')
    def test_search_tools_walks_providers_when_empty(self, mock_mcp_provider, mock_sqlite_provider):
        """search_tools should continue to later providers when earlier ones return empty successes."""
        mock_mcp_provider.return_value = {
            "status": "success",
            "tools": [],
            "message": "No MCP tools available",
        }
        mock_sqlite_provider.return_value = {
            "status": "success",
            "enabled_tools": ["sqlite_batch"],
        }

        with patch(
            "api.agent.tools.search_tools._SEARCH_PROVIDERS",
            [
                ("mcp", mock_mcp_provider),
                ("sqlite", mock_sqlite_provider),
            ],
        ):
            result = search_tools(self.agent, "sqlite please")

        self.assertEqual(result, mock_sqlite_provider.return_value)
        mock_sqlite_provider.assert_called_once()
        
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
            self.agent, "mcp_test_tool", {"param": "value"}
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
