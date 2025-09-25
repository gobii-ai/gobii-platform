from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.tools.web_message_sender import execute_send_web_message
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentMessage,
    make_web_agent_address,
    make_web_user_address,
)


@tag("batch_send_web_message_tool")
class SendWebMessageToolTests(TestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="password123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.owner,
            name="Browser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="Helper",
            charter="Assist the team",
            browser_use_agent=self.browser_agent,
        )

    def test_execute_send_web_message_creates_outbound_message(self):
        params = {
            "user_id": str(self.owner.id),
            "body": "Thanks for the update!",
        }

        result = execute_send_web_message(self.agent, params)

        message = PersistentAgentMessage.objects.get(id=result["message_id"])

        self.assertTrue(message.is_outbound)
        self.assertEqual(message.owner_agent_id, self.agent.id)
        self.assertEqual(message.raw_payload.get("channel"), CommsChannel.WEB)
        self.assertEqual(message.raw_payload.get("user_id"), str(self.owner.id))
        self.assertEqual(message.body, "Thanks for the update!")
        self.assertEqual(message.conversation.address, make_web_user_address(self.owner.id))
        self.assertIsNone(message.to_endpoint)

        # Ensure the agent endpoint was created and linked
        self.assertEqual(message.from_endpoint.address, make_web_agent_address(self.agent.id))

    def test_execute_send_web_message_requires_body(self):
        with self.assertRaises(ValueError):
            execute_send_web_message(self.agent, {"user_id": str(self.owner.id), "body": ""})
