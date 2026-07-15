import importlib
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connections
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from api.agent.comms.email_oauth import _maybe_refresh_email_oauth_credential
from api.models import (
    AgentEmailAccount,
    AgentEmailIntegration,
    AgentEmailOAuthCredential,
    BrowserUseAgent,
    CommsChannel,
    NativeIntegrationOAuthSession,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentEmailEndpoint,
)
from api.services.agent_email_integrations import connect_agent_email_oauth, resolve_email_oauth_identity
from api.services.persistent_agents import ensure_default_agent_email_endpoint


@override_settings(
    PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False,
    ENABLE_DEFAULT_AGENT_EMAIL=True,
    GMAIL_CLIENT_ID="gmail-client",
    GMAIL_CLIENT_SECRET="gmail-secret",
    GOOGLE_CLIENT_ID="legacy-google-client",
    GOOGLE_CLIENT_SECRET="legacy-google-secret",
    MICROSOFT_CLIENT_ID="microsoft-client",
    MICROSOFT_CLIENT_SECRET="microsoft-secret",
)
@tag("batch_console_email_oauth")
class NativeAgentEmailIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="email-integration-user",
            email="email-integration@example.com",
            password="password123",
        )
        cls.other_user = User.objects.create_user(
            username="other-email-user",
            email="other-email@example.com",
            password="password123",
        )
        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="BA")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Email Agent",
            charter="c",
            browser_use_agent=browser_agent,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _settings_payload(self, mode, account=None, **overrides):
        payload = {
            "expectedActiveMode": mode,
            "endpointAddress": account.endpoint.address if account else "",
            "connectionMode": "custom",
            "smtpHost": account.smtp_host if account else "",
            "smtpPort": account.smtp_port if account else None,
            "smtpSecurity": account.smtp_security if account else "starttls",
            "smtpAuth": account.smtp_auth if account else "login",
            "smtpUsername": account.smtp_username if account else "",
            "imapHost": account.imap_host if account else "",
            "imapPort": account.imap_port if account else None,
            "imapSecurity": account.imap_security if account else "ssl",
            "imapAuth": account.imap_auth if account else "login",
            "imapUsername": account.imap_username if account else "",
            "imapFolder": account.imap_folder if account else "INBOX",
            "isOutboundEnabled": account.is_outbound_enabled if account else False,
            "isInboundEnabled": account.is_inbound_enabled if account else False,
            "imapIdleEnabled": account.imap_idle_enabled if account else True,
            "pollIntervalSec": account.poll_interval_sec if account else 120,
            "displayName": "",
            "defaultDisplayName": "",
        }
        payload.update(overrides)
        return payload

    def _start(self, provider="gmail"):
        response = self.client.post(
            reverse("console-native-integration-connect", args=[provider]),
            data=json.dumps({"agent_id": str(self.agent.pk)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_shared_oauth_start_binds_agent_owner_and_pkce(self):
        payload = self._start()
        session = NativeIntegrationOAuthSession.objects.get(state=payload["state"])
        self.assertEqual(session.agent_id, self.agent.id)
        self.assertEqual(session.initiated_by_id, self.user.id)
        self.assertEqual(session.provider_key, "gmail")
        self.assertEqual(session.client_id, "gmail-client")
        self.assertEqual(session.client_secret, "gmail-secret")
        self.assertTrue(session.code_verifier)
        self.assertEqual(session.code_challenge_method, "S256")
        self.assertIn("code_challenge=", payload["authorization_url"])
        self.assertIn("profile", payload["authorization_url"])
        self.assertIn("gmail.send", payload["authorization_url"])
        self.assertIn("gmail.readonly", payload["authorization_url"])
        self.assertNotIn("mail.google.com", payload["authorization_url"])

    def test_shared_oauth_start_requires_agent_and_manage_permission(self):
        url = reverse("console-native-integration-connect", args=["gmail"])
        response = self.client.post(url, data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.client.force_login(self.other_user)
        response = self.client.post(
            url,
            data=json.dumps({"agent_id": str(self.agent.pk)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_migration_reverse_removes_only_accountless_oauth_sessions(self):
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="legacy-session@example.com",
        )
        account = AgentEmailAccount.objects.create(endpoint=endpoint)
        expires_at = timezone.now() + timedelta(minutes=10)
        accountless = NativeIntegrationOAuthSession.objects.create(
            agent=self.agent,
            provider_key="gmail",
            initiated_by=self.user,
            user=self.user,
            state="accountless-migration-session",
            expires_at=expires_at,
        )
        account_backed = NativeIntegrationOAuthSession.objects.create(
            account=account,
            provider_key="gmail",
            initiated_by=self.user,
            user=self.user,
            state="account-backed-migration-session",
            expires_at=expires_at,
        )
        migration = importlib.import_module(
            "api.migrations.0423_native_email_integrations"
        )

        migration.remove_unrepresentable_oauth_sessions(
            apps,
            SimpleNamespace(connection=connections["default"]),
        )

        self.assertFalse(
            NativeIntegrationOAuthSession.objects.filter(pk=accountless.pk).exists()
        )
        self.assertTrue(
            NativeIntegrationOAuthSession.objects.filter(pk=account_backed.pk).exists()
        )

    def test_native_provider_payload_and_agent_connections_are_agent_scoped(self):
        response = self.client.get(reverse("console-native-integration-list"))
        self.assertEqual(response.status_code, 200, response.content)
        providers = {item["provider_key"]: item for item in response.json()["providers"]}
        self.assertEqual(providers["gmail"]["connection_scope"], "agent")
        self.assertEqual(providers["outlook"]["connection_scope"], "agent")
        self.assertEqual(providers["gmail"]["connected_agent_count"], 0)
        connections = self.client.get(providers["gmail"]["agent_connections_url"])
        self.assertEqual(connections.status_code, 200, connections.content)
        agent_payload = next(item for item in connections.json()["agents"] if item["agent_id"] == str(self.agent.pk))
        self.assertFalse(agent_payload["connected"])
        self.assertEqual(agent_payload["active_mode"], "none")

    def test_custom_mode_blocks_oauth_until_disabled(self):
        integration = AgentEmailIntegration.objects.create(
            agent=self.agent,
            active_mode=AgentEmailIntegration.ActiveMode.CUSTOM,
        )
        response = self.client.post(
            reverse("console-native-integration-connect", args=["gmail"]),
            data=json.dumps({"agent_id": str(self.agent.pk)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        integration.active_mode = AgentEmailIntegration.ActiveMode.NONE
        integration.save(update_fields=["active_mode"])
        response = self.client.post(
            reverse("console-native-integration-connect", args=["gmail"]),
            data=json.dumps({"agent_id": str(self.agent.pk)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)

    @patch("console.native_integrations_api._validate_agent_imap_connection", create=True)
    @patch("console.native_integrations_api._validate_agent_smtp_connection", create=True)
    @patch("console.email_settings.views._validate_agent_imap_connection", return_value=(True, ""))
    @patch("console.email_settings.views._validate_agent_smtp_connection", return_value=(True, ""))
    @patch("console.native_integrations_api.resolve_email_oauth_identity")
    @patch("console.native_integrations_api.request_oauth_token")
    def test_shared_gmail_callback_derives_mailbox_and_preserves_refresh_token(
        self,
        mock_token,
        mock_identity,
        _smtp,
        _imap,
        _unused_smtp,
        _unused_imap,
    ):
        payload = self._start()
        mock_identity.return_value = {
            "address": "agent.mailbox@gmail.com",
            "display_name": "Mailbox Name",
            "account_type": "gmail",
        }
        mock_token.return_value = {
            "access_token": "access-one",
            "refresh_token": "refresh-one",
            "expires_in": 3600,
        }
        callback = self.client.post(
            reverse("console-native-integration-callback", args=["gmail"]),
            data=json.dumps({"authorization_code": "code", "state": payload["state"]}),
            content_type="application/json",
        )
        self.assertEqual(callback.status_code, 200, callback.content)
        integration = AgentEmailIntegration.objects.get(agent=self.agent)
        credential = integration.oauth_account.oauth_credential
        self.assertEqual(integration.active_mode, AgentEmailIntegration.ActiveMode.OAUTH)
        self.assertEqual(integration.oauth_account.endpoint.address, "agent.mailbox@gmail.com")
        self.assertEqual(credential.client_id, "gmail-client")
        self.assertEqual(credential.refresh_token, "refresh-one")
        self.assertTrue(integration.oauth_account.is_outbound_enabled)
        self.assertTrue(integration.oauth_account.is_inbound_enabled)
        self.assertEqual(
            PersistentAgentEmailEndpoint.objects.get(endpoint=integration.oauth_account.endpoint).display_name,
            "Mailbox Name",
        )

        second = self._start()
        mock_token.return_value = {"access_token": "access-two", "expires_in": 3600}
        callback = self.client.post(
            reverse("console-native-integration-callback", args=["gmail"]),
            data=json.dumps({"authorization_code": "code-two", "state": second["state"]}),
            content_type="application/json",
        )
        self.assertEqual(callback.status_code, 200, callback.content)
        credential.refresh_from_db()
        self.assertEqual(credential.refresh_token, "refresh-one")

    @patch("api.services.agent_email_integrations.httpx.get")
    def test_gmail_identity_uses_google_profile_name(self, mock_get):
        response = MagicMock()
        response.json.return_value = {
            "email": "person@gmail.com",
            "email_verified": True,
            "name": "Google Profile Name",
        }
        mock_get.return_value = response
        identity = resolve_email_oauth_identity(
            "gmail",
            {"access_token": "access"},
            "gmail-client",
        )
        self.assertEqual(identity["display_name"], "Google Profile Name")

    def test_oauth_display_name_falls_back_to_agent_name(self):
        account = connect_agent_email_oauth(
            agent=self.agent,
            provider_key="gmail",
            identity={"address": "fallback@gmail.com", "display_name": "", "account_type": "gmail"},
            token_payload={"access_token": "access", "refresh_token": "refresh"},
            client_id="gmail-client",
            client_secret="gmail-secret",
            user=self.user,
            organization=None,
            token_endpoint="https://oauth2.googleapis.com/token",
            requested_scope="openid profile email",
        )
        email_meta = PersistentAgentEmailEndpoint.objects.get(endpoint=account.endpoint)
        self.assertEqual(email_meta.display_name, self.agent.name)

        email_meta.display_name = ""
        email_meta.save(update_fields=["display_name"])
        response = self.client.get(reverse("console_agent_email_settings", args=[self.agent.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["endpoint"]["displayName"], self.agent.name)

    def test_outlook_consumer_and_microsoft365_presets(self):
        consumer = connect_agent_email_oauth(
            agent=self.agent,
            provider_key="outlook",
            identity={"address": "consumer@outlook.com", "display_name": "Consumer", "account_type": "consumer"},
            token_payload={"access_token": "access", "refresh_token": "refresh"},
            client_id="microsoft-client",
            client_secret="microsoft-secret",
            user=self.user,
            organization=None,
            token_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/token",
            requested_scope="offline_access",
        )
        self.assertEqual(consumer.smtp_host, "smtp-mail.outlook.com")
        self.assertEqual(consumer.imap_host, "outlook.office365.com")

        second_agent = self._create_agent("M365 Agent")
        business = connect_agent_email_oauth(
            agent=second_agent,
            provider_key="outlook",
            identity={"address": "person@company.example", "display_name": "Person", "account_type": "microsoft365"},
            token_payload={"access_token": "access", "refresh_token": "refresh"},
            client_id="microsoft-client",
            client_secret="microsoft-secret",
            user=self.user,
            organization=None,
            token_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/token",
            requested_scope="offline_access",
        )
        self.assertEqual(business.smtp_host, "smtp.office365.com")

    @patch("api.services.agent_email_integrations.jwt.decode")
    @patch("api.services.agent_email_integrations.jwt.PyJWKClient")
    def test_outlook_identity_claims_select_consumer_account_type(self, mock_jwk_client, mock_decode):
        mock_jwk_client.return_value.get_signing_key_from_jwt.return_value.key = "key"
        consumer_tenant = "9188040d-6c67-4c5b-b112-36a304b66dad"
        mock_decode.return_value = {
            "tid": consumer_tenant,
            "iss": f"https://login.microsoftonline.com/{consumer_tenant}/v2.0",
            "preferred_username": "person@outlook.com",
            "name": "Person",
        }
        identity = resolve_email_oauth_identity(
            "outlook",
            {"id_token": "signed-id-token"},
            "microsoft-client",
        )
        self.assertEqual(identity["address"], "person@outlook.com")
        self.assertEqual(identity["account_type"], "consumer")

    def test_custom_disable_retains_profile_and_secrets(self):
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="custom@example.com",
            is_primary=True,
        )
        account = AgentEmailAccount.objects.create(
            endpoint=endpoint,
            connection_mode=AgentEmailAccount.ConnectionMode.CUSTOM,
            smtp_host="smtp.example.com",
            smtp_port=587,
            is_outbound_enabled=True,
            is_inbound_enabled=True,
            connection_last_ok_at=timezone.now(),
        )
        account.set_smtp_password("secret")
        account.save()
        AgentEmailIntegration.objects.create(
            agent=self.agent,
            active_mode=AgentEmailIntegration.ActiveMode.CUSTOM,
            custom_account=account,
        )
        response = self.client.post(
            reverse("console_agent_email_settings", args=[self.agent.pk]),
            data=json.dumps({"action": "disable_custom"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        account.refresh_from_db()
        integration = AgentEmailIntegration.objects.get(agent=self.agent)
        self.assertEqual(integration.active_mode, AgentEmailIntegration.ActiveMode.NONE)
        self.assertEqual(integration.custom_account_id, account.pk)
        self.assertEqual(account.smtp_host, "smtp.example.com")
        self.assertEqual(account.get_smtp_password(), "secret")
        self.assertFalse(account.is_outbound_enabled)
        self.assertFalse(account.is_inbound_enabled)

    def test_oauth_disconnect_removes_runtime_account_and_preserves_dormant_custom(self):
        custom_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="retained@example.com",
            is_primary=True,
        )
        custom_account = AgentEmailAccount.objects.create(
            endpoint=custom_endpoint,
            connection_mode=AgentEmailAccount.ConnectionMode.CUSTOM,
            smtp_host="smtp.retained.example",
            smtp_port=587,
        )
        integration = AgentEmailIntegration.objects.create(
            agent=self.agent,
            active_mode=AgentEmailIntegration.ActiveMode.NONE,
            custom_account=custom_account,
        )
        oauth_account = connect_agent_email_oauth(
            agent=self.agent,
            provider_key="gmail",
            identity={"address": "oauth@gmail.com", "display_name": "OAuth", "account_type": "gmail"},
            token_payload={"access_token": "access", "refresh_token": "refresh"},
            client_id="gmail-client",
            client_secret="gmail-secret",
            user=self.user,
            organization=None,
            token_endpoint="https://oauth2.googleapis.com/token",
            requested_scope="https://mail.google.com/",
        )
        response = self.client.post(
            reverse("console-native-integration-revoke", args=["gmail"]),
            data=json.dumps({"agent_id": str(self.agent.pk)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        integration.refresh_from_db()
        custom_account.refresh_from_db()
        self.assertEqual(integration.active_mode, AgentEmailIntegration.ActiveMode.NONE)
        self.assertEqual(integration.custom_account_id, custom_account.pk)
        self.assertIsNone(integration.oauth_account_id)
        self.assertEqual(custom_account.smtp_host, "smtp.retained.example")
        self.assertFalse(AgentEmailAccount.objects.filter(pk=oauth_account.pk).exists())

    def test_same_mailbox_restores_custom_transport_after_oauth_disconnect(self):
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="shared@gmail.com",
            is_primary=True,
        )
        account = AgentEmailAccount.objects.create(
            endpoint=endpoint,
            connection_mode=AgentEmailAccount.ConnectionMode.CUSTOM,
            smtp_host="smtp.custom.example",
            smtp_port=2525,
            smtp_username="custom-user",
        )
        account.set_smtp_password("custom-password")
        account.save()
        integration = AgentEmailIntegration.objects.create(
            agent=self.agent,
            active_mode=AgentEmailIntegration.ActiveMode.NONE,
            custom_account=account,
        )
        connected = connect_agent_email_oauth(
            agent=self.agent,
            provider_key="gmail",
            identity={"address": endpoint.address, "display_name": "Shared", "account_type": "gmail"},
            token_payload={"access_token": "access", "refresh_token": "refresh"},
            client_id="gmail-client",
            client_secret="gmail-secret",
            user=self.user,
            organization=None,
            token_endpoint="https://oauth2.googleapis.com/token",
            requested_scope="https://mail.google.com/",
        )
        self.assertEqual(connected.pk, account.pk)
        self.assertEqual(connected.smtp_host, "smtp.gmail.com")
        response = self.client.post(
            reverse("console-native-integration-revoke", args=["gmail"]),
            data=json.dumps({"agent_id": str(self.agent.pk)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        integration.refresh_from_db()
        account.refresh_from_db()
        self.assertEqual(integration.custom_account_id, account.pk)
        self.assertEqual(account.smtp_host, "smtp.custom.example")
        self.assertEqual(account.smtp_port, 2525)
        self.assertEqual(account.smtp_username, "custom-user")
        self.assertEqual(account.get_smtp_password(), "custom-password")

    def test_separate_default_and_configured_display_names(self):
        default_endpoint = ensure_default_agent_email_endpoint(self.agent, is_primary=True)
        custom_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="configured@example.com",
            is_primary=False,
        )
        custom_account = AgentEmailAccount.objects.create(
            endpoint=custom_endpoint,
            connection_mode=AgentEmailAccount.ConnectionMode.CUSTOM,
        )
        AgentEmailIntegration.objects.create(
            agent=self.agent,
            active_mode=AgentEmailIntegration.ActiveMode.CUSTOM,
            custom_account=custom_account,
        )
        response = self.client.post(
            reverse("console_agent_email_settings", args=[self.agent.pk]),
            data=json.dumps(self._settings_payload(
                AgentEmailIntegration.ActiveMode.CUSTOM,
                custom_account,
                defaultDisplayName="Gobii Sender",
                displayName="Configured Sender",
            )),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(PersistentAgentEmailEndpoint.objects.get(endpoint=default_endpoint).display_name, "Gobii Sender")
        self.assertEqual(PersistentAgentEmailEndpoint.objects.get(endpoint=custom_endpoint).display_name, "Configured Sender")

    def test_none_mode_save_updates_only_gobii_display_name(self):
        default_endpoint = ensure_default_agent_email_endpoint(self.agent, is_primary=True)
        response = self.client.post(
            reverse("console_agent_email_settings", args=[self.agent.pk]),
            data=json.dumps(self._settings_payload(
                AgentEmailIntegration.ActiveMode.NONE,
                defaultDisplayName="Gobii Only",
                displayName="Ignored external name",
            )),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            PersistentAgentEmailEndpoint.objects.get(endpoint=default_endpoint).display_name,
            "Gobii Only",
        )
        self.assertEqual(AgentEmailAccount.objects.count(), 0)

    def test_oauth_save_stages_display_names_and_directions(self):
        default_endpoint = ensure_default_agent_email_endpoint(self.agent, is_primary=True)
        account = connect_agent_email_oauth(
            agent=self.agent,
            provider_key="gmail",
            identity={"address": "staged@gmail.com", "display_name": "Before", "account_type": "gmail"},
            token_payload={"access_token": "access", "refresh_token": "refresh"},
            client_id="gmail-client",
            client_secret="gmail-secret",
            user=self.user,
            organization=None,
            token_endpoint="https://oauth2.googleapis.com/token",
            requested_scope="https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly",
        )
        response = self.client.post(
            reverse("console_agent_email_settings", args=[self.agent.pk]),
            data=json.dumps(self._settings_payload(
                AgentEmailIntegration.ActiveMode.OAUTH,
                account,
                defaultDisplayName="Gobii OAuth Sender",
                displayName="Connected Sender",
                isOutboundEnabled=False,
                isInboundEnabled=True,
            )),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        account.refresh_from_db()
        self.assertFalse(account.is_outbound_enabled)
        self.assertTrue(account.is_inbound_enabled)
        self.assertEqual(
            PersistentAgentEmailEndpoint.objects.get(endpoint=default_endpoint).display_name,
            "Gobii OAuth Sender",
        )
        self.assertEqual(
            PersistentAgentEmailEndpoint.objects.get(endpoint=account.endpoint).display_name,
            "Connected Sender",
        )

    def test_legacy_oauth_remains_editable_and_disconnectable(self):
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="legacy-oauth@example.com",
            is_primary=True,
        )
        account = AgentEmailAccount.objects.create(
            endpoint=endpoint,
            connection_mode=AgentEmailAccount.ConnectionMode.OAUTH2,
            smtp_auth=AgentEmailAccount.AuthMode.OAUTH2,
            imap_auth=AgentEmailAccount.ImapAuthMode.OAUTH2,
            is_outbound_enabled=True,
            is_inbound_enabled=True,
        )
        AgentEmailOAuthCredential.objects.create(
            account=account,
            user=self.user,
            provider="legacy-provider",
        )
        AgentEmailIntegration.objects.create(
            agent=self.agent,
            active_mode=AgentEmailIntegration.ActiveMode.OAUTH,
            oauth_account=account,
        )
        response = self.client.post(
            reverse("console_agent_email_settings", args=[self.agent.pk]),
            data=json.dumps(self._settings_payload(
                AgentEmailIntegration.ActiveMode.OAUTH,
                account,
                displayName="Legacy Sender",
                isInboundEnabled=False,
            )),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        account.refresh_from_db()
        self.assertFalse(account.is_inbound_enabled)
        self.assertEqual(PersistentAgentEmailEndpoint.objects.get(endpoint=endpoint).display_name, "Legacy Sender")

        response = self.client.post(
            reverse("console_agent_email_settings", args=[self.agent.pk]),
            data=json.dumps({"action": "disconnect_legacy_oauth"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            AgentEmailIntegration.objects.get(agent=self.agent).active_mode,
            AgentEmailIntegration.ActiveMode.NONE,
        )

    @patch("console.email_settings.views._validate_agent_imap_connection", return_value=(True, ""))
    @patch("console.email_settings.views._validate_agent_smtp_connection", return_value=(True, ""))
    def test_custom_save_creates_complete_profile_after_validation(self, mock_smtp, mock_imap):
        AgentEmailIntegration.objects.create(
            agent=self.agent,
            active_mode=AgentEmailIntegration.ActiveMode.CUSTOM,
        )
        response = self.client.post(
            reverse("console_agent_email_settings", args=[self.agent.pk]),
            data=json.dumps(self._settings_payload(
                AgentEmailIntegration.ActiveMode.CUSTOM,
                endpointAddress="custom-save@example.com",
                smtpHost="smtp.example.com",
                smtpPort=587,
                smtpSecurity="starttls",
                smtpAuth="login",
                smtpUsername="smtp-user",
                smtpPassword="smtp-secret",
                imapHost="imap.example.com",
                imapPort=993,
                imapSecurity="ssl",
                imapAuth="login",
                imapUsername="imap-user",
                imapPassword="imap-secret",
                imapFolder="Archive",
                isOutboundEnabled=True,
                isInboundEnabled=True,
                displayName="Custom Sender",
                defaultDisplayName="Gobii Sender",
            )),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        integration = AgentEmailIntegration.objects.get(agent=self.agent)
        account = integration.custom_account
        self.assertEqual(account.endpoint.address, "custom-save@example.com")
        self.assertEqual(account.smtp_host, "smtp.example.com")
        self.assertEqual(account.imap_folder, "Archive")
        self.assertEqual(account.get_smtp_password(), "smtp-secret")
        self.assertEqual(account.get_imap_password(), "imap-secret")
        self.assertTrue(account.is_outbound_enabled)
        self.assertTrue(account.is_inbound_enabled)
        mock_smtp.assert_called_once()
        mock_imap.assert_called_once()

    @patch("console.email_settings.views._validate_agent_imap_connection")
    @patch("console.email_settings.views._validate_agent_smtp_connection")
    def test_display_only_custom_save_preserves_passwords_without_reconnecting(self, mock_smtp, mock_imap):
        now = timezone.now()
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="healthy@example.com",
            is_primary=True,
        )
        account = AgentEmailAccount.objects.create(
            endpoint=endpoint,
            connection_mode=AgentEmailAccount.ConnectionMode.CUSTOM,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="smtp-user",
            imap_host="imap.example.com",
            imap_port=993,
            imap_username="imap-user",
            is_outbound_enabled=True,
            is_inbound_enabled=True,
            smtp_last_ok_at=now,
            imap_last_ok_at=now,
        )
        account.set_smtp_password("saved-smtp")
        account.set_imap_password("saved-imap")
        account.save()
        AgentEmailIntegration.objects.create(
            agent=self.agent,
            active_mode=AgentEmailIntegration.ActiveMode.CUSTOM,
            custom_account=account,
        )
        response = self.client.post(
            reverse("console_agent_email_settings", args=[self.agent.pk]),
            data=json.dumps(self._settings_payload(
                AgentEmailIntegration.ActiveMode.CUSTOM,
                account,
                displayName="New display name",
            )),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        account.refresh_from_db()
        self.assertEqual(account.get_smtp_password(), "saved-smtp")
        self.assertEqual(account.get_imap_password(), "saved-imap")
        mock_smtp.assert_not_called()
        mock_imap.assert_not_called()

    @patch("console.email_settings.views._validate_agent_smtp_connection", return_value=(True, ""))
    def test_newly_enabled_custom_direction_is_validated(self, mock_smtp):
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="enable-send@example.com",
            is_primary=True,
        )
        account = AgentEmailAccount.objects.create(
            endpoint=endpoint,
            connection_mode=AgentEmailAccount.ConnectionMode.CUSTOM,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="smtp-user",
            is_outbound_enabled=False,
        )
        account.set_smtp_password("saved-password")
        account.save()
        AgentEmailIntegration.objects.create(
            agent=self.agent,
            active_mode=AgentEmailIntegration.ActiveMode.CUSTOM,
            custom_account=account,
        )
        response = self.client.post(
            reverse("console_agent_email_settings", args=[self.agent.pk]),
            data=json.dumps(self._settings_payload(
                AgentEmailIntegration.ActiveMode.CUSTOM,
                account,
                isOutboundEnabled=True,
            )),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        account.refresh_from_db()
        self.assertTrue(account.is_outbound_enabled)
        mock_smtp.assert_called_once()

    @patch("console.email_settings.views._validate_agent_smtp_connection", return_value=(False, "SMTP unavailable"))
    def test_custom_validation_failure_rolls_back_all_changes(self, _mock_smtp):
        default_endpoint = ensure_default_agent_email_endpoint(self.agent, is_primary=False)
        PersistentAgentEmailEndpoint.objects.update_or_create(
            endpoint=default_endpoint,
            defaults={"display_name": "Old Gobii"},
        )
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="rollback@example.com",
            is_primary=True,
        )
        PersistentAgentEmailEndpoint.objects.create(endpoint=endpoint, display_name="Old custom")
        account = AgentEmailAccount.objects.create(
            endpoint=endpoint,
            connection_mode=AgentEmailAccount.ConnectionMode.CUSTOM,
            smtp_host="smtp.old.example",
            smtp_port=587,
            smtp_username="user",
            is_outbound_enabled=True,
            smtp_last_ok_at=timezone.now(),
        )
        AgentEmailIntegration.objects.create(
            agent=self.agent,
            active_mode=AgentEmailIntegration.ActiveMode.CUSTOM,
            custom_account=account,
        )
        response = self.client.post(
            reverse("console_agent_email_settings", args=[self.agent.pk]),
            data=json.dumps(self._settings_payload(
                AgentEmailIntegration.ActiveMode.CUSTOM,
                account,
                smtpHost="smtp.new.example",
                defaultDisplayName="New Gobii",
                displayName="New custom",
            )),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        account.refresh_from_db()
        self.assertEqual(account.smtp_host, "smtp.old.example")
        self.assertEqual(PersistentAgentEmailEndpoint.objects.get(endpoint=default_endpoint).display_name, "Old Gobii")
        self.assertEqual(PersistentAgentEmailEndpoint.objects.get(endpoint=endpoint).display_name, "Old custom")

    @patch("console.email_settings.views.AgentEmailAccount.full_clean")
    def test_custom_persistence_failure_rolls_back_endpoint_and_account_updates(self, mock_full_clean):
        default_endpoint = ensure_default_agent_email_endpoint(self.agent, is_primary=False)
        PersistentAgentEmailEndpoint.objects.update_or_create(
            endpoint=default_endpoint,
            defaults={"display_name": "Old Gobii"},
        )
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="atomic@example.com",
            is_primary=True,
        )
        account = AgentEmailAccount.objects.create(
            endpoint=endpoint,
            connection_mode=AgentEmailAccount.ConnectionMode.CUSTOM,
            smtp_host="smtp.old.example",
            smtp_port=587,
            smtp_username="user",
            is_outbound_enabled=False,
        )
        AgentEmailIntegration.objects.create(
            agent=self.agent,
            active_mode=AgentEmailIntegration.ActiveMode.CUSTOM,
            custom_account=account,
        )
        mock_full_clean.side_effect = ValidationError({"smtp_host": ["Invalid host"]})
        response = self.client.post(
            reverse("console_agent_email_settings", args=[self.agent.pk]),
            data=json.dumps(self._settings_payload(
                AgentEmailIntegration.ActiveMode.CUSTOM,
                account,
                smtpHost="smtp.new.example",
                defaultDisplayName="New Gobii",
            )),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        account.refresh_from_db()
        self.assertEqual(account.smtp_host, "smtp.old.example")
        self.assertEqual(PersistentAgentEmailEndpoint.objects.get(endpoint=default_endpoint).display_name, "Old Gobii")

    def test_stale_mode_save_is_rejected_without_writes(self):
        default_endpoint = ensure_default_agent_email_endpoint(self.agent, is_primary=True)
        PersistentAgentEmailEndpoint.objects.update_or_create(
            endpoint=default_endpoint,
            defaults={"display_name": "Before"},
        )
        AgentEmailIntegration.objects.create(
            agent=self.agent,
            active_mode=AgentEmailIntegration.ActiveMode.CUSTOM,
        )
        response = self.client.post(
            reverse("console_agent_email_settings", args=[self.agent.pk]),
            data=json.dumps(self._settings_payload(
                AgentEmailIntegration.ActiveMode.NONE,
                defaultDisplayName="After",
            )),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn("Reload", response.json()["error"])
        self.assertEqual(PersistentAgentEmailEndpoint.objects.get(endpoint=default_endpoint).display_name, "Before")

    @patch("console.email_settings.views._validate_agent_imap_connection", return_value=(True, ""))
    @patch("console.email_settings.views._validate_agent_smtp_connection", return_value=(True, ""))
    def test_custom_draft_test_performs_no_database_writes(self, _mock_smtp, _mock_imap):
        integration = AgentEmailIntegration.objects.create(
            agent=self.agent,
            active_mode=AgentEmailIntegration.ActiveMode.CUSTOM,
        )
        endpoint_count = PersistentAgentCommsEndpoint.objects.count()
        account_count = AgentEmailAccount.objects.count()
        response = self.client.post(
            reverse("console_agent_email_settings_test", args=[self.agent.pk]),
            data=json.dumps({
                **self._settings_payload(
                    AgentEmailIntegration.ActiveMode.CUSTOM,
                    endpointAddress="draft-only@example.com",
                    smtpHost="smtp.example.com",
                    smtpPort=587,
                    smtpUsername="smtp-user",
                    smtpPassword="smtp-secret",
                    imapHost="imap.example.com",
                    imapPort=993,
                    imapUsername="imap-user",
                    imapPassword="imap-secret",
                ),
                "testOutbound": True,
                "testInbound": True,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["ok"])
        self.assertNotIn("settings", response.json())
        integration.refresh_from_db()
        self.assertIsNone(integration.custom_account_id)
        self.assertEqual(PersistentAgentCommsEndpoint.objects.count(), endpoint_count)
        self.assertEqual(AgentEmailAccount.objects.count(), account_count)

    def test_legacy_browser_callback_redirects_to_native_callback(self):
        response = self.client.get(reverse("app-email-oauth-callback-view"), {"code": "abc", "state": "xyz"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"{reverse('console-native-integration-oauth-callback-view')}?code=abc&state=xyz",
        )

    def test_legacy_google_refresh_fallback_preserves_refresh_token(self):
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="legacy@gmail.com",
        )
        account = AgentEmailAccount.objects.create(endpoint=endpoint)
        credential = AgentEmailOAuthCredential.objects.create(
            account=account,
            user=self.user,
            provider="gmail",
            expires_at=timezone.now() - timedelta(minutes=5),
            metadata={"token_endpoint": "https://oauth2.googleapis.com/token"},
        )
        credential.access_token = "old-access"
        credential.refresh_token = "old-refresh"
        credential.save()
        response = MagicMock()
        response.json.return_value = {"access_token": "new-access", "expires_in": 3600}
        response.raise_for_status.return_value = None
        with patch("api.agent.comms.email_oauth.requests.post", return_value=response) as mock_post:
            refreshed = _maybe_refresh_email_oauth_credential(credential)
        self.assertEqual(refreshed.access_token, "new-access")
        self.assertEqual(refreshed.refresh_token, "old-refresh")
        self.assertEqual(mock_post.call_args.kwargs["data"]["client_id"], "legacy-google-client")

    def _create_agent(self, name):
        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            browser_agent = BrowserUseAgent.objects.create(user=self.user, name=f"BA-{name}")
        return PersistentAgent.objects.create(
            user=self.user,
            name=name,
            charter="c",
            browser_use_agent=browser_agent,
        )
