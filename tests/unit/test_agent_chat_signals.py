from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from api.agent.comms.message_service import ingest_inbound_webhook_message
from api.models import (
    AgentSpawnRequest,
    AgentCollaborator,
    BrowserUseAgent,
    BrowserUseAgentTask,
    CommsAllowlistRequest,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentConversation,
    PersistentAgentCommsEndpoint,
    PersistentAgentHumanInputRequest,
    PersistentAgentInboundWebhook,
    PersistentAgentMessage,
    PersistentAgentSecret,
    PersistentAgentStep,
    PersistentAgentToolCall,
    build_web_agent_address,
    build_web_user_address,
)
from console.agent_chat import signals as agent_chat_signals


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
        agent_chat_signals._LAST_PROCESSING_PROFILE_STATE_BY_AGENT_ID.clear()
        self.channel_layer = get_channel_layer()
        self.timeline_channel_name = async_to_sync(self.channel_layer.new_channel)("test.agent.chat.")
        self.owner_profile_channel_name = async_to_sync(self.channel_layer.new_channel)("test.agent.profile.owner.")
        self.collaborator_profile_channel_name = async_to_sync(self.channel_layer.new_channel)(
            "test.agent.profile.collaborator."
        )
        self.owner_user_stream_channel_name = async_to_sync(self.channel_layer.new_channel)("test.agent.user.owner.")
        self.collaborator_user_stream_channel_name = async_to_sync(self.channel_layer.new_channel)(
            "test.agent.user.collaborator."
        )
        self.group_name = f"agent-chat-{self.agent.id}"
        self.owner_profile_group_name = f"agent-chat-user-{self.user.id}"
        self.collaborator_profile_group_name = f"agent-chat-user-{self.collaborator_user.id}"
        self.owner_user_stream_group_name = f"agent-chat-{self.agent.id}-user-{self.user.id}"
        self.collaborator_user_stream_group_name = (
            f"agent-chat-{self.agent.id}-user-{self.collaborator_user.id}"
        )
        async_to_sync(self.channel_layer.group_add)(self.group_name, self.timeline_channel_name)
        async_to_sync(self.channel_layer.group_add)(self.owner_profile_group_name, self.owner_profile_channel_name)
        async_to_sync(self.channel_layer.group_add)(
            self.collaborator_profile_group_name,
            self.collaborator_profile_channel_name,
        )
        async_to_sync(self.channel_layer.group_add)(
            self.owner_user_stream_group_name,
            self.owner_user_stream_channel_name,
        )
        async_to_sync(self.channel_layer.group_add)(
            self.collaborator_user_stream_group_name,
            self.collaborator_user_stream_channel_name,
        )
        self.agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel="web",
            address=build_web_agent_address(self.agent.id),
        )
        self.requester_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel="web",
            address=build_web_user_address(self.user.id, self.agent.id),
        )

    def tearDown(self):
        async_to_sync(self.channel_layer.group_discard)(self.group_name, self.timeline_channel_name)
        async_to_sync(self.channel_layer.group_discard)(self.owner_profile_group_name, self.owner_profile_channel_name)
        async_to_sync(self.channel_layer.group_discard)(
            self.collaborator_profile_group_name,
            self.collaborator_profile_channel_name,
        )
        async_to_sync(self.channel_layer.group_discard)(
            self.owner_user_stream_group_name,
            self.owner_user_stream_channel_name,
        )
        async_to_sync(self.channel_layer.group_discard)(
            self.collaborator_user_stream_group_name,
            self.collaborator_user_stream_channel_name,
        )

    def _drain_timeline_events(self) -> list[dict]:
        return self._drain_channel_events(self.timeline_channel_name)

    def _drain_channel_events(self, channel_name: str) -> list[dict]:
        drained: list[dict] = []
        while True:
            try:
                drained.append(self._receive_with_timeout(channel_name, timeout=0.05))
            except AssertionError:
                break
        return drained

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
        self.assertEqual(timeline.get("agent_id"), str(self.agent.id))
        payload = timeline.get("payload", {})
        self.assertEqual(payload.get("kind"), "steps")
        entries = payload.get("entries", [])
        self.assertTrue(entries)
        self.assertEqual(entries[0].get("toolName"), "test_tool")

        processing = self._receive_with_timeout()
        self.assertEqual(processing.get("type"), "processing_event")
        self.assertEqual(processing.get("agent_id"), str(self.agent.id))
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
    def test_create_video_tool_call_emits_preview_url(self):
        step = PersistentAgentStep.objects.create(agent=self.agent, description="Create video")

        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="create_video",
            tool_params={
                "prompt": "Product teaser reel",
                "file_path": "/exports/hero.mp4",
            },
            result=json.dumps(
                {
                    "status": "ok",
                    "file": "$[/exports/hero.mp4]",
                }
            ),
        )

        timeline = self._receive_with_timeout()
        self.assertEqual(timeline.get("type"), "timeline_event")
        payload = timeline.get("payload", {})
        self.assertEqual(payload.get("kind"), "steps")

        entries = payload.get("entries", [])
        self.assertTrue(entries)
        preview_url = entries[0].get("createVideoUrl")
        self.assertIsInstance(preview_url, str)

        parsed = urlparse(preview_url)
        expected_path = reverse("console_agent_fs_download", kwargs={"agent_id": self.agent.id})
        self.assertEqual(parsed.path, expected_path)
        self.assertEqual(parse_qs(parsed.query).get("path"), ["/exports/hero.mp4"])

    @tag("batch_agent_chat")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_inbound_webhook_message_emits_webhook_timeline_event(self, mock_delay):
        webhook = PersistentAgentInboundWebhook.objects.create(
            agent=self.agent,
            name="Signal Hook",
        )
        self._drain_timeline_events()

        with self.captureOnCommitCallbacks(execute=True):
            ingest_inbound_webhook_message(
                webhook,
                body='{\n  "signal": true\n}',
                raw_payload={
                    "source": "inbound_webhook",
                    "source_kind": "webhook",
                    "source_label": webhook.name,
                    "content_type": "application/json",
                    "method": "POST",
                    "payload_kind": "json",
                    "json_payload": {"signal": True},
                    "webhook_name": webhook.name,
                },
            )

        timeline = self._receive_with_timeout()
        self.assertEqual(timeline.get("type"), "timeline_event")
        payload = timeline.get("payload", {})
        self.assertEqual(payload.get("kind"), "message")
        message_payload = payload.get("message", {})
        self.assertEqual(message_payload.get("sourceKind"), "webhook")
        self.assertEqual(message_payload.get("sourceLabel"), "Signal Hook")
        self.assertEqual(message_payload.get("senderName"), "Signal Hook")
        self.assertEqual(message_payload.get("webhookMeta", {}).get("payloadKind"), "json")
        self.assertEqual(message_payload.get("webhookMeta", {}).get("payload"), {"signal": True})
        mock_delay.assert_called_once_with(str(self.agent.id))

    @tag("batch_agent_chat")
    @patch("console.agent_chat.signals.transition_agent_to_signup_preview_waiting", return_value=False)
    def test_visible_outbound_message_emits_message_notification_event(self, _mock_transition):
        with self.captureOnCommitCallbacks(execute=True):
            message = PersistentAgentMessage.objects.create(
                owner_agent=self.agent,
                is_outbound=True,
                from_endpoint=self.agent_endpoint,
                to_endpoint=self.requester_endpoint,
                body="The agent finished the task.",
                raw_payload={"source": "test"},
            )

        owner_notification = self._receive_with_timeout(self.owner_profile_channel_name)
        collaborator_notification = self._receive_with_timeout(self.collaborator_profile_channel_name)

        self.assertEqual(owner_notification.get("type"), "message_notification_event")
        self.assertEqual(collaborator_notification.get("type"), "message_notification_event")
        self.assertEqual(owner_notification.get("payload", {}).get("agent_id"), str(self.agent.id))
        self.assertEqual(
            owner_notification.get("payload", {}).get("message", {}).get("id"),
            str(message.id),
        )
        self.assertEqual(
            owner_notification.get("payload", {}).get("message", {}).get("body_preview"),
            "The agent finished the task.",
        )
        self.assertEqual(
            owner_notification.get("payload", {}).get("workspace"),
            {
                "type": "personal",
                "id": str(self.user.id),
            },
        )

    @tag("batch_agent_chat")
    def test_inbound_message_does_not_emit_message_notification_event(self):
        with self.captureOnCommitCallbacks(execute=True):
            PersistentAgentMessage.objects.create(
                owner_agent=self.agent,
                is_outbound=False,
                from_endpoint=self.requester_endpoint,
                to_endpoint=self.agent_endpoint,
                body="A user replied",
                raw_payload={"source": "test"},
            )

        with self.assertRaises(AssertionError):
            self._receive_with_timeout(self.owner_profile_channel_name, timeout=0.05)

    @tag("batch_agent_chat")
    def test_hidden_outbound_message_does_not_emit_message_notification_event(self):
        with self.captureOnCommitCallbacks(execute=True):
            PersistentAgentMessage.objects.create(
                owner_agent=self.agent,
                is_outbound=True,
                from_endpoint=self.agent_endpoint,
                to_endpoint=self.requester_endpoint,
                body="This should stay hidden",
                raw_payload={"hide_in_chat": True, "source": "test"},
            )

        with self.assertRaises(AssertionError):
            self._receive_with_timeout(self.owner_profile_channel_name, timeout=0.05)

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
        self.assertIn("/avatar/thumb/", payload.get("agent_avatar_url", ""))

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
    @patch("console.agent_chat.signals.build_processing_snapshot")
    def test_processing_broadcast_emits_agent_profile_event_with_processing_state(self, mock_build_processing_snapshot):
        mock_build_processing_snapshot.return_value = type(
            "Snapshot",
            (),
            {
                "active": True,
                "web_tasks": [],
                "next_scheduled_at": None,
            },
        )()

        with self.captureOnCommitCallbacks(execute=True):
            BrowserUseAgentTask.objects.create(
                agent=self.browser_agent,
                user=self.user,
                prompt="Monitor the queue",
                status=BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
            )

        profile_event = self._receive_with_timeout(self.owner_profile_channel_name)
        self.assertEqual(profile_event.get("type"), "agent_profile_event")
        self.assertTrue(profile_event.get("payload", {}).get("processing_active"))

    @tag("batch_agent_chat")
    @patch("console.agent_chat.signals.build_processing_snapshot")
    def test_processing_broadcast_skips_duplicate_profile_event_when_state_is_unchanged(
        self,
        mock_build_processing_snapshot,
    ):
        mock_build_processing_snapshot.return_value = type(
            "Snapshot",
            (),
            {
                "active": True,
                "web_tasks": [],
                "next_scheduled_at": None,
            },
        )()

        with self.captureOnCommitCallbacks(execute=True):
            BrowserUseAgentTask.objects.create(
                agent=self.browser_agent,
                user=self.user,
                prompt="Monitor the queue",
                status=BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
            )

        first_profile_event = self._receive_with_timeout(self.owner_profile_channel_name)
        self.assertEqual(first_profile_event.get("type"), "agent_profile_event")
        self.assertTrue(first_profile_event.get("payload", {}).get("processing_active"))

        with self.captureOnCommitCallbacks(execute=True):
            BrowserUseAgentTask.objects.create(
                agent=self.browser_agent,
                user=self.user,
                prompt="Keep monitoring the queue",
                status=BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
            )

        with self.assertRaises(AssertionError):
            self._receive_with_timeout(self.owner_profile_channel_name, timeout=0.05)

    @tag("batch_agent_chat")
    def test_collaborator_profile_group_receives_avatar_update(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.agent.avatar.save("avatar-collab.png", ContentFile(b"fake-avatar-bytes"), save=False)
            self.agent.save(update_fields=["avatar"])

        collaborator_profile_event = self._receive_with_timeout(self.collaborator_profile_channel_name)
        self.assertEqual(collaborator_profile_event.get("type"), "agent_profile_event")
        payload = collaborator_profile_event.get("payload", {})
        self.assertEqual(payload.get("agent_id"), str(self.agent.id))
        self.assertIn("/avatar/thumb/", payload.get("agent_avatar_url", ""))

    @tag("batch_agent_chat")
    def test_human_input_request_save_emits_pending_requests_update(self):
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel="web",
            address=build_web_user_address(self.user.id, self.agent.id),
        )
        requested_message = PersistentAgentMessage.objects.create(
            is_outbound=True,
            owner_agent=self.agent,
            from_endpoint=self.agent_endpoint,
            to_endpoint=self.requester_endpoint,
            conversation=conversation,
            body="Need your input",
            raw_payload={"source": "test"},
        )
        self._drain_timeline_events()

        with self.captureOnCommitCallbacks(execute=True):
            PersistentAgentHumanInputRequest.objects.create(
                agent=self.agent,
                conversation=conversation,
                requested_message=requested_message,
                question="What should we do next?",
                options_json=[],
                input_mode=PersistentAgentHumanInputRequest.InputMode.FREE_TEXT_ONLY,
                recipient_channel="web",
                recipient_address=build_web_user_address(self.user.id, self.agent.id),
                requested_via_channel="web",
            )

        realtime_event = self._receive_with_timeout()
        self.assertEqual(realtime_event.get("type"), "human_input_requests_event")
        self.assertEqual(realtime_event.get("agent_id"), str(self.agent.id))
        payload = realtime_event.get("payload", {})
        self.assertEqual(payload.get("agent_id"), str(self.agent.id))
        pending_requests = payload.get("pending_human_input_requests", [])
        self.assertEqual(len(pending_requests), 1)
        self.assertEqual(pending_requests[0].get("question"), "What should we do next?")

    @tag("batch_agent_chat")
    def test_pending_action_updates_are_filtered_per_viewer(self):
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel="web",
            address=build_web_user_address(self.user.id, self.agent.id),
        )

        with self.captureOnCommitCallbacks(execute=True):
            PersistentAgentHumanInputRequest.objects.create(
                agent=self.agent,
                conversation=conversation,
                question="What should we do next?",
                options_json=[],
                input_mode=PersistentAgentHumanInputRequest.InputMode.FREE_TEXT_ONLY,
                requested_via_channel="web",
            )
            AgentSpawnRequest.objects.create(
                agent=self.agent,
                requested_charter="Handle procurement approvals.",
                handoff_message="Take over vendor approvals.",
            )
            requested_secret = PersistentAgentSecret(
                agent=self.agent,
                name="Procurement API Key",
                description="Used for procurement sync",
                secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
                domain_pattern="https://procurement.example.com",
                requested=True,
            )
            requested_secret.key = "procurement_api_key"
            requested_secret.encrypted_value = b""
            requested_secret.save()
            CommsAllowlistRequest.objects.create(
                agent=self.agent,
                channel="email",
                address="approver@example.com",
                reason="Need procurement approval",
                purpose="Approve vendor contract",
            )

        owner_event = self._drain_channel_events(self.owner_user_stream_channel_name)[-1]
        collaborator_event = self._drain_channel_events(self.collaborator_user_stream_channel_name)[-1]

        self.assertEqual(owner_event.get("type"), "pending_action_requests_event")
        self.assertEqual(collaborator_event.get("type"), "pending_action_requests_event")
        self.assertEqual(owner_event.get("agent_id"), str(self.agent.id))
        self.assertEqual(collaborator_event.get("agent_id"), str(self.agent.id))

        owner_kinds = [item.get("kind") for item in owner_event.get("payload", {}).get("pending_action_requests", [])]
        collaborator_kinds = [item.get("kind") for item in collaborator_event.get("payload", {}).get("pending_action_requests", [])]

        self.assertEqual(owner_kinds, ["human_input", "spawn_request", "requested_secrets", "contact_requests"])
        self.assertEqual(collaborator_kinds, ["human_input"])
