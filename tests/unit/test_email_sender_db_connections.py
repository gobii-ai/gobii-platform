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
    PersistentAgentConversation,
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

    def _create_email_conversation(self, address=None):
        return PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=address or self.user.email,
        )

    def _create_inbound_email_message(
        self,
        *,
        address=None,
        raw_payload=None,
        body="Inbound body",
    ):
        conversation = self._create_email_conversation(address=address)
        sender_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address=address or self.user.email,
        )
        return PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=sender_endpoint,
            conversation=conversation,
            is_outbound=False,
            body=body,
            raw_payload=raw_payload or {
                "subject": "Inbound subject",
                "message_id": "<inbound-message@example.com>",
            },
        )

    def test_send_email_tool_requires_html_tables(self):
        tool = get_send_email_tool()
        description = tool["function"]["description"]
        properties = tool["function"]["parameters"]["properties"]

        self.assertIn("<table>", description)
        self.assertIn("<tr>", description)
        self.assertIn("<th>", description)
        self.assertIn("<td>", description)
        self.assertIn("do NOT use Markdown pipe tables", description)
        self.assertIn("reply_to_message_id", properties)

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
        self.assertIsNone(message.to_endpoint_id)
        self.assertEqual(message.conversation.address, params["to_address"])
        self.assertIsNone(message.parent_id)
        self.assertListEqual(
            list(message.cc_endpoints.values_list("address", flat=True)),
            params["cc_addresses"],
        )
        participant_addresses = list(message.conversation.participants.values_list("endpoint__address", flat=True))
        self.assertIn(self.from_ep.address, participant_addresses)
        self.assertIn(params["to_address"], participant_addresses)

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
        self.assertIsNone(message.to_endpoint_id)
        self.assertEqual(message.conversation.address, custom_primary.address)

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
        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertIsNone(message.parent_id)
        self.assertEqual(message.conversation.address, self.user.email)

    def test_execute_send_email_duplicate_guard_matches_legacy_outbound_email(self):
        legacy_to_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address=self.user.email,
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.from_ep,
            to_endpoint=legacy_to_endpoint,
            is_outbound=True,
            body="<p>The report is ready for review.</p>",
            raw_payload={"subject": "Quick update"},
            latest_status=DeliveryStatus.DELIVERED,
        )

        result = execute_send_email(
            self.agent,
            {
                "to_address": self.user.email,
                "subject": "Quick update",
                "mobile_first_html": "<p>The report is ready for review.</p>",
            },
        )

        self.assertEqual(result.get("status"), "error")
        self.assertTrue(result.get("duplicate_detected"))
        self.assertEqual(result.get("duplicate_reason"), "exact")

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

    def test_execute_send_email_reply_uses_parent_message(self):
        inbound = self._create_inbound_email_message(
            raw_payload={
                "subject": "Inbound thread",
                "message_id": "<thread-root@example.com>",
                "references": "<older@example.com>",
            },
            body="Can you reply here?",
        )

        with patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_message_delivered,
        ):
            result = execute_send_email(
                self.agent,
                {
                    "to_address": self.user.email,
                    "subject": "Re: Inbound thread",
                    "mobile_first_html": "<p>Replying in thread.</p>",
                    "reply_to_message_id": str(inbound.id),
                },
            )

        self.assertEqual(result.get("status"), "ok")
        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertEqual(message.parent_id, inbound.id)
        self.assertEqual(message.conversation_id, inbound.conversation_id)

    def test_execute_send_email_reply_accepts_prior_outbound_message_id(self):
        conversation = self._create_email_conversation()
        prior = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.from_ep,
            conversation=conversation,
            is_outbound=True,
            body="<p>First send</p>",
            raw_payload={
                "subject": "Prior outbound",
                "message_id": "<prior-outbound@example.com>",
            },
        )

        with patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_message_delivered,
        ):
            result = execute_send_email(
                self.agent,
                {
                    "to_address": self.user.email,
                    "subject": "Re: Prior outbound",
                    "mobile_first_html": "<p>Replying to prior outbound.</p>",
                    "reply_to_message_id": str(prior.id),
                },
            )

        self.assertEqual(result.get("status"), "ok")
        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertEqual(message.parent_id, prior.id)

    def test_execute_send_email_reply_rejects_non_email_message_id(self):
        sms_sender = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15550001111",
        )
        sms_recipient = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.SMS,
            address="+15550002222",
        )
        sms_message = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=sms_sender,
            to_endpoint=sms_recipient,
            is_outbound=True,
            body="SMS body",
            raw_payload={},
        )

        result = execute_send_email(
            self.agent,
            {
                "to_address": self.user.email,
                "subject": "Wrong channel",
                "mobile_first_html": "<p>Nope</p>",
                "reply_to_message_id": str(sms_message.id),
            },
        )

        self.assertEqual(result.get("status"), "error")
        self.assertIn("must reference an email message", result.get("message", ""))

    def test_execute_send_email_reply_rejects_missing_rfc_message_id(self):
        inbound = self._create_inbound_email_message(raw_payload={"subject": "Missing id"})

        result = execute_send_email(
            self.agent,
            {
                "to_address": self.user.email,
                "subject": "Missing RFC id",
                "mobile_first_html": "<p>Nope</p>",
                "reply_to_message_id": str(inbound.id),
            },
        )

        self.assertEqual(result.get("status"), "error")
        self.assertIn("stored RFC Message-ID", result.get("message", ""))

    def test_execute_send_email_reply_rejects_recipient_mismatch(self):
        from api.models import CommsAllowlistEntry

        inbound = self._create_inbound_email_message()
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="other@example.com",
        )

        result = execute_send_email(
            self.agent,
            {
                "to_address": "other@example.com",
                "subject": "Mismatch",
                "mobile_first_html": "<p>Nope</p>",
                "reply_to_message_id": str(inbound.id),
            },
        )

        self.assertEqual(result.get("status"), "error")
        self.assertIn("does not match to_address", result.get("message", ""))
