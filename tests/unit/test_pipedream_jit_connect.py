from datetime import timedelta
from unittest.mock import patch, MagicMock
import uuid as uuid_module

from django.test import TestCase, Client, tag, override_settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.urls import reverse
from django.utils import timezone

from api.models import (
    PersistentAgent,
    BrowserUseAgent,
    PipedreamConnectSession,
    Organization,
    OrganizationMembership,
)


def _create_browser_agent(user, name=None):
    if name is None:
        name = f"test-browser-agent-{uuid_module.uuid4().hex[:8]}"
    with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
        return BrowserUseAgent.objects.create(user=user, name=name)


@tag("pipedream_jit_connect")
@override_settings(
    PIPEDREAM_CLIENT_ID='test_client_id',
    PIPEDREAM_CLIENT_SECRET='test_client_secret',
    PIPEDREAM_PROJECT_ID='test_project_id',
)
class PipedreamJitConnectRedirectTests(TestCase):
    """Tests for the just-in-time Pipedream connect redirect endpoint."""

    def setUp(self):
        Site.objects.update_or_create(id=1, defaults={"domain": "example.com", "name": "example"})
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username="jit@example.com", email="jit@example.com", password="testpass123")
        self.other_user = User.objects.create_user(username="other@example.com", email="other@example.com", password="testpass123")
        self.bua = _create_browser_agent(self.user)
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="test-agent",
            charter="test charter",
            browser_use_agent=self.bua
        )

    def _get_url(self, agent_id, app_slug):
        return reverse("pipedream_jit_connect", kwargs={"agent_id": agent_id, "app_slug": app_slug})

    def test_unauthenticated_request_redirects_to_login(self):
        """Unauthenticated users should be redirected to login with next= param."""
        url = self._get_url(self.agent.id, "google_sheets")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        self.assertIn(f'next={url}', response.url)

    def test_nonexistent_agent_redirects_to_console(self):
        """Request for a non-existent agent should redirect to console."""
        self.client.login(username="jit@example.com", password="testpass123")
        fake_agent_id = uuid_module.uuid4()
        url = self._get_url(fake_agent_id, "google_sheets")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/console/')

    def test_agent_not_owned_by_user_redirects_to_console(self):
        """User without access should be redirected to console."""
        self.client.login(username="other@example.com", password="testpass123")
        url = self._get_url(self.agent.id, "google_sheets")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/console/')

    def test_expired_agent_redirects_to_console(self):
        """Expired agents should redirect to console."""
        self.client.login(username="jit@example.com", password="testpass123")
        # Mark agent as expired
        self.agent.life_state = PersistentAgent.LifeState.EXPIRED
        self.agent.save()

        url = self._get_url(self.agent.id, "google_sheets")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/console/')

    @override_settings(PIPEDREAM_CLIENT_ID='', PIPEDREAM_CLIENT_SECRET='', PIPEDREAM_PROJECT_ID='')
    def test_pipedream_not_configured_shows_info_page(self):
        """When Pipedream is not configured, show a helpful info page."""
        self.client.login(username="jit@example.com", password="testpass123")
        url = self._get_url(self.agent.id, "google_sheets")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 503)
        self.assertTemplateUsed(response, "integrations/pipedream_connect_error.html")
        self.assertIn(b"Integration not available", response.content)
        self.assertIn(b"not configured", response.content)

    @patch("api.integrations.pipedream_connect.create_connect_session")
    def test_successful_redirect(self, mock_create_session):
        """Authenticated user accessing their own agent should get a redirect."""
        self.client.login(username="jit@example.com", password="testpass123")

        # Mock the session creation
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_create_session.return_value = (mock_session, "https://pipedream.com/connect?token=abc&app=google_sheets")

        url = self._get_url(self.agent.id, "google_sheets")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://pipedream.com/connect?token=abc&app=google_sheets")
        mock_create_session.assert_called_once()

    @patch("api.integrations.pipedream_connect.create_connect_session")
    def test_failed_session_creation_returns_503(self, mock_create_session):
        """If session creation fails, return a user-friendly 503 error page."""
        self.client.login(username="jit@example.com", password="testpass123")

        # Mock failed session creation
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_create_session.return_value = (mock_session, None)

        url = self._get_url(self.agent.id, "google_sheets")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 503)
        self.assertTemplateUsed(response, "integrations/pipedream_connect_error.html")
        self.assertIn(b"Unable to connect", response.content)

    @patch("api.integrations.pipedream_connect.create_connect_session")
    def test_organization_member_can_access_org_agent(self, mock_create_session):
        """Organization members should be able to access organization agents."""
        User = get_user_model()
        org_member = User.objects.create_user(username="member@example.com", email="member@example.com", password="testpass123")

        # Create organization and membership
        org = Organization.objects.create(name="Test Org", slug="test-org", created_by=self.user)
        # Set purchased_seats to allow creating org agents
        billing = org.billing
        billing.purchased_seats = 5
        billing.save()
        # Refresh org to pick up the updated billing
        org.refresh_from_db()

        OrganizationMembership.objects.create(org=org, user=org_member, role=OrganizationMembership.OrgRole.MEMBER)

        # Create an org agent
        org_bua = _create_browser_agent(self.user)
        org_agent = PersistentAgent.objects.create(
            user=self.user,
            organization=org,
            name="org-agent",
            charter="org charter",
            browser_use_agent=org_bua
        )

        self.client.login(username="member@example.com", password="testpass123")

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_create_session.return_value = (mock_session, "https://pipedream.com/connect?token=xyz&app=trello")

        url = self._get_url(org_agent.id, "trello")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://pipedream.com/connect?token=xyz&app=trello")

    def test_non_org_member_cannot_access_org_agent(self):
        """Non-organization members should be redirected to console."""
        User = get_user_model()
        non_member = User.objects.create_user(username="nonmember@example.com", email="nonmember@example.com", password="testpass123")

        # Create organization (user is creator but non_member is not a member)
        org = Organization.objects.create(name="Test Org 2", slug="test-org-2", created_by=self.user)
        # Set purchased_seats to allow creating org agents
        billing = org.billing
        billing.purchased_seats = 5
        billing.save()
        # Refresh org to pick up the updated billing
        org.refresh_from_db()

        # Create an org agent
        org_bua = _create_browser_agent(self.user)
        org_agent = PersistentAgent.objects.create(
            user=self.user,
            organization=org,
            name="org-agent-2",
            charter="org charter",
            browser_use_agent=org_bua
        )

        self.client.login(username="nonmember@example.com", password="testpass123")

        url = self._get_url(org_agent.id, "google_sheets")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/console/')

    @patch("api.integrations.pipedream_connect.create_connect_session")
    def test_different_app_slugs(self, mock_create_session):
        """Test that different app slugs are passed correctly."""
        self.client.login(username="jit@example.com", password="testpass123")

        mock_session = MagicMock()
        mock_session.id = "test-session-id"

        app_slugs = ["google_sheets", "google_docs", "trello", "greenhouse"]
        for app_slug in app_slugs:
            mock_create_session.return_value = (mock_session, f"https://pipedream.com/connect?token=abc&app={app_slug}")

            url = self._get_url(self.agent.id, app_slug)
            response = self.client.get(url)

            self.assertEqual(response.status_code, 302)
            self.assertIn(f"app={app_slug}", response.url)

    def test_reuses_existing_valid_session(self):
        """Existing pending session with sufficient time should be reused."""
        self.client.login(username="jit@example.com", password="testpass123")

        # Create an existing session with plenty of time remaining (3 hours from now)
        existing_session = PipedreamConnectSession.objects.create(
            agent=self.agent,
            app_slug="google_sheets",
            external_user_id=str(self.agent.id),
            conversation_id=str(self.agent.id),
            connect_token="existing_token",
            connect_link_url="https://pipedream.com/connect?token=existing&app=google_sheets",
            expires_at=timezone.now() + timedelta(hours=3),
            webhook_secret="secret123",
            status=PipedreamConnectSession.Status.PENDING,
        )

        url = self._get_url(self.agent.id, "google_sheets")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://pipedream.com/connect?token=existing&app=google_sheets")

    @patch("api.integrations.pipedream_connect.create_connect_session")
    def test_creates_new_session_when_existing_expired(self, mock_create_session):
        """Expired session should not be reused - create a new one."""
        self.client.login(username="jit@example.com", password="testpass123")

        # Create an expired session (5 minutes remaining, less than 15 min buffer)
        PipedreamConnectSession.objects.create(
            agent=self.agent,
            app_slug="google_sheets",
            external_user_id=str(self.agent.id),
            conversation_id=str(self.agent.id),
            connect_token="expired_token",
            connect_link_url="https://pipedream.com/connect?token=expired&app=google_sheets",
            expires_at=timezone.now() + timedelta(minutes=5),
            webhook_secret="secret123",
            status=PipedreamConnectSession.Status.PENDING,
        )

        # Mock should be called since existing session is too close to expiry
        mock_session = MagicMock()
        mock_session.id = "new-session-id"
        mock_create_session.return_value = (mock_session, "https://pipedream.com/connect?token=new&app=google_sheets")

        url = self._get_url(self.agent.id, "google_sheets")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://pipedream.com/connect?token=new&app=google_sheets")
        mock_create_session.assert_called_once()

    @patch("api.integrations.pipedream_connect.create_connect_session")
    def test_does_not_reuse_successful_session(self, mock_create_session):
        """Sessions that already succeeded should not be reused."""
        self.client.login(username="jit@example.com", password="testpass123")

        # Create a successful (completed) session
        PipedreamConnectSession.objects.create(
            agent=self.agent,
            app_slug="google_sheets",
            external_user_id=str(self.agent.id),
            conversation_id=str(self.agent.id),
            connect_token="completed_token",
            connect_link_url="https://pipedream.com/connect?token=completed&app=google_sheets",
            expires_at=timezone.now() + timedelta(hours=3),
            webhook_secret="secret123",
            status=PipedreamConnectSession.Status.SUCCESS,
        )

        # Mock should be called since existing session is not PENDING
        mock_session = MagicMock()
        mock_session.id = "new-session-id"
        mock_create_session.return_value = (mock_session, "https://pipedream.com/connect?token=new&app=google_sheets")

        url = self._get_url(self.agent.id, "google_sheets")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://pipedream.com/connect?token=new&app=google_sheets")
        mock_create_session.assert_called_once()


@tag("pipedream_jit_connect")
class BuildJitConnectUrlTests(TestCase):
    """Tests for the _build_jit_connect_url helper function."""

    def setUp(self):
        Site.objects.update_or_create(id=1, defaults={"domain": "test.gobii.ai", "name": "test"})

    def test_builds_correct_url_format(self):
        """The helper should build a valid JIT connect URL."""
        from api.agent.tools.mcp_manager import _build_jit_connect_url

        agent_id = "12345678-1234-5678-1234-567812345678"
        app_slug = "google_sheets"

        url = _build_jit_connect_url(agent_id, app_slug)

        self.assertEqual(
            url,
            f"https://test.gobii.ai/connect/pipedream/{agent_id}/{app_slug}/"
        )

    def test_handles_different_app_slugs(self):
        """The helper should correctly encode different app slugs."""
        from api.agent.tools.mcp_manager import _build_jit_connect_url

        agent_id = "12345678-1234-5678-1234-567812345678"

        for app_slug in ["google_sheets", "trello", "greenhouse", "slack"]:
            url = _build_jit_connect_url(agent_id, app_slug)
            self.assertIn(f"/{app_slug}/", url)

    def test_uses_site_domain(self):
        """The helper should use the current Site's domain."""
        from api.agent.tools.mcp_manager import _build_jit_connect_url

        # Update site domain
        Site.objects.update_or_create(id=1, defaults={"domain": "custom.example.com", "name": "custom"})

        agent_id = "12345678-1234-5678-1234-567812345678"
        url = _build_jit_connect_url(agent_id, "google_sheets")

        self.assertTrue(url.startswith("https://custom.example.com/"))

    def test_always_uses_https(self):
        """The helper should always generate HTTPS URLs."""
        from api.agent.tools.mcp_manager import _build_jit_connect_url

        agent_id = "12345678-1234-5678-1234-567812345678"
        url = _build_jit_connect_url(agent_id, "google_sheets")

        self.assertTrue(url.startswith("https://"))
