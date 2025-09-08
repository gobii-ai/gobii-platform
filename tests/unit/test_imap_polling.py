from __future__ import annotations

import email
from email.message import EmailMessage
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.tasks.email_polling import _poll_account_locked
from api.models import (
    PersistentAgent,
    BrowserUseAgent,
    UserQuota,
    PersistentAgentCommsEndpoint,
    CommsChannel,
    AgentEmailAccount,
    CommsAllowlistEntry,
)


class _FakeIMAP:
    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._selected = False
        self._logged_in = False

        # Prepare two simple messages
        self._msgs = {
            "1": self._build_msg("sender1@example.com", "Hello 1"),
            "2": self._build_msg("sender1@example.com", "Hello 2"),
        }

    def _build_msg(self, from_addr: str, body: str) -> bytes:
        m = EmailMessage()
        m["From"] = from_addr
        m["To"] = "agent@example.org"
        m["Subject"] = "Test"
        m.set_content(body)
        return m.as_bytes()

    # Protocol methods
    def starttls(self):
        return "OK", [b"TLS started"]

    def login(self, user, pwd):
        self._logged_in = True
        return "OK", [b"Logged in"]

    def select(self, folder, readonly=True):
        self._selected = True
        return "OK", [b"1"]

    def uid(self, cmd, _unused, query):
        cmd = (cmd or "").upper()
        if cmd == "SEARCH":
            return "OK", [b"1 2"]
        if cmd == "FETCH":
            uid = _unused  # imaplib passes uid as second argument
            blob = self._msgs.get(uid)
            if blob is None:
                return "OK", []
            return "OK", [(b"BODY[]", blob)]
        return "OK", []

    def noop(self):
        return "OK", [b"PONG"]

    def logout(self):
        return "BYE", [b"Logout"]


@tag("batch_email")
class ImapPollingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(username='imap@example.com', email='imap@example.com', password='pw')
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100
        quota.save()

        # Avoid proxy selection during BrowserUseAgent creation
        with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
            browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="ba-imap")

        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="imap-agent",
            charter="Test",
            schedule="",  # no beat
            browser_use_agent=browser_agent,
        )

    def _setup_endpoint_and_account(self) -> AgentEmailAccount:
        ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.org",
            is_primary=True,
        )
        acct = AgentEmailAccount.objects.create(
            endpoint=ep,
            imap_host="imap.example.org",
            imap_port=993,
            imap_security=AgentEmailAccount.ImapSecurity.SSL,
            imap_username="user",
            is_inbound_enabled=True,
            poll_interval_sec=30,
        )
        acct.set_imap_password("secret")
        acct.save()
        return acct

    @patch('api.agent.tasks.email_polling.ingest_inbound_message')
    @patch('api.agent.tasks.process_agent_events_task.delay')
    @patch('imaplib.IMAP4_SSL', new=_FakeIMAP)
    def test_poll_account_ingests_and_updates_uid(self, _mock_events_delay, mock_ingest):
        acct = self._setup_endpoint_and_account()
        # Whitelist the sender
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="sender1@example.com",
            allow_inbound=True,
            allow_outbound=True,
        )

        # Sanity: whitelist evaluates True
        self.assertTrue(self.agent.is_sender_whitelisted(CommsChannel.EMAIL, "sender1@example.com"))
        _poll_account_locked(acct)
        acct.refresh_from_db()
        self.assertEqual(acct.last_seen_uid, "2")
        # Ingestion should have been invoked
        self.assertGreater(mock_ingest.call_count, 0)
        # No DB persistence assertion here since ingest is mocked

    @patch('api.agent.tasks.process_agent_events_task.delay')
    @patch('imaplib.IMAP4_SSL', new=_FakeIMAP)
    def test_poll_account_skips_non_whitelisted_but_marks_seen(self, _mock_events_delay):
        acct = self._setup_endpoint_and_account()
        # No allowlist created; default policy is MANUAL (block)
        _poll_account_locked(acct)
        acct.refresh_from_db()
        # Skipped but should still advance so we don't loop forever
        self.assertEqual(acct.last_seen_uid, "2")
        self.assertEqual(self.agent.agent_messages.count(), 0)
