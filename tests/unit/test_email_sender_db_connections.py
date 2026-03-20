from allauth.account.models import EmailAddress
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
    CommsChannel,
    DeliveryStatus,
)
from api.agent.tools.email_sender import execute_send_email, get_send_email_tool
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
        # Email verification is required for outbound email sending
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
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

    def test_send_email_tool_requires_html_tables(self):
        description = get_send_email_tool()["function"]["description"]

        self.assertIn("<table>", description)
        self.assertIn("<tr>", description)
        self.assertIn("<th>", description)
        self.assertIn("<td>", description)
        self.assertIn("do NOT use Markdown pipe tables", description)

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
        self.assertTrue(result.get("message_id"))

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
        self.assertTrue(result.get("message_id"))

        message = PersistentAgentMessage.objects.get(owner_agent=self.agent)
        self.assertEqual(str(message.id), result.get("message_id"))
        self.assertNotIn("\u0019", message.body)
        self.assertIn("It's", message.body)
        self.assertEqual(message.raw_payload.get("subject", ""), params["subject"])
        self.assertEqual(message.to_endpoint.address, params["to_address"])
        self.assertListEqual(
            list(message.cc_endpoints.values_list("address", flat=True)),
            params["cc_addresses"],
        )

    def test_execute_send_email_self_send_uses_default_alias_sender(self):
        self.from_ep.is_primary = False
        self.from_ep.save(update_fields=["is_primary"])
        custom_primary = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=self.user.email,
            is_primary=True,
        )

        params = {
            "to_address": self.user.email,
            "subject": "Self send test",
            "mobile_first_html": "<p>Hello</p>",
        }

        with patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_message_delivered,
        ):
            result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "ok")
        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertEqual(message.from_endpoint_id, self.from_ep.id)
        self.assertEqual(message.to_endpoint_id, custom_primary.id)

    def test_execute_send_email_self_send_with_cc_keeps_custom_sender(self):
        self.from_ep.is_primary = False
        self.from_ep.save(update_fields=["is_primary"])
        custom_primary = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=self.user.email,
            is_primary=True,
        )

        params = {
            "to_address": self.user.email,
            "cc_addresses": ["another@example.com"],
            "subject": "Self send with cc",
            "mobile_first_html": "<p>Hello with cc</p>",
        }

        with patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_message_delivered,
        ):
            result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "error")
        self.assertIn("Recipient address 'another@example.com' not allowed", result.get("message", ""))

        # Make CC allowed by using owner email and retry to confirm sender selection.
        params["cc_addresses"] = [self.user.email]
        with patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_message_delivered,
        ):
            result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "ok")
        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertEqual(message.from_endpoint_id, custom_primary.id)

    def test_execute_send_email_rejects_attachment_claim_without_attachments(self):

        result = execute_send_email(
            self.agent,
            {
                "to_address": self.user.email,
                "subject": "Files enclosed",
                "mobile_first_html": "<p>Please find attached the updated report.</p>",
            },
        )

        self.assertEqual(result.get("status"), "error")
        self.assertIn("claims attachments are included", result.get("message", ""))
        self.assertIn("send_email.attachments", result.get("message", ""))

    def test_execute_send_email_allows_normal_email_without_attachments(self):
        params = {
            "to_address": self.user.email,
            "subject": "Quick update",
            "mobile_first_html": "<p>The report is ready for review.</p>",
        }

        with patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_message_delivered,
        ):
            result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "ok")
        self.assertTrue(result.get("message_id"))

    def test_execute_send_email_ignores_attachment_claim_in_quoted_thread(self):
        params = {
            "to_address": self.user.email,
            "subject": "Following up",
            "mobile_first_html": (
                "<p>Thanks for the follow-up.</p>"
                "<blockquote><p>Please find attached the updated report.</p></blockquote>"
            ),
        }

        with patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_message_delivered,
        ):
            result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "ok")
        self.assertTrue(result.get("message_id"))

    def test_execute_send_email_allows_attachment_claim_with_attachments(self):
        params = {
            "to_address": self.user.email,
            "subject": "Attached report",
            "mobile_first_html": "<p>See attached the updated report.</p>",
            "attachments": ["$[/exports/report.csv]"],
        }
        resolved_attachment = MagicMock()

        with patch(
            "api.agent.tools.email_sender.resolve_filespace_attachments",
            return_value=[resolved_attachment],
        ), patch(
            "api.agent.tools.email_sender.create_message_attachments",
        ) as create_message_attachments_mock, patch(
            "api.agent.tools.email_sender.broadcast_message_attachment_update",
        ), patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_message_delivered,
        ):
            result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "ok")
        create_message_attachments_mock.assert_called_once()
