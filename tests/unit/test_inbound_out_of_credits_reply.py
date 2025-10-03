from unittest.mock import patch

from django.test import TestCase, override_settings, tag
from django.contrib.auth import get_user_model
from django.core import mail

from api.models import (
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    BrowserUseAgent,
    CommsChannel,
)
from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message
from config import settings


User = get_user_model()


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
@tag("batch_email")
class InboundOutOfCreditsReplyTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="pw",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.owner, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="Email Agent",
            charter="charter",
            browser_use_agent=self.browser_agent,
        )
        # Primary email endpoint for the agent (recipient address)
        default_domain = settings.DEFAULT_AGENT_EMAIL_DOMAIN
        self.agent_email = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=f"agent@{default_domain}",
            is_primary=True,
        )

    @tag("batch_email")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("tasks.services.TaskCreditService.calculate_available_tasks", return_value=0)
    def test_reply_sent_and_processing_skipped_when_out_of_credits(self, mock_calc, mock_delay):
        sender = self.owner.email  # owner is whitelisted by default
        parsed = ParsedMessage(
            sender=sender,
            recipient=self.agent_email.address,
            subject="Test Subject",
            body="Hello",
            attachments=[],
            raw_payload={"provider": "test"},
            msg_channel=CommsChannel.EMAIL,
        )

        mail.outbox.clear()

        ingest_inbound_message(CommsChannel.EMAIL, parsed)

        # Should have sent one email reply to sender and owner, and skipped processing
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(sender, mail.outbox[0].to)
        self.assertIn(self.owner.email, mail.outbox[0].to)
        mock_delay.assert_not_called()

    @tag("batch_email")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("tasks.services.TaskCreditService.calculate_available_tasks", return_value=10)
    def test_no_reply_and_processing_runs_when_has_credits(self, mock_calc, mock_delay):
        sender = self.owner.email
        parsed = ParsedMessage(
            sender=sender,
            recipient=self.agent_email.address,
            subject="Test Subject",
            body="Hello",
            attachments=[],
            raw_payload={"provider": "test"},
            msg_channel=CommsChannel.EMAIL,
        )

        mail.outbox.clear()

        ingest_inbound_message(CommsChannel.EMAIL, parsed)

        # No reply email; processing was triggered
        self.assertEqual(len(mail.outbox), 0)
        mock_delay.assert_called_once()
