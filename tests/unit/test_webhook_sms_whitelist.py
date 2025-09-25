from __future__ import annotations

from unittest.mock import patch

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
    UserPhoneNumber,
)
from api.webhooks import sms_webhook
from config import settings


User = get_user_model()


@tag("batch_sms")
class SmsWebhookWhitelistTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.owner = User.objects.create_user(
            username="own", email="own@example.com", password="pw"
        )
        self.browser = BrowserUseAgent.objects.create(user=self.owner, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.owner, name="Agent", charter="c", browser_use_agent=self.browser
        )
        self.to_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent, channel=CommsChannel.SMS, address="+15550001111"
        )

    def _req(self, from_number: str, to_number: str, body: str = "hi"):
        return self.factory.post(
            f"/api/webhooks/inbound/sms/?t={settings.TWILIO_INCOMING_WEBHOOK_TOKEN}",
            data={
                "From": from_number,
                "To": to_number,
                "Body": body,
            },
        )

    @tag("batch_sms")
    @patch("api.webhooks.ingest_inbound_message")
    def test_manual_allowlist_sender_allowed(self, mock_ingest):
        # Switch agent policy to MANUAL and add sender to allowlist
        self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
        self.agent.save(update_fields=["whitelist_policy"])
        CommsAllowlistEntry.objects.create(
            agent=self.agent, channel=CommsChannel.SMS, address="+15551234567"
        )

        req = self._req("+15551234567", self.to_ep.address)
        resp = sms_webhook(req)
        self.assertEqual(resp.status_code, 200)
        mock_ingest.assert_called_once()

    @tag("batch_sms")
    @patch("api.webhooks.ingest_inbound_message")
    def test_manual_allowlist_sender_rejected(self, mock_ingest):
        self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
        self.agent.save(update_fields=["whitelist_policy"])
        req = self._req("+19998887777", self.to_ep.address)
        resp = sms_webhook(req)
        self.assertEqual(resp.status_code, 200)
        mock_ingest.assert_not_called()

    @patch("api.webhooks.ingest_inbound_message")
    def test_default_policy_user_owned_sender(self, mock_ingest):
        # Verified owner number allowed
        UserPhoneNumber.objects.create(user=self.owner, phone_number="+15554443333", is_verified=True)
        req = self._req("+15554443333", self.to_ep.address)
        resp = sms_webhook(req)
        self.assertEqual(resp.status_code, 200)
        mock_ingest.assert_called_once()

    @patch("api.webhooks.ingest_inbound_message")
    def test_default_policy_org_owned_sender(self, mock_ingest):
        # Make agent org-owned and add an active member with a verified phone
        org = Organization.objects.create(name="Acme", slug="acme", created_by=self.owner)
        billing = org.billing
        billing.purchased_seats = 1
        billing.save(update_fields=["purchased_seats"])
        self.agent.organization = org
        self.agent.save(update_fields=["organization"])

        member = User.objects.create_user(username="m", email="m@example.com", password="pw")
        OrganizationMembership.objects.create(
            org=org,
            user=member,
            role=OrganizationMembership.OrgRole.MEMBER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        UserPhoneNumber.objects.create(user=member, phone_number="+15551112222", is_verified=True)

        req = self._req("+15551112222", self.to_ep.address)
        resp = sms_webhook(req)
        self.assertEqual(resp.status_code, 200)
        mock_ingest.assert_called_once()

    @patch("api.webhooks.ingest_inbound_message")
    def test_unroutable_number_discards(self, mock_ingest):
        req = self._req("+15550000000", "+19999999999")  # no endpoint for this To
        resp = sms_webhook(req)
        self.assertEqual(resp.status_code, 200)
        mock_ingest.assert_not_called()
