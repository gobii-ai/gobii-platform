from __future__ import annotations

import json
from unittest.mock import patch

from django.test import TestCase, RequestFactory, tag
from django.contrib.auth import get_user_model

from api.models import (
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    CommsAllowlistEntry,
    CommsChannel,
    BrowserUseAgent,
)
from api.webhooks import email_webhook
from config import settings


User = get_user_model()


@tag("batch_allowlist_rules")
class ManualEmailDisplayNameAndCaseTests(TestCase):
    def setUp(self):
        # Enable feature-gated whitelist logic for tests
        self._p_flag = patch("api.models.flag_is_active", return_value=True)
        self._p_switch = patch("api.models.switch_is_active", return_value=True)
        self._p_flag.start(); self._p_switch.start()
        self.addCleanup(self._p_flag.stop); self.addCleanup(self._p_switch.stop)

        self.factory = RequestFactory()
        self.owner = User.objects.create_user(
            username="ownerx", email="ownerx@example.com", password="pw"
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
        resp = email_webhook(req)
        self.assertEqual(resp.status_code, 200)
        mock_ingest.assert_called_once()
