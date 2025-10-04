from __future__ import annotations

from django.test import TransactionTestCase, tag
from django.contrib.auth import get_user_model
from unittest.mock import patch, MagicMock
from django.db.utils import OperationalError

from django.utils import timezone

from api.models import (
    PersistentAgent,
    BrowserUseAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    DeliveryStatus,
)
from api.agent.tools.email_sender import execute_send_email
from config import settings


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
        self.default_domain = settings.DEFAULT_AGENT_EMAIL_DOMAIN
        # Primary from endpoint for the agent
        self.from_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel="email",
            address=f"ricardo.kingsley@{self.default_domain}",
            is_primary=True,
        )

    def _mark_message_delivered(self, message):
        message.latest_status = DeliveryStatus.DELIVERED
        message.latest_sent_at = timezone.now()
        message.latest_error_message = ""
        message.save(update_fields=["latest_status", "latest_sent_at", "latest_error_message"])

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

        with patch(
            "api.agent.tools.email_sender.PersistentAgentCommsEndpoint.objects.get_or_create",
            side_effect=_flaky_get_or_create,
        ), patch(
            "api.agent.tools.email_sender.PersistentAgentMessage.objects.create",
            side_effect=_flaky_create_msg,
        ), patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_message_delivered,
        ):
            result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "ok")

    def test_execute_send_email_strips_control_characters(self):
        params = {
            "to_address": self.user.email,
            "subject": "Hello Team",
            "mobile_first_html": "<p>It\u0019s great to chat</p>",
            "cc_addresses": [self.user.email],
        }

        with patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_message_delivered,
        ):
            result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "ok")

        message = PersistentAgentMessage.objects.get(owner_agent=self.agent)
        self.assertNotIn("\u0019", message.body)
        self.assertIn("It's", message.body)
        self.assertEqual(message.raw_payload.get("subject", ""), params["subject"])
        self.assertEqual(message.to_endpoint.address, params["to_address"])
        self.assertListEqual(
            list(message.cc_endpoints.values_list("address", flat=True)),
            params["cc_addresses"],
        )
