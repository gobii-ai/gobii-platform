from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.models import (
    BrowserUseAgent,
    MCPServerConfig,
    Organization,
    PersistentAgent,
    PersistentAgentMCPServer,
)
from api.services import mcp_servers


User = get_user_model()


@tag('mcp_org_assignment_batch')
class MCPServerAssignmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", email="owner@example.com", password="pw")
        self.org = Organization.objects.create(name="Org", slug="org", created_by=self.user)
        billing = self.org.billing
        billing.purchased_seats = 1
        billing.save(update_fields=["purchased_seats"])
        self.browser = BrowserUseAgent.objects.create(user=self.user, name="Browser")
        self.org_agent = PersistentAgent.objects.create(
            user=self.user,
            organization=self.org,
            name="Org Agent",
            charter="Help the org",
            browser_use_agent=self.browser,
        )

    @patch("api.services.mcp_servers.sandbox_compute_enabled_for_agent", return_value=True)
    def test_org_server_requires_explicit_assignment(
        self,
        _mock_sandbox_compute_enabled_for_agent,
    ):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.ORGANIZATION,
            organization=self.org,
            name="org-server",
            display_name="Org Server",
            command="/bin/true",
        )

        overview = mcp_servers.agent_server_overview(self.org_agent)
        org_entry = next((item for item in overview if item['id'] == str(server.id)), None)
        self.assertIsNotNone(org_entry)
        self.assertFalse(org_entry['assigned'])

        accessible = mcp_servers.agent_accessible_server_configs(self.org_agent)
        self.assertFalse(any(cfg.id == server.id for cfg in accessible))

        PersistentAgentMCPServer.objects.create(agent=self.org_agent, server_config=server)

        overview_after = mcp_servers.agent_server_overview(self.org_agent)
        org_entry_after = next((item for item in overview_after if item['id'] == str(server.id)), None)
        self.assertTrue(org_entry_after['assigned'])

        accessible_after = mcp_servers.agent_accessible_server_configs(self.org_agent)
        self.assertTrue(any(cfg.id == server.id for cfg in accessible_after))

    @patch("api.services.mcp_servers.sandbox_compute_enabled_for_agent", return_value=False)
    def test_org_stdio_assignment_requires_agent_sandbox(
        self,
        _mock_sandbox_compute_enabled_for_agent,
    ):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.ORGANIZATION,
            organization=self.org,
            name="org-stdio",
            display_name="Org STDIO",
            command="/bin/true",
        )

        with self.assertRaisesRegex(ValueError, "sandbox compute"):
            mcp_servers.update_agent_org_servers(self.org_agent, [str(server.id)])

        self.assertFalse(
            PersistentAgentMCPServer.objects.filter(
                agent=self.org_agent,
                server_config=server,
            ).exists()
        )

        PersistentAgentMCPServer.objects.create(agent=self.org_agent, server_config=server)
        accessible = mcp_servers.agent_accessible_server_configs(self.org_agent)
        self.assertFalse(any(cfg.id == server.id for cfg in accessible))

    @patch("api.services.mcp_servers.sandbox_compute_enabled_for_agent", return_value=False)
    def test_personal_stdio_assignment_requires_agent_sandbox(
        self,
        _mock_sandbox_compute_enabled_for_agent,
    ):
        personal_browser = BrowserUseAgent.objects.create(user=self.user, name="Personal Browser")
        personal_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Personal Agent",
            charter="Help the user",
            browser_use_agent=personal_browser,
        )
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="personal-stdio",
            display_name="Personal STDIO",
            command="/bin/true",
        )

        with self.assertRaisesRegex(ValueError, "sandbox compute"):
            mcp_servers.update_agent_personal_servers(personal_agent, [str(server.id)])

        self.assertFalse(
            PersistentAgentMCPServer.objects.filter(
                agent=personal_agent,
                server_config=server,
            ).exists()
        )
