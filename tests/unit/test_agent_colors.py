from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.models import (
    AgentColor,
    BrowserUseAgent,
    Organization,
    OrganizationMembership,
    PersistentAgent,
)

User = get_user_model()


class PersistentAgentColorAssignmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='owner@example.com',
            email='owner@example.com',
            password='password123',
        )
        AgentColor.objects.all().delete()
        AgentColor.get_active_palette()  # Seed default color.

    def _create_agent(self, user, name: str, organization: Organization | None = None) -> PersistentAgent:
        browser_agent = BrowserUseAgent.objects.create(
            user=user,
            name=f"{name}-browser",
        )
        agent = PersistentAgent.objects.create(
            user=user,
            organization=organization,
            name=name,
            charter="",
            browser_use_agent=browser_agent,
        )
        return PersistentAgent.objects.get(pk=agent.pk)

    @tag('batch_agent_colors')
    @patch('api.models.AgentService.get_agents_available', return_value=10)
    def test_assigns_default_color_per_user(self, _mock_get_agents_available):
        first_agent = self._create_agent(self.user, "Personal Agent 1")
        second_agent = self._create_agent(self.user, "Personal Agent 2")

        self.assertIsNotNone(first_agent.agent_color_id)
        self.assertIsNotNone(second_agent.agent_color_id)
        self.assertEqual(first_agent.agent_color_id, second_agent.agent_color_id)
        self.assertEqual(first_agent.get_display_color().upper(), AgentColor.DEFAULT_HEX.upper())
        self.assertEqual(second_agent.get_display_color().upper(), AgentColor.DEFAULT_HEX.upper())

    @tag('batch_agent_colors')
    @patch('api.models.AgentService.get_agents_available', return_value=10)
    def test_assigns_default_color_per_organization(self, _mock_get_agents_available):
        organization = Organization.objects.create(
            name="Acme Org",
            slug="acme-org",
            plan="pro",
            created_by=self.user,
        )
        OrganizationMembership.objects.create(
            org=organization,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
        )
        billing = organization.billing
        billing.purchased_seats = 5
        billing.save(update_fields=['purchased_seats'])

        first_agent = self._create_agent(self.user, "Org Agent 1", organization=organization)
        second_agent = self._create_agent(self.user, "Org Agent 2", organization=organization)

        self.assertIsNotNone(first_agent.agent_color_id)
        self.assertIsNotNone(second_agent.agent_color_id)
        self.assertEqual(first_agent.agent_color_id, second_agent.agent_color_id)
        self.assertEqual(first_agent.get_display_color().upper(), AgentColor.DEFAULT_HEX.upper())
        self.assertEqual(second_agent.get_display_color().upper(), AgentColor.DEFAULT_HEX.upper())
