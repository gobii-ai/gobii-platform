from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlparse

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from api.models import (
    AgentCollaborator,
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
        cls.collaborator_user = user_model.objects.create_user(
            username="signal-collaborator",
            email="signal-collaborator@example.com",
            password="password123",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Signal Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Signal Tester",
            charter="Ensure realtime emits",
            browser_use_agent=cls.browser_agent,
        )
        AgentCollaborator.objects.create(
            agent=cls.agent,
            user=cls.collaborator_user,
            invited_by=cls.user,
        )

    def setUp(self):
        self.channel_layer = get_channel_layer()
        self.timeline_channel_name = async_to_sync(self.channel_layer.new_channel)("test.agent.chat.")
        self.owner_profile_channel_name = async_to_sync(self.channel_layer.new_channel)("test.agent.profile.owner.")
        self.collaborator_profile_channel_name = async_to_sync(self.channel_layer.new_channel)(
            "test.agent.profile.collaborator."
        )
        self.group_name = f"agent-chat-{self.agent.id}"
        self.owner_profile_group_name = f"agent-chat-user-{self.user.id}"
        self.collaborator_profile_group_name = f"agent-chat-user-{self.collaborator_user.id}"
        async_to_sync(self.channel_layer.group_add)(self.group_name, self.timeline_channel_name)
        async_to_sync(self.channel_layer.group_add)(self.owner_profile_group_name, self.owner_profile_channel_name)
        async_to_sync(self.channel_layer.group_add)(
            self.collaborator_profile_group_name,
            self.collaborator_profile_channel_name,
        )

    def tearDown(self):
        async_to_sync(self.channel_layer.group_discard)(self.group_name, self.timeline_channel_name)
        async_to_sync(self.channel_layer.group_discard)(self.owner_profile_group_name, self.owner_profile_channel_name)
        async_to_sync(self.channel_layer.group_discard)(
            self.collaborator_profile_group_name,
            self.collaborator_profile_channel_name,
        )

    def _receive_with_timeout(self, channel_name: str | None = None, timeout: float = 1.0):
        target_channel_name = channel_name or self.timeline_channel_name

        async def _recv():
            return await asyncio.wait_for(self.channel_layer.receive(target_channel_name), timeout)

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
    def test_create_image_tool_call_emits_preview_url(self):
        step = PersistentAgentStep.objects.create(agent=self.agent, description="Create image")

        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="create_image",
            tool_params={
                "prompt": "Product hero shot",
                "file_path": "/exports/hero.png",
            },
            result=json.dumps(
                {
                    "status": "ok",
                    "file": "$[/exports/hero.png]",
                }
            ),
        )

        timeline = self._receive_with_timeout()
        self.assertEqual(timeline.get("type"), "timeline_event")
        payload = timeline.get("payload", {})
        self.assertEqual(payload.get("kind"), "steps")

        entries = payload.get("entries", [])
        self.assertTrue(entries)
        preview_url = entries[0].get("createImageUrl")
        self.assertIsInstance(preview_url, str)

        parsed = urlparse(preview_url)
        expected_path = reverse("console_agent_fs_download", kwargs={"agent_id": self.agent.id})
        self.assertEqual(parsed.path, expected_path)
        self.assertEqual(parse_qs(parsed.query).get("path"), ["/exports/hero.png"])

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

    @tag("batch_agent_chat")
    def test_avatar_update_emits_agent_profile_event(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.agent.avatar.save("avatar.png", ContentFile(b"fake-avatar-bytes"), save=False)
            self.agent.save(update_fields=["avatar"])

        profile_event = self._receive_with_timeout(self.owner_profile_channel_name)
        self.assertEqual(profile_event.get("type"), "agent_profile_event")
        payload = profile_event.get("payload", {})
        self.assertEqual(payload.get("agent_id"), str(self.agent.id))
        self.assertEqual(payload.get("agent_name"), self.agent.name)
        self.assertEqual(payload.get("mini_description"), "")
        self.assertEqual(payload.get("short_description"), "")
        self.assertIn("/console/agents/", payload.get("agent_avatar_url", ""))

    @tag("batch_agent_chat")
    def test_description_update_emits_agent_profile_event(self):
        self.agent.mini_description = "Outbound sales assistant"
        self.agent.short_description = "Finds qualified leads and drafts personalized outreach."
        with self.captureOnCommitCallbacks(execute=True):
            self.agent.save(update_fields=["mini_description", "short_description"])

        profile_event = self._receive_with_timeout(self.owner_profile_channel_name)
        self.assertEqual(profile_event.get("type"), "agent_profile_event")
        payload = profile_event.get("payload", {})
        self.assertEqual(payload.get("agent_id"), str(self.agent.id))
        self.assertEqual(payload.get("mini_description"), "Outbound sales assistant")
        self.assertEqual(
            payload.get("short_description"),
            "Finds qualified leads and drafts personalized outreach.",
        )

    @tag("batch_agent_chat")
    def test_collaborator_profile_group_receives_avatar_update(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.agent.avatar.save("avatar-collab.png", ContentFile(b"fake-avatar-bytes"), save=False)
            self.agent.save(update_fields=["avatar"])

        collaborator_profile_event = self._receive_with_timeout(self.collaborator_profile_channel_name)
        self.assertEqual(collaborator_profile_event.get("type"), "agent_profile_event")
        payload = collaborator_profile_event.get("payload", {})
        self.assertEqual(payload.get("agent_id"), str(self.agent.id))
        self.assertIn("/console/agents/", payload.get("agent_avatar_url", ""))
