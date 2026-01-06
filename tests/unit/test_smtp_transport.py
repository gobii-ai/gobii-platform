from unittest.mock import patch, MagicMock

from django.test import TestCase, tag
from django.contrib.auth import get_user_model

from api.models import (
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    BrowserUseAgent,
    CommsChannel,
    AgentEmailAccount,
    AgentEmailOAuthCredential,
)
from api.agent.comms.smtp_transport import SmtpTransport


User = get_user_model()


@tag("smtp", "batch_outbound_email")
class TestSmtpTransport(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="smtpuser", email="smtpuser@example.com", password="pw")
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user, name="SMTP Agent", charter="c", browser_use_agent=self.browser_agent
        )
        self.from_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.com",
        )
        self.to_addr = "recipient@example.com"

    def _create_acct(self, security: str = AgentEmailAccount.SmtpSecurity.STARTTLS, auth: str = AgentEmailAccount.AuthMode.LOGIN) -> AgentEmailAccount:
        acct = AgentEmailAccount.objects.create(
            endpoint=self.from_ep,
            smtp_host="smtp.example.com",
            smtp_port=587 if security != AgentEmailAccount.SmtpSecurity.SSL else 465,
            smtp_security=security,
            smtp_auth=auth,
            smtp_username="agent@example.com",
            is_outbound_enabled=False,  # not enforced in transport tests
        )
        acct.set_smtp_password("secret")
        acct.save()
        return acct

    @patch("smtplib.SMTP")
    def test_send_starttls_with_auth(self, mock_smtp):
        acct = self._create_acct(security=AgentEmailAccount.SmtpSecurity.STARTTLS, auth=AgentEmailAccount.AuthMode.LOGIN)
        client = MagicMock()
        mock_smtp.return_value = client

        SmtpTransport.send(
            account=acct,
            from_addr=self.from_ep.address,
            to_addrs=[self.to_addr],
            subject="Hello",
            plaintext_body="Hi",
            html_body="<p>Hi</p>",
            attempt_id="attempt-1",
        )

        mock_smtp.assert_called_once_with("smtp.example.com", 587, timeout=SmtpTransport.DEFAULT_TIMEOUT)
        client.starttls.assert_called()
        client.login.assert_called_with("agent@example.com", "secret")
        client.send_message.assert_called()
        client.quit.assert_called()

    @patch("smtplib.SMTP_SSL")
    def test_send_ssl_no_auth(self, mock_ssl):
        acct = self._create_acct(security=AgentEmailAccount.SmtpSecurity.SSL, auth=AgentEmailAccount.AuthMode.NONE)
        client = MagicMock()
        mock_ssl.return_value = client

        SmtpTransport.send(
            account=acct,
            from_addr=self.from_ep.address,
            to_addrs=[self.to_addr, "cc@example.com"],
            subject="Hi",
            plaintext_body="plain",
            html_body="<p>plain</p>",
            attempt_id="attempt-2",
        )

        mock_ssl.assert_called_once_with("smtp.example.com", 465, timeout=SmtpTransport.DEFAULT_TIMEOUT)
        client.login.assert_not_called()
        client.send_message.assert_called()
        client.quit.assert_called()

    @patch("smtplib.SMTP")
    def test_send_oauth2_auth(self, mock_smtp):
        acct = self._create_acct(security=AgentEmailAccount.SmtpSecurity.STARTTLS, auth=AgentEmailAccount.AuthMode.OAUTH2)
        credential = AgentEmailOAuthCredential.objects.create(
            account=acct,
            user=self.user,
            provider="gmail",
        )
        credential.access_token = "oauth-token"
        credential.save()
        client = MagicMock()
        mock_smtp.return_value = client

        SmtpTransport.send(
            account=acct,
            from_addr=self.from_ep.address,
            to_addrs=[self.to_addr],
            subject="OAuth",
            plaintext_body="Hi",
            html_body="<p>Hi</p>",
            attempt_id="attempt-3",
        )

        client.login.assert_not_called()
        client.auth.assert_called()
        auth_args = client.auth.call_args[0]
        self.assertEqual(auth_args[0], "XOAUTH2")
        auth_callback = auth_args[1]
        self.assertIn("oauth-token", auth_callback(b""))
