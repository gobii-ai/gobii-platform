import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, RequestFactory, tag
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.urls import reverse

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
        resp.json.return_value = {
            "token": "ctok_abc",
            "connect_link_url": "https://pipedream.com/_static/connect.html?token=ctok_abc",
            "expires_at": "2025-09-30T00:00:00Z",
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
    @patch("api.agent.tools.mcp_manager.MCPToolManager._execute_async")
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
        loop.run_until_complete.return_value = r
        mock_loop.return_value = loop

        # Our session factory returns custom URL
        fake_session = MagicMock()
        mock_create.return_value = (fake_session, "https://example.com/connect?token=abc&app=google_sheets")

        # Act
        res = mgr.execute_mcp_tool(agent, "google_sheets-add-single-row", {"instruction": "x"})

        # Assert
        self.assertEqual(res.get("status"), "action_required")
        self.assertIn("example.com/connect", res.get("connect_url"))
