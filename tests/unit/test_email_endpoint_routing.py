from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.comms.email_endpoint_routing import (
    EmailSenderSelectionError,
    resolve_agent_email_sender_endpoint,
    resolve_agent_email_sender_endpoint_for_message,
)
from api.agent.tools.email_sender import get_send_email_tool
from api.models import (
    AgentEmailAccount,
    AgentEmailIntegration,
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
)
from config import settings


@tag("batch_email")
class EmailEndpointRoutingTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="routing-owner",
            email="routing-owner@example.com",
            password="password",
        )
        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            browser_agent = BrowserUseAgent.objects.create(user=user, name="routing-browser")
        self.agent = PersistentAgent.objects.create(
            user=user,
            name="routing-agent",
            charter="route email",
            browser_use_agent=browser_agent,
        )
        domain = (settings.DEFAULT_AGENT_EMAIL_DOMAIN or "my.gobii.ai").strip().lower()
        self.gobii_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=f"routing-agent@{domain}",
            is_primary=False,
        )
        self.configured_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="routing-owner@example.com",
            is_primary=True,
        )
        self.account = AgentEmailAccount.objects.create(
            endpoint=self.configured_endpoint,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username=self.configured_endpoint.address,
            is_outbound_enabled=True,
            connection_last_ok_at=timezone.now(),
        )
        self.integration = AgentEmailIntegration.objects.create(
            agent=self.agent,
            active_mode=AgentEmailIntegration.ActiveMode.CUSTOM,
            custom_account=self.account,
        )

    def test_omitted_sender_prefers_enabled_configured_mailbox(self):
        sender = resolve_agent_email_sender_endpoint(self.agent, to_address="someone@example.com")
        self.assertEqual(sender, self.configured_endpoint)

    def test_omitted_sender_falls_back_to_gobii_when_configured_sending_disabled(self):
        self.account.is_outbound_enabled = False
        self.account.save(update_fields=["is_outbound_enabled"])
        sender = resolve_agent_email_sender_endpoint(self.agent, to_address="someone@example.com")
        self.assertEqual(sender, self.gobii_endpoint)

    def test_explicit_gobii_sender_is_always_available(self):
        sender = resolve_agent_email_sender_endpoint(
            self.agent,
            requested_from=self.gobii_endpoint.address,
        )
        self.assertEqual(sender, self.gobii_endpoint)

    def test_explicit_disabled_configured_sender_has_clear_error(self):
        self.account.is_outbound_enabled = False
        self.account.save(update_fields=["is_outbound_enabled"])
        with self.assertRaisesMessage(EmailSenderSelectionError, "is disabled"):
            resolve_agent_email_sender_endpoint(
                self.agent,
                requested_from=self.configured_endpoint.address,
            )

    def test_unknown_sender_lists_exact_available_addresses(self):
        with self.assertRaises(EmailSenderSelectionError) as context:
            resolve_agent_email_sender_endpoint(
                self.agent,
                requested_from="unknown@example.com",
            )
        self.assertIn(self.configured_endpoint.address, str(context.exception))
        self.assertIn(self.gobii_endpoint.address, str(context.exception))

    def test_message_routing_honors_explicit_sender(self):
        sender = resolve_agent_email_sender_endpoint_for_message(
            self.agent,
            to_endpoint=self.configured_endpoint,
            cc_endpoints=[],
            requested_from=self.gobii_endpoint.address,
        )
        self.assertEqual(sender, self.gobii_endpoint)

    def test_send_email_schema_documents_exact_agent_sender_addresses(self):
        tool = get_send_email_tool(self.agent)
        from_property = tool["function"]["parameters"]["properties"]["from"]
        self.assertIn(self.configured_endpoint.address, from_property["description"])
        self.assertIn(self.gobii_endpoint.address, from_property["description"])
        self.assertNotIn("from", tool["function"]["parameters"]["required"])
