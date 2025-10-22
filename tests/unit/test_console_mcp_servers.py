from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from unittest.mock import patch

from api.models import MCPServerConfig
from console.forms import MCPServerConfigForm


class MCPServerConfigDeleteViewTests(TestCase):
    @tag("batch_console_mcp_servers")
    @patch("console.views.get_mcp_manager")
    def test_htmx_delete_returns_success_partial(self, mock_get_mcp_manager):
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
