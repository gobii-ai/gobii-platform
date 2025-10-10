import json
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, RequestFactory, tag
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.urls import reverse
from django.utils import timezone

from api.models import PersistentAgent, BrowserUseAgent, PipedreamConnectSession, PersistentAgentSystemStep
from api.integrations.pipedream_connect import create_connect_session
from api.webhooks import pipedream_connect_webhook


def _create_browser_agent(user):
    with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
        return BrowserUseAgent.objects.create(user=user, name="test-browser-agent")


@tag("pipedream_connect")
class PipedreamConnectHelperTests(TestCase):
    def setUp(self):
        Site.objects.update_or_create(id=1, defaults={"domain": "example.com", "name": "example"})

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
        self.assertEqual(session.connect_token, "ctok_abc")
        # Stored link is the Pipedream connect link
        self.assertIn("pipedream.com/_static/connect.html", session.connect_link_url)

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
        self.assertTrue(PersistentAgentSystemStep.objects.filter(step__agent=agent, code=PersistentAgentSystemStep.Code.CREDENTIALS_PROVIDED).exists())


@tag("pipedream_connect")
class PipedreamManagerConnectLinkTests(TestCase):
    def setUp(self):
        Site.objects.update_or_create(id=1, defaults={"domain": "example.com", "name": "example"})

    @patch("api.integrations.pipedream_connect.create_connect_session")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._execute_async", new_callable=MagicMock)
    def test_execute_tool_rewrites_connect_link(self, mock_exec, mock_loop, mock_create):
        # Arrange agent
        User = get_user_model()
        user = User.objects.create_user(username="p3@example.com")
        with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
            bua = BrowserUseAgent.objects.create(user=user, name="bua")
        agent = PersistentAgent.objects.create(user=user, name="agent3", charter="c", browser_use_agent=bua)

        # Prepare manager
        from api.agent.tools.mcp_manager import MCPToolManager, MCPToolInfo, enable_mcp_tool
        mgr = MCPToolManager()
        mgr._initialized = True
        tool = MCPToolInfo("google_sheets-add-single-row", "pipedream", "google_sheets-add-single-row", "desc", {})
        mgr._tools_cache = {"pipedream": [tool]}

        # Enable tool for agent
        with patch('api.agent.tools.mcp_manager._mcp_manager.get_all_available_tools') as mock_all:
            mock_all.return_value = [tool]
            enable_mcp_tool(agent, "google_sheets-add-single-row")

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
        res = mgr.execute_mcp_tool(agent, "google_sheets-add-single-row", {"instruction": "x"})

        # Assert
        self.assertEqual(res.get("status"), "action_required")
        self.assertIn("example.com/connect", res.get("connect_url"))

    @patch("api.integrations.pipedream_connect.create_connect_session")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._execute_async", new_callable=MagicMock)
    def test_execute_tool_blocks_expired_connect_link(self, mock_exec, mock_loop, mock_create):
        User = get_user_model()
        user = User.objects.create_user(username="p4@example.com")
        with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
            bua = BrowserUseAgent.objects.create(user=user, name="bua2")
        agent = PersistentAgent.objects.create(user=user, name="agent4", charter="c", browser_use_agent=bua)

        from api.agent.tools.mcp_manager import MCPToolManager, MCPToolInfo, enable_mcp_tool
        mgr = MCPToolManager()
        mgr._initialized = True
        tool = MCPToolInfo("google_sheets-add-single-row", "pipedream", "google_sheets-add-single-row", "desc", {})
        mgr._tools_cache = {"pipedream": [tool]}

        with patch('api.agent.tools.mcp_manager._mcp_manager.get_all_available_tools') as mock_all:
            mock_all.return_value = [tool]
            enable_mcp_tool(agent, "google_sheets-add-single-row")

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

        res = mgr.execute_mcp_tool(agent, "google_sheets-add-single-row", {"instruction": "x"})

        self.assertEqual(res.get("status"), "action_required")
        self.assertIsNone(res.get("connect_url"))
        self.assertIn("expired", res.get("result", "").lower())

    @patch("api.integrations.pipedream_connect.create_connect_session")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._ensure_event_loop")
    @patch("api.agent.tools.mcp_manager.MCPToolManager._execute_async", new_callable=MagicMock)
    def test_execute_tool_reuses_pending_session(self, mock_exec, mock_loop, mock_create):
        User = get_user_model()
        user = User.objects.create_user(username="reuse@example.com")
        with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
            bua = BrowserUseAgent.objects.create(user=user, name="bua-reuse")
        agent = PersistentAgent.objects.create(user=user, name="agent-reuse", charter="c", browser_use_agent=bua)

        from api.agent.tools.mcp_manager import MCPToolManager, MCPToolInfo, enable_mcp_tool
        mgr = MCPToolManager()
        mgr._initialized = True
        tool = MCPToolInfo("google_sheets-add-single-row", "pipedream", "google_sheets-add-single-row", "desc", {})
        mgr._tools_cache = {"pipedream": [tool]}

        with patch('api.agent.tools.mcp_manager._mcp_manager.get_all_available_tools') as mock_all:
            mock_all.return_value = [tool]
            enable_mcp_tool(agent, "google_sheets-add-single-row")

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

        res = mgr.execute_mcp_tool(agent, "google_sheets-add-single-row", {"instruction": "x"})

        self.assertEqual(res.get("status"), "action_required")
        self.assertIn("ctok_reuse", res.get("connect_url", ""))
        self.assertIn("app=google_sheets", res.get("connect_url", ""))
        mock_create.assert_not_called()
        session.refresh_from_db()
        self.assertEqual(session.status, PipedreamConnectSession.Status.PENDING)
