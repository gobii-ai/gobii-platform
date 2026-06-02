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

from api.agent.system_skills.defaults import APOLLO_NATIVE_SYSTEM_SKILL_KEY, GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.registry import get_system_skill_definition, shortlist_system_skills
from api.agent.system_skills.service import enable_system_skills
from api.agent.core.prompt_context import _get_secrets_block
from api.agent.tools.http_request import execute_http_request
from api.agent.tools.sqlite_skills import format_recent_skills_for_prompt
from api.models import (
    BrowserUseAgent,
    GlobalSecret,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentEnabledTool,
)
from api.services.native_integrations import (
    APOLLO_PROVIDER,
    GOOGLE_DRIVE_PROVIDER,
    apply_native_integration_auth,
    get_native_integration_provider,
)


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
    GOOGLE_DRIVE_CLIENT_ID="google-client-id",
    GOOGLE_DRIVE_CLIENT_SECRET="google-client-secret",
    GOOGLE_PICKER_API_KEY="picker-api-key",
    GOOGLE_PICKER_APP_ID="123456789",
    APOLLO_CLIENT_ID="apollo-client-id",
    APOLLO_CLIENT_SECRET="apollo-client-secret",
    GOBII_PROPRIETARY_MODE=False,
    PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False,
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

    def _credentials(self, *, provider=GOOGLE_DRIVE_PROVIDER, access_token=None, refresh_token=None, expires_at=None):
        default_prefix = "apollo" if provider.key == APOLLO_PROVIDER.key else ""
        token_prefix = f"{default_prefix}-" if default_prefix else ""
        return {
            "provider_key": provider.key,
            "auth_type": "oauth2",
            "access_token": access_token or f"{token_prefix}access-token",
            "refresh_token": refresh_token or f"{token_prefix}refresh-token",
            "token_type": "Bearer",
            "scope": provider.scope_string,
            "expires_at": expires_at or (timezone.now() + timedelta(hours=1)).isoformat(),
        }

    def _expired_credentials(self, *, provider=GOOGLE_DRIVE_PROVIDER, access_token=None):
        default_prefix = "apollo" if provider.key == APOLLO_PROVIDER.key else ""
        token_prefix = f"{default_prefix}-" if default_prefix else ""
        return self._credentials(
            provider=provider,
            access_token=access_token or f"expired-{token_prefix}token",
            expires_at=(timezone.now() - timedelta(minutes=1)).isoformat(),
        )

    def _create_integration_secret(self, *, owner_user=None, owner_org=None, credentials=None, provider=GOOGLE_DRIVE_PROVIDER):
        payload = credentials or self._credentials(provider=provider)
        secret = GlobalSecret(
            user=owner_user,
            organization=owner_org,
            name=provider.display_name,
            description=provider.description,
            secret_type=GlobalSecret.SecretType.INTEGRATION,
            domain_pattern=GlobalSecret.INTEGRATION_DOMAIN_SENTINEL,
            key=provider.secret_key,
        )
        secret.set_value(json.dumps(payload))
        secret.save()
        return secret

    def _token_response(self, *, access_token, refresh_token=None, provider=GOOGLE_DRIVE_PROVIDER):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": provider.scope_string,
        }
        if refresh_token is not None:
            response.json.return_value["refresh_token"] = refresh_token
        return response

    def _start_oauth(self, *, headers=None, provider_key="google_drive"):
        kwargs = {"headers": headers} if headers else {}
        response = self.client.post(reverse("console-native-integration-connect", args=[provider_key]), **kwargs)
        return response.json()["state"]

    def _post_oauth_callback(self, state, *, headers=None, provider_key="google_drive"):
        kwargs = {"headers": headers} if headers else {}
        return self.client.post(
            reverse("console-native-integration-callback", args=[provider_key]),
            data=json.dumps({"authorization_code": "auth-code", "state": state}),
            content_type="application/json",
            **kwargs,
        )

    def test_provider_registry_serializes_native_providers(self):
        cases = (
            (
                "google_drive",
                {
                    "display_name": "Google Drive",
                    "api_hosts": ("sheets.googleapis.com", "docs.googleapis.com", "drive.googleapis.com"),
                    "api_url_prefixes": ("https://www.googleapis.com/drive/",),
                    "scopes": ("https://www.googleapis.com/auth/drive.file",),
                },
            ),
            (
                "apollo",
                {
                    "display_name": "Apollo",
                    "authorization_endpoint": "https://app.apollo.io/#/oauth/authorize",
                    "token_endpoint": "https://app.apollo.io/api/v1/oauth/token",
                    "api_url_prefixes": ("https://api.apollo.io/", "https://app.apollo.io/api/v1/users/api_profile"),
                    "scopes": ("read_user_profile", "contacts_search", "person_read"),
                },
            ),
        )
        for provider_key, expected in cases:
            with self.subTest(provider_key=provider_key):
                provider = get_native_integration_provider(provider_key)
                self.assertEqual(provider.display_name, expected["display_name"])
                self.assertEqual(provider.auth_type, "oauth2")
                for attr, value in expected.items():
                    if attr == "display_name":
                        continue
                    self.assertEqual(getattr(provider, attr), value)

    def test_provider_registry_accepts_google_sheets_alias(self):
        provider = get_native_integration_provider("google_sheets")

        self.assertEqual(provider.key, "google_drive")

    def test_list_reports_connected_state_for_user_context(self):
        self._create_integration_secret(owner_user=self.user)

        response = self.client.get(reverse("console-native-integration-list"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["owner_scope"], "user")
        provider = payload["providers"][0]
        self.assertEqual(provider["provider_key"], "google_drive")
        self.assertTrue(provider["connected"])
        self.assertEqual(provider["connect_url"], reverse("console-native-integration-connect", args=["google_drive"]))
        self.assertEqual(provider["files_url"], reverse("console-native-integration-files", args=["google_drive"]))
        self.assertEqual(
            provider["picker_token_url"],
            reverse("console-native-integration-picker-token", args=["google_drive"]),
        )

    def test_list_reports_connected_state_for_apollo(self):
        self._create_integration_secret(
            owner_user=self.user,
            provider=APOLLO_PROVIDER,
        )

        response = self.client.get(reverse("console-native-integration-list"))

        self.assertEqual(response.status_code, 200)
        providers = {provider["provider_key"]: provider for provider in response.json()["providers"]}
        provider = providers["apollo"]
        self.assertTrue(provider["connected"])
        self.assertEqual(provider["display_name"], "Apollo")
        self.assertEqual(provider["connect_url"], reverse("console-native-integration-connect", args=["apollo"]))
        self.assertEqual(provider["revoke_url"], reverse("console-native-integration-revoke", args=["apollo"]))

    def test_list_treats_legacy_google_sheets_secret_as_connected(self):
        self._create_integration_secret(owner_user=self.user)
        GlobalSecret.objects.update(key="native_google_sheets")

        response = self.client.get(reverse("console-native-integration-list"))

        self.assertEqual(response.status_code, 200)
        provider = response.json()["providers"][0]
        self.assertEqual(provider["provider_key"], "google_drive")
        self.assertTrue(provider["connected"])

    def test_list_uses_organization_context(self):
        self._set_org_context()
        self._create_integration_secret(owner_org=self.org)

        response = self.client.get(reverse("console-native-integration-list"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["owner_scope"], "organization")
        self.assertEqual(payload["owner_label"], self.org.name)
        self.assertTrue(payload["providers"][0]["connected"])

    def test_organization_member_permission_denial_returns_json(self):
        OrganizationMembership.objects.filter(org=self.org, user=self.user).update(
            role=OrganizationMembership.OrgRole.MEMBER
        )
        self._set_org_context()

        response = self.client.get(reverse("console-native-integration-list"))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(
            response.json(),
            {"error": "You do not have permission to manage organization integrations."},
        )

    def test_connect_returns_authorization_url_for_native_oauth_providers(self):
        cases = (
            (
                "google_drive",
                (
                    "https://accounts.google.com/o/oauth2/v2/auth",
                    "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive.file",
                ),
            ),
            (
                "apollo",
                (
                    "https://app.apollo.io/#/oauth/authorize",
                    "client_id=apollo-client-id",
                    "scope=read_user_profile+contacts_search+person_read",
                ),
            ),
        )
        for provider_key, expected_terms in cases:
            with self.subTest(provider_key=provider_key):
                response = self.client.post(reverse("console-native-integration-connect", args=[provider_key]))
                self.assertEqual(response.status_code, 201, response.content)
                payload = response.json()
                self.assertEqual(payload["provider_key"], provider_key)
                self.assertIn("redirect_uri=http%3A%2F%2Ftestserver%2Fintegrations%2Foauth%2Fcallback%2F", payload["authorization_url"])
                self.assertIn("state=", payload["authorization_url"])
                for term in expected_terms:
                    self.assertIn(term, payload["authorization_url"])

    @override_settings(GOOGLE_DRIVE_CLIENT_ID="", GOOGLE_DRIVE_CLIENT_SECRET="")
    def test_connect_returns_configuration_error_without_google_oauth_credentials(self):
        response = self.client.post(reverse("console-native-integration-connect", args=["google_drive"]))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Google Drive OAuth is not configured."})

    def test_picker_token_returns_access_token_for_connected_provider(self):
        self._create_integration_secret(owner_user=self.user)

        response = self.client.get(reverse("console-native-integration-picker-token", args=["google_drive"]))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Cache-Control"], "no-store")
        payload = response.json()
        self.assertEqual(payload["access_token"], "access-token")
        self.assertEqual(payload["developer_key"], "picker-api-key")
        self.assertEqual(payload["app_id"], "123456789")
        self.assertEqual(payload["scope"], GOOGLE_DRIVE_PROVIDER.scope_string)

    @patch("api.services.native_integrations.httpx.get")
    def test_files_returns_accessible_google_drive_files(self, mock_get):
        self._create_integration_secret(owner_user=self.user)
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "files": [
                {
                    "id": "sheet-123",
                    "name": "Pipeline",
                    "mimeType": "application/vnd.google-apps.spreadsheet",
                    "webViewLink": "https://docs.google.com/spreadsheets/d/sheet-123/edit",
                },
                {
                    "id": "doc-123",
                    "name": "Brief",
                    "mimeType": "application/vnd.google-apps.document",
                    "webViewLink": "https://docs.google.com/document/d/doc-123/edit",
                },
                {
                    "id": "pdf-123",
                    "name": "Ignored PDF",
                    "mimeType": "application/pdf",
                    "webViewLink": "https://drive.google.com/file/d/pdf-123/view",
                },
            ]
        }
        mock_get.return_value = response

        files_response = self.client.get(reverse("console-native-integration-files", args=["google_drive"]))

        self.assertEqual(files_response.status_code, 200, files_response.content)
        self.assertEqual(files_response["Cache-Control"], "no-store")
        payload = files_response.json()
        self.assertEqual(payload["provider_key"], "google_drive")
        self.assertEqual(
            payload["files"],
            [
                {
                    "external_id": "sheet-123",
                    "name": "Pipeline",
                    "mime_type": "application/vnd.google-apps.spreadsheet",
                    "web_url": "https://docs.google.com/spreadsheets/d/sheet-123/edit",
                },
                {
                    "external_id": "doc-123",
                    "name": "Brief",
                    "mime_type": "application/vnd.google-apps.document",
                    "web_url": "https://docs.google.com/document/d/doc-123/edit",
                },
            ],
        )
        request_kwargs = mock_get.call_args.kwargs
        self.assertEqual(request_kwargs["headers"]["Authorization"], "Bearer access-token")
        self.assertIn("mimeType", request_kwargs["params"]["q"])

    def test_files_requires_connected_provider(self):
        response = self.client.get(reverse("console-native-integration-files", args=["google_drive"]))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "Google Drive is not connected."})

    def test_apollo_does_not_support_picker_or_files(self):
        picker_response = self.client.get(reverse("console-native-integration-picker-token", args=["apollo"]))
        files_response = self.client.get(reverse("console-native-integration-files", args=["apollo"]))

        self.assertEqual(picker_response.status_code, 400)
        self.assertEqual(picker_response.json(), {"error": "Apollo does not support file picking."})
        self.assertEqual(files_response.status_code, 400)
        self.assertEqual(files_response.json(), {"error": "Apollo does not expose files."})

    @override_settings(GOOGLE_PICKER_API_KEY="", GOOGLE_PICKER_APP_ID="")
    def test_picker_token_requires_picker_configuration(self):
        self._create_integration_secret(owner_user=self.user)

        response = self.client.get(reverse("console-native-integration-picker-token", args=["google_drive"]))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Google Picker is not configured."})

    @override_settings(GOOGLE_DRIVE_CLIENT_ID="", GOOGLE_DRIVE_CLIENT_SECRET="")
    def test_picker_token_returns_configuration_error_when_refresh_needs_oauth_credentials(self):
        self._create_integration_secret(
            owner_user=self.user,
            credentials=self._expired_credentials(),
        )

        response = self.client.get(reverse("console-native-integration-picker-token", args=["google_drive"]))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Google Drive OAuth is not configured."})

    @patch("api.services.native_integrations.httpx.post")
    def test_callback_stores_hidden_integration_secret(self, mock_post):
        state = self._start_oauth()
        mock_post.return_value = self._token_response(
            access_token="new-access-token",
            refresh_token="new-refresh-token",
        )

        response = self._post_oauth_callback(state)

        self.assertEqual(response.status_code, 200, response.content)
        secret = GlobalSecret.objects.get(user=self.user, secret_type=GlobalSecret.SecretType.INTEGRATION)
        self.assertEqual(secret.key, "native_google_drive")
        self.assertEqual(secret.domain_pattern, GlobalSecret.INTEGRATION_DOMAIN_SENTINEL)
        stored = json.loads(secret.get_value())
        self.assertEqual(stored["access_token"], "new-access-token")
        self.assertEqual(stored["refresh_token"], "new-refresh-token")
        self.assertEqual(
            mock_post.call_args.kwargs["data"]["redirect_uri"],
            "http://testserver/integrations/oauth/callback/",
        )

    @patch("api.services.native_integrations.httpx.post")
    def test_callback_stores_hidden_apollo_integration_secret(self, mock_post):
        state = self._start_oauth(provider_key="apollo")
        mock_post.return_value = self._token_response(
            access_token="new-apollo-access-token",
            refresh_token="new-apollo-refresh-token",
            provider=APOLLO_PROVIDER,
        )

        response = self._post_oauth_callback(state, provider_key="apollo")

        self.assertEqual(response.status_code, 200, response.content)
        secret = GlobalSecret.objects.get(user=self.user, secret_type=GlobalSecret.SecretType.INTEGRATION)
        self.assertEqual(secret.key, "native_apollo")
        self.assertEqual(secret.domain_pattern, GlobalSecret.INTEGRATION_DOMAIN_SENTINEL)
        stored = json.loads(secret.get_value())
        self.assertEqual(stored["provider_key"], "apollo")
        self.assertEqual(stored["access_token"], "new-apollo-access-token")
        self.assertEqual(stored["refresh_token"], "new-apollo-refresh-token")
        self.assertEqual(mock_post.call_args.kwargs["data"]["client_id"], "apollo-client-id")
        self.assertEqual(mock_post.call_args.kwargs["data"]["client_secret"], "apollo-client-secret")

    @patch("api.services.native_integrations.httpx.post")
    def test_callback_accepts_matching_organization_context_override(self, mock_post):
        headers = {
            "X-Gobii-Context-Type": "organization",
            "X-Gobii-Context-Id": str(self.org.id),
        }
        state = self._start_oauth(headers=headers)
        mock_post.return_value = self._token_response(
            access_token="org-access-token",
            refresh_token="org-refresh-token",
        )

        response = self._post_oauth_callback(state, headers=headers)

        self.assertEqual(response.status_code, 200, response.content)
        secret = GlobalSecret.objects.get(organization=self.org, secret_type=GlobalSecret.SecretType.INTEGRATION)
        stored = json.loads(secret.get_value())
        self.assertEqual(stored["access_token"], "org-access-token")

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

        response = self.client.post(reverse("console-native-integration-revoke", args=["google_drive"]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["revoked"])
        self.assertFalse(GlobalSecret.objects.filter(secret_type=GlobalSecret.SecretType.INTEGRATION).exists())
        self.assertTrue(GlobalSecret.objects.filter(id=credential.id).exists())

    def test_secret_apis_exclude_integration_secrets(self):
        self._create_integration_secret(owner_user=self.user)

        global_response = self.client.get(reverse("console-global-secret-list"))
        agent_response = self.client.get(reverse("console-agent-secret-list", args=[self.agent.id]))

        self.assertEqual(global_response.status_code, 200)
        self.assertEqual(agent_response.status_code, 200)
        self.assertEqual(global_response.json()["secrets"], [])
        self.assertEqual(agent_response.json()["agent_secrets"], [])
        self.assertEqual(agent_response.json()["global_secrets"], [])

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    def test_http_request_injects_native_provider_auth(self, mock_request, mock_proxy):
        self._create_integration_secret(owner_user=self.user)
        self._create_integration_secret(owner_user=self.user, provider=APOLLO_PROVIDER)
        mock_proxy.return_value = None
        mock_request.return_value = _mock_response(b'{"ok": true}')

        cases = (
            ("https://sheets.googleapis.com/v4/spreadsheets/test", "Bearer access-token"),
            ("https://api.apollo.io/api/v1/users", "Bearer apollo-access-token"),
        )
        for url, expected_auth in cases:
            with self.subTest(url=url):
                result = execute_http_request(self.agent, {"method": "GET", "url": url})
                self.assertEqual(result["status"], "ok")
                self.assertEqual(mock_request.call_args.kwargs["headers"]["Authorization"], expected_auth)

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    def test_http_request_returns_native_integration_not_connected_before_provider_call(self, mock_request, mock_proxy):
        mock_proxy.return_value = None

        cases = (
            ("https://sheets.googleapis.com/v4/spreadsheets/test", ()),
            ("https://api.apollo.io/api/v1/users", ("connect Apollo",)),
        )
        for url, expected_terms in cases:
            with self.subTest(url=url):
                result = execute_http_request(
                    self.agent,
                    {"method": "GET", "url": url, "will_continue_work": True},
                )
                self.assertEqual(result["status"], "error")
                self.assertIn("native_integration_not_connected", result["message"])
                self.assertIn("/app/integrations", result["message"])
                for term in expected_terms:
                    self.assertIn(term, result["message"])
        mock_request.assert_not_called()

    def test_native_integration_auth_matches_provider_api_urls(self):
        self._create_integration_secret(owner_user=self.user)
        self._create_integration_secret(owner_user=self.user, provider=APOLLO_PROVIDER)

        cases = (
            ("https://docs.googleapis.com/v1/documents/test", "Bearer access-token"),
            ("https://www.googleapis.com/drive/v3/files", "Bearer access-token"),
            ("https://www.googleapis.com/oauth2/v3/userinfo", None),
            ("https://api.apollo.io/api/v1/mixed_people/api_search", "Bearer apollo-access-token"),
            ("https://app.apollo.io/api/v1/users/api_profile", "Bearer apollo-access-token"),
            ("https://app.apollo.io/api/v1/oauth/token", None),
            ("https://www.apollo.io/pricing", None),
        )
        for url, expected_auth in cases:
            with self.subTest(url=url):
                headers = apply_native_integration_auth(self.agent, url, {})
                if expected_auth:
                    self.assertEqual(headers["Authorization"], expected_auth)
                else:
                    self.assertNotIn("Authorization", headers)

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    @patch("api.services.native_integrations.httpx.post")
    def test_http_request_does_not_override_explicit_native_authorization(self, mock_refresh, mock_request, mock_proxy):
        self._create_integration_secret(
            owner_user=self.user,
            credentials=self._expired_credentials(),
        )
        self._create_integration_secret(
            owner_user=self.user,
            credentials=self._expired_credentials(provider=APOLLO_PROVIDER),
            provider=APOLLO_PROVIDER,
        )
        mock_proxy.return_value = None
        mock_request.return_value = _mock_response(b'{"ok": true}')

        for url in ("https://sheets.googleapis.com/v4/spreadsheets/test", "https://api.apollo.io/api/v1/users"):
            with self.subTest(url=url):
                result = execute_http_request(
                    self.agent,
                    {"method": "GET", "url": url, "headers": {"Authorization": "Bearer explicit-token"}},
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(mock_request.call_args.kwargs["headers"]["Authorization"], "Bearer explicit-token")
        mock_refresh.assert_not_called()

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    @patch("api.services.native_integrations.httpx.post")
    def test_http_request_refreshes_expired_native_tokens(self, mock_refresh, mock_request, mock_proxy):
        google_secret = self._create_integration_secret(
            owner_user=self.user,
            credentials=self._expired_credentials(),
        )
        apollo_secret = self._create_integration_secret(
            owner_user=self.user,
            credentials=self._expired_credentials(provider=APOLLO_PROVIDER),
            provider=APOLLO_PROVIDER,
        )
        mock_refresh.side_effect = (
            self._token_response(access_token="refreshed-token"),
            self._token_response(access_token="refreshed-apollo-token", provider=APOLLO_PROVIDER),
        )
        mock_proxy.return_value = None
        mock_request.return_value = _mock_response(b'{"ok": true}')

        cases = (
            ("https://sheets.googleapis.com/v4/spreadsheets/test", google_secret, "Bearer refreshed-token", "refreshed-token"),
            ("https://api.apollo.io/api/v1/users", apollo_secret, "Bearer refreshed-apollo-token", "refreshed-apollo-token"),
        )
        for url, secret, expected_auth, expected_token in cases:
            with self.subTest(url=url):
                result = execute_http_request(self.agent, {"method": "GET", "url": url})
                self.assertEqual(result["status"], "ok")
                self.assertEqual(mock_request.call_args.kwargs["headers"]["Authorization"], expected_auth)
                secret.refresh_from_db()
                self.assertEqual(json.loads(secret.get_value())["access_token"], expected_token)
        self.assertEqual(mock_refresh.call_count, 2)

    def test_prompt_mentions_native_integration_without_secret_key(self):
        self._create_integration_secret(owner_user=self.user)
        self._create_integration_secret(
            owner_user=self.user,
            provider=APOLLO_PROVIDER,
        )

        block = _get_secrets_block(self.agent)

        self.assertIn("Native integrations available through tools", block)
        self.assertIn("Google Drive", block)
        self.assertIn("Apollo", block)
        self.assertNotIn("native_google_drive", block)
        self.assertNotIn("native_google_sheets", block)
        self.assertNotIn("native_apollo", block)

    def test_native_system_skills_are_registered_and_enable_http_request(self):
        cases = (
            (GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY, "Google Sheets", ("read google sheets rows", "search my test spreadsheet")),
            (APOLLO_NATIVE_SYSTEM_SKILL_KEY, "Apollo", ("search Apollo prospects", "enrich contacts in Apollo")),
        )
        for skill_key, expected_name, queries in cases:
            with self.subTest(skill_key=skill_key):
                definition = get_system_skill_definition(skill_key)
                self.assertIsNotNone(definition)
                self.assertEqual(definition.name, expected_name)
                self.assertEqual(definition.tool_names, ("http_request",))
                for query in queries:
                    search_results = shortlist_system_skills(query, available_tool_names={"http_request"})
                    self.assertIn(skill_key, [result.skill_key for result in search_results])

                result = enable_system_skills(self.agent, [skill_key])
                self.assertEqual(result["invalid"], [])
                self.assertIn(skill_key, result["enabled"])
                self.assertTrue(
                    PersistentAgentEnabledTool.objects.filter(agent=self.agent, tool_full_name="http_request").exists()
                )

    @override_settings(PUBLIC_SITE_URL="https://app.example.test")
    def test_google_sheets_prompt_tells_agent_how_to_discover_accessible_spreadsheets(self):
        enable_system_skills(self.agent, [GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY])

        block = format_recent_skills_for_prompt(self.agent, limit=3)

        self.assertIn("System Skill: Google Sheets", block)
        self.assertIn("Tools: http_request", block)
        self.assertIn("If the user supplies a concrete spreadsheet ID, use it directly with the Sheets API", block)
        self.assertIn("List accessible spreadsheets", block)
        self.assertIn("Do not use web search or public `docs.google.com` results", block)
        self.assertIn("https://www.googleapis.com/drive/v3/files", block)
        self.assertIn("https://www.googleapis.com/drive/", block)
        self.assertIn("mimeType = 'application/vnd.google-apps.spreadsheet'", block)
        self.assertIn("name contains 'text'", block)
        self.assertIn("fields=files(id,name,mimeType,webViewLink)", block)
        self.assertIn("Never call partial", block)
        self.assertIn("?q=mimeType%3D", block)
        self.assertIn("?q=name%20%3D", block)
        self.assertIn("?q=name%20contains%20", block)
        self.assertIn("omit the name predicate", block)
        self.assertIn("drive.file", block)

    @override_settings(PUBLIC_SITE_URL="https://app.example.test")
    def test_apollo_prompt_tells_agent_how_to_use_native_rest_api(self):
        enable_system_skills(self.agent, [APOLLO_NATIVE_SYSTEM_SKILL_KEY])

        block = format_recent_skills_for_prompt(self.agent, limit=3)

        self.assertIn("System Skill: Apollo", block)
        self.assertIn("Tools: http_request", block)
        self.assertIn("https://api.apollo.io/api/v1", block)
        self.assertIn("Native Apollo OAuth is applied automatically", block)
        self.assertIn("page", block)
        self.assertIn("per_page", block)
        self.assertIn("credit-sensitive", block)
        self.assertIn("Never invent webhook URLs", block)
        self.assertIn("/app/integrations", block)
        self.assertIn("mixed_people/api_search", block)
        self.assertIn("do not use `/mixed_people/search`", block)
        self.assertIn("mixed_companies/search", block)
        self.assertIn("people/match", block)
