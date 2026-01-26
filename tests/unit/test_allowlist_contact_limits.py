"""
Tests for outbound contact cap enforcement.
"""
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.models import (
    BrowserUseAgent,
    CommsChannel,
    CommsOutboundContactUsage,
    PersistentAgent,
)
from api.services.contact_limits import (
    check_and_register_outbound_contact,
    check_and_register_outbound_contacts,
    get_contact_usage_summary,
)

User = get_user_model()


@tag("batch_allowlist_rules")
class OutboundContactCapTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Test Browser Agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Test Agent",
            charter="Test charter",
            browser_use_agent=self.browser_agent,
            whitelist_policy=PersistentAgent.WhitelistPolicy.MANUAL,
        )
        self.period_start = date(2024, 1, 1)
        self.period_end = date(2024, 1, 31)

    @patch("api.services.contact_limits.BillingService.get_current_billing_period_for_owner")
    @patch("api.services.contact_limits.get_user_max_contacts_per_agent", return_value=2)
    def test_contact_cap_enforced_per_channel(self, cap_mock, billing_mock):
        billing_mock.return_value = (self.period_start, self.period_end)

        result = check_and_register_outbound_contacts(
            self.agent,
            CommsChannel.EMAIL,
            ["a@example.com", "b@example.com"],
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.remaining, 0)
        self.assertEqual(
            CommsOutboundContactUsage.objects.filter(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                period_start=self.period_start,
            ).count(),
            2,
        )

        result = check_and_register_outbound_contact(
            self.agent,
            CommsChannel.EMAIL,
            "c@example.com",
        )
        self.assertFalse(result.allowed)
        self.assertIn("Contact limit reached", result.reason or "")
        self.assertEqual(
            CommsOutboundContactUsage.objects.filter(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                period_start=self.period_start,
            ).count(),
            2,
        )

        result = check_and_register_outbound_contact(
            self.agent,
            CommsChannel.EMAIL,
            "a@example.com",
        )
        self.assertTrue(result.allowed)
        self.assertEqual(
            CommsOutboundContactUsage.objects.filter(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                period_start=self.period_start,
            ).count(),
            2,
        )

        result = check_and_register_outbound_contact(
            self.agent,
            CommsChannel.SMS,
            "+15551230000",
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.remaining, 1)
        self.assertEqual(
            CommsOutboundContactUsage.objects.filter(
                agent=self.agent,
                channel=CommsChannel.SMS,
                period_start=self.period_start,
            ).count(),
            1,
        )

    @patch("api.services.contact_limits.BillingService.get_current_billing_period_for_owner")
    @patch("api.services.contact_limits.get_user_max_contacts_per_agent", return_value=1)
    def test_privileged_contacts_excluded_from_usage(self, cap_mock, billing_mock):
        billing_mock.return_value = (self.period_start, self.period_end)

        result = check_and_register_outbound_contacts(
            self.agent,
            CommsChannel.EMAIL,
            [self.user.email, "friend@example.com"],
        )
        self.assertTrue(result.allowed)
        self.assertEqual(
            CommsOutboundContactUsage.objects.filter(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                period_start=self.period_start,
            ).count(),
            1,
        )

        result = check_and_register_outbound_contact(
            self.agent,
            CommsChannel.EMAIL,
            "another@example.com",
        )
        self.assertFalse(result.allowed)

    @patch("api.services.contact_limits.BillingService.get_current_billing_period_for_owner")
    @patch("api.services.contact_limits.get_user_max_contacts_per_agent", return_value=1)
    def test_email_normalization(self, cap_mock, billing_mock):
        billing_mock.return_value = (self.period_start, self.period_end)

        result = check_and_register_outbound_contact(
            self.agent,
            CommsChannel.EMAIL,
            "Name <Mixed@Example.com>",
        )
        self.assertTrue(result.allowed)
        usage = CommsOutboundContactUsage.objects.get(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            period_start=self.period_start,
        )
        self.assertEqual(usage.address, "mixed@example.com")

    @patch("api.services.contact_limits.get_user_max_contacts_per_agent", return_value=0)
    def test_unlimited_cap_skips_usage(self, cap_mock):
        result = check_and_register_outbound_contact(
            self.agent,
            CommsChannel.EMAIL,
            "free@example.com",
        )
        self.assertTrue(result.allowed)
        self.assertEqual(CommsOutboundContactUsage.objects.count(), 0)

    def test_empty_addresses_rejected(self):
        result = check_and_register_outbound_contacts(self.agent, CommsChannel.EMAIL, [])
        self.assertFalse(result.allowed)
        self.assertIn("Recipient address is required", result.reason or "")

        result = check_and_register_outbound_contacts(
            self.agent,
            CommsChannel.EMAIL,
            [""],
        )
        self.assertFalse(result.allowed)

    @patch("api.services.contact_limits.BillingService.get_current_billing_period_for_owner")
    @patch("api.services.contact_limits.get_user_max_contacts_per_agent", return_value=3)
    def test_contact_usage_summary_per_channel(self, cap_mock, billing_mock):
        billing_mock.return_value = (self.period_start, self.period_end)

        CommsOutboundContactUsage.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="summary@example.com",
            period_start=self.period_start,
            period_end=self.period_end,
        )
        CommsOutboundContactUsage.objects.create(
            agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15551230001",
            period_start=self.period_start,
            period_end=self.period_end,
        )

        summary = get_contact_usage_summary(self.agent)
        self.assertEqual(summary["limit_per_channel"], 3)
        channel_map = {entry["channel"]: entry for entry in summary["channels"]}
        self.assertEqual(channel_map[CommsChannel.EMAIL]["used"], 1)
        self.assertEqual(channel_map[CommsChannel.SMS]["used"], 1)
        self.assertEqual(channel_map[CommsChannel.WEB]["used"], 0)
