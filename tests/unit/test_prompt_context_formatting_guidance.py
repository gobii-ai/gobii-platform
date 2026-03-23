from unittest.mock import MagicMock

from django.test import SimpleTestCase, tag

from api.agent.core.prompt_context import _get_formatting_guidance
from api.models import CommsChannel


def _make_agent(channel=None):
    """Return a minimal agent mock for _get_formatting_guidance."""
    agent = MagicMock()
    if channel is None:
        agent.preferred_contact_endpoint = None
    else:
        agent.preferred_contact_endpoint = MagicMock()
        agent.preferred_contact_endpoint.channel = channel
    return agent


@tag("batch_promptree")
class FormattingGuidanceOtherChannelTests(SimpleTestCase):
    def test_other_channel_returns_rich_markdown(self):
        # Peer DMs (OTHER) should get rich web-style Markdown guidance, not the generic fallback.
        agent = _make_agent(channel=CommsChannel.OTHER)
        guidance = _get_formatting_guidance(agent, implied_send_active=False)
        self.assertIn("Web chat formatting", guidance)
        self.assertIn("**Bold what matters**", guidance)
        self.assertIn("Make it feel designed", guidance)

    def test_other_channel_does_not_return_generic_fallback(self):
        agent = _make_agent(channel=CommsChannel.OTHER)
        guidance = _get_formatting_guidance(agent, implied_send_active=False)
        self.assertNotIn("Formatting by channel", guidance)

    def test_web_implied_send_returns_rich_markdown(self):
        agent = _make_agent()  # no endpoint needed when implied_send_active is True
        guidance = _get_formatting_guidance(agent, implied_send_active=True)
        self.assertIn("Web chat formatting", guidance)
        self.assertIn("Make it feel designed", guidance)

    def test_no_endpoint_returns_generic_fallback(self):
        agent = _make_agent(channel=None)
        guidance = _get_formatting_guidance(agent, implied_send_active=False)
        self.assertIn("Formatting by channel", guidance)
