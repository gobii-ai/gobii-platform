from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from unittest.mock import patch

from api.models import MCPServerConfig


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
