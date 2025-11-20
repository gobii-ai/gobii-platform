from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from api.integrations.pipedream_connect import create_connect_session
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PipedreamConnectSession,
    MCPServerConfig,
    PersistentAgentEnabledTool,
)
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
    with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
        return BrowserUseAgent.objects.create(user=user, name="test-browser-agent")


@tag("pipedream_connect")
class PipedreamTrelloConnectTests(TestCase):
    def setUp(self):
        Site.objects.update_or_create(id=1, defaults={"domain": "example.com", "name": "example"})

    @patch("api.integrations.pipedream_connect.requests.post")
    @patch("api.integrations.pipedream_connect.get_mcp_manager")
    def test_create_connect_session_trello(self, mock_get_mgr, mock_post):
        """Trello connect session appends app=trello and persists token/link."""

        User = get_user_model()
        user = User.objects.create_user(username="trello@example.com")
        bua = _create_browser_agent(user)
        agent = PersistentAgent.objects.create(user=user, name="trello-agent", charter="c", browser_use_agent=bua)

        mgr = MagicMock()
        mgr._get_pipedream_access_token.return_value = "pd_token"
        mock_get_mgr.return_value = mgr

        resp = MagicMock()
        future_expires = (timezone.now() + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        resp.json.return_value = {
            "token": "ctok_trello",
            "connect_link_url": "https://pipedream.com/_static/connect.html?token=ctok_trello",
            "expires_at": future_expires,
        }
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp

        with override_settings(PIPEDREAM_PROJECT_ID="proj_123", PIPEDREAM_ENVIRONMENT="development"):
            session, url = create_connect_session(agent, "trello")

        self.assertIsInstance(session, PipedreamConnectSession)
        self.assertIn("app=trello", url)
        self.assertEqual(session.connect_token, "ctok_trello")
        self.assertIn("pipedream.com/_static/connect.html", session.connect_link_url)


@tag("pipedream_connect")
class PipedreamTrelloManagerTests(TestCase):
    def setUp(self):
        Site.objects.update_or_create(id=1, defaults={"domain": "example.com", "name": "example"})

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    @patch("api.integrations.pipedream_connect.create_connect_session")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._execute_async", new_callable=MagicMock)
    def test_execute_tool_rewrites_connect_link_trello(self, mock_exec, mock_loop, mock_create):
        """Connect link extraction and rewrite works for Trello tools."""

        User = get_user_model()
        user = User.objects.create_user(username="trello2@example.com")
        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            bua = BrowserUseAgent.objects.create(user=user, name="bua")
        agent = PersistentAgent.objects.create(user=user, name="agent-trello", charter="c", browser_use_agent=bua)

        from api.agent.tools.mcp_manager import MCPToolInfo, MCPToolManager

        mgr = MCPToolManager()
        mgr._get_pipedream_access_token = MagicMock(return_value="pd_token")
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
            auth_method=config.auth_method,
            env=config.environment or {},
            headers=config.headers or {},
            prefetch_apps=list(config.prefetch_apps or []),
            scope=config.scope,
            organization_id=str(config.organization_id) if config.organization_id else None,
            user_id=str(config.user_id) if config.user_id else None,
            updated_at=config.updated_at,
        )
        tool = MCPToolInfo(str(config.id), "trello-create-card", "pipedream", "trello-create-card", "desc", {})
        mgr._server_cache = {runtime.config_id: runtime}
        mgr._tools_cache = {runtime.config_id: [tool]}
        mgr._clients = {runtime.config_id: MagicMock()}
        PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name="trello-create-card",
            tool_server="pipedream",
            tool_name="trello-create-card",
            server_config_id=runtime.config_id,
        )

        fake_result = MagicMock()
        fake_result.is_error = False
        fake_result.data = None
        block = MagicMock()
        block.text = "Please connect: https://pipedream.com/_static/connect.html?token=ctok_trello&app=trello"
        fake_result.content = [block]
        loop = MagicMock()
        loop.run_until_complete.side_effect = lambda _: fake_result
        mock_loop.return_value = loop
        mock_exec.return_value = fake_result

        mock_create.return_value = (MagicMock(), "https://example.com/connect?token=abc&app=trello")

        with patch.object(mgr, "_select_agent_proxy_url", return_value=(None, None)):
            res = mgr.execute_mcp_tool(agent, "trello-create-card", {"instruction": "x"})

        self.assertEqual(res.get("status"), "action_required")
        self.assertIn("example.com/connect", res.get("connect_url"))


@tag("pipedream_connect")
class PipedreamTrelloDiscoveryTests(TestCase):
    def setUp(self):
        Site.objects.update_or_create(id=1, defaults={"domain": "example.com", "name": "example"})

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    @patch("api.agent.tools.mcp_manager.select_proxy", return_value=None)
    @patch("api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop")
    @patch("api.agent.tools.mcp_manager.Client")
    @patch("fastmcp.client.transports.StreamableHttpTransport")
    def test_discovery_initial_app_slug_trello(self, mock_transport, mock_client_cls, mock_loop, mock_select_proxy):
        """When Trello is the only prefetch app, headers include the Trello slug."""

        from api.agent.tools.mcp_manager import MCPToolManager

        mgr = MCPToolManager()

        loop = MagicMock()
        loop.run_until_complete.return_value = []
        mock_loop.return_value = loop
        mock_client_cls.return_value = MagicMock()

        seen_app = {}

        def fake_headers(mode, app_slug, external_user_id, conversation_id):
            seen_app["app"] = app_slug
            return {"Authorization": "Bearer x", "x-pd-app-slug": app_slug or ""}

        with patch.object(mgr, "_pd_build_headers", side_effect=fake_headers):
            with patch.object(mgr, "_fetch_server_tools", return_value=[]):
                with patch.object(mgr, "_get_pipedream_access_token", return_value="pd_token"):
                    with override_settings(
                        PIPEDREAM_CLIENT_ID="cli",
                        PIPEDREAM_CLIENT_SECRET="sec",
                        PIPEDREAM_PROJECT_ID="proj",
                        PIPEDREAM_ENVIRONMENT="development",
                        PIPEDREAM_PREFETCH_APPS="trello",
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
                            auth_method=config.auth_method,
                            env=config.environment or {},
                            headers=config.headers or {},
                            prefetch_apps=["trello"],
                            scope=config.scope,
                            organization_id=str(config.organization_id) if config.organization_id else None,
                            user_id=str(config.user_id) if config.user_id else None,
                            updated_at=config.updated_at,
                        )
                        mgr._server_cache = {runtime.config_id: runtime}
                        mgr._register_server(runtime)

        self.assertEqual(seen_app.get("app"), "trello")
        _, kwargs = mock_transport.call_args
        self.assertEqual(kwargs["headers"].get("x-pd-app-slug"), "trello")
        self.assertIn("httpx_client_factory", kwargs)
        self.assertTrue(callable(kwargs["httpx_client_factory"]))
