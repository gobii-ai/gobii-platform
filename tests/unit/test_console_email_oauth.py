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
