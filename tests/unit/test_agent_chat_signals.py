from __future__ import annotations

import asyncio

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentStep,
    PersistentAgentToolCall,
)


CHANNEL_LAYER_SETTINGS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}


@override_settings(CHANNEL_LAYERS=CHANNEL_LAYER_SETTINGS)
class AgentChatSignalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="signal-owner",
            email="signal-owner@example.com",
            password="password123",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Signal Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Signal Tester",
            charter="Ensure realtime emits",
            browser_use_agent=cls.browser_agent,
        )

    def setUp(self):
        self.channel_layer = get_channel_layer()
        self.channel_name = async_to_sync(self.channel_layer.new_channel)("test.agent.chat.")
        self.group_name = f"agent-chat-{self.agent.id}"
        async_to_sync(self.channel_layer.group_add)(self.group_name, self.channel_name)

    def tearDown(self):
        async_to_sync(self.channel_layer.group_discard)(self.group_name, self.channel_name)

    def _receive_with_timeout(self, timeout: float = 1.0):
        async def _recv():
            return await asyncio.wait_for(self.channel_layer.receive(self.channel_name), timeout)

        try:
            return async_to_sync(_recv)()
        except asyncio.TimeoutError as exc:  # pragma: no cover - defensive assertion clarity
            self.fail(f"Timed out waiting for channel message: {exc}")

    @tag("batch_agent_chat")
    def test_tool_call_creation_emits_timeline_event(self):
        step = PersistentAgentStep.objects.create(agent=self.agent, description="Call tool")

        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="test_tool",
            tool_params={"arg": 1},
            result="ok",
        )

        timeline = self._receive_with_timeout()
        self.assertEqual(timeline.get("type"), "timeline_event")
        payload = timeline.get("payload", {})
        self.assertEqual(payload.get("kind"), "steps")
        entries = payload.get("entries", [])
        self.assertTrue(entries)
        self.assertEqual(entries[0].get("toolName"), "test_tool")

        processing = self._receive_with_timeout()
        self.assertEqual(processing.get("type"), "processing_event")
        processing_payload = processing.get("payload", {})
        self.assertIn("active", processing_payload)

    @tag("batch_agent_chat")
    def test_completion_emits_thinking_timeline_event(self):
        completion = PersistentAgentCompletion.objects.create(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.ORCHESTRATOR,
            thinking_content="Thinking output",
        )

        timeline = self._receive_with_timeout()
        self.assertEqual(timeline.get("type"), "timeline_event")
        payload = timeline.get("payload", {})
        self.assertEqual(payload.get("kind"), "thinking")
        self.assertEqual(payload.get("completionId"), str(completion.id))
