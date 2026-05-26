import asyncio
import json
from contextlib import ExitStack
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
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
from api.services.pipedream_apps import PipedreamCatalogError, enable_pipedream_apps_for_agent
from api.services.pipedream_connections import (
    PipedreamConnectedAccount,
    invalidate_pipedream_connected_accounts_cache,
    list_pipedream_connected_accounts,
)
from api.services.mcp_servers import update_agent_personal_servers
from api.agent.tools.mcp_manager import MCPToolManager
from util.analytics import AnalyticsEvent, AnalyticsSource


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
class MCPServerManagementPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="mcp-page-user",
            email="mcp-page@example.com",
            password="test-pass-123",
        )
        self.client.force_login(self.user)

    @override_settings(
        PIPEDREAM_CLIENT_ID="",
        PIPEDREAM_CLIENT_SECRET="",
        PIPEDREAM_PROJECT_ID="",
    )
    def test_management_page_hides_pipedream_data_attributes_when_unconfigured(self):
        response = self.client.get(reverse("console-mcp-servers"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-app="mcp-servers"')
        self.assertNotContains(response, 'data-pipedream-apps-url=')
        self.assertNotContains(response, 'data-pipedream-app-search-url=')

    def test_platform_management_page_requires_staff(self):
        response = self.client.get(reverse("staff-platform-mcp"))

        self.assertEqual(response.status_code, 403)

    def test_staff_platform_management_page_mounts_platform_api(self):
        staff_user = get_user_model().objects.create_user(
            username="mcp-platform-staff",
            email="mcp-platform-staff@example.com",
            password="test-pass-123",
            is_staff=True,
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse("staff-platform-mcp"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-app="mcp-servers"')
        self.assertContains(response, 'data-owner-scope="platform"')
        self.assertContains(response, 'data-owner-label="Platform"')
        self.assertContains(response, reverse("staff-platform-mcp-server-list"))
        self.assertNotContains(response, "Console Menu")
        self.assertContains(response, 'href="/staff/mcp/"')
        self.assertNotContains(response, 'data-pipedream-apps-url=')

    def test_staff_menu_links_platform_mcp_below_llm_config(self):
        staff_user = get_user_model().objects.create_user(
            username="mcp-menu-staff",
            email="mcp-menu-staff@example.com",
            password="test-pass-123",
            is_staff=True,
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse("llm-config"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        llm_index = content.index("LLM Config")
        platform_index = content.index("Platform MCP")
        self.assertLess(llm_index, platform_index)


@tag("batch_console_mcp_servers")
class PlatformMCPServerAPITests(TestCase):
    def setUp(self):
        self.staff_user = get_user_model().objects.create_user(
            username="platform-mcp-staff",
            email="platform-mcp-staff@example.com",
            password="test-pass-123",
            is_staff=True,
        )
        self.regular_user = get_user_model().objects.create_user(
            username="platform-mcp-user",
            email="platform-mcp-user@example.com",
            password="test-pass-123",
        )
        self.client.force_login(self.staff_user)

    def test_platform_list_requires_staff(self):
        self.client.force_login(self.regular_user)

        response = self.client.get(reverse("staff-platform-mcp-server-list"))

        self.assertEqual(response.status_code, 403)

    def test_platform_list_filters_to_platform_scope(self):
        platform_server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="platform-list",
            display_name="Platform List",
            url="https://platform.example.com/mcp",
        )
        MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.regular_user,
            name="user-list",
            display_name="User List",
            url="https://user.example.com/mcp",
        )

        response = self.client.get(reverse("staff-platform-mcp-server-list"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["owner_scope"], MCPServerConfig.Scope.PLATFORM)
        self.assertEqual(payload["owner_label"], "Platform")
        self.assertEqual(payload["result_count"], 1)
        self.assertEqual(payload["servers"][0]["id"], str(platform_server.id))
        self.assertEqual(payload["servers"][0]["scope"], MCPServerConfig.Scope.PLATFORM)

    @patch("console.api_views._track_org_event_for_console")
    @patch("console.api_views.get_mcp_manager")
    def test_create_platform_server_via_staff_api(self, mock_get_mcp_manager, mock_track_event):
        payload = {
            "display_name": "Platform Command",
            "command": "npx",
            "command_args": ["-y", "@example/mcp"],
            "auth_method": MCPServerConfig.AuthMethod.NONE,
            "is_active": True,
            "environment": {"API_TOKEN": "secret"},
            "headers": {"X-Platform": "1"},
            "prefetch_apps": ["Google Sheets", "greenhouse", "google_sheets", ""],
        }

        response = self.client.post(
            reverse("staff-platform-mcp-server-list"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        server = MCPServerConfig.objects.get()
        self.assertEqual(server.scope, MCPServerConfig.Scope.PLATFORM)
        self.assertIsNone(server.user)
        self.assertIsNone(server.organization)
        self.assertEqual(server.command, "npx")
        self.assertEqual(server.command_args, ["-y", "@example/mcp"])
        self.assertEqual(server.environment, {"API_TOKEN": "secret"})
        self.assertEqual(server.headers, {"X-Platform": "1"})
        self.assertEqual(server.prefetch_apps, ["google_sheets", "greenhouse"])
        mock_get_mcp_manager.return_value.refresh_server.assert_called_once_with(str(server.id))
        mock_track_event.assert_called_once()
        track_args, _track_kwargs = mock_track_event.call_args
        self.assertEqual(track_args[1], AnalyticsEvent.MCP_SERVER_CREATED)
        self.assertEqual(track_args[2]["server_scope"], MCPServerConfig.Scope.PLATFORM)
        self.assertEqual(track_args[2]["owner_scope"], MCPServerConfig.Scope.PLATFORM)

    @patch("console.api_views._track_org_event_for_console")
    @patch("console.api_views.get_mcp_manager")
    def test_update_platform_server_via_staff_api(self, mock_get_mcp_manager, mock_track_event):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="platform-update",
            display_name="Platform Update",
            command="npx",
            command_args=["old"],
        )

        response = self.client.patch(
            reverse("staff-platform-mcp-server-detail", args=[server.id]),
            data=json.dumps(
                {
                    "display_name": "Platform Updated",
                    "name": server.name,
                    "url": "https://updated.example.com/mcp",
                    "auth_method": MCPServerConfig.AuthMethod.BEARER_TOKEN,
                    "is_active": False,
                    "headers": {"Authorization": "Bearer updated"},
                    "environment": {},
                    "command": "",
                    "command_args": [],
                    "prefetch_apps": ["slack", "Google Docs"],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        server.refresh_from_db()
        self.assertEqual(server.display_name, "Platform Updated")
        self.assertEqual(server.scope, MCPServerConfig.Scope.PLATFORM)
        self.assertEqual(server.url, "https://updated.example.com/mcp")
        self.assertEqual(server.command, "")
        self.assertEqual(server.auth_method, MCPServerConfig.AuthMethod.BEARER_TOKEN)
        self.assertEqual(server.headers, {"Authorization": "Bearer updated"})
        self.assertEqual(server.prefetch_apps, ["slack", "google_docs"])
        self.assertFalse(server.is_active)
        mock_get_mcp_manager.return_value.refresh_server.assert_called_once_with(str(server.id))
        mock_track_event.assert_called_once()
        track_args, _track_kwargs = mock_track_event.call_args
        self.assertEqual(track_args[1], AnalyticsEvent.MCP_SERVER_UPDATED)
        self.assertEqual(track_args[2]["server_scope"], MCPServerConfig.Scope.PLATFORM)

    @patch("console.api_views._track_org_event_for_console")
    @patch("console.api_views.get_mcp_manager")
    def test_delete_platform_server_via_staff_api(self, mock_get_mcp_manager, mock_track_event):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="platform-delete",
            display_name="Platform Delete",
            url="https://delete.example.com/mcp",
        )

        response = self.client.delete(reverse("staff-platform-mcp-server-detail", args=[server.id]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(MCPServerConfig.objects.filter(id=server.id).exists())
        mock_get_mcp_manager.return_value.remove_server.assert_called_once_with(str(server.id))
        mock_track_event.assert_called_once()
        track_args, _track_kwargs = mock_track_event.call_args
        self.assertEqual(track_args[1], AnalyticsEvent.MCP_SERVER_DELETED)
        self.assertEqual(track_args[2]["server_scope"], MCPServerConfig.Scope.PLATFORM)

    def test_existing_owner_detail_api_still_rejects_platform_servers_for_staff(self):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="platform-existing-api",
            display_name="Platform Existing API",
            url="https://platform.example.com/mcp",
        )

        response = self.client.get(reverse("console-mcp-server-detail", args=[server.id]))

        self.assertEqual(response.status_code, 403)

    def test_staff_can_start_platform_mcp_oauth_session(self):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="platform-oauth",
            display_name="Platform OAuth",
            url="https://oauth.example.com/mcp",
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
        )

        response = self.client.post(
            reverse("console-mcp-oauth-start"),
            data=json.dumps(
                {
                    "server_config_id": str(server.id),
                    "state": "platform-state",
                    "redirect_uri": "https://testserver/console/mcp/oauth/callback/",
                    "token_endpoint": "https://oauth.example.com/token",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        session = MCPServerOAuthSession.objects.get(server_config=server)
        self.assertEqual(session.initiated_by, self.staff_user)
        self.assertIsNone(session.organization)
        self.assertIsNone(session.user)

    def test_non_staff_cannot_start_platform_mcp_oauth_session(self):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="platform-oauth-forbidden",
            display_name="Platform OAuth Forbidden",
            url="https://oauth.example.com/mcp",
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
        )
        self.client.force_login(self.regular_user)

        response = self.client.post(
            reverse("console-mcp-oauth-start"),
            data=json.dumps({"server_config_id": str(server.id)}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)


@tag("batch_console_mcp_servers")
class MCPServerTestAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="mcp-test-user",
            email="mcp-test-user@example.com",
            password="test-pass-123",
        )
        self.other_user = get_user_model().objects.create_user(
            username="mcp-test-other",
            email="mcp-test-other@example.com",
            password="test-pass-123",
        )

    def _tool(self, *, full_name="mcp_demo_search", tool_name="search"):
        return SimpleNamespace(
            full_name=full_name,
            tool_name=tool_name,
            server_name="demo",
            description="Search things",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        )

    def test_test_endpoint_requires_login(self):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="login-test",
            display_name="Login Test",
            url="https://example.com/mcp",
        )

        response = self.client.post(
            reverse("console-mcp-server-test", args=[server.id]),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 302)

    @patch("console.api_views.get_mcp_manager")
    def test_test_http_server_success_returns_tools(self, mock_get_mcp_manager):
        self.client.force_login(self.user)
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="http-test",
            display_name="HTTP Test",
            url="https://example.com/mcp",
        )
        mock_get_mcp_manager.return_value.test_server_tools.return_value = (True, [self._tool()], {})

        response = self.client.post(
            reverse("console-mcp-server-test", args=[server.id]),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertFalse(payload["sandboxed"])
        self.assertEqual(payload["tools"][0]["full_name"], "mcp_demo_search")
        self.assertEqual(payload["tools"][0]["parameters"]["properties"]["query"]["type"], "string")
        mock_get_mcp_manager.return_value.test_server_tools.assert_called_once_with(str(server.id))

    @patch("console.api_views.get_mcp_manager")
    def test_test_inactive_server_returns_bad_request(self, mock_get_mcp_manager):
        self.client.force_login(self.user)
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="inactive-test",
            display_name="Inactive Test",
            url="https://example.com/mcp",
            is_active=False,
        )

        response = self.client.post(
            reverse("console-mcp-server-test", args=[server.id]),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        mock_get_mcp_manager.assert_not_called()

    @patch("console.api_views.get_mcp_manager")
    def test_test_discovery_failure_returns_safe_details(self, mock_get_mcp_manager):
        self.client.force_login(self.user)
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="failure-test",
            display_name="Failure Test",
            url="https://example.com/mcp",
        )
        mock_get_mcp_manager.return_value.test_server_tools.return_value = (
            False,
            [],
            {
                "phase": "discover_tools",
                "error_type": "RuntimeError",
                "message": "connection refused",
                "env": {"SECRET": "redacted"},
            },
        )

        response = self.client.post(
            reverse("console-mcp-server-test", args=[server.id]),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["details"]["message"], "connection refused")
        self.assertNotIn("env", payload["details"])
        self.assertEqual(payload["tools"], [])

    def test_mcp_sync_runner_handles_running_event_loop(self):
        manager = MCPToolManager()

        async def sample():
            return "ok"

        async def run_inside_event_loop():
            return manager._run_coroutine_sync(sample())

        self.assertEqual(asyncio.run(run_inside_event_loop()), "ok")

    def test_mcp_sync_runner_avoids_cached_running_event_loop(self):
        manager = MCPToolManager()
        running_loop = SimpleNamespace(
            is_closed=lambda: False,
            is_running=lambda: True,
            run_until_complete=lambda _coroutine: (_ for _ in ()).throw(
                RuntimeError("this event loop is already running.")
            ),
        )
        manager._loop = running_loop

        async def sample():
            return "ok"

        self.assertEqual(manager._run_coroutine_sync(sample()), "ok")

    def test_existing_owner_test_api_still_rejects_platform_servers_for_staff(self):
        staff_user = get_user_model().objects.create_user(
            username="mcp-test-staff",
            email="mcp-test-staff@example.com",
            password="test-pass-123",
            is_staff=True,
        )
        self.client.force_login(staff_user)
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="platform-owner-test",
            display_name="Platform Owner Test",
            url="https://platform.example.com/mcp",
        )

        response = self.client.post(
            reverse("console-mcp-server-test", args=[server.id]),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    @patch("console.api_views.get_mcp_manager")
    def test_staff_can_test_platform_server(self, mock_get_mcp_manager):
        staff_user = get_user_model().objects.create_user(
            username="mcp-test-platform-staff",
            email="mcp-test-platform-staff@example.com",
            password="test-pass-123",
            is_staff=True,
        )
        self.client.force_login(staff_user)
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="platform-test",
            display_name="Platform Test",
            url="https://platform.example.com/mcp",
        )
        mock_get_mcp_manager.return_value.test_server_tools.return_value = (True, [self._tool()], {})

        response = self.client.post(
            reverse("staff-platform-mcp-server-test", args=[server.id]),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        mock_get_mcp_manager.return_value.test_server_tools.assert_called_once_with(str(server.id))

    def test_non_staff_cannot_test_platform_server(self):
        self.client.force_login(self.user)
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="platform-test-forbidden",
            display_name="Platform Test Forbidden",
            url="https://platform.example.com/mcp",
        )

        response = self.client.post(
            reverse("staff-platform-mcp-server-test", args=[server.id]),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    @patch("console.api_views.SandboxComputeService")
    def test_user_stdio_test_requires_agent_id(self, mock_sandbox_service):
        self.client.force_login(self.user)
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="stdio-test",
            display_name="Stdio Test",
            command="npx",
            command_args=["-y", "@example/mcp"],
        )

        response = self.client.post(
            reverse("console-mcp-server-test", args=[server.id]),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        mock_sandbox_service.assert_not_called()

    @patch("console.api_views.SandboxComputeService")
    def test_user_stdio_test_rejects_ineligible_agent_id(self, mock_sandbox_service):
        self.client.force_login(self.user)
        other_agent = _create_console_test_agent(user=self.other_user, name="Other Agent")
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="stdio-invalid-agent",
            display_name="Stdio Invalid Agent",
            command="npx",
            command_args=["-y", "@example/mcp"],
        )

        response = self.client.post(
            reverse("console-mcp-server-test", args=[server.id]),
            data=json.dumps({"agent_id": str(other_agent.id)}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        mock_sandbox_service.assert_not_called()

    @patch("console.api_views.SandboxComputeService")
    def test_user_stdio_test_uses_sandbox_discovery_with_agent_context(self, mock_sandbox_service):
        self.client.force_login(self.user)
        agent = _create_console_test_agent(user=self.user, name="Sandbox Agent")
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="stdio-valid-agent",
            display_name="Stdio Valid Agent",
            command="npx",
            command_args=["-y", "@example/mcp"],
        )
        mock_sandbox_service.return_value.discover_mcp_tools.return_value = {
            "status": "ok",
            "tools": [
                {
                    "full_name": "mcp_stdio_valid_agent_lookup",
                    "tool_name": "lookup",
                    "server_name": "stdio-valid-agent",
                    "description": "Lookup records",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
        }

        response = self.client.post(
            reverse("console-mcp-server-test", args=[server.id]),
            data=json.dumps({"agent_id": str(agent.id)}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["sandboxed"])
        self.assertEqual(payload["agent"]["id"], str(agent.id))
        self.assertEqual(payload["tools"][0]["tool_name"], "lookup")
        mock_sandbox_service.return_value.discover_mcp_tools.assert_called_once_with(
            str(server.id),
            reason="manual_test",
            agent=agent,
        )

    @patch("console.api_views.SandboxComputeService")
    def test_user_stdio_test_reports_sandbox_unavailable(self, mock_sandbox_service):
        self.client.force_login(self.user)
        agent = _create_console_test_agent(user=self.user, name="Unavailable Agent")
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="stdio-unavailable",
            display_name="Stdio Unavailable",
            command="npx",
            command_args=["-y", "@example/mcp"],
        )
        mock_sandbox_service.side_effect = RuntimeError("sandbox disabled")

        response = self.client.post(
            reverse("console-mcp-server-test", args=[server.id]),
            data=json.dumps({"agent_id": str(agent.id)}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["details"]["phase"], "sandbox_discovery")


@tag("batch_console_mcp_servers")
@override_settings(
    PIPEDREAM_CLIENT_ID="test-client-id",
    PIPEDREAM_CLIENT_SECRET="test-client-secret",
    PIPEDREAM_PROJECT_ID="test-project-id",
    PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False,
)
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

    @patch("api.services.pipedream_connections._list_pipedream_connected_accounts")
    def test_connected_account_lookup_uses_agent_app_cache(self, mock_lookup):
        agent = _create_console_test_agent(user=self.user, name="Cached Connection Agent")
        mock_lookup.side_effect = [
            [PipedreamConnectedAccount(id="apn_trello", app_slug="trello")],
            [],
        ]

        first_result = list_pipedream_connected_accounts(agent, app_slug="trello")
        cached_result = list_pipedream_connected_accounts(agent, app_slug="trello")
        invalidate_pipedream_connected_accounts_cache(agent, app_slug="trello")
        refreshed_result = list_pipedream_connected_accounts(agent, app_slug="trello")

        self.assertEqual([account.id for account in first_result], ["apn_trello"])
        self.assertEqual([account.id for account in cached_result], ["apn_trello"])
        self.assertEqual(refreshed_result, [])
        self.assertEqual(mock_lookup.call_count, 2)

    @patch("api.services.pipedream_connections._list_pipedream_connected_accounts")
    def test_connected_account_lookup_does_not_cache_empty_results(self, mock_lookup):
        agent = _create_console_test_agent(user=self.user, name="Uncached Empty Connection Agent")
        mock_lookup.side_effect = [
            [],
            [PipedreamConnectedAccount(id="apn_trello", app_slug="trello")],
        ]

        first_result = list_pipedream_connected_accounts(agent, app_slug="trello")
        second_result = list_pipedream_connected_accounts(agent, app_slug="trello")

        self.assertEqual(first_result, [])
        self.assertEqual([account.id for account in second_result], ["apn_trello"])
        self.assertEqual(mock_lookup.call_count, 2)

    @patch("console.pipedream_apps_api.PipedreamCatalogService.get_apps")
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

    @patch("console.pipedream_apps_api.PipedreamCatalogService.get_apps")
    @patch("console.pipedream_apps_api.get_mcp_manager")
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

    @patch("console.pipedream_apps_api.PipedreamCatalogService.get_apps")
    @patch("console.pipedream_apps_api.get_mcp_manager")
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

    @patch("console.pipedream_apps_api.PipedreamCatalogService.get_apps")
    @patch("console.pipedream_apps_api.get_mcp_manager")
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

    @patch("console.pipedream_apps_api.PipedreamCatalogService.get_apps")
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

    @override_settings(
        PIPEDREAM_CLIENT_ID="",
        PIPEDREAM_CLIENT_SECRET="",
        PIPEDREAM_PROJECT_ID="",
    )
    @patch("console.pipedream_apps_api.PipedreamCatalogService.get_apps")
    def test_get_returns_disabled_error_when_pipedream_is_unconfigured(self, mock_get_apps):
        response = self.client.get(self.settings_url)

        self.assertEqual(response.status_code, 503)
        self.assertIn("Pipedream integration is not configured", response.json()["error"])
        mock_get_apps.assert_not_called()

    @patch("console.pipedream_apps_api.PipedreamCatalogService.get_apps")
    @patch("console.pipedream_apps_api.get_mcp_manager")
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

    @override_settings(
        PIPEDREAM_CLIENT_ID="",
        PIPEDREAM_CLIENT_SECRET="",
        PIPEDREAM_PROJECT_ID="",
    )
    @patch("console.pipedream_apps_api.get_mcp_manager")
    def test_patch_returns_disabled_error_when_pipedream_is_unconfigured(self, mock_get_mcp_manager):
        response = self.client.patch(
            self.settings_url,
            data=json.dumps({"selected_app_slugs": ["trello"]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Pipedream integration is not configured", response.json()["error"])
        mock_get_mcp_manager.assert_not_called()

    @patch("console.pipedream_apps_api.PipedreamCatalogService.search_apps")
    def test_search_returns_results(self, mock_search_apps):
        mock_search_apps.return_value = [
            type("App", (), {"to_dict": lambda self: PipedreamAppsAPITests._app("trello")})()
        ]

        response = self.client.get(self.search_url, {"q": "trello"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["slug"], "trello")

    @patch("console.pipedream_apps_api.PipedreamCatalogService.search_apps")
    def test_search_returns_upstream_error(self, mock_search_apps):
        mock_search_apps.side_effect = PipedreamCatalogError("boom")

        response = self.client.get(self.search_url, {"q": "trello"})

        self.assertEqual(response.status_code, 502)

    @patch("api.services.pipedream_agent_apps.list_pipedream_connected_accounts")
    def test_app_agent_connections_returns_owner_agents_connected_first(self, mock_connected_accounts):
        disconnected_agent = _create_console_test_agent(user=self.user, name="Alpha Disconnected")
        connected_agent = _create_console_test_agent(user=self.user, name="Beta Connected")
        second_connected_agent = _create_console_test_agent(user=self.user, name="Gamma Connected")
        other_user = get_user_model().objects.create_user(
            username="other-pipedream-owner",
            email="other-pipedream-owner@example.com",
            password="test-pass-123",
        )
        _create_console_test_agent(user=other_user, name="Other Owner")

        def connected_accounts_for_agent(agent, *, app_slug=None):
            self.assertEqual(app_slug, "trello")
            if agent.id == connected_agent.id:
                return [SimpleNamespace(id="apn_trello", app_slug="trello")]
            if agent.id == second_connected_agent.id:
                return [SimpleNamespace(id="apn_second_trello", app_slug="trello")]
            return []

        mock_connected_accounts.side_effect = connected_accounts_for_agent

        response = self.client.get(reverse("console-pipedream-app-agent-connections", args=["trello"]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["app_slug"], "trello")
        self.assertEqual([agent["agent_id"] for agent in payload["agents"]], [
            str(connected_agent.id),
            str(second_connected_agent.id),
            str(disconnected_agent.id),
        ])
        self.assertTrue(payload["agents"][0]["connected"])
        self.assertEqual(payload["agents"][0]["account_ids"], ["apn_trello"])
        self.assertTrue(payload["agents"][1]["connected"])
        self.assertEqual(payload["agents"][1]["account_ids"], ["apn_second_trello"])
        self.assertFalse(payload["agents"][2]["connected"])

    @override_settings(
        PIPEDREAM_CLIENT_ID="",
        PIPEDREAM_CLIENT_SECRET="",
        PIPEDREAM_PROJECT_ID="",
    )
    @patch("console.pipedream_apps_api.PipedreamCatalogService.search_apps")
    def test_search_returns_disabled_error_when_pipedream_is_unconfigured(self, mock_search_apps):
        response = self.client.get(self.search_url, {"q": "trello"})

        self.assertEqual(response.status_code, 503)
        self.assertIn("Pipedream integration is not configured", response.json()["error"])
        mock_search_apps.assert_not_called()

    @patch("api.services.pipedream_agent_apps.list_pipedream_connected_accounts")
    @patch("api.services.pipedream_agent_apps.PipedreamCatalogService.search_apps")
    @patch("api.services.pipedream_agent_apps.PipedreamCatalogService.get_apps")
    def test_agent_app_list_merges_sources_and_connection_state_without_search(
        self,
        mock_get_apps,
        mock_search_apps,
        mock_connected_accounts,
    ):
        agent = _create_console_test_agent(user=self.user, name="Pipedream Agent Apps")
        PipedreamAppSelection.objects.create(user=self.user, selected_app_slugs=["trello"])
        mock_get_apps.side_effect = lambda slugs: [
            type("App", (), {"to_dict": lambda self, slug=slug: PipedreamAppsAPITests._app(slug), "slug": slug})()
            for slug in slugs
        ]
        mock_search_apps.return_value = [
            type(
                "App",
                (),
                {
                    "to_dict": lambda self: PipedreamAppsAPITests._app("slack"),
                    "slug": "slack",
                },
            )()
        ]
        mock_connected_accounts.return_value = [SimpleNamespace(id="apn_trello", app_slug="trello")]

        response = self.client.get(reverse("console-agent-pipedream-apps", args=[agent.id]))

        self.assertEqual(response.status_code, 200)
        apps = response.json()["apps"]
        self.assertEqual([app["slug"] for app in apps], ["google_sheets", "google_docs", "trello"])
        source_by_slug = {app["slug"]: app["source"] for app in apps}
        self.assertEqual(source_by_slug["google_sheets"], "built_in")
        self.assertEqual(source_by_slug["trello"], "added")
        trello = next(app for app in apps if app["slug"] == "trello")
        self.assertTrue(trello["connected"])
        self.assertEqual(trello["account_ids"], ["apn_trello"])
        mock_search_apps.assert_not_called()

    @patch("api.services.pipedream_agent_apps.list_pipedream_connected_accounts")
    @patch("api.services.pipedream_agent_apps.PipedreamCatalogService.search_apps")
    @patch("api.services.pipedream_agent_apps.PipedreamCatalogService.get_apps")
    def test_agent_app_search_returns_only_search_matches(
        self,
        mock_get_apps,
        mock_search_apps,
        mock_connected_accounts,
    ):
        agent = _create_console_test_agent(user=self.user, name="Pipedream Agent Search")
        PipedreamAppSelection.objects.create(user=self.user, selected_app_slugs=["trello"])
        mock_search_apps.return_value = [
            type(
                "App",
                (),
                {
                    "to_dict": lambda self: PipedreamAppsAPITests._app("slack"),
                    "slug": "slack",
                },
            )()
        ]
        mock_connected_accounts.return_value = []

        response = self.client.get(reverse("console-agent-pipedream-apps", args=[agent.id]), {"q": "slack"})

        self.assertEqual(response.status_code, 200)
        apps = response.json()["apps"]
        self.assertEqual([app["slug"] for app in apps], ["slack"])
        self.assertEqual(apps[0]["source"], "available")
        mock_get_apps.assert_not_called()

    @patch("api.services.pipedream_agent_apps.PipedreamCatalogService.get_app")
    @patch("api.services.pipedream_agent_apps.get_mcp_manager")
    def test_agent_app_connect_adds_non_built_in_app_and_returns_jit_url(self, mock_get_mcp_manager, mock_get_app):
        agent = _create_console_test_agent(user=self.user, name="Connect Trello")
        mock_get_app.return_value = type(
            "App",
            (),
            {"to_dict": lambda self: PipedreamAppsAPITests._app("trello")},
        )()

        response = self.client.post(reverse("console-agent-pipedream-app-connect", args=[agent.id, "trello"]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(f"/connect/pipedream/{agent.id}/trello/", payload["connect_url"])
        selection = PipedreamAppSelection.objects.get(user=self.user)
        self.assertEqual(selection.selected_app_slugs, ["trello"])
        manager = mock_get_mcp_manager.return_value
        manager.invalidate_pipedream_owner_cache.assert_called_once_with("user", str(self.user.id))
        manager.prewarm_pipedream_owner_cache.assert_called_once_with(
            "user",
            str(self.user.id),
            app_slugs=["trello"],
        )

    @patch("api.services.pipedream_agent_apps.PipedreamCatalogService.get_app")
    @patch("api.services.pipedream_agent_apps.get_mcp_manager")
    def test_agent_app_connect_leaves_built_in_app_selection_unchanged(self, mock_get_mcp_manager, mock_get_app):
        agent = _create_console_test_agent(user=self.user, name="Connect Built In")
        mock_get_app.return_value = type(
            "App",
            (),
            {"to_dict": lambda self: PipedreamAppsAPITests._app("google_sheets")},
        )()

        response = self.client.post(reverse("console-agent-pipedream-app-connect", args=[agent.id, "google_sheets"]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(PipedreamAppSelection.objects.filter(user=self.user).exists())
        mock_get_mcp_manager.assert_not_called()

    @patch("api.services.pipedream_agent_apps.delete_pipedream_connected_accounts")
    @patch("api.services.pipedream_agent_apps.list_pipedream_connected_accounts")
    def test_agent_app_disconnect_deletes_accounts_without_disabling_app_or_tools(
        self,
        mock_connected_accounts,
        mock_delete_accounts,
    ):
        agent = _create_console_test_agent(user=self.user, name="Disconnect Trello")
        PipedreamAppSelection.objects.create(user=self.user, selected_app_slugs=["trello"])
        pipedream_server = MCPServerConfig.objects.get(scope=MCPServerConfig.Scope.PLATFORM, name="pipedream")
        PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name="trello-create-card",
            tool_server="pipedream",
            tool_name="trello-create-card",
            server_config=pipedream_server,
        )
        mock_connected_accounts.return_value = [
            SimpleNamespace(id="apn_one", app_slug="trello"),
            SimpleNamespace(id="apn_two", app_slug="trello"),
        ]
        mock_delete_accounts.return_value = 2

        response = self.client.delete(reverse("console-agent-pipedream-app-connection", args=[agent.id, "trello"]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted_count"], 2)
        mock_delete_accounts.assert_called_once()
        self.assertEqual(PipedreamAppSelection.objects.get(user=self.user).selected_app_slugs, ["trello"])
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(agent=agent, tool_full_name="trello-create-card").exists()
        )

    @patch("api.services.pipedream_agent_apps.get_mcp_manager")
    def test_agent_app_remove_removes_added_app_without_deleting_connection(self, mock_get_mcp_manager):
        agent = _create_console_test_agent(user=self.user, name="Remove Trello")
        PipedreamAppSelection.objects.create(user=self.user, selected_app_slugs=["trello"])

        response = self.client.delete(reverse("console-agent-pipedream-app", args=[agent.id, "trello"]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["removed"])
        self.assertFalse(PipedreamAppSelection.objects.filter(user=self.user).exists())
        manager = mock_get_mcp_manager.return_value
        manager.invalidate_pipedream_owner_cache.assert_called_once_with("user", str(self.user.id))
        manager.prewarm_pipedream_owner_cache.assert_called_once_with(
            "user",
            str(self.user.id),
            app_slugs=[],
        )

    @patch("api.services.pipedream_agent_apps.get_mcp_manager")
    def test_agent_app_remove_rejects_built_in_app(self, mock_get_mcp_manager):
        agent = _create_console_test_agent(user=self.user, name="Remove Built In")

        response = self.client.delete(reverse("console-agent-pipedream-app", args=[agent.id, "google_sheets"]))

        self.assertEqual(response.status_code, 400)
        self.assertIn("Built-in apps cannot be removed", response.json()["error"])
        mock_get_mcp_manager.assert_not_called()


@tag("batch_console_mcp_servers")
class PipedreamAppEnablementServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="service-owner",
            email="service-owner@example.com",
            password="test-pass-123",
        )
        self.platform_server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="pipedream",
            display_name="Pipedream",
            url="https://remote.mcp.pipedream.net",
            prefetch_apps=["google_sheets", "google_docs"],
        )

    @patch("api.agent.tools.mcp_manager.get_mcp_manager")
    def test_enable_pipedream_apps_for_user_agent_updates_selection(self, mock_get_mcp_manager):
        agent = _create_console_test_agent(user=self.user, name="User Agent")

        result = enable_pipedream_apps_for_agent(
            agent,
            ["slack"],
            available_app_slugs=["slack", "hubspot"],
        )

        self.assertEqual(result["enabled"], ["slack"])
        self.assertEqual(result["already_enabled"], [])
        self.assertEqual(result["invalid"], [])
        self.assertEqual(result["effective_apps"], ["google_sheets", "google_docs", "slack"])
        selection = PipedreamAppSelection.objects.get(user=self.user)
        self.assertEqual(selection.selected_app_slugs, ["slack"])
        manager = mock_get_mcp_manager.return_value
        manager.invalidate_pipedream_owner_cache.assert_called_once_with("user", str(self.user.id))
        manager.prewarm_pipedream_owner_cache.assert_called_once_with(
            "user",
            str(self.user.id),
            app_slugs=["slack"],
        )

    @patch("api.agent.tools.mcp_manager.get_mcp_manager")
    def test_enable_pipedream_apps_for_org_agent_updates_org_selection(self, mock_get_mcp_manager):
        org = Organization.objects.create(name="Acme Org", slug="acme-org-service", created_by=self.user)
        OrganizationMembership.objects.create(
            org=org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
        )
        agent = _create_console_test_agent(user=self.user, organization=org, name="Org Agent")

        result = enable_pipedream_apps_for_agent(
            agent,
            ["hubspot"],
            available_app_slugs=["hubspot", "slack"],
        )

        self.assertEqual(result["enabled"], ["hubspot"])
        self.assertEqual(result["effective_apps"], ["google_sheets", "google_docs", "hubspot"])
        selection = PipedreamAppSelection.objects.get(organization=org)
        self.assertEqual(selection.selected_app_slugs, ["hubspot"])
        manager = mock_get_mcp_manager.return_value
        manager.invalidate_pipedream_owner_cache.assert_called_once_with("organization", str(org.id))
        manager.prewarm_pipedream_owner_cache.assert_called_once_with(
            "organization",
            str(org.id),
            app_slugs=["hubspot"],
        )

    @patch("api.agent.tools.mcp_manager.get_mcp_manager")
    def test_enable_pipedream_apps_marks_repeated_and_platform_apps_as_already_enabled(self, mock_get_mcp_manager):
        agent = _create_console_test_agent(user=self.user, name="Repeat Agent")
        PipedreamAppSelection.objects.create(user=self.user, selected_app_slugs=["slack"])

        result = enable_pipedream_apps_for_agent(
            agent,
            ["slack", "google_docs"],
            available_app_slugs=["slack", "google_docs"],
        )

        self.assertEqual(result["enabled"], [])
        self.assertEqual(result["already_enabled"], ["slack", "google_docs"])
        self.assertEqual(result["effective_apps"], ["google_sheets", "google_docs", "slack"])
        selection = PipedreamAppSelection.objects.get(user=self.user)
        self.assertEqual(selection.selected_app_slugs, ["slack"])
        manager = mock_get_mcp_manager.return_value
        manager.invalidate_pipedream_owner_cache.assert_not_called()
        manager.prewarm_pipedream_owner_cache.assert_not_called()

    @patch("api.agent.tools.mcp_manager.get_mcp_manager")
    def test_enable_pipedream_apps_reports_invalid_without_mutation(self, mock_get_mcp_manager):
        agent = _create_console_test_agent(user=self.user, name="Invalid Agent")

        result = enable_pipedream_apps_for_agent(
            agent,
            ["unknown_app"],
            available_app_slugs=["slack"],
        )

        self.assertEqual(result["enabled"], [])
        self.assertEqual(result["already_enabled"], [])
        self.assertEqual(result["invalid"], ["unknown_app"])
        self.assertEqual(result["effective_apps"], ["google_sheets", "google_docs"])
        self.assertFalse(PipedreamAppSelection.objects.filter(user=self.user).exists())
        manager = mock_get_mcp_manager.return_value
        manager.invalidate_pipedream_owner_cache.assert_not_called()
        manager.prewarm_pipedream_owner_cache.assert_not_called()

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
class MCPServerCustomEventTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="assign-custom-user",
            email="assign-custom@example.com",
            password="test-pass-123",
        )

    @patch("api.services.mcp_servers.emit_configured_custom_capi_event")
    def test_update_agent_personal_servers_emits_integration_added_custom_event(self, mock_emit_custom_event):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="personal-add",
            display_name="Personal Add",
            url="https://personal-add.example.com/mcp",
        )
        agent = _create_console_test_agent(user=self.user, name="Assignable")

        with self.captureOnCommitCallbacks(execute=True):
            update_agent_personal_servers(
                agent,
                [str(server.id)],
                actor_user_id=self.user.id,
                source=AnalyticsSource.WEB,
            )

        mock_emit_custom_event.assert_called_once()
        call_kwargs = mock_emit_custom_event.call_args.kwargs
        self.assertEqual(call_kwargs["event_name"], "IntegrationAdded")
        self.assertEqual(call_kwargs["user"], self.user)
        self.assertEqual(call_kwargs["properties"]["agent_id"], str(agent.id))
        self.assertEqual(call_kwargs["properties"]["integration_type"], "mcp")
        self.assertEqual(call_kwargs["properties"]["mcp_server_id"], str(server.id))
        self.assertEqual(call_kwargs["properties"]["mcp_server_scope"], MCPServerConfig.Scope.USER)


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

    def test_form_rejects_invalid_environment_variable_name_for_command_server(self):
        form = MCPServerConfigForm(
            data={
                "display_name": "Command Server",
                "name": "",
                "command": "npx",
                "command_args": "[]",
                "url": "",
                "metadata": "{}",
                "environment": '{"WEB_UNLOCKER_ZONE_FALLBACK=fallback-zone": "mcp_serp"}',
                "headers": "{}",
                "auth_method": MCPServerConfig.AuthMethod.NONE,
                "is_active": "on",
            },
            allow_commands=True,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("Invalid environment variable name", form.errors["environment"][0])

    def test_form_rejects_invalid_env_fallback_metadata_names(self):
        form = MCPServerConfigForm(
            data={
                "display_name": "Command Server",
                "name": "",
                "command": "npx",
                "command_args": "[]",
                "url": "",
                "metadata": '{"env_fallback": {"WEB_UNLOCKER_ZONE=bad": "LOCAL_ZONE"}}',
                "environment": "{}",
                "headers": "{}",
                "auth_method": MCPServerConfig.AuthMethod.NONE,
                "is_active": "on",
            },
            allow_commands=True,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("metadata.env_fallback", form.errors["metadata"][0])

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
