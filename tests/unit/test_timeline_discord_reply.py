"""#248: a Discord reply must carry its reply context into the web timeline.

The ingestion payload stores the full replied-to message under discord_reply_to, and the agent's
prompt context uses it — but the web timeline dropped it entirely, so a card like "have you been
there?" rendered with no indication of what "there" meant.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
)
from console.agent_chat.timeline import serialize_message_event


@tag("batch_agent_chat")
class TimelineDiscordReplyTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="reply-card@example.test",
            email="reply-card@example.test",
        )
        self.agent = PersistentAgent.objects.create(
            user=user,
            name="Relay",
            charter="Relay Discord.",
            browser_use_agent=BrowserUseAgent.objects.create(user=user, name="browser"),
        )
        self.endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.DISCORD,
            address="discord://agent/x/guild/1/channel/2",
        )
        self.conversation = PersistentAgentConversation.objects.create(
            channel=CommsChannel.DISCORD,
            address="discord://guild/1/channel/2",
        )

    def _discord_message(self, raw_payload: dict) -> PersistentAgentMessage:
        return PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.endpoint,
            conversation=self.conversation,
            is_outbound=False,
            body="have you been there?",
            raw_payload=raw_payload,
        )

    def test_reply_context_reaches_the_card(self):
        message = self._discord_message({
            "source_kind": "discord",
            "discord_author_name": "asker",
            "discord_reply_to": {
                "author_name": "Alyssa Perkins",
                "content": "honestly maybe like a 30. it's strip-mall Frederick right off 26.",
            },
        })

        payload = serialize_message_event(message)["message"]

        self.assertEqual(payload["replyTo"], {
            "authorName": "Alyssa Perkins",
            "bodyText": "honestly maybe like a 30. it's strip-mall Frederick right off 26.",
        })

    def test_an_unavailable_reply_carries_no_context(self):
        """Discord marks fetch failures; a banner with an empty quote asserts nothing useful."""
        message = self._discord_message({
            "source_kind": "discord",
            "discord_reply_to": {
                "author_name": "Alyssa Perkins",
                "content": "",
                "unavailable": True,
            },
        })

        payload = serialize_message_event(message)["message"]

        self.assertIsNone(payload["replyTo"])

    def test_a_plain_message_has_no_reply_context(self):
        message = self._discord_message({"source_kind": "discord"})

        payload = serialize_message_event(message)["message"]

        self.assertIsNone(payload["replyTo"])
