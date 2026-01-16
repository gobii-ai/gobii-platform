from __future__ import annotations

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
)
from api.webhooks import email_webhook_postmark
from config import settings


User = get_user_model()


@tag("batch_allowlist_rules")
class ManualEmailDisplayNameAndCaseTests(TestCase):
    def setUp(self):
        # No feature flags; behavior is always-on now

        self.factory = RequestFactory()
        self.owner = User.objects.create_user(
            username="ownerx", email="ownerx@example.com", password="pw"
        )
        # Email verification is required for inbound email processing
        EmailAddress.objects.create(
            user=self.owner,
            email=self.owner.email,
            verified=True,
            primary=True,
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.owner, name="BAx")
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="AgentManualEmail",
            charter="c",
            browser_use_agent=self.browser_agent,
            whitelist_policy=PersistentAgent.WhitelistPolicy.MANUAL,
        )
        self.agent_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent, channel=CommsChannel.EMAIL, address="agentx@test.gobii"
        )

        CommsAllowlistEntry.objects.create(
            agent=self.agent, channel=CommsChannel.EMAIL, address="friend@example.com"
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
            "TextBody": "hi"
        }
        return self.factory.post(
            "/api/webhooks/inbound/email/",
            data=json.dumps(payload),
            content_type="application/json",
            query_params={"t": settings.POSTMARK_INCOMING_WEBHOOK_TOKEN},
        )

    @patch("api.webhooks.ingest_inbound_message")
    def test_display_name_and_case_insensitive_match(self, mock_ingest):
        req = self._postmark_req('"Friend" <FRIEND@EXAMPLE.COM>')
        resp = email_webhook_postmark(req)
        self.assertEqual(resp.status_code, 200)
        mock_ingest.assert_called_once()
