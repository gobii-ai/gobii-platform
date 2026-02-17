from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.comms.outbound_delivery import deliver_agent_sms
from api.agent.tools.sms_sender import execute_send_sms
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    DeliveryStatus,
    OutboundMessageAttempt,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    SmsSuppression,
)

User = get_user_model()


@tag("batch_sms")
class SmsSuppressionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="sms-suppression-user",
            email="sms-suppression@example.com",
            password="password",
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Suppression Agent",
            charter="charter",
            browser_use_agent=self.browser_agent,
        )
        self.from_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15550001111",
            is_primary=True,
        )
        self.to_number = "+15556667777"
        self.to_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.SMS,
            address=self.to_number,
        )

    @tag("batch_sms")
    @patch("api.agent.tools.sms_sender.deliver_agent_sms")
    def test_execute_send_sms_rejects_suppressed_number(self, mock_deliver):
        SmsSuppression.objects.create(
            phone_number=self.to_number,
            is_active=True,
            source="inbound_opt_out:stop",
        )

        result = execute_send_sms(
            self.agent,
            {
                "to_number": self.to_number,
                "body": "hello there",
                "will_continue_work": False,
            },
        )

        self.assertEqual(result.get("status"), "error")
        self.assertIn("blocked", (result.get("message") or "").lower())
        mock_deliver.assert_not_called()

    @tag("batch_sms")
    @patch("api.agent.comms.outbound_delivery.Analytics.track_event")
    @patch("util.sms.send_sms")
    def test_deliver_agent_sms_blocks_suppressed_number(self, mock_send_sms, mock_track_event):
        SmsSuppression.objects.create(
            phone_number=self.to_number,
            is_active=True,
            source="twilio_error_21610",
        )
        message = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.from_ep,
            to_endpoint=self.to_ep,
            is_outbound=True,
            body="hello there",
            raw_payload={},
        )

        send_result = deliver_agent_sms(message)

        self.assertFalse(send_result)
        mock_send_sms.assert_not_called()

        attempt = OutboundMessageAttempt.objects.filter(message=message).latest("queued_at")
        self.assertEqual(attempt.status, DeliveryStatus.FAILED)
        self.assertEqual(attempt.error_code, "suppressed")

        message.refresh_from_db()
        self.assertEqual(message.latest_status, DeliveryStatus.FAILED)
        self.assertEqual(message.latest_error_code, "suppressed")
        self.assertTrue(mock_track_event.called)
