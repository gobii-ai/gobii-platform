from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings, tag

from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentToolCall,
)

CHANNEL_LAYER_SETTINGS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}


@override_settings(CHANNEL_LAYERS=CHANNEL_LAYER_SETTINGS)
class AgentChatAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="agent-owner",
            email="owner@example.com",
            password="password123",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Browser Agent")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Console Tester",
            charter="Do useful things",
            browser_use_agent=cls.browser_agent,
        )

        cls.agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=cls.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.com",
            is_primary=True,
        )
        cls.user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.EMAIL,
            address="user@example.com",
            is_primary=False,
        )
        cls.conversation = PersistentAgentConversation.objects.create(
            owner_agent=cls.agent,
            channel=CommsChannel.EMAIL,
            address="user@example.com",
        )

        PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=cls.user_endpoint,
            conversation=cls.conversation,
            body="Hello from the owner",
            owner_agent=cls.agent,
        )

        step = PersistentAgentStep.objects.create(
            agent=cls.agent,
            description="Send recap email",
        )
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="send_email",
            tool_params={"to": "user@example.com"},
            result="queued",
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    @tag("batch_agent_chat")
    def test_timeline_endpoint_returns_expected_events(self):
        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        events = payload.get("events", [])
        self.assertGreaterEqual(len(events), 2)
        kinds = {event.get("kind") for event in events}
        self.assertIn("message", kinds)
        self.assertIn("steps", kinds)

        message_event = next(event for event in events if event["kind"] == "message")
        self.assertEqual(message_event["message"]["bodyText"], "Hello from the owner")

        tool_cluster = next(event for event in events if event["kind"] == "steps")
        self.assertEqual(tool_cluster["entries"][0]["toolName"], "send_email")
        self.assertTrue(payload.get("newest_cursor"))
        self.assertIsNotNone(payload.get("processing_active"))

    @tag("batch_agent_chat")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_message_post_creates_console_message(self, mock_delay):
        body = "Run weekly summary"
        response = self.client.post(
            f"/console/api/agents/{self.agent.id}/messages/",
            data={"body": body},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertIn("event", payload)
        event = payload["event"]
        self.assertEqual(event["kind"], "message")
        self.assertEqual(event["message"]["bodyText"], body)
        self.assertEqual(event["message"]["channel"], CommsChannel.OTHER)

        stored = (
            PersistentAgentMessage.objects.filter(owner_agent=self.agent, body=body)
            .order_by("-timestamp")
            .first()
        )
        self.assertIsNotNone(stored)
        self.assertEqual(stored.from_endpoint.address, f"console-user:{self.user.id}")
        self.assertEqual(stored.conversation.address, f"console-user:{self.user.id}")
        mock_delay.assert_called_once()
