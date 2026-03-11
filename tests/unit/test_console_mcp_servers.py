import json
from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from api.models import (
    MCPServerConfig,
    MCPServerOAuthSession,
    PipedreamAppSelection,
    Organization,
    OrganizationMembership,
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentMCPServer,
)
from console.forms import MCPServerConfigForm
from api.services.pipedream_apps import PipedreamCatalogError
from util.analytics import AnalyticsEvent


def _create_console_test_agent(*, user, organization=None, name: str) -> PersistentAgent:
    with ExitStack() as stack:
        stack.enter_context(patch.object(BrowserUseAgent, "select_random_proxy", return_value=None))
        if organization is not None:
            stack.enter_context(patch.object(PersistentAgent, "_validate_org_seats", return_value=None))
        browser = BrowserUseAgent.objects.create(user=user, name=f"{name}-browser")
        return PersistentAgent.objects.create(
            user=user,
            organization=organization,
            name=name,
            charter="",
            browser_use_agent=browser,
        )


@tag("batch_console_mcp_servers")
class MCPServerListAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="api-user",
            email="api@example.com",
            password="test-pass-123",
        )

    def test_returns_user_scope_servers(self):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="personal-server",
            display_name="Personal Server",
            url="https://api.example.com/mcp",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("console-mcp-server-list"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["owner_scope"], "user")
        self.assertEqual(payload["owner_label"], self.user.username)
        self.assertEqual(payload["result_count"], 1)
        self.assertEqual(len(payload["servers"]), 1)
        record = payload["servers"][0]
        self.assertEqual(record["id"], str(server.id))
        self.assertEqual(record["scope"], MCPServerConfig.Scope.USER)
        self.assertEqual(record["scope_label"], "User")
        self.assertEqual(record["url"], "https://api.example.com/mcp")
        self.assertIn("oauth_status_url", record)
        self.assertFalse(record["oauth_pending"])
        self.assertFalse(record["oauth_connected"])

    def test_returns_organization_scope_when_context_selected(self):
        org = Organization.objects.create(
            name="Acme Org",
            slug="acme-org",
            created_by=self.user,
        )
        OrganizationMembership.objects.create(
            org=org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
        )
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.ORGANIZATION,
            organization=org,
            name="org-server",
            display_name="Org Server",
            url="https://org.example.com/mcp",
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(org.id)
        session["context_name"] = org.name
        session.save()

        response = self.client.get(reverse("console-mcp-server-list"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["owner_scope"], "organization")
        self.assertEqual(payload["owner_label"], "Acme Org")
        self.assertEqual(payload["result_count"], 1)
        record = payload["servers"][0]
        self.assertEqual(record["id"], str(server.id))
        self.assertEqual(record["scope"], MCPServerConfig.Scope.ORGANIZATION)
        self.assertEqual(record["url"], "https://org.example.com/mcp")
        self.assertFalse(record["oauth_pending"])

    def test_viewer_role_blocked_from_org_scope(self):
        org = Organization.objects.create(
            name="Viewer Org",
            slug="viewer-org",
            created_by=self.user,
        )
        OrganizationMembership.objects.create(
            org=org,
            user=self.user,
            role=OrganizationMembership.OrgRole.VIEWER,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(org.id)
        session["context_name"] = org.name
        session.save()

        response = self.client.get(reverse("console-mcp-server-list"))

        self.assertEqual(response.status_code, 403)

    def test_marks_pending_oauth_authorization(self):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="pending-server",
            display_name="Pending Server",
            url="https://pending.example.com/mcp",
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
        )
        MCPServerOAuthSession.objects.create(
            server_config=server,
            initiated_by=self.user,
            user=self.user,
            state="pending-state",
            redirect_uri="https://app.example.com/return",
            scope="openid",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("console-mcp-server-list"))

        self.assertEqual(response.status_code, 200)
        record = response.json()["servers"][0]
        self.assertTrue(record["oauth_pending"])
        self.assertFalse(record["oauth_connected"])


@tag("batch_console_mcp_servers")
class MCPServerCrudAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="crud-user",
            email="crud@example.com",
            password="test-pass-123",
        )
        self.client.force_login(self.user)

    @patch("console.api_views._track_org_event_for_console")
    @patch("console.api_views.get_mcp_manager")
    def test_create_server_via_api(self, mock_get_mcp_manager, mock_track_event):
        payload = {
            "display_name": "HTTP Server",
            "url": "https://api.example.com/mcp",
            "auth_method": MCPServerConfig.AuthMethod.NONE,
            "is_active": True,
            "headers": {"Authorization": "Bearer demo"},
        }

        response = self.client.post(
            reverse("console-mcp-server-list"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertIn("server", data)
        server = MCPServerConfig.objects.get()
        self.assertEqual(server.display_name, "HTTP Server")
        mock_get_mcp_manager.return_value.refresh_server.assert_called_once_with(str(server.id))
        mock_track_event.assert_called_once()
        track_args, track_kwargs = mock_track_event.call_args
        self.assertEqual(track_args[1], AnalyticsEvent.MCP_SERVER_CREATED)
        props = track_args[2]
        self.assertEqual(props["server_id"], str(server.id))
        self.assertEqual(props["server_scope"], MCPServerConfig.Scope.USER)
        self.assertEqual(props["owner_scope"], "user")
        self.assertTrue(props["has_url"])
        self.assertFalse(props["has_command"])
        self.assertTrue(props["is_active"])
        self.assertIsNone(track_kwargs.get("organization"))

    @patch("console.api_views._track_org_event_for_console")
    @patch("console.api_views.get_mcp_manager")
    def test_update_server_via_api(self, mock_get_mcp_manager, mock_track_event):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="server-one",
            display_name="Server One",
            url="https://one.example.com/mcp",
        )

        response = self.client.patch(
            reverse("console-mcp-server-detail", args=[server.id]),
            data=json.dumps({
                "display_name": "Updated Server",
                "name": server.name,
                "url": "https://updated.example.com/mcp",
                "auth_method": MCPServerConfig.AuthMethod.NONE,
                "is_active": False,
                "headers": {},
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        server.refresh_from_db()
        self.assertEqual(server.display_name, "Updated Server")
        self.assertFalse(server.is_active)
        mock_get_mcp_manager.return_value.refresh_server.assert_called_once_with(str(server.id))
        mock_track_event.assert_called_once()
        track_args, track_kwargs = mock_track_event.call_args
        self.assertEqual(track_args[1], AnalyticsEvent.MCP_SERVER_UPDATED)
        props = track_args[2]
        self.assertEqual(props["server_id"], str(server.id))
        self.assertEqual(props["server_scope"], MCPServerConfig.Scope.USER)
        self.assertEqual(props["owner_scope"], server.scope)
        self.assertTrue(props["has_url"])
        self.assertFalse(props["has_command"])
        self.assertFalse(props["is_active"])
        self.assertIsNone(track_kwargs.get("organization"))

    @patch("console.api_views._track_org_event_for_console")
    @patch("console.api_views.get_mcp_manager")
    def test_delete_server_via_api(self, mock_get_mcp_manager, mock_track_event):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="delete-me",
            display_name="Delete Me",
            url="https://delete.example.com/mcp",
        )

        response = self.client.delete(reverse("console-mcp-server-detail", args=[server.id]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(MCPServerConfig.objects.filter(id=server.id).exists())
        mock_get_mcp_manager.return_value.remove_server.assert_called_once_with(str(server.id))
        mock_track_event.assert_called_once()
        track_args, track_kwargs = mock_track_event.call_args
        self.assertEqual(track_args[1], AnalyticsEvent.MCP_SERVER_DELETED)
        props = track_args[2]
        self.assertEqual(props["server_id"], str(server.id))
        self.assertEqual(props["server_scope"], MCPServerConfig.Scope.USER)
        self.assertEqual(props["owner_scope"], server.scope)
        self.assertTrue(props["has_url"])
        self.assertFalse(props["has_command"])
        self.assertTrue(props["is_active"])
        self.assertIsNone(track_kwargs.get("organization"))

    def test_create_server_validation_errors(self):
        response = self.client.post(
            reverse("console-mcp-server-list"),
            data=json.dumps({"display_name": "No URL"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("errors", payload)
        self.assertIn("url", payload["errors"])

    def test_create_server_duplicate_name_returns_validation_error(self):
        MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="dup-server",
            display_name="Dup Server",
            url="https://dup.example.com/mcp",
        )

        response = self.client.post(
            reverse("console-mcp-server-list"),
            data=json.dumps(
                {
                    "display_name": "Dup Server",
                    "url": "https://another.example.com/mcp",
                    "auth_method": MCPServerConfig.AuthMethod.NONE,
                    "is_active": True,
                    "headers": {},
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("errors", payload)
        self.assertIn("name", payload["errors"])

    def test_update_server_duplicate_name_returns_validation_error(self):
        existing = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="existing-server",
            display_name="Existing Server",
            url="https://existing.example.com/mcp",
        )
        target = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="target-server",
            display_name="Target Server",
            url="https://target.example.com/mcp",
        )

        response = self.client.patch(
            reverse("console-mcp-server-detail", args=[target.id]),
            data=json.dumps(
                {
                    "display_name": "Renamed Server",
                    "name": existing.name,
                    "url": target.url,
                    "auth_method": MCPServerConfig.AuthMethod.NONE,
                    "is_active": True,
                    "headers": {},
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("errors", payload)
        self.assertIn("name", payload["errors"])


@tag("batch_console_mcp_servers")
class PipedreamAppsAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="pipedream-owner",
            email="pipedream-owner@example.com",
            password="test-pass-123",
        )
        self.client.force_login(self.user)
        self.settings_url = reverse("console-pipedream-apps")
        self.search_url = reverse("console-pipedream-app-search")
        MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="pipedream",
            display_name="Pipedream",
            url="https://remote.mcp.pipedream.net",
            prefetch_apps=["google_sheets", "google_docs"],
        )

    def _set_org_context(self, org: Organization):
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(org.id)
        session["context_name"] = org.name
        session.save()

    @staticmethod
    def _app(slug: str) -> dict[str, str]:
        return {
            "slug": slug,
            "name": slug.replace("_", " ").title(),
            "description": f"{slug} description",
            "icon_url": f"https://example.com/{slug}.png",
        }

    @patch("console.api_views.PipedreamCatalogService.get_apps")
    def test_get_returns_user_scope_apps(self, mock_get_apps):
        PipedreamAppSelection.objects.create(
            user=self.user,
            selected_app_slugs=["trello"],
        )
        mock_get_apps.side_effect = lambda slugs: [
            type("App", (), {"to_dict": lambda self, slug=slug: PipedreamAppsAPITests._app(slug)})()
            for slug in slugs
        ]

        response = self.client.get(self.settings_url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["owner_scope"], "user")
        self.assertEqual([app["slug"] for app in payload["platform_apps"]], ["google_sheets", "google_docs"])
        self.assertEqual([app["slug"] for app in payload["selected_apps"]], ["trello"])
        self.assertEqual([app["slug"] for app in payload["effective_apps"]], ["google_sheets", "google_docs", "trello"])

    @patch("console.api_views.PipedreamCatalogService.get_apps")
    @patch("console.api_views.get_mcp_manager")
    def test_patch_updates_user_scope_apps(self, mock_get_mcp_manager, mock_get_apps):
        mock_get_apps.side_effect = lambda slugs: [
            type("App", (), {"to_dict": lambda self, slug=slug: PipedreamAppsAPITests._app(slug)})()
            for slug in slugs
        ]

        response = self.client.patch(
            self.settings_url,
            data=json.dumps({"selected_app_slugs": ["trello", "trello", "slack"]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        selection = PipedreamAppSelection.objects.get(user=self.user)
        self.assertEqual(selection.selected_app_slugs, ["trello", "slack"])
        manager = mock_get_mcp_manager.return_value
        manager.invalidate_pipedream_owner_cache.assert_called_once_with("user", str(self.user.id))
        manager.prewarm_pipedream_owner_cache.assert_called_once_with(
            "user",
            str(self.user.id),
            app_slugs=["trello", "slack"],
        )

    def test_patch_rejects_non_string_app_slugs(self):
        response = self.client.patch(
            self.settings_url,
            data=json.dumps({"selected_app_slugs": ["trello", 123]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    @patch("console.api_views.PipedreamCatalogService.get_apps")
    @patch("console.api_views.get_mcp_manager")
    def test_patch_filters_platform_defaults_from_selected_apps(self, mock_get_mcp_manager, mock_get_apps):
        mock_get_apps.side_effect = lambda slugs: [
            type("App", (), {"to_dict": lambda self, slug=slug: PipedreamAppsAPITests._app(slug)})()
            for slug in slugs
        ]

        response = self.client.patch(
            self.settings_url,
            data=json.dumps({"selected_app_slugs": ["google_docs", "trello"]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        selection = PipedreamAppSelection.objects.get(user=self.user)
        self.assertEqual(selection.selected_app_slugs, ["trello"])
        payload = response.json()
        self.assertEqual([app["slug"] for app in payload["selected_apps"]], ["trello"])
        self.assertEqual(
            [app["slug"] for app in payload["effective_apps"]],
            ["google_sheets", "google_docs", "trello"],
        )
        manager = mock_get_mcp_manager.return_value
        manager.prewarm_pipedream_owner_cache.assert_called_once_with(
            "user",
            str(self.user.id),
            app_slugs=["trello"],
        )

    @patch("console.api_views.PipedreamCatalogService.get_apps")
    @patch("console.api_views.get_mcp_manager")
    def test_patch_removes_disabled_enabled_tools(self, mock_get_mcp_manager, mock_get_apps):
        mock_get_apps.side_effect = lambda slugs: [
            type("App", (), {"to_dict": lambda self, slug=slug: PipedreamAppsAPITests._app(slug)})()
            for slug in slugs
        ]
        agent = _create_console_test_agent(user=self.user, name="Cleanup Agent")
        pipedream_server = MCPServerConfig.objects.get(scope=MCPServerConfig.Scope.PLATFORM, name="pipedream")
        PipedreamAppSelection.objects.create(user=self.user, selected_app_slugs=["trello"])
        PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name="trello-create-card",
            tool_server="pipedream",
            tool_name="trello-create-card",
            server_config=pipedream_server,
        )
        PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name="google_sheets-add-row",
            tool_server="pipedream",
            tool_name="google_sheets-add-row",
            server_config=pipedream_server,
        )

        response = self.client.patch(
            self.settings_url,
            data=json.dumps({"selected_app_slugs": []}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            PersistentAgentEnabledTool.objects.filter(agent=agent, tool_full_name="trello-create-card").exists()
        )
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(agent=agent, tool_full_name="google_sheets-add-row").exists()
        )
        self.assertFalse(PipedreamAppSelection.objects.filter(user=self.user).exists())

    @patch("console.api_views.PipedreamCatalogService.get_apps")
    def test_get_returns_org_scope_apps(self, mock_get_apps):
        org = Organization.objects.create(name="Acme", slug="acme", created_by=self.user)
        OrganizationMembership.objects.create(
            org=org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
        )
        self._set_org_context(org)
        PipedreamAppSelection.objects.create(
            organization=org,
            selected_app_slugs=["notion"],
        )
        mock_get_apps.side_effect = lambda slugs: [
            type("App", (), {"to_dict": lambda self, slug=slug: PipedreamAppsAPITests._app(slug)})()
            for slug in slugs
        ]

        response = self.client.get(self.settings_url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["owner_scope"], "organization")
        self.assertEqual([app["slug"] for app in payload["selected_apps"]], ["notion"])

    @patch("console.api_views.PipedreamCatalogService.get_apps")
    @patch("console.api_views.get_mcp_manager")
    def test_patch_updates_org_scope_apps(self, mock_get_mcp_manager, mock_get_apps):
        org = Organization.objects.create(name="Acme Patch", slug="acme-patch", created_by=self.user)
        OrganizationMembership.objects.create(
            org=org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
        )
        self._set_org_context(org)
        mock_get_apps.side_effect = lambda slugs: [
            type("App", (), {"to_dict": lambda self, slug=slug: PipedreamAppsAPITests._app(slug)})()
            for slug in slugs
        ]

        response = self.client.patch(
            self.settings_url,
            data=json.dumps({"selected_app_slugs": ["hubspot"]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        selection = PipedreamAppSelection.objects.get(organization=org)
        self.assertEqual(selection.selected_app_slugs, ["hubspot"])
        manager = mock_get_mcp_manager.return_value
        manager.invalidate_pipedream_owner_cache.assert_called_once_with("organization", str(org.id))
        manager.prewarm_pipedream_owner_cache.assert_called_once_with(
            "organization",
            str(org.id),
            app_slugs=["hubspot"],
        )

    def test_org_viewer_is_blocked(self):
        org = Organization.objects.create(name="Viewer Org", slug="viewer-org-mcp", created_by=self.user)
        OrganizationMembership.objects.create(
            org=org,
            user=self.user,
            role=OrganizationMembership.OrgRole.VIEWER,
        )
        self._set_org_context(org)

        response = self.client.get(self.settings_url)

        self.assertEqual(response.status_code, 403)

    @patch("console.api_views.PipedreamCatalogService.search_apps")
    def test_search_returns_results(self, mock_search_apps):
        mock_search_apps.return_value = [
            type("App", (), {"to_dict": lambda self: PipedreamAppsAPITests._app("trello")})()
        ]

        response = self.client.get(self.search_url, {"q": "trello"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["slug"], "trello")

    @patch("console.api_views.PipedreamCatalogService.search_apps")
    def test_search_returns_upstream_error(self, mock_search_apps):
        mock_search_apps.side_effect = PipedreamCatalogError("boom")

        response = self.client.get(self.search_url, {"q": "trello"})

        self.assertEqual(response.status_code, 502)

@tag("batch_console_mcp_servers")
class MCPServerAssignmentAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="assign-user",
            email="assign@example.com",
            password="test-pass-123",
        )
        self.client.force_login(self.user)

    def _set_org_context(self, org: Organization):
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(org.id)
        session["context_name"] = org.name
        session.save()

    def test_get_assignments_user_scope(self):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="user-scope-server",
            display_name="User Scope",
            url="https://user.example.com/mcp",
        )
        agent_one = _create_console_test_agent(user=self.user, name="Alpha")
        agent_two = _create_console_test_agent(user=self.user, name="Beta")
        PersistentAgentMCPServer.objects.create(agent=agent_one, server_config=server)

        response = self.client.get(reverse("console-mcp-server-assignments", args=[server.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["server"]["id"], str(server.id))
        self.assertEqual(payload["total_agents"], 2)
        self.assertEqual(payload["assigned_count"], 1)
        records = {record["id"]: record for record in payload["agents"]}
        self.assertTrue(records[str(agent_one.id)]["assigned"])
        self.assertFalse(records[str(agent_two.id)]["assigned"])

    @patch("console.api_views.get_mcp_manager")
    def test_update_assignments_user_scope(self, mock_get_mcp_manager):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="user-update-server",
            display_name="User Update",
            url="https://update.example.com/mcp",
        )
        agent_one = _create_console_test_agent(user=self.user, name="One")
        agent_two = _create_console_test_agent(user=self.user, name="Two")
        PersistentAgentMCPServer.objects.create(agent=agent_one, server_config=server)
        PersistentAgentEnabledTool.objects.create(
            agent=agent_one,
            tool_full_name="demo.tool",
            tool_name="demo",
            server_config=server,
        )

        url = reverse("console-mcp-server-assignments", args=[server.id])
        response = self.client.post(
            url,
            data=json.dumps({"agent_ids": [str(agent_two.id)]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["assigned_count"], 1)
        self.assertEqual(payload["message"], "Assignments updated.")

        assigned_ids = {
            str(agent_id)
            for agent_id in PersistentAgentMCPServer.objects.filter(server_config=server).values_list("agent_id", flat=True)
        }
        self.assertEqual(assigned_ids, {str(agent_two.id)})
        self.assertFalse(
            PersistentAgentEnabledTool.objects.filter(agent=agent_one, server_config=server).exists()
        )
        manager = mock_get_mcp_manager.return_value
        manager.initialize.assert_not_called()
        manager.refresh_server.assert_not_called()
        manager.remove_server.assert_not_called()

    @patch("console.api_views.get_mcp_manager")
    def test_update_assignments_org_scope(self, mock_get_mcp_manager):
        org = Organization.objects.create(name="Org Assign", slug="org-assign", created_by=self.user)
        OrganizationMembership.objects.create(
            org=org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
        )
        self._set_org_context(org)
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.ORGANIZATION,
            organization=org,
            name="org-assign",
            display_name="Org Assign Server",
            url="https://org.example.com/mcp",
        )
        agent_one = _create_console_test_agent(user=self.user, organization=org, name="Org One")
        agent_two = _create_console_test_agent(user=self.user, organization=org, name="Org Two")
        PersistentAgentEnabledTool.objects.create(
            agent=agent_two,
            tool_full_name="demo.tool",
            tool_name="demo",
            server_config=server,
        )

        url = reverse("console-mcp-server-assignments", args=[server.id])
        response = self.client.post(
            url,
            data=json.dumps({"agent_ids": [str(agent_one.id)]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["assigned_count"], 1)
        records = {record["id"]: record for record in payload["agents"]}
        self.assertTrue(records[str(agent_one.id)]["assigned"])
        self.assertFalse(records[str(agent_two.id)]["assigned"])

        assigned = PersistentAgentMCPServer.objects.filter(server_config=server).values_list("agent_id", flat=True)
        self.assertEqual({str(agent_one.id)}, {str(agent_id) for agent_id in assigned})
        self.assertFalse(
            PersistentAgentEnabledTool.objects.filter(agent=agent_two, server_config=server).exists()
        )
        manager = mock_get_mcp_manager.return_value
        manager.initialize.assert_not_called()
        manager.refresh_server.assert_not_called()
        manager.remove_server.assert_not_called()

    def test_assignments_platform_scope_blocked(self):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="platform-server",
            display_name="Platform",
            url="https://platform.example.com/mcp",
        )

        response = self.client.get(reverse("console-mcp-server-assignments", args=[server.id]))

        self.assertEqual(response.status_code, 403)

    @patch("console.api_views.get_mcp_manager")
    def test_update_assignments_rejects_invalid_agents(self, mock_get_mcp_manager):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="invalid-agent-server",
            display_name="Invalid Agent",
            url="https://invalid.example.com/mcp",
        )
        url = reverse("console-mcp-server-assignments", args=[server.id])

        response = self.client.post(
            url,
            data=json.dumps({"agent_ids": ["not-a-real-id"]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid agent ids", response.content.decode())
        manager = mock_get_mcp_manager.return_value
        manager.initialize.assert_not_called()
        manager.refresh_server.assert_not_called()
        manager.remove_server.assert_not_called()


@tag("batch_console_mcp_servers")
class MCPServerConfigFormTests(TestCase):
    def test_form_rejects_command_inputs_for_user_scope(self):
        form = MCPServerConfigForm(
            data={
                "display_name": "Command Server",
                "name": "",
                "command": "echo 'hello'",
                "command_args": '["--flag"]',
                "url": "",
                "metadata": "{}",
                "environment": "{}",
                "headers": "{}",
                "auth_method": MCPServerConfig.AuthMethod.NONE,
                "is_active": "on",
            },
            allow_commands=False,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("Command-based MCP servers are managed by Gobii", form.errors["command"][0])
        self.assertIn("Provide a URL for the MCP server.", form.errors["url"][0])
        self.assertIn("Command arguments are not supported", form.errors["command_args"][0])

    def test_form_requires_url_and_strips_command_fields(self):
        user = get_user_model().objects.create_user(
            username="existing-user",
            email="existing@example.com",
            password="test-pass-123",
        )
        config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=user,
            name="existing-server",
            display_name="Existing Server",
            command="echo 'hi'",
            command_args=["--old"],
            url="",
        )

        form = MCPServerConfigForm(
            data={
                "display_name": "Existing Server",
                "name": config.name,
                "command": "",
                "command_args": "[]",
                "url": "https://example.com/mcp",
                "metadata": "{}",
                "environment": "{}",
                "headers": "{}",
                "auth_method": MCPServerConfig.AuthMethod.NONE,
                "is_active": "on",
            },
            instance=config,
            allow_commands=False,
        )

        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save(user=user)
        self.assertEqual(updated.command, "")
        self.assertEqual(updated.command_args, [])
        self.assertEqual(updated.url, "https://example.com/mcp")
        self.assertEqual(updated.auth_method, MCPServerConfig.AuthMethod.NONE)

    def test_environment_and_metadata_ignored_for_user_scope(self):
        user = get_user_model().objects.create_user(
            username="env-user",
            email="env@example.com",
            password="test-pass-123",
        )

        form = MCPServerConfigForm(
            data={
                "display_name": "Secure Server",
                "name": "",
                "command": "",
                "command_args": "[]",
                "url": "https://secure.example.com/mcp",
                "metadata": '{"timer": "30"}',
                "environment": '{"API_KEY": "secret"}',
                "headers": "{}",
                "auth_method": MCPServerConfig.AuthMethod.NONE,
                "is_active": "on",
            },
            allow_commands=False,
        )

        self.assertTrue(form.is_valid(), form.errors)
        config = form.save(user=user)
        self.assertEqual(config.environment, {})
        self.assertEqual(config.metadata, {})
        self.assertEqual(config.auth_method, MCPServerConfig.AuthMethod.NONE)

    def test_reserved_identifier_rejected_for_user_scope(self):
        form = MCPServerConfigForm(
            data={
                "display_name": "Pipedream",
                "name": "",
                "command": "",
                "command_args": "[]",
                "url": "https://user-pipedream.example",
                "metadata": "{}",
                "environment": "{}",
                "headers": "{}",
                "auth_method": MCPServerConfig.AuthMethod.NONE,
                "is_active": "on",
            },
            allow_commands=False,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("reserved for Gobii-managed integrations", form.non_field_errors()[0])
