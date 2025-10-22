import json
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, RequestFactory, tag, override_settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.utils import timezone

from api.models import (
    PersistentAgent,
    BrowserUseAgent,
    PipedreamConnectSession,
    MCPServerConfig,
    PersistentAgentEnabledTool,
)
from api.integrations.pipedream_connect import create_connect_session
from api.agent.tools.mcp_manager import MCPServerRuntime


def _get_or_create_pipedream_config():
    defaults = {
        "display_name": "Pipedream",
        "description": "Test Pipedream server",
        "command": "",
        "command_args": [],
        "url": "https://remote.mcp.pipedream.net",
        "prefetch_apps": [],
        "metadata": {},
        "is_active": True,
    }
    config, created = MCPServerConfig.objects.get_or_create(
        scope=MCPServerConfig.Scope.PLATFORM,
        name="pipedream",
        defaults=defaults,
    )
    if created:
        return config
    updated = False
    if not config.url:
        config.url = defaults["url"]
        updated = True
    if config.command:
        config.command = ""
        updated = True
    if updated:
        config.save(update_fields=["url", "command"])
    return config


def _create_browser_agent(user):
    with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
        return BrowserUseAgent.objects.create(user=user, name="test-browser-agent")


@tag("pipedream_connect")
class PipedreamGreenhouseConnectTests(TestCase):
    def setUp(self):
        Site.objects.update_or_create(id=1, defaults={"domain": "example.com", "name": "example"})

    @patch("api.integrations.pipedream_connect.requests.post")
    @patch("api.integrations.pipedream_connect.get_mcp_manager")
    def test_create_connect_session_greenhouse(self, mock_get_mgr, mock_post):
        """Greenhouse connect session appends app=greenhouse and persists token/link."""
        # Arrange agent
        User = get_user_model()
        user = User.objects.create_user(username="gh@example.com")
        bua = _create_browser_agent(user)
        agent = PersistentAgent.objects.create(user=user, name="a", charter="c", browser_use_agent=bua)

        # Mock token and API response
        mgr = MagicMock()
        mgr._get_pipedream_access_token.return_value = "pd_token"
        mock_get_mgr.return_value = mgr

        resp = MagicMock()
        future_expires = (timezone.now() + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        resp.json.return_value = {
            "token": "ctok_gh",
            "connect_link_url": "https://pipedream.com/_static/connect.html?token=ctok_gh",
            "expires_at": future_expires,
        }
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp

        # Act
        from django.test import override_settings
        with override_settings(PIPEDREAM_PROJECT_ID="proj_123", PIPEDREAM_ENVIRONMENT="development"):
            session, url = create_connect_session(agent, "greenhouse")

        # Assert
        self.assertTrue(isinstance(session, PipedreamConnectSession))
        self.assertIn("app=greenhouse", url)
        self.assertEqual(session.connect_token, "ctok_gh")
        self.assertIn("pipedream.com/_static/connect.html", session.connect_link_url)

    @patch("api.integrations.pipedream_connect.create_connect_session")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._execute_async", new_callable=MagicMock)
    def test_execute_tool_rewrites_connect_link_greenhouse(self, mock_exec, mock_loop, mock_create):
        """Connect Link rewrite works for Greenhouse app/tool names."""
        # Arrange agent
        User = get_user_model()
        user = User.objects.create_user(username="pgh@example.com")
        with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
            bua = BrowserUseAgent.objects.create(user=user, name="bua")
        agent = PersistentAgent.objects.create(user=user, name="agent-gh", charter="c", browser_use_agent=bua)

        # Prepare manager and discovered tool (Pipedream unprefixed)
        from api.agent.tools.mcp_manager import MCPToolManager, MCPToolInfo
        mgr = MCPToolManager()
        mgr._initialized = True
        config = _get_or_create_pipedream_config()
        runtime = MCPServerRuntime(
            config_id=str(config.id),
            name=config.name,
            display_name=config.display_name,
            description=config.description,
            command=config.command or None,
            args=list(config.command_args or []),
            url=config.url or "",
            env=config.environment or {},
            headers=config.headers or {},
            prefetch_apps=list(config.prefetch_apps or []),
            scope=config.scope,
            organization_id=str(config.organization_id) if config.organization_id else None,
            user_id=str(config.user_id) if config.user_id else None,
            updated_at=config.updated_at,
        )
        tool = MCPToolInfo(str(config.id), "greenhouse-create-candidate", "pipedream", "greenhouse-create-candidate", "desc", {})
        mgr._server_cache = {runtime.config_id: runtime}
        mgr._tools_cache = {runtime.config_id: [tool]}
        mgr._clients = {runtime.config_id: MagicMock()}
        PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name="greenhouse-create-candidate",
            tool_server="pipedream",
            tool_name="greenhouse-create-candidate",
            server_config_id=runtime.config_id,
        )

        # Fake result containing Pipedream's connect link for greenhouse
        r = MagicMock()
        r.is_error = False
        r.data = None
        block = MagicMock()
        block.text = "Please connect: https://pipedream.com/_static/connect.html?token=ctok_gh&app=greenhouse"
        r.content = [block]
        loop = MagicMock()
        loop.run_until_complete.side_effect = lambda _: r
        mock_loop.return_value = loop
        mock_exec.return_value = r

        # Our session factory returns custom URL (first-party)
        fake_session = MagicMock()
        mock_create.return_value = (fake_session, "https://example.com/connect?token=abc&app=greenhouse")

        # Act
        with patch.object(mgr, "_select_agent_proxy_url", return_value=(None, None)):
            res = mgr.execute_mcp_tool(agent, "greenhouse-create-candidate", {"instruction": "x"})

        # Assert
        self.assertEqual(res.get("status"), "action_required")
        self.assertIn("example.com/connect", res.get("connect_url"))


@tag("pipedream_connect")
class PipedreamGreenhouseDiscoveryTests(TestCase):
    def setUp(self):
        Site.objects.update_or_create(id=1, defaults={"domain": "example.com", "name": "example"})

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    @patch('api.agent.tools.mcp_manager.select_proxy', return_value=None)
    @patch('api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop')
    @patch('api.agent.tools.mcp_manager.Client')
    @patch('fastmcp.client.transports.StreamableHttpTransport')
    def test_discovery_initial_app_slug_greenhouse(self, mock_transport, mock_client_cls, mock_loop, mock_select_proxy):
        """When prefetch is set to greenhouse only, headers use app=greenhouse."""
        from api.agent.tools.mcp_manager import MCPToolManager

        mgr = MCPToolManager()

        # Prepare loop to avoid running the coroutine
        loop = MagicMock()
        loop.run_until_complete.return_value = []
        mock_loop.return_value = loop
        # Client constructor returns a simple mock client; we don't actually use it
        mock_client_cls.return_value = MagicMock()

        # Intercept _pd_build_headers to assert app_slug
        seen_app = {}
        def fake_headers(mode, app_slug, external_user_id, conversation_id):
            seen_app['app'] = app_slug
            return {"Authorization": "Bearer x", "x-pd-app-slug": app_slug or ""}

        with patch.object(mgr, '_pd_build_headers', side_effect=fake_headers) as mock_hdrs:
            # Avoid creating an un-awaited coroutine by stubbing async fetch
            with patch.object(mgr, '_fetch_server_tools', return_value=[]):
                from django.test import override_settings
                with override_settings(
                    PIPEDREAM_CLIENT_ID="cli",
                    PIPEDREAM_CLIENT_SECRET="sec",
                    PIPEDREAM_PROJECT_ID="proj",
                    PIPEDREAM_ENVIRONMENT="development",
                    PIPEDREAM_PREFETCH_APPS="greenhouse",
                ):
                    config = _get_or_create_pipedream_config()
                    runtime = MCPServerRuntime(
                        config_id=str(config.id),
                        name=config.name,
                        display_name=config.display_name,
                        description=config.description,
                        command=config.command or None,
                        args=list(config.command_args or []),
                        url=config.url or "",
                        env=config.environment or {},
                        headers=config.headers or {},
                        prefetch_apps=["greenhouse"],
                        scope=config.scope,
                        organization_id=str(config.organization_id) if config.organization_id else None,
                        user_id=str(config.user_id) if config.user_id else None,
                        updated_at=config.updated_at,
                    )
                    mgr._server_cache = {runtime.config_id: runtime}
                    mgr._register_server(runtime)

        self.assertEqual(seen_app.get('app'), 'greenhouse')
        # Transport should be initialized with headers including x-pd-app-slug
        args, kwargs = mock_transport.call_args
        self.assertIn('headers', kwargs)
        self.assertEqual(kwargs['headers'].get('x-pd-app-slug'), 'greenhouse')
        self.assertIn('httpx_client_factory', kwargs)
        self.assertTrue(callable(kwargs['httpx_client_factory']))

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    @patch('api.agent.tools.mcp_manager.select_proxy', side_effect=RuntimeError("No proxies"))
    @patch('api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop')
    @patch('api.agent.tools.mcp_manager.Client')
    @patch('fastmcp.client.transports.StreamableHttpTransport')
    def test_discovery_logs_warning_when_proxy_unavailable(self, mock_transport, mock_client_cls, mock_loop, mock_select_proxy):
        from api.agent.tools.mcp_manager import MCPToolManager

        mgr = MCPToolManager()
        loop = MagicMock()
        loop.run_until_complete.return_value = []
        mock_loop.return_value = loop
        mock_client_cls.return_value = MagicMock()

        with patch.object(mgr, '_fetch_server_tools', return_value=[]), \
             patch.object(mgr, '_pd_build_headers', return_value={}), \
             self.assertLogs('api.agent.tools.mcp_manager', level='WARNING') as logs:
            config = _get_or_create_pipedream_config()
            runtime = MCPServerRuntime(
                config_id=str(config.id),
                name=config.name,
                display_name=config.display_name,
                description=config.description,
                command=config.command or None,
                args=list(config.command_args or []),
                url=config.url or "",
                env=config.environment or {},
                headers=config.headers or {},
                prefetch_apps=["greenhouse"],
                scope=config.scope,
                organization_id=str(config.organization_id) if config.organization_id else None,
                user_id=str(config.user_id) if config.user_id else None,
                updated_at=config.updated_at,
            )
            mgr._server_cache = {runtime.config_id: runtime}
            mgr._register_server(runtime)

        self.assertTrue(any("falling back to direct connection" in message for message in logs.output))

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch('api.agent.tools.mcp_manager.select_proxy', return_value=None)
    @patch('api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop')
    @patch('api.agent.tools.mcp_manager.Client')
    @patch('fastmcp.client.transports.StreamableHttpTransport')
    def test_discovery_errors_when_proxy_required(self, mock_transport, mock_client_cls, mock_loop, mock_select_proxy):
        from api.agent.tools.mcp_manager import MCPToolManager

        mgr = MCPToolManager()
        loop = MagicMock()
        loop.run_until_complete.return_value = []
        mock_loop.return_value = loop
        mock_client_cls.return_value = MagicMock()

        with patch.object(mgr, '_fetch_server_tools', return_value=[]), \
             patch.object(mgr, '_pd_build_headers', return_value={}), \
             self.assertLogs('api.agent.tools.mcp_manager', level='ERROR') as logs, \
             self.assertRaises(RuntimeError):
            config = _get_or_create_pipedream_config()
            runtime = MCPServerRuntime(
                config_id=str(config.id),
                name=config.name,
                display_name=config.display_name,
                description=config.description,
                command=config.command or None,
                args=list(config.command_args or []),
                url=config.url or "",
                env=config.environment or {},
                headers=config.headers or {},
                prefetch_apps=["greenhouse"],
                scope=config.scope,
                organization_id=str(config.organization_id) if config.organization_id else None,
                user_id=str(config.user_id) if config.user_id else None,
                updated_at=config.updated_at,
            )
            mgr._server_cache = {runtime.config_id: runtime}
            mgr._register_server(runtime)

        self.assertTrue(any("requires a proxy" in message for message in logs.output))
