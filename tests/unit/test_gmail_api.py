import base64
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.comms.gmail_api import (
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    GmailApiTransport,
    uses_gmail_api,
)
from api.agent.comms.outbound_delivery import deliver_agent_email
from api.agent.tasks.email_polling import _poll_account_locked
from api.models import (
    AgentEmailAccount,
    AgentEmailOAuthCredential,
    BrowserUseAgent,
    CommsAllowlistEntry,
    CommsChannel,
    DeliveryStatus,
    OutboundMessageAttempt,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
)


@tag("batch_console_email_oauth")
class GmailApiEmailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="gmail-api-user",
            email="gmail-api-user@example.com",
            password="password123",
        )
        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Gmail API Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Gmail API Agent",
            charter="Test Gmail API email",
            browser_use_agent=browser_agent,
        )

    def _create_account(self, *, scope: str, inbound: bool = True, outbound: bool = True):
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent.mailbox@gmail.com",
            is_primary=True,
        )
        account = AgentEmailAccount.objects.create(
            endpoint=endpoint,
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            smtp_auth=AgentEmailAccount.AuthMode.OAUTH2,
            imap_host="imap.gmail.com",
            imap_port=993,
            imap_auth=AgentEmailAccount.ImapAuthMode.OAUTH2,
            is_inbound_enabled=inbound,
            is_outbound_enabled=outbound,
        )
        credential = AgentEmailOAuthCredential.objects.create(
            account=account,
            user=self.user,
            provider="gmail",
            scope=scope,
            metadata={"token_endpoint": "https://oauth2.googleapis.com/token"},
        )
        credential.access_token = "access-token"
        credential.save()
        return account, credential

    @patch("api.agent.comms.gmail_api.requests.request")
    def test_gmail_api_transport_sends_rfc_message(self, mock_request):
        account, _credential = self._create_account(
            scope=f"openid email {GMAIL_SEND_SCOPE} {GMAIL_READONLY_SCOPE}"
        )
        response = MagicMock(ok=True, status_code=200)
        response.json.return_value = {"id": "gmail-message-id"}
        mock_request.return_value = response

        provider_id = GmailApiTransport.send(
            account=account,
            from_addr="Mailbox Name <agent.mailbox@gmail.com>",
            to_addrs=["recipient@example.com", "copy@example.com"],
            subject="Gmail API subject",
            plaintext_body="Plain body",
            html_body="<p>HTML body</p>",
            attempt_id="attempt-1",
            message_id="<message@gobii.test>",
        )

        self.assertEqual(provider_id, "gmail-message-id")
        call = mock_request.call_args
        self.assertEqual(call.args[:2], ("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"))
        raw = base64.urlsafe_b64decode(call.kwargs["json"]["raw"])
        message = BytesParser(policy=policy.default).parsebytes(raw)
        self.assertEqual(message["From"], "Mailbox Name <agent.mailbox@gmail.com>")
        self.assertEqual(message["To"], "recipient@example.com")
        self.assertEqual(message["Cc"], "copy@example.com")
        self.assertEqual(message["Message-ID"], "<message@gobii.test>")

    @patch("api.agent.comms.outbound_delivery.SmtpTransport.send")
    @patch("api.agent.comms.outbound_delivery.GmailApiTransport.send", return_value="gmail-provider-id")
    def test_outbound_delivery_routes_new_scope_to_gmail_api(self, mock_gmail_send, mock_smtp_send):
        account, _credential = self._create_account(scope=f"{GMAIL_SEND_SCOPE} {GMAIL_READONLY_SCOPE}")
        recipient = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="recipient@example.com",
        )
        message = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=account.endpoint,
            to_endpoint=recipient,
            is_outbound=True,
            body="Hello from Gmail",
            raw_payload={"subject": "Gmail routing"},
            latest_status=DeliveryStatus.QUEUED,
        )

        deliver_agent_email(message)

        message.refresh_from_db()
        attempt = OutboundMessageAttempt.objects.get(message=message)
        self.assertEqual(message.latest_status, DeliveryStatus.SENT)
        self.assertEqual(attempt.provider, "gmail_api")
        self.assertEqual(attempt.provider_message_id, "gmail-provider-id")
        mock_gmail_send.assert_called_once()
        mock_smtp_send.assert_not_called()

    @patch("api.agent.tasks.email_polling.ingest_inbound_message")
    @patch("api.agent.tasks.email_polling.get_gmail_raw_message")
    @patch("api.agent.tasks.email_polling.list_gmail_history")
    @patch("api.agent.comms.gmail_api.get_gmail_profile")
    def test_polling_uses_gmail_history_and_raw_message_api(
        self,
        mock_profile,
        mock_history,
        mock_raw_message,
        mock_ingest,
    ):
        account, credential = self._create_account(scope=f"{GMAIL_SEND_SCOPE} {GMAIL_READONLY_SCOPE}")
        credential.metadata = {
            **credential.metadata,
            "gmail_history_id": "100",
        }
        credential.save(update_fields=["metadata"])
        mock_profile.return_value = {"emailAddress": account.endpoint.address, "historyId": "100"}
        mock_history.side_effect = [
            {
                "history": [
                    {
                        "id": "101",
                        "messagesAdded": [{"message": {"id": "gmail-inbound-1"}}],
                    }
                ],
                "nextPageToken": "next-page",
                "historyId": "102",
            },
            {"history": [], "historyId": "102"},
        ]
        inbound = EmailMessage()
        inbound["From"] = "allowed@example.com"
        inbound["To"] = account.endpoint.address
        inbound["Subject"] = "Inbound Gmail"
        inbound["Message-ID"] = "<inbound@gmail.test>"
        inbound.set_content("Hello")
        mock_raw_message.return_value = inbound.as_bytes()
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="allowed@example.com",
            allow_inbound=True,
            allow_outbound=True,
        )

        _poll_account_locked(account)

        account.refresh_from_db()
        credential.refresh_from_db()
        self.assertEqual(credential.metadata["gmail_history_id"], "102")
        self.assertEqual(account.imap_error, "")
        self.assertIsNotNone(account.imap_last_ok_at)
        mock_raw_message.assert_called_once_with(account, "gmail-inbound-1")
        mock_ingest.assert_called_once()
        self.assertEqual(mock_history.call_count, 2)
        self.assertEqual(mock_history.call_args_list[0].kwargs["start_history_id"], "100")
        self.assertEqual(mock_history.call_args_list[1].kwargs["start_history_id"], "100")
        self.assertEqual(mock_history.call_args_list[1].kwargs["page_token"], "next-page")

    def test_legacy_full_mail_scope_stays_on_smtp_imap(self):
        account, _credential = self._create_account(scope="openid email https://mail.google.com/")
        self.assertFalse(uses_gmail_api(account))
