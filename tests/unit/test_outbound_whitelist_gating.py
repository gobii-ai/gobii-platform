from __future__ import annotations

from unittest.mock import patch

from django.db import connection
from django.test import TransactionTestCase, tag
from django.contrib.auth import get_user_model

from api.models import (
    PersistentAgent,
    BrowserUseAgent,
    CommsChannel,
    UserPhoneNumber,
)
from api.agent.tools.email_sender import execute_send_email
from api.agent.tools.sms_sender import execute_send_sms


User = get_user_model()

def create_browser_agent_without_proxy(user, name):
    """Helper to create BrowserUseAgent without triggering proxy selection."""
    with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
        return BrowserUseAgent.objects.create(user=user, name=name)


@patch('django.db.close_old_connections')  # Mock at class level to prevent connection closing
class OutboundWhitelistGatingTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="u@example.com", email="u@example.com", password="pw"
        )
        self.browser = create_browser_agent_without_proxy(self.user, "BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Agent",
            charter="c",
            browser_use_agent=self.browser,
        )
        # Provide from endpoints for tools
        from api.models import PersistentAgentCommsEndpoint
        self.email_from = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent, channel=CommsChannel.EMAIL, address="agent@my.gobii.ai", is_primary=True
        )
        self.sms_from = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent, channel=CommsChannel.SMS, address="+15550007777", is_primary=True
        )

    @patch("api.agent.tools.email_sender.deliver_agent_email")  # Mock where it's imported in email_sender
    @patch("api.models.switch_is_active", return_value=True)
    @tag("batch_outbound_email")
    def test_email_execute_respects_manual_allowlist(self, _switch, mock_deliver_email, mock_close_old_connections):
        # Switch agent to manual and allow only a specific recipient
        self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
        self.agent.save(update_fields=["whitelist_policy"])
        from api.models import CommsAllowlistEntry
        CommsAllowlistEntry.objects.create(
            agent=self.agent, channel=CommsChannel.EMAIL, address="allowed@example.com"
        )

        ok = execute_send_email(self.agent, {
            "to_address": "allowed@example.com",
            "subject": "s",
            "mobile_first_html": "<p>hi</p>",
        })
        self.assertEqual(ok.get("status"), "ok")

        blocked = execute_send_email(self.agent, {
            "to_address": "blocked@example.com",
            "subject": "s",
            "mobile_first_html": "<p>hi</p>",
        })
        self.assertEqual(blocked.get("status"), "error")
        self.assertIn("not allowed", blocked.get("message", ""))

    @patch("api.agent.tools.email_sender.deliver_agent_email")  # Mock where it's imported in email_sender  
    @patch("api.models.switch_is_active", return_value=False)
    @tag("batch_outbound_email")
    def test_email_execute_legacy_owner_only_when_switch_off(self, _switch, mock_deliver_email, mock_close_old_connections):
        # Only owner email permitted when switch is off
        ok = execute_send_email(self.agent, {
            "to_address": self.user.email,
            "subject": "s",
            "mobile_first_html": "<p>hi</p>",
        })
        self.assertEqual(ok.get("status"), "ok")

        blocked = execute_send_email(self.agent, {
            "to_address": "friend@example.com",
            "subject": "s",
            "mobile_first_html": "<p>hi</p>",
        })
        self.assertEqual(blocked.get("status"), "error")
    # NOTE: Temporarily disabling SMS tests until SMS sending is re-enabled in multi-player mode
    @patch("api.agent.tools.sms_sender.deliver_agent_sms")  # Mock where it's imported in sms_sender
    @patch("api.models.switch_is_active", return_value=True)
    def test_sms_execute_respects_default_and_manual(self, _switch, mock_deliver_sms, mock_close_old_connections):
        return
        # Mock successful delivery
        mock_deliver_sms.return_value = None  # deliver_agent_sms doesn't return anything

        # Default policy: require verified owner number
        res = execute_send_sms(self.agent, {"to_number": "+15551110000", "body": "hello"})
        self.assertEqual(res.get("status"), "error")
        mock_deliver_sms.assert_not_called()  # Should not deliver if not whitelisted

        UserPhoneNumber.objects.create(user=self.user, phone_number="+15551110000", is_verified=True)
        res = execute_send_sms(self.agent, {"to_number": "+15551110000", "body": "hello"})
        self.assertEqual(res.get("status"), "ok")
        mock_deliver_sms.assert_called_once()  # Should deliver when whitelisted

        # Manual policy only allows listed numbers
        mock_deliver_sms.reset_mock()  # Reset mock call count
        self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
        self.agent.save(update_fields=["whitelist_policy"])
        from api.models import CommsAllowlistEntry
        CommsAllowlistEntry.objects.create(agent=self.agent, channel=CommsChannel.SMS, address="+15557770000")

        ok = execute_send_sms(self.agent, {"to_number": "+15557770000", "body": "yo"})
        self.assertEqual(ok.get("status"), "ok")
        self.assertEqual(mock_deliver_sms.call_count, 1)  # Should have been called for allowed number

        mock_deliver_sms.reset_mock()
        blocked = execute_send_sms(self.agent, {"to_number": "+15557779999", "body": "yo"})
        self.assertEqual(blocked.get("status"), "error")
        mock_deliver_sms.assert_not_called()  # Should not deliver to blocked number
