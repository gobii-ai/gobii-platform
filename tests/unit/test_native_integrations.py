import json
import os
from datetime import timedelta
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone
from waffle.models import Flag

from api.agent.core.prompt_context import _get_secrets_block
from api.agent.tools.http_request import execute_http_request
from api.models import BrowserUseAgent, GlobalSecret, Organization, OrganizationMembership, PersistentAgent, PersistentAgentSecret
from api.services.native_integrations import GOOGLE_SHEETS_PROVIDER, get_native_integration_provider


User = get_user_model()


def _ensure_encryption_key():
    if not os.environ.get("GOBII_ENCRYPTION_KEY"):
        os.environ["GOBII_ENCRYPTION_KEY"] = "test-key-for-native-integrations"


def _mock_response(content: bytes = b"{}", content_type: str = "application/json", status_code: int = 200):
    response = MagicMock()
    response.status_code = status_code
    response.headers = {"Content-Type": content_type, "Content-Length": str(len(content))}

    def iter_content(chunk_size=1024):
        stream = BytesIO(content)
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            yield chunk

    response.iter_content = iter_content
    response.close = MagicMock()
    return response


@tag("batch_native_integrations")
@override_settings(
    GOOGLE_CLIENT_ID="google-client-id",
    GOOGLE_CLIENT_SECRET="google-client-secret",
    SEGMENT_WRITE_KEY="",
    SEGMENT_WEB_WRITE_KEY="",
)
class NativeIntegrationTests(TestCase):
    def setUp(self):
        _ensure_encryption_key()
        Flag.objects.update_or_create(name="organizations", defaults={"everyone": True})
        self.user = User.objects.create_user(
            username="native-user",
            email="native@example.com",
            password="password123",
        )
        self.org = Organization.objects.create(
            name="Native Org",
            slug="native-org",
            created_by=self.user,
        )
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Native Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Native Agent",
            charter="native integrations",
            browser_use_agent=self.browser_agent,
        )
        self.client.force_login(self.user)

    def _set_org_context(self):
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(self.org.id)
        session["context_name"] = self.org.name
        session.save()

    def _create_integration_secret(self, *, owner_user=None, owner_org=None, credentials=None):
        payload = credentials or {
            "provider_key": GOOGLE_SHEETS_PROVIDER.key,
            "auth_type": "oauth2",
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "token_type": "Bearer",
            "scope": GOOGLE_SHEETS_PROVIDER.scope_string,
            "expires_at": (timezone.now() + timedelta(hours=1)).isoformat(),
        }
        secret = GlobalSecret(
            user=owner_user,
            organization=owner_org,
            name=GOOGLE_SHEETS_PROVIDER.display_name,
            description=GOOGLE_SHEETS_PROVIDER.description,
            secret_type=GlobalSecret.SecretType.INTEGRATION,
            domain_pattern=GlobalSecret.INTEGRATION_DOMAIN_SENTINEL,
            key=GOOGLE_SHEETS_PROVIDER.secret_key,
        )
        secret.set_value(json.dumps(payload))
        secret.save()
        return secret

    def test_provider_registry_serializes_google_sheets(self):
        provider = get_native_integration_provider("google_sheets")

        self.assertEqual(provider.display_name, "Google Sheets")
        self.assertEqual(provider.auth_type, "oauth2")
        self.assertEqual(provider.api_hosts, ("sheets.googleapis.com",))
        self.assertEqual(provider.scopes, ("https://www.googleapis.com/auth/spreadsheets",))

    def test_list_reports_connected_state_for_user_context(self):
        self._create_integration_secret(owner_user=self.user)

        response = self.client.get(reverse("console-native-integration-list"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["owner_scope"], "user")
        provider = payload["providers"][0]
        self.assertEqual(provider["provider_key"], "google_sheets")
        self.assertTrue(provider["connected"])
        self.assertEqual(provider["connect_url"], reverse("console-native-integration-connect", args=["google_sheets"]))

    def test_list_uses_organization_context(self):
        self._set_org_context()
        self._create_integration_secret(owner_org=self.org)

        response = self.client.get(reverse("console-native-integration-list"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["owner_scope"], "organization")
        self.assertEqual(payload["owner_label"], self.org.name)
        self.assertTrue(payload["providers"][0]["connected"])

    def test_connect_returns_google_authorization_url(self):
        response = self.client.post(reverse("console-native-integration-connect", args=["google_sheets"]))

        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertEqual(payload["provider_key"], "google_sheets")
        self.assertIn("https://accounts.google.com/o/oauth2/v2/auth", payload["authorization_url"])
        self.assertIn("scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fspreadsheets", payload["authorization_url"])
        self.assertIn("state=", payload["authorization_url"])

    @patch("console.native_integrations_api.httpx.post")
    def test_callback_stores_hidden_integration_secret(self, mock_post):
        start = self.client.post(reverse("console-native-integration-connect", args=["google_sheets"]))
        state = start.json()["state"]
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": GOOGLE_SHEETS_PROVIDER.scope_string,
        }
        mock_post.return_value = token_response

        response = self.client.post(
            reverse("console-native-integration-callback", args=["google_sheets"]),
            data=json.dumps({"authorization_code": "auth-code", "state": state}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        secret = GlobalSecret.objects.get(user=self.user, secret_type=GlobalSecret.SecretType.INTEGRATION)
        self.assertEqual(secret.key, "native_google_sheets")
        self.assertEqual(secret.domain_pattern, GlobalSecret.INTEGRATION_DOMAIN_SENTINEL)
        stored = json.loads(secret.get_value())
        self.assertEqual(stored["access_token"], "new-access-token")
        self.assertEqual(stored["refresh_token"], "new-refresh-token")

    def test_revoke_deletes_only_provider_integration_secret(self):
        self._create_integration_secret(owner_user=self.user)
        credential = GlobalSecret(
            user=self.user,
            name="Visible Credential",
            secret_type=GlobalSecret.SecretType.CREDENTIAL,
            domain_pattern="https://api.example.com",
            key="visible_credential",
        )
        credential.set_value("visible-value")
        credential.save()

        response = self.client.post(reverse("console-native-integration-revoke", args=["google_sheets"]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["revoked"])
        self.assertFalse(GlobalSecret.objects.filter(secret_type=GlobalSecret.SecretType.INTEGRATION).exists())
        self.assertTrue(GlobalSecret.objects.filter(id=credential.id).exists())

    def test_secret_apis_exclude_integration_secrets(self):
        self._create_integration_secret(owner_user=self.user)
        agent_integration = PersistentAgentSecret(
            agent=self.agent,
            name="Agent Integration",
            secret_type=PersistentAgentSecret.SecretType.INTEGRATION,
            domain_pattern=PersistentAgentSecret.INTEGRATION_DOMAIN_SENTINEL,
            key="native_google_sheets",
        )
        agent_integration.set_value(json.dumps({"provider_key": "google_sheets"}))
        agent_integration.save()

        global_response = self.client.get(reverse("console-global-secret-list"))
        agent_response = self.client.get(reverse("console-agent-secret-list", args=[self.agent.id]))

        self.assertEqual(global_response.status_code, 200)
        self.assertEqual(agent_response.status_code, 200)
        self.assertEqual(global_response.json()["secrets"], [])
        self.assertEqual(agent_response.json()["agent_secrets"], [])
        self.assertEqual(agent_response.json()["global_secrets"], [])

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    def test_http_request_injects_google_sheets_auth(self, mock_request, mock_proxy):
        self._create_integration_secret(owner_user=self.user)
        mock_proxy.return_value = None
        mock_request.return_value = _mock_response(b'{"ok": true}')

        result = execute_http_request(
            self.agent,
            {
                "method": "GET",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/test",
            },
        )

        self.assertEqual(result["status"], "ok")
        request_kwargs = mock_request.call_args.kwargs
        self.assertEqual(request_kwargs["headers"]["Authorization"], "Bearer access-token")

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    @patch("api.services.native_integrations.httpx.post")
    def test_http_request_does_not_override_explicit_authorization(self, mock_refresh, mock_request, mock_proxy):
        self._create_integration_secret(
            owner_user=self.user,
            credentials={
                "provider_key": GOOGLE_SHEETS_PROVIDER.key,
                "auth_type": "oauth2",
                "access_token": "expired-token",
                "refresh_token": "refresh-token",
                "token_type": "Bearer",
                "scope": GOOGLE_SHEETS_PROVIDER.scope_string,
                "expires_at": (timezone.now() - timedelta(minutes=1)).isoformat(),
            },
        )
        mock_proxy.return_value = None
        mock_request.return_value = _mock_response(b'{"ok": true}')

        result = execute_http_request(
            self.agent,
            {
                "method": "GET",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/test",
                "headers": {"Authorization": "Bearer explicit-token"},
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(mock_request.call_args.kwargs["headers"]["Authorization"], "Bearer explicit-token")
        mock_refresh.assert_not_called()

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    @patch("api.services.native_integrations.httpx.post")
    def test_http_request_refreshes_expired_google_sheets_token(self, mock_refresh, mock_request, mock_proxy):
        secret = self._create_integration_secret(
            owner_user=self.user,
            credentials={
                "provider_key": GOOGLE_SHEETS_PROVIDER.key,
                "auth_type": "oauth2",
                "access_token": "expired-token",
                "refresh_token": "refresh-token",
                "token_type": "Bearer",
                "scope": GOOGLE_SHEETS_PROVIDER.scope_string,
                "expires_at": (timezone.now() - timedelta(minutes=1)).isoformat(),
            },
        )
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            "access_token": "refreshed-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": GOOGLE_SHEETS_PROVIDER.scope_string,
        }
        mock_refresh.return_value = token_response
        mock_proxy.return_value = None
        mock_request.return_value = _mock_response(b'{"ok": true}')

        result = execute_http_request(
            self.agent,
            {
                "method": "GET",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/test",
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(mock_request.call_args.kwargs["headers"]["Authorization"], "Bearer refreshed-token")
        secret.refresh_from_db()
        self.assertEqual(json.loads(secret.get_value())["access_token"], "refreshed-token")
        mock_refresh.assert_called_once()

    def test_prompt_mentions_native_integration_without_secret_key(self):
        self._create_integration_secret(owner_user=self.user)

        block = _get_secrets_block(self.agent)

        self.assertIn("Native integrations available through tools", block)
        self.assertIn("Google Sheets", block)
        self.assertNotIn("native_google_sheets", block)
