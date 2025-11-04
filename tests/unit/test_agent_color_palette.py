from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.models import AgentColor, BrowserUseAgent, PersistentAgent, BASE_AGENT_COLOR_PALETTE


User = get_user_model()


@tag("batch_agent_colors")
class AgentColorPaletteTests(TestCase):
    def setUp(self):
        AgentColor.objects.all().delete()

    def test_palette_auto_seeds_full_base_list(self):
        palette = AgentColor.get_active_palette()

        self.assertEqual(len(palette), len(BASE_AGENT_COLOR_PALETTE))
        self.assertEqual(palette[0].hex_value, AgentColor.DEFAULT_HEX)

    @patch('api.models.AgentService.get_agents_available', return_value=100)
    def test_agents_use_unique_colors_until_palette_exhausted(self, _mock_agents_available):
        owner = User.objects.create_user(username="palette_owner", email="palette@example.com", password="pw")
        browser_agent_one = BrowserUseAgent.objects.create(user=owner, name="Palette BA 1")
        browser_agent_two = BrowserUseAgent.objects.create(user=owner, name="Palette BA 2")

        first = PersistentAgent.objects.create(
            user=owner,
            name="Palette Agent 1",
            charter="charter",
            browser_use_agent=browser_agent_one,
        )
        second = PersistentAgent.objects.create(
            user=owner,
            name="Palette Agent 2",
            charter="charter",
            browser_use_agent=browser_agent_two,
        )

        self.assertNotEqual(first.agent_color_id, second.agent_color_id)

    @patch('api.models.AgentService.get_agents_available', return_value=100)
    def test_agents_reuse_least_used_color_when_palette_full(self, _mock_agents_available):
        owner = User.objects.create_user(username="palette_owner2", email="palette2@example.com", password="pw")

        agents = []
        for index in range(len(BASE_AGENT_COLOR_PALETTE) + 1):
            browser_agent = BrowserUseAgent.objects.create(user=owner, name=f"Palette BA {index}")
            agent = PersistentAgent.objects.create(
                user=owner,
                name=f"Palette Agent {index}",
                charter="charter",
                browser_use_agent=browser_agent,
            )
            agents.append(agent)

        fallback_agent = agents[-1]
        self.assertEqual(
            fallback_agent.agent_color.hex_value,
            AgentColor.DEFAULT_HEX,
        )
