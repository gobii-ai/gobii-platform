import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.http import HttpResponse

from api.models import (
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    OutboundMessageAttempt,
    CommsChannel,
    BrowserUseAgent,
    DeliveryStatus,
)
from api.webhooks import email_webhook, sms_status_webhook
from config import settings

User = get_user_model()


class EmailWebhookTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.owner = User.objects.create_user(
            username="testowner", email="owner@example.com", password="password"
        )
        self.non_owner = User.objects.create_user(
            username="nonowner", email="nonowner@example.com", password="password"
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.owner, name="Test Browser Agent")
        self.agent = PersistentAgent.objects.create(
            user=self.owner, name="Test Agent", charter="Test charter", browser_use_agent=self.browser_agent
        )
        self.agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent@my.gobii.ai",
        )

    def _create_postmark_request(self, from_email, to_email, subject="Test Subject", body="Test Body"):
        """Helper to create a mock request with a Postmark-style JSON payload."""
        payload = {
            "From": from_email,
            "To": to_email,
            "Subject": subject,
            "TextBody": body,
        }
        request = self.factory.post(
            "/api/webhooks/inbound/email/",
            data=json.dumps(payload),
            content_type="application/json",
            query_params={
                "t": settings.POSTMARK_INCOMING_WEBHOOK_TOKEN,
            }
        )
        return request

    @patch("api.webhooks.ingest_inbound_message")
    def test_email_from_owner_is_accepted(self, mock_ingest):
        """Verify that an email from the agent's owner is processed."""
        request = self._create_postmark_request(
            from_email=self.owner.email, to_email=self.agent_endpoint.address
        )
        response: HttpResponse = email_webhook(request)

        self.assertEqual(response.status_code, 200)
        mock_ingest.assert_called_once()
        self.assertEqual(mock_ingest.call_args[0][0], CommsChannel.EMAIL)

    @patch("api.webhooks.ingest_inbound_message")
    @patch("api.webhooks.logger.info")
    def test_email_from_non_owner_is_discarded(self, mock_logger, mock_ingest):
        """Verify that an email from a non-owner is discarded and logged."""
        request = self._create_postmark_request(
            from_email=self.non_owner.email, to_email=self.agent_endpoint.address
        )
        response: HttpResponse = email_webhook(request)

        self.assertEqual(response.status_code, 200)
        mock_ingest.assert_not_called()
        mock_logger.assert_called_with(
            f"Discarding email from non-whitelisted sender '{self.non_owner.email}' to agent '{self.agent.name}'."
        )

    @patch("api.webhooks.ingest_inbound_message")
    @patch("api.webhooks.logger.info")
    def test_email_from_owner_with_display_name_is_accepted(self, mock_logger, mock_ingest):
        """Verify that a 'From' address with a display name is parsed correctly."""
        from_address = f'"Test Owner" <{self.owner.email}>'
        request = self._create_postmark_request(
            from_email=from_address, to_email=self.agent_endpoint.address
        )
        response: HttpResponse = email_webhook(request)

        self.assertEqual(response.status_code, 200)
        mock_ingest.assert_called_once()

    @patch("api.webhooks.ingest_inbound_message")
    @patch("api.webhooks.logger.info")
    def test_email_to_unroutable_address_is_discarded(self, mock_logger, mock_ingest):
        """Verify that an email to a non-existent agent address is discarded."""
        request = self._create_postmark_request(
            from_email=self.owner.email, to_email="nonexistent@my.gobii.ai"
        )
        response: HttpResponse = email_webhook(request)

        self.assertEqual(response.status_code, 200)
        mock_ingest.assert_not_called()
        mock_logger.assert_called_with(
            "Discarding email to unroutable address: nonexistent@my.gobii.ai"
        )


class SmsStatusWebhookTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username="smsuser",
            email="sms@example.com",
            password="password",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="SMS Agent",
            charter="charter",
            browser_use_agent=self.browser_agent,
        )
        self.from_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15558675309",
        )
        self.to_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15558675310",
        )
        self.message = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.from_ep,
            to_endpoint=self.to_ep,
            is_outbound=True,
            body="hi",
            raw_payload={},
        )
        self.attempt = OutboundMessageAttempt.objects.create(
            message=self.message,
            provider="twilio",
            provider_message_id="SM123",
            status=DeliveryStatus.SENT,
        )

    def _req(self, status, code=None):
        data = {
            "MessageSid": "SM123",
            "MessageStatus": status,
        }
        if code:
            data["ErrorCode"] = code
        return self.factory.post(
            f"/api/v1/webhooks/status/sms/?t={settings.TWILIO_INCOMING_WEBHOOK_TOKEN}",
            data=data
        )

    def test_delivered_status_updates_message(self):
        request = self._req("delivered")
        resp: HttpResponse = sms_status_webhook(request)
        self.assertEqual(resp.status_code, 200)
        self.message.refresh_from_db()
        self.attempt.refresh_from_db()
        self.assertEqual(self.message.latest_status, DeliveryStatus.DELIVERED)
        self.assertEqual(self.attempt.status, DeliveryStatus.DELIVERED)

    def test_failed_status_records_error(self):
        request = self._req("failed", code="30007")
        resp: HttpResponse = sms_status_webhook(request)
        self.assertEqual(resp.status_code, 200)
        self.message.refresh_from_db()
        self.attempt.refresh_from_db()
        self.assertEqual(self.message.latest_status, DeliveryStatus.FAILED)
        self.assertEqual(self.message.latest_error_code, "30007")
        self.assertEqual(self.attempt.error_code, "30007")