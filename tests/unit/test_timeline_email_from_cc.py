"""The chat timeline must name the sending mailbox and any cc recipients.

Agents send from several custom-domain mailboxes, so "which mailbox sent this" is not answerable
from the agent name. Bcc is deliberately absent: it is never persisted on the message.
"""
from __future__ import annotations

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
class EmailFromAndCcSerializationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="sender@example.test", email="sender@example.test", password="pw-12345"
        )
        browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Sender Agent")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user, name="Sender Agent", charter="Send email.", browser_use_agent=browser_agent
        )

    def _outbound_email(self, *, sender: str, cc: list[str]) -> PersistentAgentMessage:
        from_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            owner_agent=self.agent, channel=CommsChannel.EMAIL, address=sender
        )
        conversation, _ = PersistentAgentConversation.objects.get_or_create(
            channel=CommsChannel.EMAIL,
            address="prospect@example.test",
            defaults={"display_name": "Prospect"},
        )
        message = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=from_endpoint,
            conversation=conversation,
            is_outbound=True,
            body="Following up.",
        )
        for address in cc:
            endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
                owner_agent=None, channel=CommsChannel.EMAIL, address=address
            )
            message.cc_endpoints.add(endpoint)
        return message

    def _serialized_message(self, message: PersistentAgentMessage) -> dict:
        return serialize_message_event(message)["message"]

    def test_reports_the_mailbox_that_actually_sent_it(self):
        message = self._outbound_email(sender="alpha@custom-domain.test", cc=[])

        serialized = self._serialized_message(message)

        self.assertEqual(serialized["senderAddress"], "alpha@custom-domain.test")

    def test_lists_cc_recipients(self):
        message = self._outbound_email(
            sender="alpha@custom-domain.test",
            cc=["one@example.test", "two@example.test"],
        )

        serialized = self._serialized_message(message)

        self.assertEqual(
            sorted(serialized["ccAddresses"]), ["one@example.test", "two@example.test"]
        )

    def test_cc_is_empty_rather_than_absent_when_there_are_none(self):
        message = self._outbound_email(sender="alpha@custom-domain.test", cc=[])

        serialized = self._serialized_message(message)

        self.assertEqual(serialized["ccAddresses"], [])
