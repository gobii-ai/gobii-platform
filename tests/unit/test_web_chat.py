from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from api.agent.tools.web_chat_sender import execute_send_chat_message
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    DeliveryStatus,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentStep,
    build_web_user_address,
)

User = get_user_model()


def _create_browser_agent(user, name):
    with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
        return BrowserUseAgent.objects.create(user=user, name=name)


class WebChatToolTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("web@example.com", "web@example.com", "pw")
        self.browser_agent = _create_browser_agent(self.user, "Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Web Agent",
            charter="Do things",
            browser_use_agent=self.browser_agent,
        )
        self.user_address = build_web_user_address(self.user.id, self.agent.id)

    def test_execute_send_chat_message_creates_outbound_message(self):
        result = execute_send_chat_message(
            self.agent,
            {"body": "Howdy from tool", "to_address": self.user_address},
        )
        self.assertEqual(result["status"], "ok")

        message = PersistentAgentMessage.objects.get(owner_agent=self.agent, is_outbound=True)
        self.assertEqual(message.body, "Howdy from tool")
        self.assertEqual(message.from_endpoint.channel, CommsChannel.WEB)
        self.assertEqual(message.conversation.channel, CommsChannel.WEB)
        self.assertEqual(message.latest_status, DeliveryStatus.DELIVERED)
        self.assertFalse(message.latest_error_code)

    def test_execute_send_chat_message_rejects_unseen_address(self):
        stranger_address = build_web_user_address(self.user.id + 999, self.agent.id)
        result = execute_send_chat_message(
            self.agent,
            {"body": "should fail", "to_address": stranger_address},
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("authorized", result["message"].lower())


class WebChatViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("viewer@example.com", "viewer@example.com", "pw")
        self.browser_agent = _create_browser_agent(self.user, "Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Viewer Agent",
            charter="Observe",
            browser_use_agent=self.browser_agent,
        )
        self.client.force_login(self.user)

    @patch("console.views.process_agent_events_task.delay")
    def test_agent_chat_send_creates_inbound_message(self, mock_delay):
        url = reverse("agent_chat_send", args=[self.agent.id])
        payload = {"body": "Ping the agent"}
        resp = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertTrue(PersistentAgentMessage.objects.filter(owner_agent=self.agent, is_outbound=False, body="Ping the agent").exists())
        self.assertTrue(PersistentAgentMessage.objects.filter(owner_agent=self.agent).exists())

    def test_agent_chat_history_returns_events(self):
        # Seed one inbound message and one step
        conversation = execute_send_chat_message(
            self.agent,
            {"body": "Seed", "to_address": build_web_user_address(self.user.id, self.agent.id)},
        )
        self.assertEqual(conversation["status"], "ok")
        PersistentAgentStep.objects.create(agent=self.agent, description="Step summary")

        url = reverse("agent_chat_history", args=[self.agent.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(len(data["events"]), 2)
        self.assertIn("agent_working", data)

    def test_access_control_blocks_other_users(self):
        other = User.objects.create_user("other@example.com", "other@example.com", "pw")
        self.client.force_login(other)
        url = reverse("agent_chat_history", args=[self.agent.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)
