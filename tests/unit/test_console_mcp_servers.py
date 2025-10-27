import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from api.models import MCPServerConfig, MCPServerOAuthSession, Organization, OrganizationMembership
from console.forms import MCPServerConfigForm
from util.analytics import AnalyticsEvent


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
        mock_get_mcp_manager.return_value.initialize.assert_called_once_with(force=True)
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
        mock_get_mcp_manager.return_value.initialize.assert_called_once_with(force=True)
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
        mock_get_mcp_manager.return_value.initialize.assert_called_once_with(force=True)
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
