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
        self.remote_server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="remote-server",
            display_name="Remote Server",
            command="npx",
            command_args=["mcp-remote", "https://remote.example.com/sse"],
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
        mock_get_manager.return_value.refresh_server.assert_called_once_with(str(self.server.id))

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
        mock_get_manager.return_value.refresh_server.assert_called_once_with(str(self.server.id))

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

    def test_remote_auth_start_rejects_non_remote_server(self):
        response = self.client.post(
            reverse("console-mcp-remote-auth-start"),
            data=json.dumps({"server_config_id": str(self.server.id)}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not configured with mcp-remote", response.content.decode())

    @patch("console.api_views.SandboxComputeService")
    def test_remote_auth_start_calls_sandbox_backend(self, mock_service_cls):
        mock_service = mock_service_cls.return_value
        mock_service.mcp_remote_auth_start.return_value = {
            "status": "pending_auth",
            "session_id": "session-123",
            "authorization_url": "https://idp.example.com/auth",
            "config_id": str(self.remote_server.id),
        }

        response = self.client.post(
            reverse("console-mcp-remote-auth-start"),
            data=json.dumps({"server_config_id": str(self.remote_server.id)}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertEqual(payload["status"], "pending_auth")
        self.assertTrue(payload["session_id"])
        self.assertEqual(payload["server_config_id"], str(self.remote_server.id))
        mock_service.mcp_remote_auth_start.assert_called_once()
        _call_args, call_kwargs = mock_service.mcp_remote_auth_start.call_args
        self.assertIn("/console/mcp/oauth/callback/", call_kwargs["redirect_url"])
        self.assertNotIn("remote_auth=", call_kwargs["redirect_url"])
        self.assertNotIn("remote_auth_session_id=", call_kwargs["redirect_url"])

    @patch("console.api_views.SandboxComputeService")
    def test_remote_auth_start_preserves_loopback_host(self, mock_service_cls):
        mock_service = mock_service_cls.return_value
        mock_service.mcp_remote_auth_start.return_value = {
            "status": "pending_auth",
            "session_id": "session-123",
            "authorization_url": "https://idp.example.com/auth",
            "config_id": str(self.remote_server.id),
        }

        response = self.client.post(
            reverse("console-mcp-remote-auth-start"),
            data=json.dumps({"server_config_id": str(self.remote_server.id)}),
            content_type="application/json",
            HTTP_HOST="127.0.0.1:8000",
        )

        self.assertEqual(response.status_code, 201, response.content)
        _call_args, call_kwargs = mock_service.mcp_remote_auth_start.call_args
        self.assertTrue(call_kwargs["redirect_url"].startswith("http://127.0.0.1:8000/console/mcp/oauth/callback/"))

    @patch("console.api_views.SandboxComputeService")
    def test_remote_auth_status_checks_server_permissions(self, mock_service_cls):
        mock_service = mock_service_cls.return_value
        mock_service.mcp_remote_auth_status.return_value = {
            "status": "pending_auth",
            "session_id": "session-xyz",
            "config_id": str(self.remote_server.id),
        }

        response = self.client.get(
            reverse("console-mcp-remote-auth-status", args=["session-xyz"]),
        )

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["session_id"], "session-xyz")
        self.assertEqual(payload["server_config_id"], str(self.remote_server.id))
        mock_service.mcp_remote_auth_status.assert_called_once_with("session-xyz")

    @patch("console.api_views.store_remote_auth_state")
    @patch("console.api_views.SandboxComputeService")
    def test_remote_auth_status_persists_remote_state(self, mock_service_cls, mock_store_state):
        mock_service = mock_service_cls.return_value
        mock_service.mcp_remote_auth_status.return_value = {
            "status": "authorized",
            "session_id": "session-xyz",
            "config_id": str(self.remote_server.id),
            "remote_auth_state": {"version": 1, "files": [{"path": "mcp-remote-0.1.40/abc_tokens.json"}]},
        }

        response = self.client.get(
            reverse("console-mcp-remote-auth-status", args=["session-xyz"]),
        )

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["status"], "authorized")
        self.assertNotIn("remote_auth_state", payload)
        mock_store_state.assert_called_once()

    @patch("console.api_views.SandboxComputeService")
    def test_remote_auth_authorize_submits_code(self, mock_service_cls):
        mock_service = mock_service_cls.return_value
        mock_service.mcp_remote_auth_status.return_value = {
            "status": "pending_auth",
            "session_id": "session-abc",
            "config_id": str(self.remote_server.id),
        }
        mock_service.mcp_remote_auth_authorize.return_value = {
            "status": "code_submitted",
            "session_id": "session-abc",
        }

        response = self.client.post(
            reverse("console-mcp-remote-auth-authorize"),
            data=json.dumps(
                {
                    "session_id": "session-abc",
                    "authorization_code": "code-123",
                    "state": "state-123",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["status"], "code_submitted")
        mock_service.mcp_remote_auth_status.assert_called_once_with("session-abc")
        mock_service.mcp_remote_auth_authorize.assert_called_once_with(
            session_id="session-abc",
            authorization_code="code-123",
            state="state-123",
            error="",
        )

    @patch("console.api_views.store_remote_auth_state")
    @patch("console.api_views.SandboxComputeService")
    def test_remote_auth_authorize_persists_remote_state(self, mock_service_cls, mock_store_state):
        mock_service = mock_service_cls.return_value
        mock_service.mcp_remote_auth_status.return_value = {
            "status": "pending_auth",
            "session_id": "session-abc",
            "config_id": str(self.remote_server.id),
        }
        mock_service.mcp_remote_auth_authorize.return_value = {
            "status": "authorized",
            "session_id": "session-abc",
            "remote_auth_state": {"version": 1, "files": [{"path": "mcp-remote-0.1.40/abc_tokens.json"}]},
        }

        response = self.client.post(
            reverse("console-mcp-remote-auth-authorize"),
            data=json.dumps(
                {
                    "session_id": "session-abc",
                    "authorization_code": "code-123",
                    "state": "state-123",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["status"], "authorized")
        self.assertNotIn("remote_auth_state", payload)
        mock_store_state.assert_called_once()
