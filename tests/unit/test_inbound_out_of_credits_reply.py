from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings, tag
from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone

from api.models import (
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    BrowserUseAgent,
    CommsChannel,
    Organization,
    OrganizationMembership,
    UserPhoneNumber,
    build_web_agent_address,
    build_web_user_address,
)
from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message
from api.services.owner_execution_pause import (
    EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY,
    EXECUTION_PAUSE_REASON_TRIAL_ENDED_NON_RENEWAL,
)
from config import settings


User = get_user_model()


class PauseOwnerMixin:
    def _pause_owner(self, reason: str) -> None:
        billing = self.owner.billing
        billing.execution_paused = True
        billing.execution_pause_reason = reason
        billing.execution_paused_at = timezone.now()
        billing.save(
            update_fields=[
                "execution_paused",
                "execution_pause_reason",
                "execution_paused_at",
            ]
        )


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
@tag("batch_email")
class InboundOutOfCreditsReplyTests(PauseOwnerMixin, TestCase):
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
    @override_settings(PUBLIC_SITE_URL="https://example.com")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("tasks.services.TaskCreditService.calculate_available_tasks_for_owner", return_value=0)
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

        with self.captureOnCommitCallbacks(execute=True):
            ingest_inbound_message(CommsChannel.EMAIL, parsed)

        # Should have sent one email reply to sender and owner, and skipped processing
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(sender, mail.outbox[0].to)
        self.assertIn(self.owner.email, mail.outbox[0].to)
        self.assertIn("/console/billing/", mail.outbox[0].body)
        mock_delay.assert_not_called()

    @tag("batch_email")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("tasks.services.TaskCreditService.calculate_available_tasks_for_owner", return_value=10)
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

        with self.captureOnCommitCallbacks(execute=True):
            ingest_inbound_message(CommsChannel.EMAIL, parsed)

        # No reply email; processing was triggered
        self.assertEqual(len(mail.outbox), 0)
        mock_delay.assert_called_once()

    @tag("batch_email")
    @override_settings(PUBLIC_SITE_URL="https://example.com")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("tasks.services.TaskCreditService.calculate_available_tasks_for_owner", return_value=0)
    def test_daily_limit_email_queues_processing_without_sending_notice(
        self,
        mock_calc,
        mock_delay,
    ):
        self.agent.daily_credit_limit = 1
        self.agent.save(update_fields=["daily_credit_limit"])
        owner_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.EMAIL,
            address=self.owner.email,
            is_primary=True,
        )
        self.agent.preferred_contact_endpoint = owner_endpoint
        self.agent.save(update_fields=["preferred_contact_endpoint"])

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

        with patch.object(PersistentAgent, "get_daily_credit_remaining", return_value=Decimal("0")):
            with self.captureOnCommitCallbacks(execute=True):
                ingest_inbound_message(CommsChannel.EMAIL, parsed)

        mock_calc.assert_called_once()
        mock_delay.assert_called_once()
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(
            PersistentAgentMessage.objects.filter(
                owner_agent=self.agent,
                is_outbound=True,
            ).exists()
        )

    @tag("batch_email")
    @override_settings(PUBLIC_SITE_URL="https://example.com")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("tasks.services.TaskCreditService.calculate_available_tasks_for_owner", return_value=0)
    def test_org_owned_reply_sent_to_sender_and_owner_when_out_of_credits(self, mock_calc, mock_delay):
        org = Organization.objects.create(name="Acme", slug="acme", created_by=self.owner)
        org_billing = org.billing
        org_billing.purchased_seats = 1
        org_billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=org,
            user=self.owner,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        agent_creator = User.objects.create_user(
            username="creator",
            email="creator@example.com",
            password="pw",
        )
        self.agent.user = agent_creator
        self.agent.save(update_fields=["user"])
        member = User.objects.create_user(
            username="member",
            email="member@example.com",
            password="pw",
        )
        OrganizationMembership.objects.create(
            org=org,
            user=member,
            role=OrganizationMembership.OrgRole.MEMBER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self.agent.organization = org
        self.agent.save(update_fields=["organization"])

        parsed = ParsedMessage(
            sender=member.email,
            recipient=self.agent_email.address,
            subject="Org Subject",
            body="Hello from org",
            attachments=[],
            raw_payload={"provider": "test"},
            msg_channel=CommsChannel.EMAIL,
        )

        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True):
            ingest_inbound_message(CommsChannel.EMAIL, parsed)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(set(mail.outbox[0].to), {member.email, self.owner.email})
        self.assertNotIn(agent_creator.email, mail.outbox[0].to)
        self.assertIn("/console/billing/?context_type=organization", mail.outbox[0].body)
        self.assertIn(str(org.id), mail.outbox[0].body)
        mock_delay.assert_not_called()
        mock_calc.assert_called_once()

    @tag("batch_email")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.comms.outbound_delivery.deliver_agent_email")
    def test_paused_agent_email_sends_sender_only_reply_and_skips_processing(
        self,
        mock_deliver_email,
        mock_delay,
    ):
        self._pause_owner(EXECUTION_PAUSE_REASON_TRIAL_ENDED_NON_RENEWAL)
        parsed = ParsedMessage(
            sender=self.owner.email,
            recipient=self.agent_email.address,
            subject="Need help",
            body="Hello",
            attachments=[],
            raw_payload={"provider": "test"},
            msg_channel=CommsChannel.EMAIL,
        )

        ingest_inbound_message(CommsChannel.EMAIL, parsed)

        mock_delay.assert_not_called()
        mock_deliver_email.assert_called_once()
        outbound = mock_deliver_email.call_args.args[0]
        self.assertEqual(outbound.owner_agent, self.agent)
        self.assertEqual(outbound.to_endpoint.address, self.owner.email)
        self.assertEqual(outbound.from_endpoint, self.agent_email)
        self.assertIn("can't reply right now", outbound.raw_payload.get("subject", ""))
        self.assertIn("trial ended", outbound.body.lower())

    @tag("batch_email")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.comms.outbound_delivery.deliver_agent_email")
    def test_paused_agent_email_uses_generic_billing_copy_for_delinquency(
        self,
        mock_deliver_email,
        mock_delay,
    ):
        self._pause_owner(EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY)
        parsed = ParsedMessage(
            sender=self.owner.email,
            recipient=self.agent_email.address,
            subject="Need help",
            body="Hello",
            attachments=[],
            raw_payload={"provider": "test"},
            msg_channel=CommsChannel.EMAIL,
        )

        ingest_inbound_message(CommsChannel.EMAIL, parsed)

        mock_delay.assert_not_called()
        mock_deliver_email.assert_called_once()
        outbound = mock_deliver_email.call_args.args[0]
        self.assertIn("billing needs attention", outbound.body.lower())
        self.assertNotIn("trial ended", outbound.body.lower())

    @tag("batch_email")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.comms.outbound_delivery.deliver_agent_email")
    def test_paused_agent_email_does_not_reply_to_non_whitelisted_sender(
        self,
        mock_deliver_email,
        mock_delay,
    ):
        self._pause_owner(EXECUTION_PAUSE_REASON_TRIAL_ENDED_NON_RENEWAL)
        parsed = ParsedMessage(
            sender="external@example.com",
            recipient=self.agent_email.address,
            subject="Need help",
            body="Hello",
            attachments=[],
            raw_payload={"provider": "test"},
            msg_channel=CommsChannel.EMAIL,
        )

        ingest_inbound_message(CommsChannel.EMAIL, parsed)

        mock_delay.assert_not_called()
        mock_deliver_email.assert_not_called()


@tag("batch_sms")
class InboundDailyCreditsSmsTests(PauseOwnerMixin, TestCase):
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
    @override_settings(PUBLIC_SITE_URL="https://example.com")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_daily_limit_sms_queues_processing_without_sending_notice(
        self,
        mock_delay,
    ):
        self.agent.daily_credit_limit = 1
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

        with patch.object(PersistentAgent, "get_daily_credit_remaining", return_value=Decimal("0")):
            with self.captureOnCommitCallbacks(execute=True):
                ingest_inbound_message(CommsChannel.SMS, parsed)

        mock_delay.assert_called_once()
        self.assertFalse(
            PersistentAgentMessage.objects.filter(
                owner_agent=self.agent,
                is_outbound=True,
            ).exists()
        )

    @tag("batch_sms")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.comms.outbound_delivery.deliver_agent_sms")
    def test_paused_agent_sms_sends_sender_only_reply_and_skips_processing(
        self,
        mock_deliver_sms,
        mock_delay,
    ):
        self._pause_owner(EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY)
        parsed = ParsedMessage(
            sender=self.owner_phone,
            recipient=self.sms_endpoint.address,
            subject=None,
            body="Ping",
            attachments=[],
            raw_payload={"provider": "test"},
            msg_channel=CommsChannel.SMS,
        )

        ingest_inbound_message(CommsChannel.SMS, parsed)

        mock_delay.assert_not_called()
        mock_deliver_sms.assert_called_once()
        outbound_msg = mock_deliver_sms.call_args.args[0]
        self.assertEqual(outbound_msg.owner_agent, self.agent)
        self.assertEqual(outbound_msg.from_endpoint, self.sms_endpoint)
        self.assertEqual(outbound_msg.to_endpoint.address, self.owner_phone)
        self.assertIn("billing needs attention", outbound_msg.body.lower())

    @tag("batch_sms")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.comms.outbound_delivery.deliver_agent_sms")
    def test_paused_agent_sms_does_not_reply_to_non_whitelisted_sender(
        self,
        mock_deliver_sms,
        mock_delay,
    ):
        self._pause_owner(EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY)
        parsed = ParsedMessage(
            sender="+15557654321",
            recipient=self.sms_endpoint.address,
            subject=None,
            body="Ping",
            attachments=[],
            raw_payload={"provider": "test"},
            msg_channel=CommsChannel.SMS,
        )

        ingest_inbound_message(CommsChannel.SMS, parsed)

        mock_delay.assert_not_called()
        mock_deliver_sms.assert_not_called()


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
    @override_settings(PUBLIC_SITE_URL="https://example.com")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("tasks.services.TaskCreditService.calculate_available_tasks_for_owner", return_value=0)
    def test_daily_limit_web_queues_processing_without_sending_notice(
        self,
        mock_calc,
        mock_delay,
    ):
        self.agent.daily_credit_limit = 1
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

        with patch.object(PersistentAgent, "get_daily_credit_remaining", return_value=Decimal("0")):
            with self.captureOnCommitCallbacks(execute=True):
                ingest_inbound_message(CommsChannel.WEB, parsed)

        mock_delay.assert_called_once()
        mock_calc.assert_called_once()
        self.assertFalse(
            PersistentAgentMessage.objects.filter(
                owner_agent=self.agent,
                is_outbound=True,
            ).exists()
        )
