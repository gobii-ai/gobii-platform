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

TEST_AGENT_COLOR_PALETTE = [
    "#0074D4",
    "#705FE3",
    "#B35E90",
    "#90A7DC",
    "#CA4166",
    "#D7A31A",
    "#C05200",
    "#008D90",
    "#008A47",
    "#FF7999",
    "#414656",
    "#FFEECB",
    "#A5ABBD",
    "#8CED85",
    "#E3F0FF",
    "#877555",
]

User = get_user_model()


class PersistentAgentColorAssignmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='owner@example.com',
            email='owner@example.com',
            password='password123',
        )
        self.palette_hex = {
            hex_value.upper()
            for hex_value in AgentColor.objects.values_list('hex_value', flat=True)
        }
        if not self.palette_hex:
            for index, hex_value in enumerate(TEST_AGENT_COLOR_PALETTE):
                AgentColor.objects.update_or_create(
                    name=f"color_{index + 1}",
                    defaults={
                        "hex_value": hex_value,
                        "sort_order": index,
                        "is_active": True,
                    },
                )
            self.palette_hex = {
                hex_value.upper()
                for hex_value in AgentColor.objects.values_list('hex_value', flat=True)
            }

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
    def test_assigns_unique_colors_per_user(self, _mock_get_agents_available):
        first_agent = self._create_agent(self.user, "Personal Agent 1")
        second_agent = self._create_agent(self.user, "Personal Agent 2")

        self.assertIsNotNone(first_agent.agent_color_id)
        self.assertIsNotNone(second_agent.agent_color_id)
        self.assertNotEqual(first_agent.agent_color_id, second_agent.agent_color_id)
        self.assertIn(first_agent.get_display_color().upper(), self.palette_hex)
        self.assertIn(second_agent.get_display_color().upper(), self.palette_hex)

    @tag('batch_agent_colors')
    @patch('api.models.AgentService.get_agents_available', return_value=10)
    def test_assigns_unique_colors_per_organization(self, _mock_get_agents_available):
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
        self.assertNotEqual(first_agent.agent_color_id, second_agent.agent_color_id)
        self.assertIn(first_agent.get_display_color().upper(), self.palette_hex)
        self.assertIn(second_agent.get_display_color().upper(), self.palette_hex)
