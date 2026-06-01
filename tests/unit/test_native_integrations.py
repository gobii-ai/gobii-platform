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

from api.agent.system_skills.defaults import GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.registry import get_system_skill_definition, shortlist_system_skills
from api.agent.system_skills.service import enable_system_skills
from api.agent.core.prompt_context import _get_secrets_block
from api.agent.tools.http_request import execute_http_request
from api.agent.tools.sqlite_skills import format_recent_skills_for_prompt
from api.models import (
    BrowserUseAgent,
    GlobalSecret,
    NativeIntegrationGrantedFile,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentSecret,
)
from api.services.native_integrations import (
    GOOGLE_DRIVE_PROVIDER,
    apply_native_integration_auth,
    get_native_integration_provider,
)


User = get_user_model()
GOOGLE_SHEETS_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
GOOGLE_DOCS_MIME_TYPE = "application/vnd.google-apps.document"


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

    def _create_integration_secret(self, *, owner_user=None, owner_org=None, credentials=None):
        payload = credentials or {
            "provider_key": GOOGLE_DRIVE_PROVIDER.key,
            "auth_type": "oauth2",
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "token_type": "Bearer",
            "scope": GOOGLE_DRIVE_PROVIDER.scope_string,
            "expires_at": (timezone.now() + timedelta(hours=1)).isoformat(),
        }
        secret = GlobalSecret(
            user=owner_user,
            organization=owner_org,
            name=GOOGLE_DRIVE_PROVIDER.display_name,
            description=GOOGLE_DRIVE_PROVIDER.description,
            secret_type=GlobalSecret.SecretType.INTEGRATION,
            domain_pattern=GlobalSecret.INTEGRATION_DOMAIN_SENTINEL,
            key=GOOGLE_DRIVE_PROVIDER.secret_key,
        )
        secret.set_value(json.dumps(payload))
        secret.save()
        return secret

    def test_provider_registry_serializes_google_drive(self):
        provider = get_native_integration_provider("google_drive")

        self.assertEqual(provider.display_name, "Google Drive")
        self.assertEqual(provider.auth_type, "oauth2")
        self.assertEqual(provider.api_hosts, ("sheets.googleapis.com", "docs.googleapis.com", "drive.googleapis.com"))
        self.assertEqual(provider.scopes, ("https://www.googleapis.com/auth/drive.file",))

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
        self.assertEqual(
            provider["picker_token_url"],
            reverse("console-native-integration-picker-token", args=["google_drive"]),
        )
        self.assertEqual(provider["files_url"], reverse("console-native-integration-files", args=["google_drive"]))

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

    def test_connect_returns_google_authorization_url(self):
        response = self.client.post(reverse("console-native-integration-connect", args=["google_drive"]))

        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertEqual(payload["provider_key"], "google_drive")
        self.assertIn("https://accounts.google.com/o/oauth2/v2/auth", payload["authorization_url"])
        self.assertIn("scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive.file", payload["authorization_url"])
        self.assertIn("state=", payload["authorization_url"])

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

    @override_settings(GOOGLE_PICKER_API_KEY="", GOOGLE_PICKER_APP_ID="")
    def test_picker_token_requires_picker_configuration(self):
        self._create_integration_secret(owner_user=self.user)

        response = self.client.get(reverse("console-native-integration-picker-token", args=["google_drive"]))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Google Picker is not configured."})

    def test_files_api_upserts_picker_files_for_user_context(self):
        self._create_integration_secret(owner_user=self.user)
        files_url = reverse("console-native-integration-files", args=["google_drive"])

        response = self.client.post(
            files_url,
            data=json.dumps(
                {
                    "files": [
                        {
                            "external_file_id": "sheet-1",
                            "name": "Pipeline",
                            "mime_type": GOOGLE_SHEETS_MIME_TYPE,
                            "url": "https://docs.google.com/spreadsheets/d/sheet-1/edit",
                        },
                        {
                            "external_file_id": "doc-1",
                            "name": "Notes",
                            "mime_type": GOOGLE_DOCS_MIME_TYPE,
                            "url": "https://docs.google.com/document/d/doc-1/edit",
                        },
                    ]
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["upserted_count"], 2)
        self.assertEqual(NativeIntegrationGrantedFile.objects.filter(user=self.user).count(), 2)

        update_response = self.client.post(
            files_url,
            data=json.dumps(
                {
                    "files": [
                        {
                            "external_file_id": "sheet-1",
                            "name": "Pipeline Updated",
                            "mime_type": GOOGLE_SHEETS_MIME_TYPE,
                            "url": "https://docs.google.com/spreadsheets/d/sheet-1/edit#gid=0",
                        }
                    ]
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(update_response.status_code, 201, update_response.content)
        self.assertEqual(NativeIntegrationGrantedFile.objects.filter(user=self.user).count(), 2)
        file_record = NativeIntegrationGrantedFile.objects.get(user=self.user, external_file_id="sheet-1")
        self.assertEqual(file_record.name, "Pipeline Updated")

        list_response = self.client.get(files_url)

        self.assertEqual(list_response.status_code, 200)
        listed_names = {file_payload["name"] for file_payload in list_response.json()["files"]}
        self.assertEqual(listed_names, {"Pipeline Updated", "Notes"})

    def test_files_api_requires_connected_provider_for_save(self):
        response = self.client.post(
            reverse("console-native-integration-files", args=["google_drive"]),
            data=json.dumps(
                {
                    "files": [
                        {
                            "external_file_id": "sheet-1",
                            "name": "Pipeline",
                            "mime_type": GOOGLE_SHEETS_MIME_TYPE,
                        }
                    ]
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "Google Drive is not connected."})

    def test_files_api_isolates_user_and_organization_context(self):
        self._create_integration_secret(owner_user=self.user)
        NativeIntegrationGrantedFile.objects.create(
            user=self.user,
            provider_key=GOOGLE_DRIVE_PROVIDER.key,
            external_file_id="user-sheet",
            name="User Sheet",
            mime_type=GOOGLE_SHEETS_MIME_TYPE,
        )
        NativeIntegrationGrantedFile.objects.create(
            organization=self.org,
            provider_key=GOOGLE_DRIVE_PROVIDER.key,
            external_file_id="org-sheet",
            name="Org Sheet",
            mime_type=GOOGLE_SHEETS_MIME_TYPE,
        )

        user_response = self.client.get(reverse("console-native-integration-files", args=["google_drive"]))

        self.assertEqual(user_response.status_code, 200)
        self.assertEqual([file_payload["name"] for file_payload in user_response.json()["files"]], ["User Sheet"])

        self._set_org_context()
        self._create_integration_secret(owner_org=self.org)
        org_response = self.client.get(reverse("console-native-integration-files", args=["google_drive"]))

        self.assertEqual(org_response.status_code, 200)
        self.assertEqual([file_payload["name"] for file_payload in org_response.json()["files"]], ["Org Sheet"])

    def test_files_api_delete_removes_registry_file_not_secret(self):
        self._create_integration_secret(owner_user=self.user)
        granted_file = NativeIntegrationGrantedFile.objects.create(
            user=self.user,
            provider_key=GOOGLE_DRIVE_PROVIDER.key,
            external_file_id="sheet-1",
            name="Pipeline",
            mime_type=GOOGLE_SHEETS_MIME_TYPE,
        )

        response = self.client.delete(
            reverse("console-native-integration-file-detail", args=["google_drive", granted_file.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(NativeIntegrationGrantedFile.objects.filter(id=granted_file.id).exists())
        self.assertTrue(GlobalSecret.objects.filter(user=self.user, key=GOOGLE_DRIVE_PROVIDER.secret_key).exists())

    @patch("console.native_integrations_api.httpx.post")
    def test_callback_stores_hidden_integration_secret(self, mock_post):
        start = self.client.post(reverse("console-native-integration-connect", args=["google_drive"]))
        state = start.json()["state"]
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": GOOGLE_DRIVE_PROVIDER.scope_string,
        }
        mock_post.return_value = token_response

        response = self.client.post(
            reverse("console-native-integration-callback", args=["google_drive"]),
            data=json.dumps({"authorization_code": "auth-code", "state": state}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        secret = GlobalSecret.objects.get(user=self.user, secret_type=GlobalSecret.SecretType.INTEGRATION)
        self.assertEqual(secret.key, "native_google_drive")
        self.assertEqual(secret.domain_pattern, GlobalSecret.INTEGRATION_DOMAIN_SENTINEL)
        stored = json.loads(secret.get_value())
        self.assertEqual(stored["access_token"], "new-access-token")
        self.assertEqual(stored["refresh_token"], "new-refresh-token")

    @patch("console.native_integrations_api.httpx.post")
    def test_callback_accepts_matching_organization_context_override(self, mock_post):
        headers = {
            "X-Gobii-Context-Type": "organization",
            "X-Gobii-Context-Id": str(self.org.id),
        }
        start = self.client.post(
            reverse("console-native-integration-connect", args=["google_drive"]),
            headers=headers,
        )
        state = start.json()["state"]
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            "access_token": "org-access-token",
            "refresh_token": "org-refresh-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": GOOGLE_DRIVE_PROVIDER.scope_string,
        }
        mock_post.return_value = token_response

        response = self.client.post(
            reverse("console-native-integration-callback", args=["google_drive"]),
            data=json.dumps({"authorization_code": "auth-code", "state": state}),
            content_type="application/json",
            headers=headers,
        )

        self.assertEqual(response.status_code, 200, response.content)
        secret = GlobalSecret.objects.get(organization=self.org, secret_type=GlobalSecret.SecretType.INTEGRATION)
        stored = json.loads(secret.get_value())
        self.assertEqual(stored["access_token"], "org-access-token")

    def test_revoke_deletes_only_provider_integration_secret(self):
        self._create_integration_secret(owner_user=self.user)
        NativeIntegrationGrantedFile.objects.create(
            user=self.user,
            provider_key=GOOGLE_DRIVE_PROVIDER.key,
            external_file_id="sheet-1",
            name="Pipeline",
            mime_type=GOOGLE_SHEETS_MIME_TYPE,
        )
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
        self.assertFalse(NativeIntegrationGrantedFile.objects.filter(user=self.user).exists())
        self.assertTrue(GlobalSecret.objects.filter(id=credential.id).exists())

    def test_secret_apis_exclude_integration_secrets(self):
        self._create_integration_secret(owner_user=self.user)
        agent_integration = PersistentAgentSecret(
            agent=self.agent,
            name="Agent Integration",
            secret_type=PersistentAgentSecret.SecretType.INTEGRATION,
            domain_pattern=PersistentAgentSecret.INTEGRATION_DOMAIN_SENTINEL,
            key="native_google_drive",
        )
        agent_integration.set_value(json.dumps({"provider_key": "google_drive"}))
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
    def test_http_request_injects_google_drive_auth_for_sheets(self, mock_request, mock_proxy):
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

    def test_native_integration_auth_matches_google_docs_api(self):
        self._create_integration_secret(owner_user=self.user)

        headers = apply_native_integration_auth(
            self.agent,
            "https://docs.googleapis.com/v1/documents/test",
            {},
        )

        self.assertEqual(headers["Authorization"], "Bearer access-token")

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    @patch("api.services.native_integrations.httpx.post")
    def test_http_request_does_not_override_explicit_authorization(self, mock_refresh, mock_request, mock_proxy):
        self._create_integration_secret(
            owner_user=self.user,
            credentials={
                "provider_key": GOOGLE_DRIVE_PROVIDER.key,
                "auth_type": "oauth2",
                "access_token": "expired-token",
                "refresh_token": "refresh-token",
                "token_type": "Bearer",
                "scope": GOOGLE_DRIVE_PROVIDER.scope_string,
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
    def test_http_request_refreshes_expired_google_drive_token(self, mock_refresh, mock_request, mock_proxy):
        secret = self._create_integration_secret(
            owner_user=self.user,
            credentials={
                "provider_key": GOOGLE_DRIVE_PROVIDER.key,
                "auth_type": "oauth2",
                "access_token": "expired-token",
                "refresh_token": "refresh-token",
                "token_type": "Bearer",
                "scope": GOOGLE_DRIVE_PROVIDER.scope_string,
                "expires_at": (timezone.now() - timedelta(minutes=1)).isoformat(),
            },
        )
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            "access_token": "refreshed-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": GOOGLE_DRIVE_PROVIDER.scope_string,
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
        self.assertIn("Google Drive", block)
        self.assertNotIn("native_google_drive", block)
        self.assertNotIn("native_google_sheets", block)

    def test_google_sheets_native_system_skill_is_registered_and_enables_http_request(self):
        definition = get_system_skill_definition(GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY)

        self.assertIsNotNone(definition)
        self.assertEqual(definition.name, "Google Sheets")
        self.assertEqual(definition.tool_names, ("http_request",))
        search_results = shortlist_system_skills("read google sheets rows", available_tool_names={"http_request"})
        self.assertIn(GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY, [result.skill_key for result in search_results])

        result = enable_system_skills(self.agent, [GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY])

        self.assertEqual(result["invalid"], [])
        self.assertIn(GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY, result["enabled"])
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(agent=self.agent, tool_full_name="http_request").exists()
        )

    @override_settings(PUBLIC_SITE_URL="https://app.example.test")
    def test_google_sheets_prompt_lists_selected_spreadsheets_only(self):
        NativeIntegrationGrantedFile.objects.create(
            user=self.user,
            provider_key=GOOGLE_DRIVE_PROVIDER.key,
            external_file_id="sheet-1",
            name="Pipeline",
            mime_type=GOOGLE_SHEETS_MIME_TYPE,
            url="https://docs.google.com/spreadsheets/d/sheet-1/edit",
        )
        NativeIntegrationGrantedFile.objects.create(
            user=self.user,
            provider_key=GOOGLE_DRIVE_PROVIDER.key,
            external_file_id="doc-1",
            name="Planning Doc",
            mime_type=GOOGLE_DOCS_MIME_TYPE,
            url="https://docs.google.com/document/d/doc-1/edit",
        )
        enable_system_skills(self.agent, [GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY])

        block = format_recent_skills_for_prompt(self.agent, limit=3)

        self.assertIn("System Skill: Google Sheets", block)
        self.assertIn("Tools: http_request", block)
        self.assertIn("Accessible Google Sheets selected through Google Drive", block)
        self.assertIn("Pipeline (id: sheet-1", block)
        self.assertIn("https://docs.google.com/spreadsheets/d/sheet-1/edit", block)
        self.assertNotIn("Planning Doc", block)
        self.assertIn("drive.file", block)
        self.assertIn("https://app.example.test/app/integrations", block)

    @override_settings(PUBLIC_SITE_URL="https://app.example.test")
    def test_google_sheets_prompt_has_empty_state_when_no_spreadsheets_are_selected(self):
        enable_system_skills(self.agent, [GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY])

        block = format_recent_skills_for_prompt(self.agent, limit=3)

        self.assertIn("System Skill: Google Sheets", block)
        self.assertIn("None recorded", block)
        self.assertIn("choose the spreadsheet in the Google Drive native integration", block)
        self.assertIn("https://app.example.test/app/integrations", block)
