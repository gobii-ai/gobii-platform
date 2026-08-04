import os
import uuid
from importlib import import_module
from unittest.mock import MagicMock, patch

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from api.agent.system_skills.defaults import _google_sheets_native_prompt_instructions
from api.agent.system_skills.registry import SystemSkillDefinition
from api.agent.system_skills.service import enable_system_skills
from api.agent.tools.mcp_manager import MCPToolManager, execute_mcp_tool
from api.agent.tools.tool_manager import get_enabled_tool_definitions, mark_tool_enabled_without_discovery
from api.models import (
    BrowserUseAgent,
    GlobalSecret,
    MCPServerConfig,
    NativeIntegrationRoutingLock,
    Organization,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentSystemSkillState,
)
from api.services.integration_routing import (
    PipedreamAppSupersededError,
    ensure_native_integration_routing_lock,
    get_pipedream_app_routing_status,
    get_superseded_pipedream_app_slugs,
)
from api.services.pipedream_agent_apps import start_agent_pipedream_app_connect
from api.services.pipedream_apps import (
    PipedreamAppSummary,
    enable_pipedream_apps_for_agent,
    get_owner_apps_state,
    get_effective_pipedream_app_slugs_for_agent,
    get_pipedream_app_visibility_for_agent,
    serialize_owner_apps_state,
    set_owner_selected_app_slugs,
)


User = get_user_model()


@tag("batch_native_integrations")
@override_settings(
    PIPEDREAM_PREFETCH_APPS="google_sheets,google_drive,google_docs",
    GOOGLE_DRIVE_CLIENT_ID="google-client-id",
    GOOGLE_DRIVE_CLIENT_SECRET="google-client-secret",
    GOBII_PROPRIETARY_MODE=False,
    PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False,
)
class NativeIntegrationRoutingTests(TestCase):
    def setUp(self):
        os.environ.setdefault("GOBII_ENCRYPTION_KEY", "test-key-for-native-integration-routing")
        self.user = User.objects.create_user(
            username="routing-owner",
            email="routing-owner@example.com",
            password="test-password",
        )
        self.other_user = User.objects.create_user(
            username="other-routing-owner",
            email="other-routing-owner@example.com",
            password="test-password",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Routing Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Routing Agent",
            charter="Use native Google integrations.",
            browser_use_agent=self.browser_agent,
        )

    def _create_google_secret(self, *, key="native_google_drive", user=None, organization=None):
        secret = GlobalSecret(
            user=user or (None if organization is not None else self.user),
            organization=organization,
            name="Google Drive",
            description="Native Google Drive",
            secret_type=GlobalSecret.SecretType.INTEGRATION,
            domain_pattern=GlobalSecret.INTEGRATION_DOMAIN_SENTINEL,
            key=key,
        )
        secret.set_value('{"access_token":"token"}')
        secret.save()
        return secret

    def test_routing_lock_requires_exactly_one_owner(self):
        lock = NativeIntegrationRoutingLock(provider_key="google_drive")
        with self.assertRaises(ValidationError):
            lock.full_clean()

        existing = NativeIntegrationRoutingLock.objects.create(
            provider_key="google_drive",
            user=self.user,
        )
        duplicate = NativeIntegrationRoutingLock(
            provider_key=existing.provider_key,
            user=self.user,
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

        org = Organization.objects.create(
            name="Routing Org",
            slug=f"routing-org-{uuid.uuid4().hex[:8]}",
            created_by=self.user,
        )
        lock.user = self.user
        lock.organization = org
        with self.assertRaises(ValidationError):
            lock.full_clean()

    def test_organization_routing_is_isolated_from_user_routing(self):
        org = Organization.objects.create(
            name="Routing Org",
            slug=f"routing-org-{uuid.uuid4().hex[:8]}",
            created_by=self.user,
        )
        ensure_native_integration_routing_lock("google_drive", None, org)

        self.assertEqual(
            get_superseded_pipedream_app_slugs(None, org),
            {"google_sheets", "google_drive"},
        )
        self.assertEqual(get_superseded_pipedream_app_slugs(self.user, None), set())
        self.assertEqual(get_superseded_pipedream_app_slugs(self.other_user, None), set())

    def test_lock_is_owner_scoped_and_persists_after_native_revoke(self):
        ensure_native_integration_routing_lock("google_drive", self.user, None)

        self.assertEqual(
            get_superseded_pipedream_app_slugs(self.user, None),
            {"google_sheets", "google_drive"},
        )
        self.assertEqual(get_superseded_pipedream_app_slugs(self.other_user, None), set())
        disconnected_status = get_pipedream_app_routing_status("google_sheets", self.user, None)
        self.assertTrue(disconnected_status.superseded)
        self.assertIn("Reconnect", disconnected_status.routing_message)

        secret = self._create_google_secret()
        connected_status = get_pipedream_app_routing_status("google_sheets", self.user, None)
        self.assertTrue(connected_status.native_connected)
        self.assertIn("Superseded", connected_status.routing_message)

        NativeIntegrationRoutingLock.objects.filter(user=self.user).delete()
        self.assertIn("google_sheets", get_superseded_pipedream_app_slugs(self.user, None))
        ensure_native_integration_routing_lock("google_drive", self.user, None)

        secret.delete()
        self.assertIn("google_sheets", get_superseded_pipedream_app_slugs(self.user, None))
        NativeIntegrationRoutingLock.objects.filter(user=self.user).delete()
        self.assertEqual(get_superseded_pipedream_app_slugs(self.user, None), set())

    def test_legacy_google_secret_activates_routing_without_backfilled_lock(self):
        self._create_google_secret(key="native_google_sheets")

        self.assertEqual(
            get_superseded_pipedream_app_slugs(self.user, None),
            {"google_sheets", "google_drive"},
        )

    def test_data_migration_backfills_current_and_legacy_google_secrets(self):
        org = Organization.objects.create(
            name="Backfill Org",
            slug=f"backfill-org-{uuid.uuid4().hex[:8]}",
            created_by=self.user,
        )
        self._create_google_secret(key="native_google_sheets", user=self.user)
        self._create_google_secret(key="native_google_drive", organization=org)
        migration = import_module("api.migrations.0450_nativeintegrationroutinglock")

        migration.backfill_google_routing_locks(django_apps, None)

        self.assertTrue(
            NativeIntegrationRoutingLock.objects.filter(
                provider_key="google_drive",
                user=self.user,
                organization__isnull=True,
            ).exists()
        )
        self.assertTrue(
            NativeIntegrationRoutingLock.objects.filter(
                provider_key="google_drive",
                organization=org,
                user__isnull=True,
            ).exists()
        )

    def test_effective_apps_and_selection_strip_superseded_defaults(self):
        ensure_native_integration_routing_lock("google_drive", self.user, None)

        selected = set_owner_selected_app_slugs(
            MCPServerConfig.Scope.USER,
            ["google_sheets", "google_drive", "trello"],
            owner_user=self.user,
        )

        self.assertEqual(selected, ["trello"])
        self.assertEqual(
            get_effective_pipedream_app_slugs_for_agent(self.agent),
            ["google_docs", "trello"],
        )

        cache_context = MCPToolManager()._pipedream_cache_context_for_owner(
            MCPServerConfig.Scope.USER,
            str(self.user.id),
            app_slugs=["google_sheets", "google_drive", "trello"],
        )
        self.assertEqual(cache_context.effective_app_slugs, ["google_docs", "trello"])

    def test_owner_settings_serialize_superseded_apps_separately(self):
        ensure_native_integration_routing_lock("google_drive", self.user, None)
        state = get_owner_apps_state(
            MCPServerConfig.Scope.USER,
            self.user.username,
            owner_user=self.user,
        )
        catalog = MagicMock()
        catalog.get_apps.side_effect = lambda slugs: [
            PipedreamAppSummary(
                slug=slug,
                name=slug,
                description=f"{slug} description",
                icon_url="",
            )
            for slug in slugs
        ]

        payload = serialize_owner_apps_state(state, catalog=catalog)

        self.assertEqual([app["slug"] for app in payload["effective_apps"]], ["google_docs"])
        self.assertEqual([app["slug"] for app in payload["platform_apps"]], ["google_docs"])
        self.assertEqual(payload["selected_apps"], [])
        self.assertEqual(
            [app["slug"] for app in payload["superseded_apps"]],
            ["google_drive", "google_sheets"],
        )
        self.assertTrue(
            all(app["routing_status"] == "superseded" for app in payload["superseded_apps"])
        )

    def test_connected_pipedream_account_does_not_override_supersession(self):
        ensure_native_integration_routing_lock("google_drive", self.user, None)

        visibility = get_pipedream_app_visibility_for_agent(
            self.agent,
            connected_app_slugs={"google_sheets", "google_drive"},
        )

        self.assertFalse(visibility.is_app_visible("google_sheets"))
        self.assertFalse(visibility.is_app_visible("google_drive"))
        self.assertTrue(visibility.is_app_visible("google_docs"))

    def test_enablement_reports_superseded_apps_without_mutating_selection(self):
        ensure_native_integration_routing_lock("google_drive", self.user, None)

        result = enable_pipedream_apps_for_agent(
            self.agent,
            ["google_sheets", "google_drive"],
            available_app_slugs=["google_sheets", "google_drive"],
        )

        self.assertEqual(result["superseded"], ["google_sheets", "google_drive"])
        self.assertEqual(result["invalid"], [])
        self.assertEqual(result["enabled"], [])

    def test_direct_enable_and_execution_return_superseded_error(self):
        ensure_native_integration_routing_lock("google_drive", self.user, None)

        enable_result = mark_tool_enabled_without_discovery(self.agent, "google_sheets-add-row")
        execution_result = execute_mcp_tool(self.agent, "google_sheets-add-row", {})

        self.assertEqual(enable_result["code"], "pipedream_app_superseded")
        self.assertEqual(execution_result["status"], "error")
        self.assertEqual(execution_result["code"], "pipedream_app_superseded")

    def test_targeted_preparation_and_final_manager_execution_are_blocked(self):
        ensure_native_integration_routing_lock("google_drive", self.user, None)
        manager = MCPToolManager()

        self.assertIsNone(
            manager.prepare_tool_for_agent(
                self.agent,
                "google_sheets-add-row",
                require_enabled=False,
            )
        )
        result = manager.execute_mcp_tool(self.agent, "google_sheets-add-row", {})
        self.assertEqual(result["code"], "pipedream_app_superseded")

    def test_system_skill_cannot_enable_a_superseded_required_app(self):
        ensure_native_integration_routing_lock("google_drive", self.user, None)
        definition = SystemSkillDefinition(
            skill_key="legacy_google_sheets",
            name="Legacy Google Sheets",
            search_summary="Legacy Google Sheets tools",
            tool_names=(),
            pipedream_app_slugs=("google_sheets",),
        )

        result = enable_system_skills(
            self.agent,
            [definition.skill_key],
            available_skills=[definition],
        )

        self.assertEqual(result["invalid"], [definition.skill_key])
        self.assertEqual(result["pipedream_apps"]["superseded"], ["google_sheets"])
        self.assertFalse(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key=definition.skill_key,
            ).exists()
        )

    @patch("api.agent.tools.tool_manager.is_custom_tools_available_for_agent", return_value=False)
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_stale_enabled_tool_is_not_rendered(self, mock_get_manager, _mock_custom_tools):
        ensure_native_integration_routing_lock("google_drive", self.user, None)
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="google_sheets-add-row",
            tool_server="pipedream",
            tool_name="google_sheets-add-row",
        )
        mock_manager = MagicMock()
        mock_manager.get_enabled_tools_definitions.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "google_sheets-add-row",
                    "description": "Add a row",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        mock_get_manager.return_value = mock_manager

        definitions = get_enabled_tool_definitions(self.agent)

        self.assertNotIn(
            "google_sheets-add-row",
            {definition["function"]["name"] for definition in definitions},
        )

    def test_connect_service_and_api_return_superseded_conflict(self):
        ensure_native_integration_routing_lock("google_drive", self.user, None)
        with self.assertRaises(PipedreamAppSupersededError):
            start_agent_pipedream_app_connect(self.agent, "google_sheets")

        self.client.force_login(self.user)
        response = self.client.post(
            reverse(
                "console-agent-pipedream-app-connect",
                args=[self.agent.id, "google_sheets"],
            )
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "pipedream_app_superseded")
        self.assertEqual(response.json()["replacement_provider_key"], "google_drive")

    def test_native_provider_response_exposes_superseded_slugs(self):
        ensure_native_integration_routing_lock("google_drive", self.user, None)
        self.client.force_login(self.user)

        response = self.client.get(reverse("console-native-integration-list"))

        self.assertEqual(response.status_code, 200)
        google = next(
            provider
            for provider in response.json()["providers"]
            if provider["provider_key"] == "google_drive"
        )
        self.assertEqual(
            google["superseded_pipedream_app_slugs"],
            ["google_sheets", "google_drive"],
        )

    def test_disconnected_native_skill_forbids_pipedream_fallback(self):
        ensure_native_integration_routing_lock("google_drive", self.user, None)

        prompt = _google_sheets_native_prompt_instructions(self.agent)

        self.assertIn("Do not use or search for Pipedream Google Sheets", prompt)
        self.assertIn("Native Google Drive is the required route", prompt)
