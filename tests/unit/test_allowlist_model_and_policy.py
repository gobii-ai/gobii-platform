from __future__ import annotations

import json
from unittest.mock import patch

from django.test import TestCase, RequestFactory, override_settings, tag
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from api.models import (
    PersistentAgent,
    BrowserUseAgent,
    CommsAllowlistEntry,
    CommsChannel,
    UserPhoneNumber,
    Organization,
    OrganizationMembership,
)


User = get_user_model()


@tag("batch_allowlist_rules")
class AllowlistModelValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="owner@example.com",
            email="owner@example.com",
            password="pw",
        )
        self.browser = BrowserUseAgent.objects.create(user=self.user, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="A",
            charter="c",
            browser_use_agent=self.browser,
            whitelist_policy=PersistentAgent.WhitelistPolicy.MANUAL,
        )

    @patch("util.subscription_helper.get_user_max_contacts_per_agent", return_value=1)
    def test_cap_enforced_via_clean(self, *_):
        CommsAllowlistEntry.objects.create(
            agent=self.agent, channel=CommsChannel.EMAIL, address="first@example.com", is_active=True
        )
        entry = CommsAllowlistEntry(
            agent=self.agent, channel=CommsChannel.EMAIL, address="second@example.com", is_active=True
        )
        with self.assertRaises(ValidationError):
            entry.full_clean()

    def test_uniqueness_and_email_normalization(self):
        e1 = CommsAllowlistEntry(agent=self.agent, channel=CommsChannel.EMAIL, address="FRIEND@EXAMPLE.COM")
        e1.full_clean();
        e1.save()

        e2 = CommsAllowlistEntry(agent=self.agent, channel=CommsChannel.EMAIL, address="friend@example.com")
        with self.assertRaises(ValidationError):  # full_clean catches duplicate post-normalization
            e2.full_clean()

    def test_inactive_entries_do_not_match(self):
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="friend@example.com",
            is_active=False,
        )
        self.assertFalse(self.agent.is_sender_whitelisted(CommsChannel.EMAIL, "friend@example.com"))


@tag("batch_allowlist_rules")
class WhitelistPolicyAndFlagsTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.owner = User.objects.create_user(
            username="own@example.com", email="own@example.com", password="pw"
        )
        self.browser = BrowserUseAgent.objects.create(user=self.owner, name="BA")
        self.agent_user_owned = PersistentAgent.objects.create(
            user=self.owner,
            name="UserOwned",
            charter="c",
            browser_use_agent=self.browser,
        )

    def test_default_owner_only_user_owned(self):
        # Only owner email allowed for EMAIL by default
        self.assertTrue(self.agent_user_owned.is_sender_whitelisted(CommsChannel.EMAIL, self.owner.email))
        self.assertTrue(self.agent_user_owned.is_recipient_whitelisted(CommsChannel.EMAIL, self.owner.email))
        self.assertFalse(self.agent_user_owned.is_sender_whitelisted(CommsChannel.EMAIL, "stranger@example.com"))
        self.assertFalse(self.agent_user_owned.is_recipient_whitelisted(CommsChannel.EMAIL, "stranger@example.com"))

        # Only verified number allowed for SMS
        UserPhoneNumber.objects.create(user=self.owner, phone_number="+15551234567", is_verified=True)
        self.assertTrue(self.agent_user_owned.is_sender_whitelisted(CommsChannel.SMS, "+15551234567"))
        self.assertTrue(self.agent_user_owned.is_recipient_whitelisted(CommsChannel.SMS, "+15551234567"))
        self.assertFalse(self.agent_user_owned.is_sender_whitelisted(CommsChannel.SMS, "+19999999999"))
        self.assertFalse(self.agent_user_owned.is_recipient_whitelisted(CommsChannel.SMS, "+19999999999"))

        # Unsupported channel returns False
        self.assertFalse(self.agent_user_owned.is_sender_whitelisted("slack", "u"))
        self.assertFalse(self.agent_user_owned.is_recipient_whitelisted("slack", "u"))

    def test_default_policy_user_owned(self):
        org_owner = self.owner
        org = Organization.objects.create(name="Acme", slug="acme", created_by=org_owner)
        member = User.objects.create_user(username="mem", email="mem@example.com", password="pw")
        OrganizationMembership.objects.create(
            org=org,
            user=member,
            role=OrganizationMembership.OrgRole.MEMBER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        # NEW: don't reuse self.browser; it's already bound to another PersistentAgent
        org_browser = BrowserUseAgent.objects.create(user=org_owner, name="Org BA")

        agent = PersistentAgent.objects.create(
            user=org_owner,
            organization=org,
            name="OrgA",
            charter="c",
            browser_use_agent=org_browser,  # <= use a fresh browser
        )

        # EMAIL: owner only
        self.assertTrue(self.agent_user_owned.is_sender_whitelisted(CommsChannel.EMAIL, self.owner.email))
        self.assertTrue(self.agent_user_owned.is_recipient_whitelisted(CommsChannel.EMAIL, self.owner.email))
        self.assertFalse(self.agent_user_owned.is_sender_whitelisted(CommsChannel.EMAIL, "x@example.com"))
        self.assertFalse(self.agent_user_owned.is_recipient_whitelisted(CommsChannel.EMAIL, "x@example.com"))

        # SMS: owner's verified only
        UserPhoneNumber.objects.create(user=self.owner, phone_number="+15552223333", is_verified=True)
        self.assertTrue(self.agent_user_owned.is_sender_whitelisted(CommsChannel.SMS, "+15552223333"))
        self.assertTrue(self.agent_user_owned.is_recipient_whitelisted(CommsChannel.SMS, "+15552223333"))
        self.assertFalse(self.agent_user_owned.is_sender_whitelisted(CommsChannel.SMS, "+19998887777"))
        self.assertFalse(self.agent_user_owned.is_recipient_whitelisted(CommsChannel.SMS, "+19998887777"))

    def test_default_policy_org_owned(self):
        # Setup org and membership
        org_owner = self.owner
        org = Organization.objects.create(name="Acme", slug="acme", created_by=org_owner)
        member = User.objects.create_user(username="mem", email="mem@example.com", password="pw")
        OrganizationMembership.objects.create(
            org=org,
            user=member,
            role=OrganizationMembership.OrgRole.MEMBER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        # Use a fresh BrowserUseAgent (don't reuse self.browser)
        org_browser = BrowserUseAgent.objects.create(user=org_owner, name="Org BA")

        # Agent owned by org
        agent = PersistentAgent.objects.create(
            user=org_owner,
            organization=org,
            name="OrgA",
            charter="c",
            browser_use_agent=org_browser,
        )

        # EMAIL: only org members
        self.assertTrue(agent.is_sender_whitelisted(CommsChannel.EMAIL, member.email))
        self.assertTrue(agent.is_recipient_whitelisted(CommsChannel.EMAIL, member.email))
        self.assertFalse(agent.is_sender_whitelisted(CommsChannel.EMAIL, "stranger@example.com"))
        self.assertFalse(agent.is_recipient_whitelisted(CommsChannel.EMAIL, "stranger@example.com"))

            # SMS: only verified numbers of org members
            # NOTE: Temporarily disabled until we add multi player SMS support
