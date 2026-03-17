import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.tools.mcp_manager import SandboxToolCacheContext
from api.models import BrowserUseAgent, MCPServerConfig, MCPServerOAuthCredential, PersistentAgent
from api.services.sandbox_compute import SandboxComputeService


@tag("batch_mcp_tools")
class MCPDiscoverySignalTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username=f"user-{uuid.uuid4().hex[:8]}",
            email=f"user-{uuid.uuid4().hex[:8]}@example.com",
            password="password",
        )

    @patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery")
    def test_config_signal_skips_stdio_discovery(self, mock_schedule):
        MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name=f"stdio-{uuid.uuid4().hex[:8]}",
            display_name="STDIO Server",
            command="npx",
            command_args=["-y", "@dummy/server"],
        )

        mock_schedule.assert_not_called()

    @patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery")
    def test_config_signal_keeps_http_discovery(self, mock_schedule):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name=f"http-{uuid.uuid4().hex[:8]}",
            display_name="HTTP Server",
            url="https://example.com/mcp",
        )

        mock_schedule.assert_called_once_with(str(server.id), reason="config_changed")

    @patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery")
    def test_credential_signal_skips_stdio_discovery(self, mock_schedule):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name=f"stdio-creds-{uuid.uuid4().hex[:8]}",
            display_name="STDIO With Creds",
            command="npx",
            command_args=["-y", "@dummy/server"],
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
        )
        mock_schedule.reset_mock()

        MCPServerOAuthCredential.objects.create(
            server_config=server,
            user=self.user,
            client_id="client-id",
        )

        mock_schedule.assert_not_called()

    @patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery")
    def test_credential_signal_keeps_http_discovery(self, mock_schedule):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name=f"http-creds-{uuid.uuid4().hex[:8]}",
            display_name="HTTP With Creds",
            url="https://example.com/mcp",
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
        )
        mock_schedule.reset_mock()

        MCPServerOAuthCredential.objects.create(
            server_config=server,
            user=self.user,
            client_id="client-id",
        )

        mock_schedule.assert_called_once_with(str(server.id), reason="credentials_changed")


@tag("batch_mcp_tools")
class SandboxComputeDiscoveryServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username=f"agent-user-{uuid.uuid4().hex[:8]}",
            email=f"agent-user-{uuid.uuid4().hex[:8]}@example.com",
            password="password",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Browser Agent")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            browser_use_agent=self.browser_agent,
            name="Persistent Agent",
            charter="Help",
        )

    @patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True)
    def test_service_skips_stdio_discovery_without_agent(self, _mock_enabled):
        backend = Mock()
        service = SandboxComputeService(backend=backend)
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name=f"service-stdio-{uuid.uuid4().hex[:8]}",
            display_name="Service STDIO",
            command="npx",
            command_args=["-y", "@dummy/server"],
        )

        result = service.discover_mcp_tools(str(server.id), reason="config_changed")

        self.assertEqual(result.get("status"), "skipped")
        backend.discover_mcp_tools.assert_not_called()

    @patch("api.services.sandbox_compute.set_cached_mcp_tool_definitions")
    @patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True)
    def test_service_stores_agent_scoped_cache_for_stdio_discovery(self, _mock_enabled, mock_set_cache):
        backend = Mock()
        backend.discover_mcp_tools.return_value = {
            "status": "ok",
            "tools": [{"full_name": "mcp_demo_tool", "tool_name": "tool", "server_name": "demo", "parameters": {}}],
        }
        service = SandboxComputeService(backend=backend)
        server_id = str(uuid.uuid4())
        session = SimpleNamespace(pod_name="sandbox-agent", proxy_server=None)
        runtime = SimpleNamespace(
            config_id=server_id,
            scope=MCPServerConfig.Scope.USER,
            command="npx",
            url="",
        )
        manager = Mock()
        manager._sandbox_cache_context_for_runtime.return_value = SandboxToolCacheContext(
            agent_cache_key=str(self.agent.id)
        )
        manager._build_tool_cache_fingerprint.return_value = "agent-fingerprint"

        with patch(
            "api.services.sandbox_compute._build_mcp_server_payload",
            return_value=({"config_id": server_id, "scope": MCPServerConfig.Scope.USER, "command": "npx", "url": ""}, runtime),
        ), patch.object(service, "_ensure_session", return_value=session) as mock_ensure_session, patch(
            "api.agent.tools.mcp_manager.get_mcp_manager",
            return_value=manager,
        ):
            result = service.discover_mcp_tools(server_id, reason="cache_miss", agent=self.agent)

        self.assertEqual(result.get("status"), "ok")
        mock_ensure_session.assert_called_once_with(self.agent, source="discover_mcp_tools")
        backend.discover_mcp_tools.assert_called_once()
        self.assertEqual(backend.discover_mcp_tools.call_args.kwargs["agent"], self.agent)
        self.assertEqual(backend.discover_mcp_tools.call_args.kwargs["session"], session)
        mock_set_cache.assert_called_once_with(server_id, "agent-fingerprint", result["tools"])
