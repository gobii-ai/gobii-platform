from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.comms.email_endpoint_routing import (
    resolve_agent_email_sender_endpoint,
    resolve_agent_email_sender_endpoint_for_message,
)
from api.models import BrowserUseAgent, CommsChannel, PersistentAgent, PersistentAgentCommsEndpoint
from config import settings


def _create_browser_agent_without_proxy(user, name: str):
    with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
        return BrowserUseAgent.objects.create(user=user, name=name)


@tag("batch_email")
class EmailEndpointRoutingTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="routing-owner",
            email="routing-owner@example.com",
            password="password",
        )
        browser_agent = _create_browser_agent_without_proxy(self.user, "routing-browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="routing-agent",
            charter="route email",
            browser_use_agent=browser_agent,
        )
        domain = (settings.DEFAULT_AGENT_EMAIL_DOMAIN or "my.gobii.ai").strip().lower()
        self.default_alias = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=f"routing-agent@{domain}",
            is_primary=False,
        )
        self.custom_primary = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="routing-owner@example.com",
            is_primary=True,
        )
        self.cc_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="cc@example.com",
        )

    def test_resolve_sender_uses_primary_by_default(self):
        sender = resolve_agent_email_sender_endpoint(
            self.agent,
            to_address="someone@example.com",
            has_cc_or_bcc=False,
            log_context="routing-test",
        )
        self.assertEqual(sender.id, self.custom_primary.id)

    def test_resolve_sender_uses_default_alias_for_self_send_without_cc_or_bcc(self):
        sender = resolve_agent_email_sender_endpoint(
            self.agent,
            to_address=self.custom_primary.address,
            has_cc_or_bcc=False,
            log_context="routing-test",
        )
        self.assertEqual(sender.id, self.default_alias.id)

    def test_resolve_sender_keeps_custom_primary_for_self_send_with_cc_or_bcc(self):
        sender = resolve_agent_email_sender_endpoint(
            self.agent,
            to_address=self.custom_primary.address,
            has_cc_or_bcc=True,
            log_context="routing-test",
        )
        self.assertEqual(sender.id, self.custom_primary.id)

    def test_resolve_sender_for_message_uses_default_alias_for_self_send(self):
        sender = resolve_agent_email_sender_endpoint_for_message(
            self.agent,
            to_endpoint=self.custom_primary,
            cc_endpoints=[],
            has_bcc=False,
            log_context="routing-test",
        )
        self.assertEqual(sender.id, self.default_alias.id)

    def test_resolve_sender_for_message_keeps_custom_primary_when_cc_exists(self):
        sender = resolve_agent_email_sender_endpoint_for_message(
            self.agent,
            to_endpoint=self.custom_primary,
            cc_endpoints=[self.cc_endpoint],
            has_bcc=False,
            log_context="routing-test",
        )
        self.assertEqual(sender.id, self.custom_primary.id)
