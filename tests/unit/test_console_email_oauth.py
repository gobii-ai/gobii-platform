import json
import os
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from api.models import (
    AgentEmailAccount,
    AgentEmailOAuthCredential,
    AgentEmailOAuthSession,
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
)
from console.api_views import _format_email_connection_error, _normalize_email_error_text


@tag("batch_console_email_oauth")
class AgentEmailOAuthApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="email-oauth-user",
            email="email-oauth@example.com",
            password="password123",
        )
        cls.other_user = User.objects.create_user(
            username="email-oauth-other",
            email="other@example.com",
            password="password123",
        )

        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="BA")

        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="OAuth Agent",
            charter="c",
            browser_use_agent=browser_agent,
        )
        cls.endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=cls.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.com",
            is_primary=True,
        )
        cls.account = AgentEmailAccount.objects.create(endpoint=cls.endpoint)

    def setUp(self):
        self.client.force_login(self.user)

    def test_start_creates_session(self):
        url = reverse("console-email-oauth-start")
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "account_id": str(self.account.pk),
                    "scope": "mail.read",
                    "token_endpoint": "https://oauth.example.com/token",
                    "code_verifier": "secret-verifier",
                    "state": "custom-state",
                    "client_id": "abc123",
                    "client_secret": "shhh",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertIn("session_id", payload)
        self.assertEqual(payload["state"], "custom-state")
        session = AgentEmailOAuthSession.objects.get(id=payload["session_id"])
        self.assertEqual(session.scope, "mail.read")
        self.assertEqual(session.code_verifier, "secret-verifier")
        self.assertEqual(session.client_id, "abc123")
        self.assertEqual(session.client_secret, "shhh")

    def test_start_requires_permission(self):
        self.client.force_login(self.other_user)
        url = reverse("console-email-oauth-start")
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "account_id": str(self.account.pk),
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    @patch.dict(
        os.environ,
        {
            "GOOGLE_CLIENT_ID": "managed-client-id",
            "GOOGLE_CLIENT_SECRET": "managed-secret",
        },
        clear=False,
    )
    def test_start_uses_managed_app(self):
        url = reverse("console-email-oauth-start")
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "account_id": str(self.account.pk),
                    "provider": "gmail",
                    "scope": "mail.read",
                    "token_endpoint": "https://oauth.example.com/token",
                    "use_gobii_app": True,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertEqual(payload["client_id"], "managed-client-id")
        session = AgentEmailOAuthSession.objects.get(id=payload["session_id"])
        self.assertEqual(session.client_id, "managed-client-id")
        self.assertEqual(session.client_secret, "managed-secret")

    @patch("console.api_views.httpx.post")
    def test_callback_stores_credentials(self, mock_httpx_post):
        session = AgentEmailOAuthSession.objects.create(
            account=self.account,
            initiated_by=self.user,
            user=self.user,
            state="state-123",
            token_endpoint="https://oauth.example.com/token",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        session.code_verifier = "verifier-xyz"
        session.client_secret = "secret"
        session.client_id = "client-id"
        session.save()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "access123",
            "refresh_token": "refresh123",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "read",
        }
        mock_httpx_post.return_value = mock_response

        url = reverse("console-email-oauth-callback")
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "session_id": str(session.id),
                    "authorization_code": "code-abc",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload["connected"])
        credential = AgentEmailOAuthCredential.objects.get(account=self.account)
        self.assertEqual(credential.access_token, "access123")
        self.assertEqual(credential.refresh_token, "refresh123")
        self.assertEqual(credential.client_id, "client-id")
        self.assertEqual(credential.client_secret, "secret")
        self.assertFalse(
            AgentEmailOAuthSession.objects.filter(id=session.id).exists(),
            "OAuth session should be removed after callback completion",
        )

    def test_status_without_credentials(self):
        url = reverse("console-email-oauth-status", args=[self.account.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["connected"])

    def test_revoke_deletes_credentials(self):
        credential = AgentEmailOAuthCredential.objects.create(
            account=self.account,
            user=self.user,
        )
        credential.access_token = "value"
        credential.save()

        url = reverse("console-email-oauth-revoke", args=[self.account.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["revoked"])
        self.assertFalse(AgentEmailOAuthCredential.objects.filter(id=credential.id).exists())

    def test_callback_page_includes_completion_script(self):
        url = reverse("console-email-oauth-callback-view")
        response = self.client.get(url, {"code": "abc", "state": "xyz"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "js/agent_email_oauth_callback.js")

    def test_settings_page_mounts_react_email_app(self):
        url = reverse("agent_email_settings", args=[self.agent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-app="agent-email-settings"')
        self.assertContains(response, reverse("console_agent_email_settings", args=[self.agent.pk]))

    def test_settings_page_creates_account_with_imap_idle_enabled_by_default(self):
        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            browser_agent = BrowserUseAgent.objects.create(user=self.user, name="BA-page-defaults")
        second_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Page Defaults Agent",
            charter="c",
            browser_use_agent=browser_agent,
        )
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=second_agent,
            channel=CommsChannel.EMAIL,
            address="page-defaults@example.com",
            is_primary=True,
        )

        url = reverse("agent_email_settings", args=[second_agent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        account = AgentEmailAccount.objects.get(endpoint=endpoint)
        self.assertTrue(account.imap_idle_enabled)

    def test_email_settings_api_get(self):
        url = reverse("console_agent_email_settings", args=[self.agent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agent"]["id"], str(self.agent.pk))
        self.assertEqual(payload["endpoint"]["address"], self.endpoint.address)
        self.assertTrue(payload["account"]["exists"])
        self.assertFalse(payload["account"]["hasSmtpPassword"])
        self.assertFalse(payload["account"]["hasImapPassword"])
        self.assertTrue(payload["account"]["imapIdleEnabled"])

    def test_email_settings_api_get_defaults_imap_idle_enabled_for_unconfigured_agent(self):
        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            browser_agent = BrowserUseAgent.objects.create(user=self.user, name="BA-no-email")
        unconfigured_agent = PersistentAgent.objects.create(
            user=self.user,
            name="No Email Agent",
            charter="c",
            browser_use_agent=browser_agent,
        )
        url = reverse("console_agent_email_settings", args=[unconfigured_agent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertFalse(payload["account"]["exists"])
        self.assertTrue(payload["account"]["imapIdleEnabled"])

    def test_email_settings_api_get_preserves_imap_idle_value_for_configured_account(self):
        self.account.imap_host = "imap.example.com"
        self.account.is_inbound_enabled = True
        self.account.imap_idle_enabled = False
        self.account.save()

        url = reverse("console_agent_email_settings", args=[self.agent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertFalse(payload["account"]["imapIdleEnabled"])

    def test_email_settings_ensure_account_creates_endpoint(self):
        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            browser_agent = BrowserUseAgent.objects.create(user=self.user, name="BA-2")
        second_agent = PersistentAgent.objects.create(
            user=self.user,
            name="OAuth Agent Two",
            charter="c",
            browser_use_agent=browser_agent,
        )
        ensure_url = reverse("console_agent_email_settings_ensure_account", args=[second_agent.pk])
        response = self.client.post(
            ensure_url,
            data=json.dumps({"endpointAddress": "second-agent@example.com"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload["settings"]["endpoint"]["exists"])
        self.assertTrue(payload["settings"]["account"]["exists"])
        self.assertTrue(payload["settings"]["account"]["imapIdleEnabled"])
        created_endpoint = PersistentAgentCommsEndpoint.objects.get(
            owner_agent=second_agent,
            channel=CommsChannel.EMAIL,
        )
        self.assertEqual(created_endpoint.address, "second-agent@example.com")

    def test_email_settings_ensure_account_rejects_invalid_endpoint(self):
        ensure_url = reverse("console_agent_email_settings_ensure_account", args=[self.agent.pk])
        response = self.client.post(
            ensure_url,
            data=json.dumps({"endpointAddress": "not-an-email"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        payload = response.json()
        self.assertIn("errors", payload)
        self.assertIn("endpoint_address", payload["errors"])

    @patch("console.api_views._validate_agent_smtp_connection", return_value=(True, ""))
    def test_email_settings_test_endpoint_runs_smtp(self, _mock_validate_smtp):
        url = reverse("console_agent_email_settings_test", args=[self.agent.pk])
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "endpointAddress": self.endpoint.address,
                    "connectionMode": "custom",
                    "isOutboundEnabled": True,
                    "isInboundEnabled": False,
                    "testOutbound": True,
                    "testInbound": False,
                    "smtpHost": "smtp.gmail.com",
                    "smtpPort": 587,
                    "smtpSecurity": "starttls",
                    "smtpAuth": "login",
                    "smtpUsername": self.endpoint.address,
                    "smtpPassword": "app-password",
                    "imapHost": "",
                    "imapPort": None,
                    "imapSecurity": "ssl",
                    "imapAuth": "login",
                    "imapUsername": "",
                    "imapPassword": "",
                    "imapFolder": "INBOX",
                    "imapIdleEnabled": False,
                    "pollIntervalSec": 120,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["results"]["smtp"]["ok"])

    @patch("console.api_views._validate_agent_smtp_connection", return_value=(False, "mock-smtp-error"))
    def test_email_settings_test_endpoint_does_not_persist_draft_settings(self, _mock_validate_smtp):
        self.account.smtp_host = "saved.smtp.example.com"
        self.account.smtp_port = 587
        self.account.smtp_security = "starttls"
        self.account.smtp_auth = "login"
        self.account.smtp_username = "saved-user@example.com"
        self.account.is_outbound_enabled = True
        self.account.imap_host = "saved.imap.example.com"
        self.account.imap_port = 993
        self.account.imap_security = "ssl"
        self.account.imap_auth = "login"
        self.account.imap_username = "saved-user@example.com"
        self.account.is_inbound_enabled = True
        self.account.save()

        url = reverse("console_agent_email_settings_test", args=[self.agent.pk])
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "endpointAddress": self.endpoint.address,
                    "connectionMode": "custom",
                    "isOutboundEnabled": False,
                    "isInboundEnabled": False,
                    "testOutbound": True,
                    "testInbound": False,
                    "smtpHost": "new.smtp.example.com",
                    "smtpPort": 465,
                    "smtpSecurity": "ssl",
                    "smtpAuth": "none",
                    "smtpUsername": "new-user@example.com",
                    "smtpPassword": "new-password",
                    "imapHost": "new.imap.example.com",
                    "imapPort": 143,
                    "imapSecurity": "starttls",
                    "imapAuth": "none",
                    "imapUsername": "new-user@example.com",
                    "imapPassword": "new-password",
                    "imapFolder": "NEWFOLDER",
                    "imapIdleEnabled": False,
                    "pollIntervalSec": 180,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertFalse(payload["ok"])

        self.account.refresh_from_db()
        self.assertEqual(self.account.smtp_host, "saved.smtp.example.com")
        self.assertEqual(self.account.smtp_port, 587)
        self.assertEqual(self.account.smtp_security, "starttls")
        self.assertEqual(self.account.smtp_auth, "login")
        self.assertEqual(self.account.smtp_username, "saved-user@example.com")
        self.assertEqual(self.account.imap_host, "saved.imap.example.com")
        self.assertEqual(self.account.imap_port, 993)
        self.assertEqual(self.account.imap_security, "ssl")
        self.assertEqual(self.account.imap_auth, "login")
        self.assertEqual(self.account.imap_username, "saved-user@example.com")
        self.assertTrue(self.account.is_outbound_enabled)
        self.assertTrue(self.account.is_inbound_enabled)
        self.assertIn("SMTP test failed: mock-smtp-error", self.account.connection_error)

    def test_normalize_email_error_text_flattens_tuple_and_bytes(self):
        normalized = _normalize_email_error_text(
            "(535, b'5.7.8 Username and Password not accepted. Learn more at https://support.google.com/mail/?p=BadCredentials x - gsmtp')"
        )
        self.assertNotIn("b'", normalized)
        self.assertIn("Username and Password not accepted", normalized)

    def test_format_email_connection_error_humanizes_missing_credentials(self):
        message = _format_email_connection_error("b'Empty username or password. af79cd13be357-8cb2e53b218mb5783094385a'")
        self.assertEqual(message, "Username or password is missing. Enter both values and try again.")
