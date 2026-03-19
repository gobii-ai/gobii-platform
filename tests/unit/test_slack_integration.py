"""
Unit tests for Slack comms channel integration: adapter parsing, webhook
signature verification, outbound delivery with thread policies, and
settings API views.
"""

import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory, override_settings, tag

from api.agent.comms.adapters import SlackEventAdapter, ParsedMessage
from api.models import (
    AgentSlackConfig,
    BrowserUseAgent,
    CommsChannel,
    DeliveryStatus,
    OutboundMessageAttempt,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
)

User = get_user_model()


def _make_slack_signature(body: str, timestamp: str, secret: str = "test-signing-secret") -> str:
    sig_basestring = f"v0:{timestamp}:{body}"
    return "v0=" + hmac.HMAC(
        secret.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@tag("batch_slack_integration")
class SlackEventAdapterTests(TestCase):
    """Test SlackEventAdapter.parse_event normalizes Slack payloads."""

    def test_parse_basic_message(self):
        event = {
            "type": "message",
            "user": "U12345",
            "text": "Hello agent!",
            "channel": "C99999",
            "ts": "1234567890.123456",
        }
        parsed = SlackEventAdapter.parse_event(event, "slack:C99999#T00001")

        self.assertIsInstance(parsed, ParsedMessage)
        self.assertEqual(parsed.sender, "U12345")
        self.assertEqual(parsed.recipient, "slack:C99999#T00001")
        self.assertIsNone(parsed.subject)
        self.assertEqual(parsed.body, "Hello agent!")
        self.assertEqual(parsed.msg_channel, CommsChannel.SLACK)
        self.assertEqual(parsed.attachments, [])

    def test_parse_message_with_files(self):
        event = {
            "type": "message",
            "user": "U12345",
            "text": "See attached",
            "channel": "C99999",
            "ts": "1234567890.123456",
            "files": [
                {
                    "url_private_download": "https://files.slack.com/file1.pdf",
                    "mimetype": "application/pdf",
                    "name": "report.pdf",
                    "size": 1024,
                },
            ],
        }
        parsed = SlackEventAdapter.parse_event(event, "slack:C99999#T00001")

        self.assertEqual(len(parsed.attachments), 1)
        self.assertEqual(parsed.attachments[0]["url"], "https://files.slack.com/file1.pdf")
        self.assertEqual(parsed.attachments[0]["content_type"], "application/pdf")
        self.assertEqual(parsed.attachments[0]["name"], "report.pdf")

    def test_parse_threaded_message_preserves_thread_ts(self):
        event = {
            "type": "message",
            "user": "U12345",
            "text": "Threaded reply",
            "channel": "C99999",
            "ts": "1234567890.200000",
            "thread_ts": "1234567890.100000",
        }
        parsed = SlackEventAdapter.parse_event(event, "slack:C99999#T00001")

        self.assertEqual(parsed.raw_payload["thread_ts"], "1234567890.100000")
        self.assertEqual(parsed.raw_payload["ts"], "1234567890.200000")

    def test_parse_empty_body(self):
        event = {
            "type": "message",
            "user": "U12345",
            "channel": "C99999",
            "ts": "1234567890.123456",
        }
        parsed = SlackEventAdapter.parse_event(event, "slack:C99999#T00001")

        self.assertEqual(parsed.body, "")
        self.assertEqual(parsed.sender, "U12345")


@tag("batch_slack_integration")
class SlackWebhookSignatureTests(TestCase):
    """Test Slack webhook signature verification."""

    @patch("config.settings.SLACK_SIGNING_SECRET", "test-signing-secret")
    def test_valid_signature_passes(self):
        from api.webhooks import _verify_slack_signature

        body = '{"type": "event_callback"}'
        timestamp = str(int(time.time()))
        signature = _make_slack_signature(body, timestamp)

        factory = RequestFactory()
        request = factory.post(
            "/api/webhooks/inbound/slack/",
            data=body,
            content_type="application/json",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_SLACK_SIGNATURE=signature,
        )

        self.assertTrue(_verify_slack_signature(request))

    @patch("config.settings.SLACK_SIGNING_SECRET", "test-signing-secret")
    def test_invalid_signature_fails(self):
        from api.webhooks import _verify_slack_signature

        body = '{"type": "event_callback"}'
        timestamp = str(int(time.time()))

        factory = RequestFactory()
        request = factory.post(
            "/api/webhooks/inbound/slack/",
            data=body,
            content_type="application/json",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_SLACK_SIGNATURE="v0=invalid",
        )

        self.assertFalse(_verify_slack_signature(request))

    @patch("config.settings.SLACK_SIGNING_SECRET", "test-signing-secret")
    def test_missing_headers_fails(self):
        from api.webhooks import _verify_slack_signature

        factory = RequestFactory()
        request = factory.post(
            "/api/webhooks/inbound/slack/",
            data="{}",
            content_type="application/json",
        )

        self.assertFalse(_verify_slack_signature(request))

    @patch("config.settings.SLACK_SIGNING_SECRET", "test-signing-secret")
    def test_expired_timestamp_fails(self):
        from api.webhooks import _verify_slack_signature

        body = '{"type": "event_callback"}'
        timestamp = str(int(time.time()) - 600)  # 10 minutes ago
        signature = _make_slack_signature(body, timestamp)

        factory = RequestFactory()
        request = factory.post(
            "/api/webhooks/inbound/slack/",
            data=body,
            content_type="application/json",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_SLACK_SIGNATURE=signature,
        )

        self.assertFalse(_verify_slack_signature(request))


@tag("batch_slack_integration")
class SlackWebhookViewTests(TestCase):
    """Test the slack_events_webhook view."""

    def test_url_verification_returns_challenge(self):
        from api.webhooks import slack_events_webhook

        body = json.dumps({"type": "url_verification", "challenge": "abc123"})
        factory = RequestFactory()
        request = factory.post(
            "/api/webhooks/inbound/slack/",
            data=body,
            content_type="application/json",
        )

        response = slack_events_webhook(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["challenge"], "abc123")

    @patch("config.settings.SLACK_SIGNING_SECRET", "test-signing-secret")
    def test_bot_messages_ignored(self):
        from api.webhooks import slack_events_webhook

        body = json.dumps({
            "type": "event_callback",
            "team_id": "T00001",
            "event": {
                "type": "message",
                "user": "U12345",
                "bot_id": "B12345",
                "text": "Bot message",
                "channel": "C99999",
                "ts": "1234567890.123456",
            },
        })
        timestamp = str(int(time.time()))
        signature = _make_slack_signature(body, timestamp)

        factory = RequestFactory()
        request = factory.post(
            "/api/webhooks/inbound/slack/",
            data=body,
            content_type="application/json",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_SLACK_SIGNATURE=signature,
        )

        response = slack_events_webhook(request)

        self.assertEqual(response.status_code, 200)


@tag("batch_slack_integration")
class SlackMarkdownConversionTests(TestCase):
    """Test Markdown to Slack mrkdwn conversion."""

    def test_bold_conversion(self):
        from api.agent.comms.outbound_delivery import _convert_markdown_to_slack_mrkdwn

        self.assertEqual(_convert_markdown_to_slack_mrkdwn("**bold**"), "*bold*")

    def test_link_conversion(self):
        from api.agent.comms.outbound_delivery import _convert_markdown_to_slack_mrkdwn

        result = _convert_markdown_to_slack_mrkdwn("[click here](https://example.com)")
        self.assertEqual(result, "<https://example.com|click here>")

    def test_empty_string(self):
        from api.agent.comms.outbound_delivery import _convert_markdown_to_slack_mrkdwn

        self.assertEqual(_convert_markdown_to_slack_mrkdwn(""), "")

    def test_plain_text_unchanged(self):
        from api.agent.comms.outbound_delivery import _convert_markdown_to_slack_mrkdwn

        self.assertEqual(_convert_markdown_to_slack_mrkdwn("hello world"), "hello world")


@tag("batch_slack_integration")
class SlackThreadPolicyTests(TestCase):
    """Test thread_policy logic in deliver_agent_slack."""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="BA")
        self.agent = PersistentAgent.objects.create(
            name="Test Agent",
            user=self.user,
            charter="test",
            browser_use_agent=self.browser_agent,
        )
        self.from_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.SLACK,
            address="slack:C99999#T00001",
            owner_agent=self.agent,
            is_primary=True,
        )
        self.to_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.SLACK,
            address="U12345",
        )
        self.conversation = PersistentAgentConversation.objects.create(
            channel=CommsChannel.SLACK,
            address="slack:C99999#T00001",
            owner_agent=self.agent,
        )

    def _create_inbound_message(self, raw_payload):
        return PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.to_endpoint,
            to_endpoint=self.from_endpoint,
            conversation=self.conversation,
            is_outbound=False,
            body="inbound",
            raw_payload=raw_payload,
        )

    def _create_outbound_message(self):
        return PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.from_endpoint,
            to_endpoint=self.to_endpoint,
            conversation=self.conversation,
            is_outbound=True,
            body="Hello from agent!",
        )

    @patch("slack_sdk.WebClient")
    def test_auto_policy_threads_when_inbound_threaded(self, MockWebClient):
        """Auto policy should reply in thread if inbound was in a thread."""
        self._create_inbound_message({
            "channel": "C99999",
            "ts": "1234567890.100000",
            "thread_ts": "1234567890.000000",
        })
        config = AgentSlackConfig.objects.create(
            endpoint=self.from_endpoint,
            channel_id="C99999",
            thread_policy="auto",
            is_enabled=True,
        )

        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "1234567890.300000"}
        MockWebClient.return_value = mock_client

        message = self._create_outbound_message()

        from api.agent.comms.outbound_delivery import deliver_agent_slack

        with override_settings(SLACK_BOT_TOKEN="xoxb-test"):
            result = deliver_agent_slack(message)

        self.assertTrue(result)
        call_kwargs = mock_client.chat_postMessage.call_args[1]
        self.assertEqual(call_kwargs["thread_ts"], "1234567890.000000")

    @patch("slack_sdk.WebClient")
    def test_auto_policy_no_thread_when_inbound_not_threaded(self, MockWebClient):
        """Auto policy should not thread if inbound was top-level."""
        self._create_inbound_message({
            "channel": "C99999",
            "ts": "1234567890.100000",
        })
        AgentSlackConfig.objects.create(
            endpoint=self.from_endpoint,
            channel_id="C99999",
            thread_policy="auto",
            is_enabled=True,
        )

        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "1234567890.300000"}
        MockWebClient.return_value = mock_client

        message = self._create_outbound_message()

        from api.agent.comms.outbound_delivery import deliver_agent_slack

        with override_settings(SLACK_BOT_TOKEN="xoxb-test"):
            result = deliver_agent_slack(message)

        self.assertTrue(result)
        call_kwargs = mock_client.chat_postMessage.call_args[1]
        self.assertNotIn("thread_ts", call_kwargs)

    @patch("slack_sdk.WebClient")
    def test_always_policy_forces_thread(self, MockWebClient):
        """Always policy should always reply in thread."""
        self._create_inbound_message({
            "channel": "C99999",
            "ts": "1234567890.100000",
        })
        AgentSlackConfig.objects.create(
            endpoint=self.from_endpoint,
            channel_id="C99999",
            thread_policy="always",
            is_enabled=True,
        )

        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "1234567890.300000"}
        MockWebClient.return_value = mock_client

        message = self._create_outbound_message()

        from api.agent.comms.outbound_delivery import deliver_agent_slack

        with override_settings(SLACK_BOT_TOKEN="xoxb-test"):
            result = deliver_agent_slack(message)

        self.assertTrue(result)
        call_kwargs = mock_client.chat_postMessage.call_args[1]
        # Should use the inbound ts as thread_ts since there's no thread_ts
        self.assertEqual(call_kwargs["thread_ts"], "1234567890.100000")

    @patch("slack_sdk.WebClient")
    def test_never_policy_no_thread(self, MockWebClient):
        """Never policy should never use thread_ts."""
        self._create_inbound_message({
            "channel": "C99999",
            "ts": "1234567890.100000",
            "thread_ts": "1234567890.000000",
        })
        AgentSlackConfig.objects.create(
            endpoint=self.from_endpoint,
            channel_id="C99999",
            thread_policy="never",
            is_enabled=True,
        )

        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "1234567890.300000"}
        MockWebClient.return_value = mock_client

        message = self._create_outbound_message()

        from api.agent.comms.outbound_delivery import deliver_agent_slack

        with override_settings(SLACK_BOT_TOKEN="xoxb-test"):
            result = deliver_agent_slack(message)

        self.assertTrue(result)
        call_kwargs = mock_client.chat_postMessage.call_args[1]
        self.assertNotIn("thread_ts", call_kwargs)
