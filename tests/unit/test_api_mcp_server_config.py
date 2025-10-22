import json

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from api.models import MCPServerConfig


@tag("batch_api_mcp_servers")
class MCPServerConfigAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="api-user",
            email="api-user@example.com",
            password="test-pass-123",
        )
        self.client.force_login(self.user)

    def test_user_cannot_create_command_based_server(self):
        response = self.client.post(
            reverse("api:mcpserverconfig-list"),
            data=json.dumps(
                {
                    "name": "command-server",
                    "display_name": "Command Server",
                    "command": "echo 'hi'",
                    "command_args": ["--demo"],
                    "url": "",
                    "is_active": True,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("command", body)
        self.assertIn("url", body)
        self.assertEqual(body["command"][0], "Command-based MCP servers are managed by Gobii. Provide a URL instead.")
        self.assertEqual(body["url"][0], "Provide a URL for the MCP server.")

    def test_user_can_create_url_based_server_and_command_fields_stripped(self):
        response = self.client.post(
            reverse("api:mcpserverconfig-list"),
            data=json.dumps(
                {
                    "name": "url-server",
                    "display_name": "URL Server",
                    "command": "",
                    "command_args": [],
                    "url": "https://example.com/mcp",
                    "is_active": True,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["command"], "")
        self.assertEqual(body["command_args"], [])
        self.assertEqual(body["url"], "https://example.com/mcp")

        config = MCPServerConfig.objects.get(id=body["id"])
        self.assertEqual(config.command, "")
        self.assertEqual(config.command_args, [])

    def test_user_cannot_update_server_to_add_command(self):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="existing-server",
            display_name="Existing Server",
            url="https://example.com/mcp",
        )

        response = self.client.patch(
            reverse("api:mcpserverconfig-detail", args=[server.id]),
            data=json.dumps({"command": "echo 'hi'"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("command", body)
        server.refresh_from_db()
        self.assertEqual(server.command, "")
