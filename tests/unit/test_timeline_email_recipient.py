"""#240: a sent-email card must identify who the agent wrote to.

An email conversation is keyed by the human counterparty, so on an outbound message that address is
the recipient. It was never serialized, leaving the recipient implied only by the salutation.
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
class TimelineEmailRecipientTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="recipient-card@example.test",
            email="recipient-card@example.test",
        )
        self.agent = PersistentAgent.objects.create(
            user=user,
            name="Alpha Scout",
            charter="Send email.",
            browser_use_agent=BrowserUseAgent.objects.create(user=user, name="browser"),
        )
        self.agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="alpha.scout@my.gobii.ai",
        )

    def _email_message(self, *, is_outbound: bool, address: str, display_name: str = "") -> PersistentAgentMessage:
        conversation = PersistentAgentConversation.objects.create(
            channel=CommsChannel.EMAIL,
            address=address,
            display_name=display_name,
        )
        return PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.agent_endpoint,
            conversation=conversation,
            is_outbound=is_outbound,
            body="Derraleigh,\n\nLoved this.",
            raw_payload={"subject": "loved this"},
        )

    def test_outbound_email_reports_the_recipient_address(self):
        message = self._email_message(is_outbound=True, address="derraleigh@example.com")

        payload = serialize_message_event(message)["message"]

        self.assertEqual(payload["recipientAddress"], "derraleigh@example.com")
        self.assertIsNone(payload["recipientName"])

    def test_outbound_email_reports_a_display_name_when_one_exists(self):
        message = self._email_message(
            is_outbound=True,
            address="derraleigh@example.com",
            display_name="Derraleigh Vance",
        )

        payload = serialize_message_event(message)["message"]

        self.assertEqual(payload["recipientName"], "Derraleigh Vance")
        self.assertEqual(payload["recipientAddress"], "derraleigh@example.com")

    def test_inbound_email_has_no_recipient(self):
        """The counterparty address is the sender there, so labelling it "to" would be wrong."""
        message = self._email_message(is_outbound=False, address="cantey@example.com")

        payload = serialize_message_event(message)["message"]

        self.assertIsNone(payload["recipientAddress"])
        self.assertIsNone(payload["recipientName"])

    def test_to_endpoint_wins_over_the_conversation_label(self):
        """The conversation is keyed by its original counterparty, but a message records who it
        actually went to. When they diverge the card must follow the message, not the thread —
        showing the thread's label as "To" misattributes the send (bug #419)."""
        message = self._email_message(
            is_outbound=True,
            address="original-counterparty@example.com",
            display_name="Original Counterparty",
        )
        message.to_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="actual-recipient@example.com",
        )
        message.save(update_fields=["to_endpoint"])

        payload = serialize_message_event(message)["message"]

        self.assertEqual(payload["recipientAddress"], "actual-recipient@example.com")
        # The thread label belongs to someone else; naming the actual recipient with it would
        # assert an identity the message does not carry.
        self.assertIsNone(payload["recipientName"])

    def test_matching_to_endpoint_keeps_the_conversation_display_name(self):
        message = self._email_message(
            is_outbound=True,
            address="derraleigh@example.com",
            display_name="Derraleigh Vance",
        )
        message.to_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="Derraleigh@Example.com",
        )
        message.save(update_fields=["to_endpoint"])

        payload = serialize_message_event(message)["message"]

        self.assertEqual(payload["recipientName"], "Derraleigh Vance")
        # Endpoint addresses are normalized on save.
        self.assertEqual(payload["recipientAddress"], "derraleigh@example.com")
