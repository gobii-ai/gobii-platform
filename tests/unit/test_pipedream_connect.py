import json
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, RequestFactory, tag
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.urls import reverse
from django.utils import timezone

from api.models import (
    PersistentAgent,
    BrowserUseAgent,
    PipedreamConnectSession,
    PersistentAgentSystemStep,
    MCPServerConfig,
    PersistentAgentEnabledTool,
)
from api.agent.tools.mcp_manager import MCPToolManager, MCPToolInfo
from api.integrations.pipedream_connect import create_connect_session
from api.services.mcp_config_validation import validate_mcp_metadata_environment_references
from api.services.pipedream_apps import get_pipedream_oauth_app_id
from api.services.pipedream_connections import PipedreamConnectionError
from api.webhooks import pipedream_connect_webhook


def _create_browser_agent(user):
    with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
        return BrowserUseAgent.objects.create(user=user, name="test-browser-agent")


def _ensure_pipedream_config():
    config, _ = MCPServerConfig.objects.get_or_create(
        scope=MCPServerConfig.Scope.PLATFORM,
        name="pipedream-test",
        defaults={
            "display_name": "Pipedream Test",
            "description": "Test config",
            "url": "https://pipedream.example.com",
            "prefetch_apps": ["google_sheets"],
        },
    )
    if not config.url:
        config.url = "https://pipedream.example.com"
        config.save(update_fields=["url"])
    return config


def _ensure_platform_pipedream_config(metadata=None):
    config, _ = MCPServerConfig.objects.update_or_create(
        scope=MCPServerConfig.Scope.PLATFORM,
        name="pipedream",
        defaults={
            "display_name": "Pipedream",
            "description": "Pipedream test config",
            "url": "https://pipedream.example.com",
            "prefetch_apps": ["google_sheets", "notion"],
            "metadata": metadata or {},
            "is_active": True,
        },
    )
    return config


def _setup_pipedream_tool(mgr, agent, description="desc"):
    config = _ensure_pipedream_config()
    tool = MCPToolInfo(
        str(config.id),
        "google_sheets-add-single-row",
        "pipedream",
        "google_sheets-add-single-row",
        description,
        {},
    )
    mgr._initialized = True
    mgr._tools_cache = {str(config.id): [tool]}
    mgr._get_pipedream_access_token = MagicMock(return_value="pd_token")
    cache_key = f"{agent.id}:google_sheets"
    client_mock = MagicMock()
    client_mock.transport = MagicMock(headers={})
    mgr._pd_agent_clients[cache_key] = client_mock
    PersistentAgentEnabledTool.objects.create(
        agent=agent,
        tool_full_name=tool.full_name,
        tool_server=tool.server_name,
        tool_name=tool.tool_name,
        server_config=config,
    )
    return tool


@tag("pipedream_connect")
class PipedreamConnectHelperTests(TestCase):
    def setUp(self):
        Site.objects.update_or_create(id=1, defaults={"domain": "example.com", "name": "example"})

    def test_pipedream_headers_request_tools_only_mode(self):
        mgr = MCPToolManager()
        mgr._get_pipedream_access_token = MagicMock(return_value="pd_token")

        headers = mgr._pd_build_headers(
            app_slug="google_sheets",
            external_user_id="agent-id",
            conversation_id="conversation-id",
        )

        self.assertEqual(headers["x-pd-app-slug"], "google_sheets")
        self.assertEqual(headers["x-pd-tool-mode"], "tools-only")
        self.assertNotIn("x-pd-oauth-app-id", headers)

    def test_pipedream_oauth_app_id_helper_normalizes_metadata_slugs(self):
        _ensure_platform_pipedream_config(
            metadata={
                "pipedream_oauth_app_ids": {
                    " Notion ": " oa_notion ",
                },
            }
        )

        self.assertEqual(get_pipedream_oauth_app_id("notion"), "oa_notion")
        self.assertEqual(get_pipedream_oauth_app_id(" Notion "), "oa_notion")
        self.assertEqual(get_pipedream_oauth_app_id("google_sheets"), "")

    def test_pipedream_oauth_app_id_metadata_validation_rejects_invalid_shape(self):
        errors = validate_mcp_metadata_environment_references(
            {"pipedream_oauth_app_ids": {"notion": "oa_notion"}}
        )
        self.assertEqual(errors, [])

        errors = validate_mcp_metadata_environment_references(
            {"pipedream_oauth_app_ids": {"notion": ""}}
        )
        self.assertTrue(any("pipedream_oauth_app_ids" in error for error in errors))

        errors = validate_mcp_metadata_environment_references(
            {"pipedream_oauth_app_ids": ["notion", "oa_notion"]}
        )
        self.assertTrue(any("pipedream_oauth_app_ids" in error for error in errors))

    def test_pipedream_headers_include_oauth_app_id_for_mapped_single_app(self):
        _ensure_platform_pipedream_config(
            metadata={"pipedream_oauth_app_ids": {"notion": "oa_notion"}}
        )
        mgr = MCPToolManager()
        mgr._get_pipedream_access_token = MagicMock(return_value="pd_token")

        headers = mgr._pd_build_headers(
            app_slug="notion",
            external_user_id="agent-id",
            conversation_id="conversation-id",
        )

        self.assertEqual(headers["x-pd-app-slug"], "notion")
        self.assertEqual(headers["x-pd-oauth-app-id"], "oa_notion")

    def test_pipedream_headers_omit_oauth_app_id_for_discovery_app_list(self):
        _ensure_platform_pipedream_config(
            metadata={"pipedream_oauth_app_ids": {"notion": "oa_notion"}}
        )
        mgr = MCPToolManager()
        mgr._get_pipedream_access_token = MagicMock(return_value="pd_token")

        headers = mgr._pd_build_headers(
            app_slug="notion,google_sheets",
            external_user_id="agent-id",
            conversation_id="conversation-id",
        )

        self.assertEqual(headers["x-pd-app-slug"], "notion,google_sheets")
        self.assertNotIn("x-pd-oauth-app-id", headers)

    def test_pipedream_agent_client_cache_key_includes_oauth_app_id_when_mapped(self):
        mgr = MCPToolManager()
        self.assertEqual(mgr._pd_agent_client_cache_key("agent-id", "notion"), "agent-id:notion")

        _ensure_platform_pipedream_config(
            metadata={"pipedream_oauth_app_ids": {"notion": "oa_notion"}}
        )

        self.assertEqual(
            mgr._pd_agent_client_cache_key("agent-id", "notion"),
            "agent-id:notion:oa_notion",
        )

    @patch("api.integrations.pipedream_connect.requests.post")
    @patch("api.integrations.pipedream_connect.get_mcp_manager")
    def test_create_connect_session_success(self, mock_get_mgr, mock_post):
        # Arrange agent
        User = get_user_model()
        user = User.objects.create_user(username="user@example.com")
        bua = _create_browser_agent(user)
        agent = PersistentAgent.objects.create(user=user, name="a", charter="c", browser_use_agent=bua)

        # Mock token and API response
        mgr = MagicMock()
        mgr._get_pipedream_access_token.return_value = "pd_token"
        mock_get_mgr.return_value = mgr

        resp = MagicMock()
        future_expires = (timezone.now() + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        resp.json.return_value = {
            "token": "ctok_abc",
            "connect_link_url": "https://pipedream.com/_static/connect.html?token=ctok_abc",
            "expires_at": future_expires,
        }
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp

        # Act
        from django.test import override_settings
        with override_settings(PIPEDREAM_PROJECT_ID="proj_123", PIPEDREAM_ENVIRONMENT="development"):
            session, url = create_connect_session(agent, "google_sheets")

        # Assert
        self.assertTrue(isinstance(session, PipedreamConnectSession))
        self.assertIn("app=google_sheets", url)
        self.assertNotIn("oauthAppId=", url)
        self.assertEqual(session.connect_token, "ctok_abc")
        # Stored link is the Pipedream connect link
        self.assertIn("pipedream.com/_static/connect.html", session.connect_link_url)

    @patch("api.integrations.pipedream_connect.requests.post")
    @patch("api.integrations.pipedream_connect.get_mcp_manager")
    def test_create_connect_session_appends_oauth_app_id_for_mapped_app(self, mock_get_mgr, mock_post):
        _ensure_platform_pipedream_config(
            metadata={"pipedream_oauth_app_ids": {"notion": "oa_notion"}}
        )
        User = get_user_model()
        user = User.objects.create_user(username="notion-user@example.com")
        bua = _create_browser_agent(user)
        agent = PersistentAgent.objects.create(user=user, name="a", charter="c", browser_use_agent=bua)

        mgr = MagicMock()
        mgr._get_pipedream_access_token.return_value = "pd_token"
        mock_get_mgr.return_value = mgr

        resp = MagicMock()
        future_expires = (timezone.now() + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        resp.json.return_value = {
            "token": "ctok_notion",
            "connect_link_url": "https://pipedream.com/_static/connect.html?token=ctok_notion",
            "expires_at": future_expires,
        }
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp

        from django.test import override_settings
        with override_settings(PIPEDREAM_PROJECT_ID="proj_123", PIPEDREAM_ENVIRONMENT="development"):
            session, url = create_connect_session(agent, "notion")

        self.assertTrue(isinstance(session, PipedreamConnectSession))
        self.assertIn("app=notion", url)
        self.assertIn("oauthAppId=oa_notion", url)

    @patch("api.integrations.pipedream_connect.requests.post")
    @patch("api.integrations.pipedream_connect.get_mcp_manager")
    def test_create_connect_session_rejects_expired_link(self, mock_get_mgr, mock_post):
        User = get_user_model()
        user = User.objects.create_user(username="expired@example.com")
        bua = _create_browser_agent(user)
        agent = PersistentAgent.objects.create(user=user, name="a-exp", charter="c", browser_use_agent=bua)

        mgr = MagicMock()
        mgr._get_pipedream_access_token.return_value = "pd_token"
        mock_get_mgr.return_value = mgr

        expired_at = (timezone.now() - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        resp = MagicMock()
        resp.json.return_value = {
            "token": "ctok_expired",
            "connect_link_url": "https://pipedream.com/_static/connect.html?token=ctok_expired",
            "expires_at": expired_at,
        }
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp

        from django.test import override_settings
        with override_settings(PIPEDREAM_PROJECT_ID="proj_123", PIPEDREAM_ENVIRONMENT="development"):
            session, url = create_connect_session(agent, "google_sheets")

        self.assertIsNone(url)
        session.refresh_from_db()
        self.assertEqual(session.status, PipedreamConnectSession.Status.ERROR)
        self.assertEqual(session.connect_token, "ctok_expired")


@tag("pipedream_connect")
class PipedreamConnectWebhookTests(TestCase):
    def setUp(self):
        Site.objects.update_or_create(id=1, defaults={"domain": "example.com", "name": "example"})
        self.factory = RequestFactory()

    def _mk_agent(self):
        User = get_user_model()
        user = User.objects.create_user(username="user2@example.com")
        bua = _create_browser_agent(user)
        return PersistentAgent.objects.create(user=user, name="a2", charter="c2", browser_use_agent=bua)

    @patch("api.agent.tasks.process_events.process_agent_events_task")
    def test_webhook_success_flow(self, mock_task):
        agent = self._mk_agent()
        session = PipedreamConnectSession.objects.create(
            agent=agent,
            external_user_id=str(agent.id),
            conversation_id=str(agent.id),
            app_slug="google_sheets",
            connect_token="ctok_123",
            webhook_secret="s3cr3t",
            status=PipedreamConnectSession.Status.PENDING,
        )

        payload = {
            "event": "CONNECTION_SUCCESS",
            "connect_token": "ctok_123",
            "environment": "development",
            "connect_session_id": 123,
            "account": {"id": "apn_abc123"},
        }

        url = f"/api/v1/webhooks/pipedream/connect/{session.id}/?t=s3cr3t"
        req = self.factory.post(url, data=json.dumps(payload), content_type="application/json")
        resp = pipedream_connect_webhook(req, session_id=str(session.id))

        self.assertEqual(resp.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.status, PipedreamConnectSession.Status.SUCCESS)
        self.assertEqual(session.account_id, "apn_abc123")
        mock_task.delay.assert_called_once()

        # system step recorded
        system_step = PersistentAgentSystemStep.objects.get(
            step__agent=agent,
            code=PersistentAgentSystemStep.Code.CREDENTIALS_PROVIDED,
        )
        self.assertIn("Call search_tools", system_step.step.description)
        self.assertIn("google_sheets", system_step.step.description)


@tag("pipedream_connect")
class PipedreamManagerConnectLinkTests(TestCase):
    def setUp(self):
        Site.objects.update_or_create(id=1, defaults={"domain": "example.com", "name": "example"})

    @patch("api.services.pipedream_connections.list_pipedream_connected_accounts", return_value=[object()])
    @patch("api.integrations.pipedream_connect.create_connect_session")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._execute_async", new_callable=MagicMock)
    def test_execute_tool_rewrites_connect_link(self, mock_exec, mock_loop, mock_create, _mock_accounts):
        # Arrange agent
        User = get_user_model()
        user = User.objects.create_user(username="p3@example.com")
        with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
            bua = BrowserUseAgent.objects.create(user=user, name="bua")
        agent = PersistentAgent.objects.create(user=user, name="agent3", charter="c", browser_use_agent=bua)

        # Prepare manager
        mgr = MCPToolManager()
        _setup_pipedream_tool(mgr, agent)

        # Fake result containing Pipedream's connect link with app
        r = MagicMock()
        r.is_error = False
        r.data = None
        block = MagicMock()
        block.text = "Please connect: https://pipedream.com/_static/connect.html?token=ctok_zzz&app=google_sheets"
        r.content = [block]
        loop = MagicMock()
        loop.run_until_complete.side_effect = lambda _: r
        mock_loop.return_value = loop
        mock_exec.return_value = r

        # Our session factory returns custom URL
        fake_session = MagicMock()
        mock_create.return_value = (fake_session, "https://example.com/connect?token=abc&app=google_sheets")

        # Act
        res = mgr.execute_mcp_tool(agent, "google_sheets-add-single-row", {"sheetId": "sheet", "worksheetId": "1"})

        # Assert
        self.assertEqual(res.get("status"), "action_required")
        self.assertIn("example.com/connect", res.get("connect_url"))

    @patch("api.services.pipedream_connections.list_pipedream_connected_accounts", return_value=[object()])
    @patch("api.integrations.pipedream_connect.create_connect_session")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._execute_async", new_callable=MagicMock)
    def test_execute_tool_blocks_expired_connect_link(self, mock_exec, mock_loop, mock_create, _mock_accounts):
        User = get_user_model()
        user = User.objects.create_user(username="p4@example.com")
        with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
            bua = BrowserUseAgent.objects.create(user=user, name="bua2")
        agent = PersistentAgent.objects.create(user=user, name="agent4", charter="c", browser_use_agent=bua)

        mgr = MCPToolManager()
        _setup_pipedream_tool(mgr, agent)

        r = MagicMock()
        r.is_error = False
        r.data = None
        block = MagicMock()
        block.text = "Please connect: https://pipedream.com/_static/connect.html?token=ctok_expired&app=google_sheets"
        r.content = [block]
        loop = MagicMock()
        loop.run_until_complete.side_effect = lambda _: r
        mock_loop.return_value = loop
        mock_exec.return_value = r

        expired_session = PipedreamConnectSession.objects.create(
            agent=agent,
            external_user_id=str(agent.id),
            conversation_id=str(agent.id),
            app_slug="google_sheets",
            connect_token="ctok_expired",
            connect_link_url="https://pipedream.com/_static/connect.html?token=ctok_expired",
            expires_at=timezone.now() - timedelta(minutes=5),
            webhook_secret="secret",
            status=PipedreamConnectSession.Status.ERROR,
        )
        mock_create.return_value = (expired_session, None)

        res = mgr.execute_mcp_tool(agent, "google_sheets-add-single-row", {"sheetId": "sheet", "worksheetId": "1"})

        self.assertEqual(res.get("status"), "action_required")
        self.assertIsNone(res.get("connect_url"))
        self.assertIn("expired", res.get("result", "").lower())

    @patch("api.services.pipedream_connections.list_pipedream_connected_accounts", return_value=[object()])
    @patch("api.integrations.pipedream_connect.create_connect_session")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._execute_async", new_callable=MagicMock)
    def test_execute_tool_reuses_pending_session(self, mock_exec, mock_loop, mock_create, _mock_accounts):
        User = get_user_model()
        user = User.objects.create_user(username="reuse@example.com")
        with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
            bua = BrowserUseAgent.objects.create(user=user, name="bua-reuse")
        agent = PersistentAgent.objects.create(user=user, name="agent-reuse", charter="c", browser_use_agent=bua)

        mgr = MCPToolManager()
        _setup_pipedream_tool(mgr, agent)

        # Tool response containing connect link
        response = MagicMock()
        response.is_error = False
        response.data = None
        block = MagicMock()
        block.text = "https://pipedream.com/_static/connect.html?token=ctok_reuse&app=google_sheets"
        response.content = [block]
        loop = MagicMock()
        loop.run_until_complete.side_effect = lambda _: response
        mock_loop.return_value = loop
        mock_exec.return_value = response

        # Existing pending session that should be reused
        future_expiry = timezone.now() + timedelta(hours=1)
        session = PipedreamConnectSession.objects.create(
            agent=agent,
            external_user_id=str(agent.id),
            conversation_id=str(agent.id),
            app_slug="google_sheets",
            connect_token="ctok_reuse",
            connect_link_url="https://pipedream.com/_static/connect.html?token=ctok_reuse",
            expires_at=future_expiry,
            webhook_secret="secret",
            status=PipedreamConnectSession.Status.PENDING,
        )

        res = mgr.execute_mcp_tool(agent, "google_sheets-add-single-row", {"sheetId": "sheet", "worksheetId": "1"})

        self.assertEqual(res.get("status"), "action_required")
        # Now returns JIT URL instead of direct Pipedream URL
        connect_url = res.get("connect_url", "")
        self.assertIn("/connect/pipedream/", connect_url)
        self.assertIn(str(agent.id), connect_url)
        self.assertIn("/google_sheets/", connect_url)
        # create_connect_session should NOT be called since we reuse existing session
        mock_create.assert_not_called()
        session.refresh_from_db()
        self.assertEqual(session.status, PipedreamConnectSession.Status.PENDING)

    @patch("api.services.pipedream_connections.list_pipedream_connected_accounts", return_value=[])
    @patch("api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._execute_async", new_callable=MagicMock)
    def test_execute_tool_returns_connect_link_when_app_has_no_account(
        self,
        mock_exec,
        mock_loop,
        mock_accounts,
    ):
        User = get_user_model()
        user = User.objects.create_user(username="missing-account@example.com")
        with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
            bua = BrowserUseAgent.objects.create(user=user, name="bua-missing")
        agent = PersistentAgent.objects.create(user=user, name="agent-missing", charter="c", browser_use_agent=bua)

        mgr = MCPToolManager()
        _setup_pipedream_tool(mgr, agent)

        res = mgr.execute_mcp_tool(agent, "google_sheets-add-single-row", {"sheetId": "sheet", "worksheetId": "1"})

        self.assertEqual(res.get("status"), "action_required")
        connect_url = res.get("connect_url", "")
        self.assertIn("/connect/pipedream/", connect_url)
        self.assertIn(str(agent.id), connect_url)
        self.assertIn("/google_sheets/", connect_url)
        self.assertIn("Authorization required", res.get("result", ""))
        mock_accounts.assert_called_once_with(agent, app_slug="google_sheets")
        mock_exec.assert_not_called()
        mock_loop.assert_not_called()

    @patch("api.services.pipedream_connections.list_pipedream_connected_accounts", return_value=[object()])
    @patch("api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._execute_async", new_callable=MagicMock)
    def test_execute_tool_proceeds_when_app_has_account(
        self,
        mock_exec,
        mock_loop,
        mock_accounts,
    ):
        User = get_user_model()
        user = User.objects.create_user(username="connected@example.com")
        with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
            bua = BrowserUseAgent.objects.create(user=user, name="bua-connected")
        agent = PersistentAgent.objects.create(user=user, name="agent-connected", charter="c", browser_use_agent=bua)

        mgr = MCPToolManager()
        _setup_pipedream_tool(mgr, agent)

        response = MagicMock()
        response.is_error = False
        response.data = {"ok": True}
        response.content = []
        loop = MagicMock()
        loop.run_until_complete.side_effect = lambda _: response
        mock_loop.return_value = loop
        mock_exec.return_value = response

        res = mgr.execute_mcp_tool(agent, "google_sheets-add-single-row", {"sheetId": "sheet", "worksheetId": "1"})

        self.assertEqual(res.get("status"), "success")
        self.assertEqual(res.get("result"), {"ok": True})
        mock_accounts.assert_called_once_with(agent, app_slug="google_sheets")
        mock_exec.assert_called_once()

    @patch("api.services.pipedream_connections.list_pipedream_connected_accounts", return_value=[])
    @patch("api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._execute_async", new_callable=MagicMock)
    def test_retrieve_options_preflights_app_from_component_key(
        self,
        mock_exec,
        mock_loop,
        mock_accounts,
    ):
        User = get_user_model()
        user = User.objects.create_user(username="options-missing@example.com")
        with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
            bua = BrowserUseAgent.objects.create(user=user, name="bua-options")
        agent = PersistentAgent.objects.create(user=user, name="agent-options", charter="c", browser_use_agent=bua)

        mgr = MCPToolManager()
        config = _ensure_pipedream_config()
        tool = MCPToolInfo(
            str(config.id),
            "retrieve_options",
            "pipedream",
            "retrieve_options",
            "Retrieve component options",
            {},
        )
        mgr._initialized = True
        mgr._tools_cache = {str(config.id): [tool]}
        mgr._get_pipedream_access_token = MagicMock(return_value="pd_token")
        PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name=tool.full_name,
            tool_server=tool.server_name,
            tool_name=tool.tool_name,
            server_config=config,
        )

        res = mgr.execute_mcp_tool(
            agent,
            "retrieve_options",
            {
                "componentKey": "google_sheets-add-single-row",
                "propName": "spreadsheetId",
            },
        )

        self.assertEqual(res.get("status"), "action_required")
        self.assertIn("/google_sheets/", res.get("connect_url", ""))
        mock_accounts.assert_called_once_with(agent, app_slug="google_sheets")
        mock_exec.assert_not_called()
        mock_loop.assert_not_called()

    @patch(
        "api.services.pipedream_connections.list_pipedream_connected_accounts",
        side_effect=PipedreamConnectionError("Pipedream account lookup failed."),
    )
    @patch("api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._execute_async", new_callable=MagicMock)
    def test_execute_tool_returns_error_when_connection_lookup_fails(
        self,
        mock_exec,
        mock_loop,
        mock_accounts,
    ):
        User = get_user_model()
        user = User.objects.create_user(username="lookup-error@example.com")
        with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
            bua = BrowserUseAgent.objects.create(user=user, name="bua-lookup-error")
        agent = PersistentAgent.objects.create(user=user, name="agent-lookup-error", charter="c", browser_use_agent=bua)

        mgr = MCPToolManager()
        _setup_pipedream_tool(mgr, agent)

        res = mgr.execute_mcp_tool(agent, "google_sheets-add-single-row", {"sheetId": "sheet", "worksheetId": "1"})

        self.assertEqual(res.get("status"), "error")
        self.assertIn("Pipedream account lookup failed", res.get("message", ""))
        self.assertNotIn("connect_url", res)
        mock_accounts.assert_called_once_with(agent, app_slug="google_sheets")
        mock_exec.assert_not_called()
        mock_loop.assert_not_called()
