import json
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.test import TestCase, RequestFactory, tag
from django.contrib.auth import get_user_model

from api.models import (
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    CommsAllowlistEntry,
    CommsChannel,
    BrowserUseAgent,
    Organization,
    OrganizationMembership,
)
from api.webhooks import email_webhook_postmark
from config import settings


User = get_user_model()


@tag("batch_email_allowlist")
class ManualAllowlistEmailTests(TestCase):
    def setUp(self):
        # No feature flags; behavior is always-on now
        self.factory = RequestFactory()
        self.owner = User.objects.create_user(
            username="owner1", email="owner1@example.com", password="pw"
        )
        # Email verification is required for inbound email processing
        EmailAddress.objects.create(
            user=self.owner,
            email=self.owner.email,
            verified=True,
            primary=True,
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.owner, name="BA1")
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="AgentManual",
            charter="c",
            browser_use_agent=self.browser_agent,
            whitelist_policy=PersistentAgent.WhitelistPolicy.MANUAL,
        )
        self.agent_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent, channel=CommsChannel.EMAIL, address="agent@test.gobii"
        )

    def _postmark_req(self, from_email: str):
        # Use the new Postmark "Full" format
        to_address = self.agent_ep.address
        payload = {
            "From": from_email,
            "To": to_address,  # Keep for backward compatibility
            "ToFull": [{"Email": to_address, "Name": "", "MailboxHash": ""}],
            "CcFull": [],
            "BccFull": [],
            "Subject": "t",
            "TextBody": "hi",
        }
        return self.factory.post(
            "/api/webhooks/inbound/email/",
            data=json.dumps(payload),
            content_type="application/json",
            query_params={"t": settings.POSTMARK_INCOMING_WEBHOOK_TOKEN},
        )

    @tag("batch_email_allowlist")
    @patch("api.webhooks.ingest_inbound_message")
    def test_manual_email_allowed(self, mock_ingest):
        CommsAllowlistEntry.objects.create(
            agent=self.agent, channel=CommsChannel.EMAIL, address="friend@example.com"
        )
        req = self._postmark_req("friend@example.com")
        resp = email_webhook_postmark(req)
        self.assertEqual(resp.status_code, 200)
        mock_ingest.assert_called_once()

    @tag("batch_email_allowlist")
    @patch("api.webhooks.ingest_inbound_message")
    def test_manual_email_rejects_others(self, mock_ingest):
        CommsAllowlistEntry.objects.create(
            agent=self.agent, channel=CommsChannel.EMAIL, address="friend@example.com"
        )
        req = self._postmark_req("stranger@example.com")
        resp = email_webhook_postmark(req)
        self.assertEqual(resp.status_code, 200)
        mock_ingest.assert_not_called()


@tag("batch_email_allowlist")
class OrgDefaultAllowlistEmailTests(TestCase):
    def setUp(self):
        # No feature flags; behavior is always-on now
        self.factory = RequestFactory()
        self.owner = User.objects.create_user(
            username="owner2", email="owner2@example.com", password="pw"
        )
        # Email verification is required for inbound email processing
        EmailAddress.objects.create(
            user=self.owner,
            email=self.owner.email,
            verified=True,
            primary=True,
        )
        self.member = User.objects.create_user(
            username="member1", email="member1@example.com", password="pw"
        )
        self.non_member = User.objects.create_user(
            username="nomem", email="nomem@example.com", password="pw"
        )
        self.org = Organization.objects.create(
            name="Acme", slug="acme", created_by=self.owner
        )
        billing = self.org.billing
        billing.purchased_seats = 1
        billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.member,
            role=OrganizationMembership.OrgRole.MEMBER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.owner, name="BA2")
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            organization=self.org,
            name="AgentOrg",
            charter="c",
            browser_use_agent=self.browser_agent,
            whitelist_policy=PersistentAgent.WhitelistPolicy.DEFAULT,
        )
        self.agent_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent, channel=CommsChannel.EMAIL, address="agent2@test.gobii"
        )

    def _postmark_req(self, from_email: str):
        # Use the new Postmark "Full" format
        to_address = self.agent_ep.address
        payload = {
            "From": from_email,
            "To": to_address,  # Keep for backward compatibility
            "ToFull": [{"Email": to_address, "Name": "", "MailboxHash": ""}],
            "CcFull": [],
            "BccFull": [],
            "Subject": "t",
            "TextBody": "hi",
        }
        return self.factory.post(
            "/api/webhooks/inbound/email/",
            data=json.dumps(payload),
            content_type="application/json",
            query_params={"t": settings.POSTMARK_INCOMING_WEBHOOK_TOKEN},
        )

    @tag("batch_email_allowlist")
    @patch("api.webhooks.ingest_inbound_message")
    def test_org_member_email_allowed(self, mock_ingest):
        req = self._postmark_req(self.member.email)
        resp = email_webhook_postmark(req)
        self.assertEqual(resp.status_code, 200)
        mock_ingest.assert_called_once()

    @tag("batch_email_allowlist")
    @patch("api.webhooks.ingest_inbound_message")
    def test_non_member_email_rejected(self, mock_ingest):
        req = self._postmark_req(self.non_member.email)
        resp = email_webhook_postmark(req)
        self.assertEqual(resp.status_code, 200)
        mock_ingest.assert_not_called()


@tag("batch_email_allowlist")
class ManualAllowlistSMSTests(TestCase):
    def setUp(self):
        # No feature flags; behavior is always-on now
        self.owner = User.objects.create_user(
            username="owner3", email="owner3@example.com", password="pw"
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.owner, name="BA3")
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="AgentManualSMS",
            charter="c",
            browser_use_agent=self.browser_agent,
            whitelist_policy=PersistentAgent.WhitelistPolicy.MANUAL,
        )

    # TODO: Re-enable when we support multi-player SMS
    def test_sms_recipient_manual(self):
        return
        CommsAllowlistEntry.objects.create(
            agent=self.agent, channel=CommsChannel.SMS, address="+15551234567"
        )
        self.assertTrue(self.agent.is_recipient_whitelisted(CommsChannel.SMS, "+15551234567"))
        self.assertFalse(self.agent.is_recipient_whitelisted(CommsChannel.SMS, "+15557654321"))
