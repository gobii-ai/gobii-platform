from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from unittest.mock import patch

from api.models import MCPServerConfig
from console.forms import MCPServerConfigForm
from util.analytics import AnalyticsEvent


class MCPServerConfigDeleteViewTests(TestCase):
    @tag("batch_console_mcp_servers")
    @patch("console.views._track_org_event_for_console")
    @patch("console.views.get_mcp_manager")
    def test_htmx_delete_returns_success_partial(self, mock_get_mcp_manager, mock_track_event):
        user = get_user_model().objects.create_user(
            username="test-user",
            email="user@example.com",
            password="test-pass-123",
        )
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=user,
            name="test-server",
            display_name="Test Server",
            url="https://example.com",
        )
        self.client.force_login(user)

        response = self.client.delete(
            reverse("console-mcp-server-delete", args=[server.id]),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Test Server", content)
        self.assertIn("was deleted", content)
        self.assertEqual(
            response.headers.get("HX-Trigger"),
            '{"refreshMcpServersTable": null}',
        )
        mock_get_mcp_manager.return_value.initialize.assert_called_once_with(force=True)
        self.assertFalse(MCPServerConfig.objects.filter(id=server.id).exists())
        mock_track_event.assert_called_once()
        track_args, track_kwargs = mock_track_event.call_args
        self.assertEqual(track_args[1], AnalyticsEvent.MCP_SERVER_DELETED)
        self.assertEqual(track_args[2]["server_id"], str(server.id))
        self.assertFalse(track_args[2]["has_command"])
        self.assertTrue(track_args[2]["has_url"])
        self.assertIsNone(track_kwargs.get("organization"))


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


class MCPServerConfigAnalyticsViewTests(TestCase):
    @tag("batch_console_mcp_servers")
    @patch("console.views._track_org_event_for_console")
    @patch("console.views.get_mcp_manager")
    def test_htmx_create_tracks_analytics(self, mock_get_mcp_manager, mock_track_event):
        user = get_user_model().objects.create_user(
            username="creator",
            email="creator@example.com",
            password="test-pass-123",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("console-mcp-server-create"),
            data={
                "display_name": "New HTTP Server",
                "name": "",
                "command": "",
                "command_args": "[]",
                "url": "https://new.example.com/mcp",
                "metadata": "{}",
                "environment": "{}",
                "headers": "{}",
                "auth_method": MCPServerConfig.AuthMethod.NONE,
                "is_active": "on",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(MCPServerConfig.objects.count(), 1)
        server = MCPServerConfig.objects.get()
        self.assertEqual(server.auth_method, MCPServerConfig.AuthMethod.NONE)
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

    @tag("batch_console_mcp_servers")
    @patch("console.views._track_org_event_for_console")
    @patch("console.views.get_mcp_manager")
    def test_htmx_update_tracks_analytics(self, mock_get_mcp_manager, mock_track_event):
        user = get_user_model().objects.create_user(
            username="updater",
            email="updater@example.com",
            password="test-pass-123",
        )
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=user,
            name="existing-server",
            display_name="Existing Server",
            url="https://existing.example.com/mcp",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("console-mcp-server-edit", args=[server.id]),
            data={
                "display_name": "Updated Server",
                "name": server.name,
                "command": "",
                "command_args": "[]",
                "url": "https://updated.example.com/mcp",
                "metadata": "{}",
                "environment": "{}",
                "headers": "{}",
                "auth_method": MCPServerConfig.AuthMethod.NONE,
                "is_active": "on",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        server.refresh_from_db()
        self.assertEqual(server.url, "https://updated.example.com/mcp")
        self.assertEqual(server.auth_method, MCPServerConfig.AuthMethod.NONE)
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
        self.assertTrue(props["is_active"])
        self.assertIsNone(track_kwargs.get("organization"))
