from unittest.mock import Mock, patch

from django.test import TestCase, override_settings, tag
from django.contrib.auth import get_user_model
from django.core import mail

from api.models import (
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    BrowserUseAgent,
    CommsChannel,
    DeliveryStatus,
    UserPhoneNumber,
    build_web_agent_address,
    build_web_user_address,
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

    @tag("batch_email")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("tasks.services.TaskCreditService.calculate_available_tasks", return_value=10)
    def test_daily_limit_reply_sent_to_sender(self, mock_calc, mock_delay):
        self.agent.daily_credit_limit = 0
        self.agent.save(update_fields=["daily_credit_limit"])

        sender = self.owner.email
        parsed = ParsedMessage(
            sender=sender,
            recipient=self.agent_email.address,
            subject="Status",
            body="Checking in",
            attachments=[],
            raw_payload={"provider": "test"},
            msg_channel=CommsChannel.EMAIL,
        )

        mail.outbox.clear()

        with patch("django.contrib.sites.models.Site.objects.get_current") as mock_site:
            mock_site.return_value = Mock(domain="example.com")
            ingest_inbound_message(CommsChannel.EMAIL, parsed)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(set(mail.outbox[0].to), {sender})
        expected_link = f"https://example.com/console/agents/{self.agent.id}/"
        self.assertIn(expected_link, mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].subject, f"{self.agent.name} hit today's task limit")
        mock_calc.assert_called_once()
        mock_delay.assert_not_called()


@tag("batch_sms")
class InboundDailyCreditsSmsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner_sms",
            email="owner_sms@example.com",
            password="pw",
        )
        self.owner_phone = "+15551234567"
        UserPhoneNumber.objects.create(
            user=self.owner,
            phone_number=self.owner_phone,
            is_verified=True,
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.owner, name="BA SMS")
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="SMS Agent",
            charter="charter",
            browser_use_agent=self.browser_agent,
        )
        self.sms_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15550009999",
            is_primary=True,
        )

    @tag("batch_sms")
    def test_daily_limit_sms_notice_sent(self):
        self.agent.daily_credit_limit = 0
        self.agent.save(update_fields=["daily_credit_limit"])

        parsed = ParsedMessage(
            sender=self.owner_phone,
            recipient=self.sms_endpoint.address,
            subject=None,
            body="Ping",
            attachments=[],
            raw_payload={"provider": "test"},
            msg_channel=CommsChannel.SMS,
        )

        with patch("django.contrib.sites.models.Site.objects.get_current") as mock_site, \
             patch("api.agent.tasks.process_agent_events_task.delay") as mock_delay, \
             patch("api.agent.comms.message_service.deliver_agent_sms") as mock_deliver_sms:
            mock_site.return_value = Mock(domain="example.com")
            ingest_inbound_message(CommsChannel.SMS, parsed)

        mock_delay.assert_not_called()
        mock_deliver_sms.assert_called_once()
        outbound_msg = mock_deliver_sms.call_args[0][0]
        expected_link = f"https://example.com/console/agents/{self.agent.id}/"
        self.assertIn(expected_link, outbound_msg.body)
        self.assertEqual(outbound_msg.from_endpoint, self.sms_endpoint)
        self.assertEqual(outbound_msg.to_endpoint.address, self.owner_phone)
        self.assertTrue(outbound_msg.is_outbound)
        self.assertEqual(outbound_msg.owner_agent, self.agent)


@tag("batch_agent_chat")
class InboundDailyCreditsWebChatTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner_web",
            email="owner_web@example.com",
            password="pw",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.owner, name="BA WEB")
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="Web Agent",
            charter="charter",
            browser_use_agent=self.browser_agent,
        )
        self.web_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=build_web_agent_address(self.agent.id),
            is_primary=True,
        )

    @tag("batch_agent_chat")
    def test_daily_limit_web_notice_sent(self):
        self.agent.daily_credit_limit = 0
        self.agent.save(update_fields=["daily_credit_limit"])

        sender_address = build_web_user_address(self.owner.id, self.agent.id)
        recipient_address = build_web_agent_address(self.agent.id)
        parsed = ParsedMessage(
            sender=sender_address,
            recipient=recipient_address,
            subject=None,
            body="Hello",
            attachments=[],
            raw_payload={"provider": "test"},
            msg_channel=CommsChannel.WEB,
        )

        with patch("django.contrib.sites.models.Site.objects.get_current") as mock_site, \
             patch("api.agent.tasks.process_agent_events_task.delay") as mock_delay:
            mock_site.return_value = Mock(domain="example.com")
            ingest_inbound_message(CommsChannel.WEB, parsed)

        mock_delay.assert_not_called()
        outbound = (
            PersistentAgentMessage.objects.filter(owner_agent=self.agent, is_outbound=True)
            .order_by("-timestamp")
            .first()
        )
        self.assertIsNotNone(outbound)
        expected_link = f"https://example.com/console/agents/{self.agent.id}/"
        self.assertIn(expected_link, outbound.body)
        self.assertEqual(outbound.raw_payload.get("source"), "daily_credit_limit_notice")
        outbound.refresh_from_db()
        self.assertEqual(outbound.latest_status, DeliveryStatus.DELIVERED)
