from unittest.mock import patch, MagicMock

from django.test import TestCase, tag
from django.contrib.auth import get_user_model

from api.models import (
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    BrowserUseAgent,
    CommsChannel,
    PersistentAgentMessage,
    OutboundMessageAttempt,
    DeliveryStatus,
    AgentEmailAccount,
)
from api.agent.comms.outbound_delivery import deliver_agent_email


User = get_user_model()


@tag("smtp")
class TestSmtpSelection(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="seluser", email="seluser@example.com", password="pw")
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user, name="Sel Agent", charter="c", browser_use_agent=self.browser_agent
        )
        self.from_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.com",
        )
        self.to_ep = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="rcpt@example.com",
        )

    def _create_acct(self) -> AgentEmailAccount:
        acct = AgentEmailAccount.objects.create(
            endpoint=self.from_ep,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_security=AgentEmailAccount.SmtpSecurity.STARTTLS,
            smtp_auth=AgentEmailAccount.AuthMode.LOGIN,
            smtp_username="agent@example.com",
            is_outbound_enabled=False,  # will not be validated here
        )
        acct.set_smtp_password("secret")
        acct.connection_last_ok_at = None  # selection doesn't validate account
        acct.save()
        return acct

    @patch("smtplib.SMTP")
    def test_selection_uses_smtp_when_enabled(self, mock_smtp):
        acct = self._create_acct()
        # Enable outbound (clean requires connection_last_ok_at normally; we bypass by setting directly)
        AgentEmailAccount.objects.filter(endpoint=self.from_ep).update(is_outbound_enabled=True)

        client = MagicMock()
        mock_smtp.return_value = client

        msg = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.from_ep,
            to_endpoint=self.to_ep,
            is_outbound=True,
            body="<p>Hello</p>",
            raw_payload={"subject": "Test"},
        )

        deliver_agent_email(msg)

        msg.refresh_from_db()
        attempts = list(OutboundMessageAttempt.objects.filter(message=msg))
        self.assertTrue(any(a.provider == "smtp" for a in attempts))
        self.assertEqual(msg.latest_status, DeliveryStatus.SENT)

    def test_selection_simulates_when_no_account(self):
        msg = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.from_ep,
            to_endpoint=self.to_ep,
            is_outbound=True,
            body="Hello",
            raw_payload={"subject": "No SMTP"},
        )

        deliver_agent_email(msg)

        msg.refresh_from_db()
        attempts = list(OutboundMessageAttempt.objects.filter(message=msg))
        # In tests, SIMULATE_EMAIL_DELIVERY=True -> simulation path
        self.assertTrue(any(a.provider == "postmark_simulation" for a in attempts))
        self.assertEqual(msg.latest_status, DeliveryStatus.DELIVERED)

