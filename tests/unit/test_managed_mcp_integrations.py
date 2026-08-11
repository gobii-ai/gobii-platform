import json
import os
from dataclasses import replace
from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone
from waffle.models import Flag
from waffle.testutils import override_flag

from api.agent.system_skills.defaults import _hubspot_native_prompt_instructions
from api.agent.tools.mcp_error_normalizers import MCPErrorNormalizerRegistry
from api.agent.tools.mcp_manager import MCPToolInfo, MCPToolManager
from api.models import (
    BrowserUseAgent,
    GlobalSecret,
    MCPServerConfig,
    MCPServerOAuthCredential,
    MCPServerOAuthSession,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentSystemSkillState,
    PipedreamAppSelection,
)
from api.services.managed_mcp_integrations import (
    HUBSPOT_MCP_PROVIDER,
    MANAGED_OAUTH_MCP_PROVIDERS,
    ManagedMCPConnectionMode,
    complete_managed_mcp_oauth,
    managed_mcp_provider_enabled,
    resolve_managed_mcp_connection_mode,
    start_managed_mcp_oauth,
    trigger_agents_for_managed_mcp_change,
)
from api.services.mcp_oauth import MCPOAuthResult, MCPOAuthStatus, ensure_mcp_oauth_credential
from api.services.mcp_servers import agent_accessible_server_configs, agent_server_overview
from api.services.native_integrations import (
    HUBSPOT_PROVIDER,
    NativeIntegrationAuthError,
    apply_native_integration_auth,
)
from api.services.pipedream_apps import (
    get_effective_pipedream_app_slugs_for_agent,
    get_owner_apps_state,
    is_pipedream_tool_visible_to_agent,
    serialize_owner_apps_state,
)


User = get_user_model()


@tag("batch_native_integrations")
@override_settings(
    HUBSPOT_MCP_CLIENT_ID="managed-hubspot-client-id",
    HUBSPOT_MCP_CLIENT_SECRET="managed-hubspot-client-secret",
    HUBSPOT_CLIENT_ID="legacy-hubspot-client-id",
    HUBSPOT_CLIENT_SECRET="legacy-hubspot-client-secret",
    GOBII_PROPRIETARY_MODE=False,
    PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False,
    SEGMENT_WRITE_KEY="",
    SEGMENT_WEB_WRITE_KEY="",
)
class ManagedHubSpotMCPTests(TestCase):
    def setUp(self):
        os.environ.setdefault("GOBII_ENCRYPTION_KEY", "test-key-for-managed-mcp")
        discovery_patcher = patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery")
        discovery_patcher.start()
        self.addCleanup(discovery_patcher.stop)
        hubspot_flag, _created = Flag.objects.update_or_create(
            name="hubspot_mcp",
            defaults={
                "everyone": None,
                "percent": 0,
                "superusers": True,
                "staff": True,
                "authenticated": False,
            },
        )
        hubspot_flag.flush()
        Flag.objects.update_or_create(name="organizations", defaults={"everyone": True})
        self.user = User.objects.create_user(
            username="managed-hubspot-staff",
            email="managed-hubspot@example.com",
            password="password123",
            is_staff=True,
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Managed Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Managed HubSpot Agent",
            charter="Use HubSpot",
            browser_use_agent=self.browser_agent,
        )
        self.client.force_login(self.user)

    @staticmethod
    def _token_response(*, access_token="managed-access", refresh_token="managed-refresh"):
        response = MagicMock()
        response.status_code = 200
        response.text = ""
        response.json.return_value = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        response.raise_for_status.return_value = None
        return response

    def _start(self):
        return self.client.post(
            reverse("console-native-integration-connect", args=["hubspot"]),
        )

    def _create_legacy_secret(self, access_token="legacy-rest-token"):
        secret = GlobalSecret(
            user=self.user,
            name="Legacy HubSpot",
            secret_type=GlobalSecret.SecretType.INTEGRATION,
            domain_pattern=GlobalSecret.INTEGRATION_DOMAIN_SENTINEL,
            key=HUBSPOT_PROVIDER.secret_key,
        )
        secret.set_value(json.dumps({"access_token": access_token}))
        secret.save()
        return secret

    def _create_managed_credential(self, *, is_active=False):
        config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="hubspot",
            display_name="HubSpot",
            url=HUBSPOT_MCP_PROVIDER.server_url,
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
            managed_integration_key="hubspot",
            is_active=is_active,
        )
        MCPServerOAuthCredential.objects.create(
            server_config=config,
            user=self.user,
        )
        return config

    def test_connection_mode_resolver_matrix(self):
        cases = (
            (False, False, False, ManagedMCPConnectionMode.LEGACY_REST),
            (False, True, False, ManagedMCPConnectionMode.LEGACY_REST),
            (False, False, True, ManagedMCPConnectionMode.LEGACY_REST),
            (False, True, True, ManagedMCPConnectionMode.LEGACY_REST),
            (True, False, False, ManagedMCPConnectionMode.MANAGED_MCP),
            (True, True, False, ManagedMCPConnectionMode.LEGACY_REST),
            (True, False, True, ManagedMCPConnectionMode.MANAGED_MCP),
            (True, True, True, ManagedMCPConnectionMode.MANAGED_MCP),
        )

        for rollout_enabled, has_legacy, has_managed, expected in cases:
            with self.subTest(
                rollout_enabled=rollout_enabled,
                has_legacy=has_legacy,
                has_managed=has_managed,
            ):
                GlobalSecret.objects.filter(user=self.user, key=HUBSPOT_PROVIDER.secret_key).delete()
                MCPServerConfig.objects.filter(user=self.user, managed_integration_key="hubspot").delete()
                if has_legacy:
                    self._create_legacy_secret()
                if has_managed:
                    self._create_managed_credential()

                with override_flag("hubspot_mcp", active=rollout_enabled):
                    mode = resolve_managed_mcp_connection_mode("hubspot", self.user, None)

                self.assertEqual(mode, expected)

        GlobalSecret.objects.filter(user=self.user, key=HUBSPOT_PROVIDER.secret_key).delete()
        MCPServerConfig.objects.filter(user=self.user, managed_integration_key="hubspot").delete()
        self._create_legacy_secret()
        config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="hubspot",
            display_name="HubSpot",
            url=HUBSPOT_MCP_PROVIDER.server_url,
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
            managed_integration_key="hubspot",
            is_active=True,
        )
        self.assertEqual(
            resolve_managed_mcp_connection_mode("hubspot", self.user, None),
            ManagedMCPConnectionMode.MANAGED_MCP,
        )
        self.assertTrue(config.is_active)

    def test_pipedream_hubspot_tools_are_suppressed_in_both_modes(self):
        self._create_legacy_secret()
        PipedreamAppSelection.objects.create(
            user=self.user,
            selected_app_slugs=["hubspot", "slack"],
        )
        self.assertEqual(
            resolve_managed_mcp_connection_mode("hubspot", self.user, None),
            ManagedMCPConnectionMode.LEGACY_REST,
        )
        effective_apps = get_effective_pipedream_app_slugs_for_agent(self.agent)
        self.assertNotIn("hubspot", effective_apps)
        self.assertIn("slack", effective_apps)
        self.assertFalse(
            is_pipedream_tool_visible_to_agent(self.agent, "hubspot-search-contacts"),
        )

        self._create_managed_credential()
        self.assertEqual(
            resolve_managed_mcp_connection_mode("hubspot", self.user, None),
            ManagedMCPConnectionMode.MANAGED_MCP,
        )
        self.assertFalse(
            is_pipedream_tool_visible_to_agent(self.agent, "hubspot-search-contacts"),
        )

    def test_pipedream_hubspot_remains_available_outside_rollout_without_native_connection(self):
        PipedreamAppSelection.objects.create(
            user=self.user,
            selected_app_slugs=["hubspot", "slack"],
        )

        with override_flag("hubspot_mcp", active=False):
            effective_apps = get_effective_pipedream_app_slugs_for_agent(self.agent)
            hubspot_visible = is_pipedream_tool_visible_to_agent(
                self.agent,
                "hubspot-search-contacts",
            )

        self.assertIn("hubspot", effective_apps)
        self.assertIn("slack", effective_apps)
        self.assertTrue(hubspot_visible)

    def test_owner_app_serialization_hides_rollout_suppressed_hubspot(self):
        PipedreamAppSelection.objects.create(
            user=self.user,
            selected_app_slugs=["hubspot", "slack"],
        )
        catalog = MagicMock()
        catalog.get_apps.side_effect = lambda slugs: [
            MagicMock(to_dict=lambda slug=slug: {
                "slug": slug,
                "name": slug.title(),
                "description": "",
                "icon_url": "",
            })
            for slug in slugs
        ]

        state = get_owner_apps_state(
            MCPServerConfig.Scope.USER,
            self.user.username,
            owner_user=self.user,
        )
        payload = serialize_owner_apps_state(state, catalog=catalog)

        self.assertEqual([app["slug"] for app in payload["selected_apps"]], ["slack"])
        effective_slugs = [app["slug"] for app in payload["effective_apps"]]
        self.assertNotIn("hubspot", effective_slugs)
        self.assertIn("slack", effective_slugs)

    @patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery")
    def test_staff_connect_start_creates_hidden_managed_config_and_server_side_pkce(self, _mock_discovery):
        response = self._start()

        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        parsed = urlparse(payload["authorization_url"])
        query = parse_qs(parsed.query)
        self.assertEqual(
            f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
            HUBSPOT_MCP_PROVIDER.authorization_endpoint,
        )
        self.assertEqual(query["client_id"], ["managed-hubspot-client-id"])
        self.assertEqual(query["redirect_uri"], ["http://testserver/integrations/oauth/callback/"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertNotIn("scope", query)

        config = MCPServerConfig.objects.get(managed_integration_key="hubspot")
        self.assertEqual(config.user, self.user)
        self.assertEqual(config.name, "hubspot")
        self.assertEqual(config.url, HUBSPOT_MCP_PROVIDER.server_url)
        self.assertEqual(config.auth_method, MCPServerConfig.AuthMethod.OAUTH2)
        self.assertFalse(config.is_active)
        self.assertNotIn(config, agent_accessible_server_configs(self.agent))
        session = MCPServerOAuthSession.objects.get(server_config=config)
        self.assertTrue(session.code_verifier)
        self.assertEqual(session.code_challenge, query["code_challenge"][0])
        self.assertEqual(session.client_id, "")
        self.assertEqual(session.client_secret, "")

        generic_list = self.client.get(reverse("console-mcp-server-list"))
        self.assertEqual(generic_list.status_code, 200)
        self.assertEqual(generic_list.json()["servers"], [])
        detail = self.client.get(reverse("console-mcp-server-detail", args=[config.id]))
        self.assertEqual(detail.status_code, 403)

    def test_generic_provider_scopes_are_requested_and_preserved_when_token_omits_scope(self):
        scoped_provider = replace(
            HUBSPOT_MCP_PROVIDER,
            scopes=("crm.objects.contacts.read", "oauth"),
        )
        token_response = self._token_response()

        with (
            patch.dict(MANAGED_OAUTH_MCP_PROVIDERS, {"hubspot": scoped_provider}),
            patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery"),
            patch("api.services.managed_mcp_integrations.httpx.post", return_value=token_response),
            patch("api.agent.tools.mcp_manager.get_mcp_manager"),
        ):
            started = start_managed_mcp_oauth(
                "hubspot",
                initiated_by=self.user,
                owner_user=self.user,
                owner_org=None,
                redirect_uri="https://example.test/oauth/callback",
            )
            query = parse_qs(urlparse(started["authorization_url"]).query)
            self.assertEqual(query["scope"], ["crm.objects.contacts.read oauth"])
            self.assertEqual(started["session"].scope, "crm.objects.contacts.read oauth")

            completed = complete_managed_mcp_oauth(
                "hubspot",
                state=started["state"],
                authorization_code="authorization-code",
                initiated_by=self.user,
                owner_user=self.user,
                owner_org=None,
            )

        self.assertEqual(completed["credential"].scope, "crm.objects.contacts.read oauth")

    @patch("api.agent.tools.mcp_manager.get_mcp_manager")
    @patch("api.services.managed_mcp_integrations.httpx.post")
    def test_callback_stores_only_tenant_tokens_and_inherits_server_for_all_owner_agents(
        self,
        mock_post,
        mock_get_manager,
    ):
        start_response = self._start()
        state = start_response.json()["state"]
        session = MCPServerOAuthSession.objects.get(state=state)
        verifier = session.code_verifier
        mock_post.return_value = self._token_response()

        response = self.client.post(
            reverse("console-native-integration-callback", args=["hubspot"]),
            data=json.dumps({"authorization_code": "authorization-code", "state": state}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["connection_kind"], "managed_mcp")
        config = MCPServerConfig.objects.get(managed_integration_key="hubspot")
        credential = MCPServerOAuthCredential.objects.get(server_config=config)
        self.assertEqual(credential.access_token, "managed-access")
        self.assertEqual(credential.refresh_token, "managed-refresh")
        self.assertEqual(credential.client_id, "")
        self.assertEqual(credential.client_secret, "")
        self.assertFalse(MCPServerOAuthSession.objects.filter(state=state).exists())
        self.assertEqual(
            mock_post.call_args.args[0],
            HUBSPOT_MCP_PROVIDER.token_endpoint,
        )
        token_data = mock_post.call_args.kwargs["data"]
        self.assertEqual(token_data["client_id"], "managed-hubspot-client-id")
        self.assertEqual(token_data["client_secret"], "managed-hubspot-client-secret")
        self.assertEqual(token_data["code_verifier"], verifier)
        mock_get_manager.return_value.refresh_server.assert_called_once_with(str(config.id))

        second_browser = BrowserUseAgent.objects.create(user=self.user, name="Second Browser")
        second_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Second Agent",
            charter="Also use HubSpot",
            browser_use_agent=second_browser,
        )
        for agent in (self.agent, second_agent):
            accessible_ids = {str(item.id) for item in agent_accessible_server_configs(agent)}
            self.assertIn(str(config.id), accessible_ids)
            overview = {item["id"]: item for item in agent_server_overview(agent)}
            self.assertTrue(overview[str(config.id)]["inherited"])
            self.assertTrue(overview[str(config.id)]["assigned"])

        provider = next(
            item
            for item in self.client.get(reverse("console-native-integration-list")).json()["providers"]
            if item["provider_key"] == "hubspot"
        )
        self.assertTrue(provider["connected"])
        self.assertEqual(provider["connection_kind"], "managed_mcp")
        self.assertEqual(provider["scopes"], [])

    @patch("api.agent.tools.mcp_manager.get_mcp_manager")
    @patch("api.services.managed_mcp_integrations.httpx.post")
    def test_callback_rejects_expired_cross_user_and_replayed_states(self, mock_post, _mock_manager):
        expired_state = self._start().json()["state"]
        MCPServerOAuthSession.objects.filter(state=expired_state).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        expired_response = self.client.post(
            reverse("console-native-integration-callback", args=["hubspot"]),
            data=json.dumps({"authorization_code": "expired-code", "state": expired_state}),
            content_type="application/json",
        )
        self.assertEqual(expired_response.status_code, 400)
        self.assertFalse(MCPServerOAuthSession.objects.filter(state=expired_state).exists())

        owner_state = self._start().json()["state"]
        other_user = User.objects.create_user(
            username="other-managed-hubspot-staff",
            email="other-managed-hubspot@example.com",
            password="password123",
            is_staff=True,
        )
        self.client.force_login(other_user)
        cross_user_response = self.client.post(
            reverse("console-native-integration-callback", args=["hubspot"]),
            data=json.dumps({"authorization_code": "wrong-user-code", "state": owner_state}),
            content_type="application/json",
        )
        self.assertEqual(cross_user_response.status_code, 400)
        self.assertTrue(MCPServerOAuthSession.objects.filter(state=owner_state).exists())

        self.client.force_login(self.user)
        mock_post.return_value = self._token_response()
        successful_response = self.client.post(
            reverse("console-native-integration-callback", args=["hubspot"]),
            data=json.dumps({"authorization_code": "owner-code", "state": owner_state}),
            content_type="application/json",
        )
        self.assertEqual(successful_response.status_code, 200)
        replay_response = self.client.post(
            reverse("console-native-integration-callback", args=["hubspot"]),
            data=json.dumps({"authorization_code": "replay-code", "state": owner_state}),
            content_type="application/json",
        )
        self.assertEqual(replay_response.status_code, 400)
        self.assertEqual(mock_post.call_count, 1)

    @patch("console.native_integrations_api.request_oauth_token")
    def test_legacy_callback_remains_legacy_if_rollout_turns_on_mid_flow(self, mock_request_token):
        with override_flag("hubspot_mcp", active=False):
            start_response = self._start()
        self.assertEqual(start_response.status_code, 201, start_response.content)
        state = start_response.json()["state"]
        self.assertFalse(MCPServerOAuthSession.objects.filter(state=state).exists())
        mock_request_token.return_value = {
            "access_token": "legacy-access",
            "refresh_token": "legacy-refresh",
            "token_type": "Bearer",
            "scope": HUBSPOT_PROVIDER.scope_string,
            "expires_in": 3600,
        }

        with override_flag("hubspot_mcp", active=True):
            callback = self.client.post(
                reverse("console-native-integration-callback", args=["hubspot"]),
                data=json.dumps({"authorization_code": "legacy-code", "state": state}),
                content_type="application/json",
            )

        self.assertEqual(callback.status_code, 200, callback.content)
        self.assertTrue(callback.json()["connected"])
        self.assertIn("secret_id", callback.json())
        self.assertFalse(
            MCPServerConfig.objects.filter(
                user=self.user,
                managed_integration_key="hubspot",
            ).exists()
        )
        self.assertTrue(
            GlobalSecret.objects.filter(
                user=self.user,
                key=HUBSPOT_PROVIDER.secret_key,
            ).exists()
        )

    @patch("api.agent.tools.mcp_manager.get_mcp_manager")
    @patch("api.services.managed_mcp_integrations.httpx.post")
    def test_organization_connection_is_inherited_only_by_organization_agents(self, mock_post, _mock_manager):
        organization = Organization.objects.create(
            name="Managed MCP Org",
            slug="managed-mcp-org",
            created_by=self.user,
        )
        OrganizationMembership.objects.create(
            org=organization,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(organization.id)
        session["context_name"] = organization.name
        session.save()
        mock_post.return_value = self._token_response()
        state = self._start().json()["state"]
        callback = self.client.post(
            reverse("console-native-integration-callback", args=["hubspot"]),
            data=json.dumps({"authorization_code": "org-code", "state": state}),
            content_type="application/json",
        )
        self.assertEqual(callback.status_code, 200, callback.content)

        config = MCPServerConfig.objects.get(managed_integration_key="hubspot")
        self.assertEqual(config.organization, organization)
        org_browser = BrowserUseAgent.objects.create(user=self.user, name="Org Browser")
        with patch.object(PersistentAgent, "_validate_org_seats", return_value=None):
            org_agent = PersistentAgent.objects.create(
                user=self.user,
                organization=organization,
                name="Org Agent",
                charter="Use org HubSpot",
                browser_use_agent=org_browser,
            )
        self.assertIn(config, agent_accessible_server_configs(org_agent))
        self.assertNotIn(config, agent_accessible_server_configs(self.agent))
        personal_managed_ids = {
            item["id"]
            for item in agent_server_overview(org_agent)
            if item["managed_integration_key"]
        }
        self.assertEqual(personal_managed_ids, {str(config.id)})

    @patch("api.agent.tools.mcp_manager.get_mcp_manager")
    @patch("api.services.managed_mcp_integrations.httpx.post")
    def test_legacy_rest_stays_authoritative_until_managed_callback_succeeds(
        self,
        mock_post,
        _mock_manager,
    ):
        legacy_secret = self._create_legacy_secret()

        self.assertEqual(
            resolve_managed_mcp_connection_mode("hubspot", self.user, None),
            ManagedMCPConnectionMode.LEGACY_REST,
        )
        before = next(
            item
            for item in self.client.get(reverse("console-native-integration-list")).json()["providers"]
            if item["provider_key"] == "hubspot"
        )
        self.assertTrue(before["connected"])
        self.assertEqual(before["connection_kind"], "native_api")

        start_response = self._start()
        self.assertEqual(start_response.status_code, 201, start_response.content)
        state = start_response.json()["state"]
        self.assertEqual(
            resolve_managed_mcp_connection_mode("hubspot", self.user, None),
            ManagedMCPConnectionMode.LEGACY_REST,
        )
        legacy_headers = apply_native_integration_auth(
            self.agent,
            "https://api.hubapi.com/crm/v3/objects/contacts",
            {},
        )
        self.assertEqual(legacy_headers["Authorization"], "Bearer legacy-rest-token")

        mock_post.return_value = self._token_response()
        callback = self.client.post(
            reverse("console-native-integration-callback", args=["hubspot"]),
            data=json.dumps({"authorization_code": "upgrade-code", "state": state}),
            content_type="application/json",
        )

        self.assertEqual(callback.status_code, 200, callback.content)
        self.assertEqual(
            resolve_managed_mcp_connection_mode("hubspot", self.user, None),
            ManagedMCPConnectionMode.MANAGED_MCP,
        )
        self.assertTrue(GlobalSecret.objects.filter(id=legacy_secret.id).exists())
        with self.assertRaises(NativeIntegrationAuthError) as raised:
            apply_native_integration_auth(
                self.agent,
                "https://api.hubapi.com/crm/v3/objects/contacts",
                {"Authorization": "Bearer manually-supplied-token"},
            )
        self.assertEqual(raised.exception.code, "managed_mcp_required")

    @patch("api.services.managed_mcp_integrations.httpx.post")
    def test_failed_managed_upgrade_leaves_legacy_rest_authoritative(self, mock_post):
        legacy_secret = self._create_legacy_secret()
        state = self._start().json()["state"]
        mock_post.return_value = MagicMock(status_code=503, text="temporarily unavailable")

        callback = self.client.post(
            reverse("console-native-integration-callback", args=["hubspot"]),
            data=json.dumps({"authorization_code": "failed-code", "state": state}),
            content_type="application/json",
        )

        self.assertEqual(callback.status_code, 503, callback.content)
        self.assertTrue(GlobalSecret.objects.filter(id=legacy_secret.id).exists())
        self.assertEqual(
            resolve_managed_mcp_connection_mode("hubspot", self.user, None),
            ManagedMCPConnectionMode.LEGACY_REST,
        )
        headers = apply_native_integration_auth(
            self.agent,
            "https://api.hubapi.com/crm/v3/objects/contacts",
            {},
        )
        self.assertEqual(headers["Authorization"], "Bearer legacy-rest-token")

    def test_disconnecting_legacy_makes_the_next_connection_managed_mcp(self):
        legacy_secret = self._create_legacy_secret()

        response = self.client.post(reverse("console-native-integration-revoke", args=["hubspot"]))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["connection_kind"], "native_api")
        self.assertFalse(GlobalSecret.objects.filter(id=legacy_secret.id).exists())
        self.assertEqual(
            resolve_managed_mcp_connection_mode("hubspot", self.user, None),
            ManagedMCPConnectionMode.MANAGED_MCP,
        )
        restart = self._start()
        self.assertEqual(restart.status_code, 201, restart.content)
        self.assertTrue(restart.json()["authorization_url"].startswith(HUBSPOT_MCP_PROVIDER.authorization_endpoint))

    @patch("api.agent.tools.mcp_manager.get_mcp_manager")
    @patch("api.services.managed_mcp_integrations.httpx.post")
    def test_disconnect_clears_mcp_and_retained_legacy_credentials(self, mock_post, mock_manager):
        mock_post.return_value = self._token_response()
        state = self._start().json()["state"]
        self.client.post(
            reverse("console-native-integration-callback", args=["hubspot"]),
            data=json.dumps({"authorization_code": "code", "state": state}),
            content_type="application/json",
        )
        config = MCPServerConfig.objects.get(managed_integration_key="hubspot")
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="mcp_hubspot_get_user_details",
            tool_server="hubspot",
            tool_name="get_user_details",
            server_config=config,
        )
        legacy_secret = self._create_legacy_secret("legacy-token")
        mock_manager.reset_mock()

        response = self.client.post(reverse("console-native-integration-revoke", args=["hubspot"]))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["revoked"])
        config.refresh_from_db()
        self.assertFalse(config.is_active)
        self.assertFalse(MCPServerOAuthCredential.objects.filter(server_config=config).exists())
        self.assertFalse(PersistentAgentEnabledTool.objects.filter(server_config=config).exists())
        self.assertFalse(GlobalSecret.objects.filter(id=legacy_secret.id).exists())
        self.assertEqual(response.json()["connection_kind"], "managed_mcp")
        mock_manager.return_value.remove_server.assert_called_once_with(str(config.id))

    @patch("api.agent.tools.mcp_manager.get_mcp_manager")
    @patch("api.services.managed_mcp_integrations.httpx.post")
    def test_disconnect_after_rollout_is_disabled_clears_managed_and_legacy_credentials(
        self,
        mock_post,
        mock_manager,
    ):
        mock_post.return_value = self._token_response()
        state = self._start().json()["state"]
        self.client.post(
            reverse("console-native-integration-callback", args=["hubspot"]),
            data=json.dumps({"authorization_code": "code", "state": state}),
            content_type="application/json",
        )
        config = MCPServerConfig.objects.get(managed_integration_key="hubspot")
        legacy_secret = GlobalSecret(
            user=self.user,
            name="Legacy HubSpot",
            secret_type=GlobalSecret.SecretType.INTEGRATION,
            domain_pattern=GlobalSecret.INTEGRATION_DOMAIN_SENTINEL,
            key=HUBSPOT_PROVIDER.secret_key,
        )
        legacy_secret.set_value(json.dumps({"access_token": "legacy-token"}))
        legacy_secret.save()
        mock_manager.reset_mock()

        with override_flag("hubspot_mcp", active=False):
            response = self.client.post(reverse("console-native-integration-revoke", args=["hubspot"]))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["revoked"])
        config.refresh_from_db()
        self.assertFalse(config.is_active)
        self.assertFalse(MCPServerOAuthCredential.objects.filter(server_config=config).exists())
        self.assertFalse(GlobalSecret.objects.filter(id=legacy_secret.id).exists())
        mock_manager.return_value.remove_server.assert_called_once_with(str(config.id))

    @patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery")
    @patch("api.services.mcp_oauth.requests.post")
    @patch("api.services.mcp_oauth.Redlock")
    @override_settings(HUBSPOT_MCP_CLIENT_SECRET="rotated-managed-secret")
    def test_refresh_resolves_rotated_managed_client_secret(self, mock_redlock, mock_post, _mock_discovery):
        config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="hubspot",
            display_name="HubSpot",
            url=HUBSPOT_MCP_PROVIDER.server_url,
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
            managed_integration_key="hubspot",
        )
        credential = MCPServerOAuthCredential.objects.create(
            server_config=config,
            user=self.user,
            expires_at=timezone.now() - timedelta(minutes=1),
            metadata={"managed_integration_key": "hubspot"},
        )
        credential.access_token = "expired-token"
        credential.refresh_token = "managed-refresh"
        credential.save()
        lock = mock_redlock.return_value
        lock.acquire.return_value = True
        mock_post.return_value = self._token_response(access_token="refreshed-access")

        result = ensure_mcp_oauth_credential(str(config.id))

        self.assertEqual(result.status, MCPOAuthStatus.USABLE)
        self.assertEqual(result.credential.access_token, "refreshed-access")
        refresh_data = mock_post.call_args.kwargs["data"]
        self.assertEqual(refresh_data["client_id"], "managed-hubspot-client-id")
        self.assertEqual(refresh_data["client_secret"], "rotated-managed-secret")
        self.assertEqual(mock_post.call_args.args[0], HUBSPOT_MCP_PROVIDER.token_endpoint)
        lock.release.assert_called_once()

    @patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery")
    @patch("api.services.mcp_oauth.requests.post")
    @patch("api.services.mcp_oauth.Redlock")
    def test_hubspot_bad_refresh_token_requires_reconnection(self, mock_redlock, mock_post, _mock_discovery):
        config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="hubspot",
            display_name="HubSpot",
            url=HUBSPOT_MCP_PROVIDER.server_url,
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
            managed_integration_key="hubspot",
        )
        credential = MCPServerOAuthCredential.objects.create(
            server_config=config,
            user=self.user,
            expires_at=timezone.now() - timedelta(minutes=1),
            metadata={"managed_integration_key": "hubspot"},
        )
        credential.access_token = "expired-token"
        credential.refresh_token = "invalid-refresh"
        credential.save()
        mock_redlock.return_value.acquire.return_value = True
        response = MagicMock(status_code=400)
        response.json.return_value = {
            "status": "BAD_REFRESH_TOKEN",
            "message": "missing or invalid refresh token",
        }
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        mock_post.return_value = response

        result = ensure_mcp_oauth_credential(str(config.id))

        self.assertEqual(result.status, MCPOAuthStatus.RECONNECT_REQUIRED)
        mock_redlock.return_value.release.assert_called_once()

    @patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery")
    def test_managed_mode_blocks_rest_auth_and_renders_mcp_skill_guidance(self, _mock_discovery):
        self.assertTrue(managed_mcp_provider_enabled("hubspot", self.user, None))
        self._create_legacy_secret()
        config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="hubspot",
            display_name="HubSpot",
            url=HUBSPOT_MCP_PROVIDER.server_url,
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
            managed_integration_key="hubspot",
        )
        credential = MCPServerOAuthCredential.objects.create(
            server_config=config,
            user=self.user,
        )
        credential.access_token = "managed-token"
        credential.save()
        with self.assertRaises(NativeIntegrationAuthError) as raised:
            apply_native_integration_auth(
                self.agent,
                "https://api.hubapi.com/crm/v3/objects/contacts",
                {"Authorization": "Bearer manually-supplied-token"},
            )
        self.assertEqual(raised.exception.code, "managed_mcp_required")
        self.assertEqual(
            resolve_managed_mcp_connection_mode("hubspot", self.user, None),
            ManagedMCPConnectionMode.MANAGED_MCP,
        )
        self.assertIn(config, agent_accessible_server_configs(self.agent))

        instructions = _hubspot_native_prompt_instructions(self.agent)
        self.assertIn("remote MCP tools", instructions)
        self.assertIn("get_user_details", instructions)
        self.assertIn("do not use `http_request`", instructions)

    def test_legacy_mode_uses_rest_guidance_and_hides_pending_managed_server(self):
        self._create_legacy_secret()
        pending_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="hubspot",
            display_name="HubSpot",
            url=HUBSPOT_MCP_PROVIDER.server_url,
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
            managed_integration_key="hubspot",
            metadata={"managed_oauth": True, "provider_key": "hubspot"},
            is_active=False,
        )

        instructions = _hubspot_native_prompt_instructions(self.agent)
        accessible_ids = {str(config.id) for config in agent_accessible_server_configs(self.agent)}
        tool_name = "mcp_hubspot_get_user_details"
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name=tool_name,
            tool_server="hubspot",
            tool_name="get_user_details",
            server_config=pending_config,
        )
        manager = MCPToolManager()
        runtime = manager._build_runtime_from_config(pending_config)
        manager._server_cache[str(pending_config.id)] = runtime
        manager._tools_cache["legacy-mode-stale-cache"] = [
            MCPToolInfo(
                config_id=str(pending_config.id),
                full_name=tool_name,
                server_name="hubspot",
                tool_name="get_user_details",
                description="Return HubSpot account details.",
                parameters={"type": "object", "properties": {}},
            )
        ]

        self.assertIn("Use `http_request` for HubSpot REST API calls", instructions)
        self.assertNotIn("remote MCP tools", instructions)
        self.assertNotIn(str(pending_config.id), accessible_ids)
        self.assertIsNone(manager.prepare_tool_for_agent(self.agent, tool_name))
        self.assertEqual(
            resolve_managed_mcp_connection_mode("hubspot", self.user, None),
            ManagedMCPConnectionMode.LEGACY_REST,
        )

    def test_hubspot_reauthorization_error_becomes_action_required(self):
        normalized = MCPErrorNormalizerRegistry.default().normalize(
            "hubspot",
            "get_user_details",
            '{"status":"REQUIRES_REAUTHORIZATION"}',
        )

        self.assertEqual(normalized["status"], "action_required")
        self.assertIn("reconnect", normalized["message"].lower())
        self.assertTrue(normalized["connect_url"].endswith("/app/integrations"))

    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    def test_connection_change_wakes_agents_with_hubspot_skill_enabled(self, mock_delay):
        PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key=HUBSPOT_MCP_PROVIDER.system_skill_key,
            is_enabled=True,
        )

        with self.captureOnCommitCallbacks(execute=True):
            triggered = trigger_agents_for_managed_mcp_change("hubspot", self.user, None)

        self.assertEqual(triggered, 1)
        mock_delay.assert_called_once_with(str(self.agent.id))

    def test_non_staff_workspace_keeps_legacy_rest_mode(self):
        non_staff = User.objects.create_user(
            username="legacy-hubspot-user",
            email="legacy-hubspot@example.com",
            password="password123",
        )
        self.client.force_login(non_staff)

        provider = next(
            item
            for item in self.client.get(reverse("console-native-integration-list")).json()["providers"]
            if item["provider_key"] == "hubspot"
        )

        self.assertEqual(provider["connection_kind"], "native_api")
        self.assertEqual(provider["scopes"], list(HUBSPOT_PROVIDER.scopes))
        self.assertFalse(MCPServerConfig.objects.filter(user=non_staff, managed_integration_key="hubspot").exists())

    def test_percentage_rollout_is_stable_for_the_workspace_owner(self):
        non_staff = User.objects.create_user(
            username="managed-hubspot-percentage",
            email="managed-hubspot-percentage@example.com",
        )
        flag = Flag.objects.get(name="hubspot_mcp")
        self.addCleanup(flag.flush)
        flag.everyone = None
        flag.percent = 50
        flag.superusers = False
        flag.staff = False
        flag.authenticated = False
        flag.save()
        flag.flush()

        with patch("waffle.models.random.uniform") as mock_random:
            decisions = [
                managed_mcp_provider_enabled("hubspot", non_staff, None)
                for _index in range(5)
            ]

        self.assertEqual(len(set(decisions)), 1)
        mock_random.assert_not_called()

    @patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery")
    def test_disabling_rollout_blocks_fresh_cached_managed_tools(self, _mock_discovery):
        config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="hubspot",
            display_name="HubSpot",
            url=HUBSPOT_MCP_PROVIDER.server_url,
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
            managed_integration_key="hubspot",
            metadata={"managed_oauth": True, "provider_key": "hubspot"},
        )
        credential = MCPServerOAuthCredential.objects.create(
            server_config=config,
            user=self.user,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        credential.access_token = "fresh-managed-token"
        credential.refresh_token = "managed-refresh-token"
        credential.save()

        tool_name = "mcp_hubspot_get_user_details"
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name=tool_name,
            tool_server="hubspot",
            tool_name="get_user_details",
            server_config=config,
        )
        manager = MCPToolManager()
        runtime = manager._build_runtime_from_config(config)
        cached_tool = MCPToolInfo(
            config_id=str(config.id),
            full_name=tool_name,
            server_name="hubspot",
            tool_name="get_user_details",
            description="Return HubSpot account details.",
            parameters={"type": "object", "properties": {}},
        )
        manager._server_cache[str(config.id)] = runtime
        manager._tools_cache["managed-rollout-test"] = [cached_tool]

        with override_flag("hubspot_mcp", active=False):
            resolved_tool = manager.prepare_tool_for_agent(self.agent, tool_name)

        self.assertIsNone(resolved_tool)

        unavailable = MCPOAuthResult(MCPOAuthStatus.TEMPORARILY_UNAVAILABLE, None)
        expired_runtime = replace(
            runtime,
            oauth_expires_at=timezone.now() - timedelta(minutes=1),
        )
        with patch(
            "api.agent.tools.mcp_manager.ensure_mcp_oauth_credential",
            return_value=unavailable,
        ):
            _prepared, validation_error = manager._ensure_runtime_oauth(expired_runtime)
        self.assertEqual(validation_error["status"], "error")
        self.assertTrue(validation_error["retryable"])

    def test_fresh_managed_runtime_token_skips_database_oauth_validation(self):
        config = self._create_managed_credential()
        credential = config.oauth_credential
        credential.access_token = "fresh-managed-token"
        credential.expires_at = timezone.now() + timedelta(hours=1)
        credential.save()
        manager = MCPToolManager()
        runtime = manager._build_runtime_from_config(config)

        with patch("api.agent.tools.mcp_manager.ensure_mcp_oauth_credential") as mock_ensure:
            prepared, auth_error = manager._ensure_runtime_oauth(runtime)

        self.assertIs(prepared, runtime)
        self.assertIsNone(auth_error)
        mock_ensure.assert_not_called()

    def test_disabling_rollout_restores_existing_legacy_rest_auth(self):
        self._create_legacy_secret()
        with override_flag("hubspot_mcp", active=False):
            mode = resolve_managed_mcp_connection_mode("hubspot", self.user, None)
            headers = apply_native_integration_auth(
                self.agent,
                "https://api.hubapi.com/crm/v3/objects/contacts",
                {},
            )

        self.assertEqual(mode, ManagedMCPConnectionMode.LEGACY_REST)
        self.assertEqual(headers["Authorization"], "Bearer legacy-rest-token")
