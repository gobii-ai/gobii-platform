from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.models import AgentColor, BrowserUseAgent, PersistentAgent


User = get_user_model()


@tag("batch_agent_colors")
class AgentColorPaletteTests(TestCase):
    def setUp(self):
        AgentColor.objects.all().delete()

    def test_palette_auto_seeds_single_color(self):
        palette = AgentColor.get_active_palette()

        self.assertEqual(len(palette), 1)
        self.assertEqual(palette[0].hex_value.upper(), AgentColor.DEFAULT_HEX.upper())

    @patch('api.models.AgentService.get_agents_available', return_value=100)
    def test_agents_share_default_color(self, _mock_agents_available):
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

        self.assertEqual(first.agent_color_id, second.agent_color_id)
        self.assertEqual(first.agent_color.hex_value.upper(), AgentColor.DEFAULT_HEX.upper())
