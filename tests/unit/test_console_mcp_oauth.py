import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from api.models import (
    MCPServerConfig,
    MCPServerOAuthCredential,
    MCPServerOAuthSession,
)


@tag("batch_console_mcp_oauth")
class MCPOAuthApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="oauth-user",
            email="oauth@example.com",
            password="password123",
        )
        self.other_user = User.objects.create_user(
            username="other",
            email="other@example.com",
            password="password123",
        )
        self.server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="test-oauth-server",
            display_name="Test OAuth",
            url="https://oauth.example.com",
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
        )
        self.client.force_login(self.user)

    def test_start_creates_session(self):
        url = reverse("console-mcp-oauth-start")
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "server_config_id": str(self.server.id),
                    "scope": "read write",
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
        session = MCPServerOAuthSession.objects.get(id=payload["session_id"])
        self.assertEqual(session.scope, "read write")
        self.assertEqual(session.code_verifier, "secret-verifier")
        self.assertEqual(session.client_id, "abc123")
        self.assertEqual(session.client_secret, "shhh")

    def test_start_requires_permission(self):
        self.client.force_login(self.other_user)
        url = reverse("console-mcp-oauth-start")
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "server_config_id": str(self.server.id),
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_start_generates_state_when_missing(self):
        url = reverse("console-mcp-oauth-start")
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "server_config_id": str(self.server.id),
                    "token_endpoint": "https://oauth.example.com/token",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertTrue(payload["state"])

    @patch("console.api_views.httpx.post")
    def test_start_auto_registers_client(self, mock_httpx_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "client_id": "dynamic-client",
            "client_secret": "dynamic-secret",
        }
        mock_httpx_post.return_value = mock_response

        url = reverse("console-mcp-oauth-start")
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "server_config_id": str(self.server.id),
                    "scope": "read",
                    "token_endpoint": "https://oauth.example.com/token",
                    "code_verifier": "secret-verifier",
                    "metadata": {
                        "registration_endpoint": "https://oauth.example.com/register",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        session = MCPServerOAuthSession.objects.get()
        self.assertEqual(session.client_id, "dynamic-client")
        self.assertEqual(session.client_secret, "dynamic-secret")

    @patch("console.api_views.httpx.get")
    def test_metadata_proxy(self, mock_httpx_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"issuer": "https://oauth.example.com"}
        mock_httpx_get.return_value = mock_response

        url = reverse("console-mcp-oauth-metadata")
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "server_config_id": str(self.server.id),
                    "resource": "/.well-known/oauth-authorization-server",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["issuer"], "https://oauth.example.com")
        mock_httpx_get.assert_called_once()

    @patch("console.api_views.get_mcp_manager")
    @patch("console.api_views.httpx.post")
    def test_callback_stores_credentials(self, mock_httpx_post, mock_get_manager):
        session = MCPServerOAuthSession.objects.create(
            server_config=self.server,
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

        url = reverse("console-mcp-oauth-callback")
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
        credential = MCPServerOAuthCredential.objects.get(server_config=self.server)
        self.assertEqual(credential.access_token, "access123")
        self.assertEqual(credential.refresh_token, "refresh123")
        self.assertEqual(credential.client_id, "client-id")
        self.assertEqual(credential.client_secret, "secret")
        self.assertFalse(
            MCPServerOAuthSession.objects.filter(id=session.id).exists(),
            "OAuth session should be removed after callback completion",
        )
        mock_get_manager.return_value.initialize.assert_called_once_with(force=True)

    def test_callback_page_includes_completion_script(self):
        url = reverse("console-mcp-oauth-callback-view")
        response = self.client.get(url, {"code": "abc", "state": "xyz"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "js/mcp_oauth_callback.js")

    def test_status_without_credentials(self):
        url = reverse("console-mcp-oauth-status", args=[self.server.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["connected"])

    @patch("console.api_views.get_mcp_manager")
    def test_revoke_deletes_credentials(self, mock_get_manager):
        credential = MCPServerOAuthCredential.objects.create(
            server_config=self.server,
            user=self.user,
        )
        credential.access_token = "value"
        credential.save()

        url = reverse("console-mcp-oauth-revoke", args=[self.server.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["revoked"])
        self.assertFalse(MCPServerOAuthCredential.objects.filter(id=credential.id).exists())
        mock_get_manager.return_value.initialize.assert_called_once_with(force=True)

    def test_session_verifier_update(self):
        session = MCPServerOAuthSession.objects.create(
            server_config=self.server,
            initiated_by=self.user,
            user=self.user,
            state="state-xyz",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        url = reverse("console-mcp-oauth-session-verifier", args=[session.id])
        response = self.client.post(
            url,
            data=json.dumps({"code_verifier": "updated-verifier"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        session.refresh_from_db()
        self.assertEqual(session.code_verifier, "updated-verifier")
