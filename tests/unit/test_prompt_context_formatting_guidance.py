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
    def test_peer_dm_context_marks_web_chat_as_active(self):
        agent = _make_agent(channel=CommsChannel.OTHER)
        guidance = _get_formatting_guidance(
            agent,
            implied_send_active=False,
            peer_dm_context={"peer_agent": MagicMock()},
        )
        self.assertIn("<active_channel>web_chat</active_channel>", guidance)
        self.assertIn("<web_chat>", guidance)
        self.assertIn("<email>", guidance)
        self.assertIn("<sms>", guidance)

    def test_other_channel_without_peer_dm_context_marks_generic_as_active(self):
        agent = _make_agent(channel=CommsChannel.OTHER)
        guidance = _get_formatting_guidance(agent, implied_send_active=False)
        self.assertIn("<active_channel>generic</active_channel>", guidance)
        self.assertIn("<fallback>", guidance)

    def test_web_implied_send_marks_web_chat_as_active(self):
        agent = _make_agent()  # no endpoint needed when implied_send_active is True
        guidance = _get_formatting_guidance(agent, implied_send_active=True)
        self.assertIn("<active_channel>web_chat</active_channel>", guidance)

    def test_web_channel_marks_web_chat_as_active(self):
        agent = _make_agent(channel=CommsChannel.WEB)
        guidance = _get_formatting_guidance(agent, implied_send_active=False)
        self.assertIn("<active_channel>web_chat</active_channel>", guidance)

    def test_email_channel_marks_email_as_active(self):
        agent = _make_agent(channel=CommsChannel.EMAIL)
        guidance = _get_formatting_guidance(agent, implied_send_active=False)
        self.assertIn("<active_channel>email</active_channel>", guidance)

    def test_sms_channel_marks_sms_as_active(self):
        agent = _make_agent(channel=CommsChannel.SMS)
        guidance = _get_formatting_guidance(agent, implied_send_active=False)
        self.assertIn("<active_channel>sms</active_channel>", guidance)

    def test_no_endpoint_marks_generic_as_active(self):
        agent = _make_agent(channel=None)
        guidance = _get_formatting_guidance(agent, implied_send_active=False)
        self.assertIn("<active_channel>generic</active_channel>", guidance)
