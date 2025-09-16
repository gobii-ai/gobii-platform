from __future__ import annotations

from django.test import TransactionTestCase, tag, SimpleTestCase
from django.contrib.auth import get_user_model
from unittest.mock import patch, MagicMock
from django.db.utils import OperationalError

from api.models import PersistentAgent, BrowserUseAgent, PersistentAgentCommsEndpoint, PersistentAgentMessage
from api.agent.tools.email_sender import execute_send_email, _restore_truncated_unicode


User = get_user_model()


def create_browser_agent_without_proxy(user, name):
    """Helper to create BrowserUseAgent without triggering proxy selection."""
    with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
        return BrowserUseAgent.objects.create(user=user, name=name)


@tag("batch_email_sender_db")
class EmailSenderDbConnectionTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="sender@example.com",
            email="sender@example.com",
            password="secret",
        )
        self.browser_agent = create_browser_agent_without_proxy(self.user, "BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="EmailAgent",
            charter="send emails",
            browser_use_agent=self.browser_agent,
        )
        # Primary from endpoint for the agent
        self.from_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel="email",
            address="ricardo.kingsley@my.gobii.ai",
            is_primary=True,
        )

    def test_execute_send_email_retries_on_operational_error(self):
        """
        Test that execute_send_email properly retries on OperationalError.
        
        IMPORTANT: This test specifically tests the retry logic that depends on
        close_old_connections() working properly, so we must NOT mock it here.
        """
        # Ensure close_old_connections is not mocked for this test
        # (in case it was mocked globally or in a parent class)
        from django.db import close_old_connections
        if hasattr(close_old_connections, '_mock_name'):
            # It's a mock, we need to use the real function
            from importlib import reload
            import django.db
            reload(django.db)
            from django.db import close_old_connections
        
        params = {
            "to_address": self.user.email,  # allowed by whitelist
            "subject": "Hello",
            "mobile_first_html": "<p>Hi!</p>",
        }

        # First get_or_create call raises OperationalError; second succeeds
        original_get_or_create = PersistentAgentCommsEndpoint.objects.get_or_create

        def _flaky_get_or_create(*args, **kwargs):
            if not getattr(_flaky_get_or_create, "called", False):
                _flaky_get_or_create.called = True  # type: ignore[attr-defined]
                raise OperationalError("simulated stale connection")
            return original_get_or_create(*args, **kwargs)

        # First message create raises OperationalError; second succeeds
        from api.models import PersistentAgentMessage
        original_create_msg = PersistentAgentMessage.objects.create

        def _flaky_create_msg(*args, **kwargs):
            if not getattr(_flaky_create_msg, "called", False):
                _flaky_create_msg.called = True  # type: ignore[attr-defined]
                raise OperationalError("simulated stale connection on create")
            return original_create_msg(*args, **kwargs)

        with patch("api.agent.tools.email_sender.PersistentAgentCommsEndpoint.objects.get_or_create", side_effect=_flaky_get_or_create), \
             patch("api.agent.tools.email_sender.PersistentAgentMessage.objects.create", side_effect=_flaky_create_msg):
            result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "ok")

    @patch("api.agent.tools.email_sender.deliver_agent_email")
    def test_execute_send_email_restores_truncated_unicode(self, mock_deliver: MagicMock):
        params = {
            "to_address": self.user.email,
            "subject": "Plan update I\x19m excited about",
            "mobile_first_html": "<p>Ready\x14set\x14go\x04</p>",
        }

        result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "ok")
        mock_deliver.assert_called_once()

        message = PersistentAgentMessage.objects.order_by("-timestamp").first()
        self.assertIsNotNone(message)
        self.assertNotIn("\x19", message.raw_payload.get("subject", ""))
        self.assertNotIn("\x14", message.body)
        self.assertNotIn("\x04", message.body)
        self.assertIn("I’m", message.raw_payload.get("subject", ""))
        self.assertIn("—", message.body)
        self.assertIn(" ", message.body)

    def test_restore_returns_original_when_no_control_chars(self):
        text = "Plain text"
        self.assertEqual(_restore_truncated_unicode(text), text)

    def test_restore_fixes_low_control_characters(self):
        text = "I\x19m using\x14smart\x04punctuation"
        repaired = _restore_truncated_unicode(text)
        self.assertEqual(repaired, "I’m using—smart punctuation")
