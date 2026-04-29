from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from allauth.account.models import EmailAddress
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message, ingest_inbound_webhook_message
from api.models import (
    AgentSpawnRequest,
    AgentCollaborator,
    BrowserUseAgent,
    BrowserUseAgentTask,
    CommsChannel,
    CommsAllowlistRequest,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentConversation,
    PersistentAgentCommsEndpoint,
    PersistentAgentHumanInputRequest,
    PersistentAgentInboundWebhook,
    PersistentAgentMessage,
    PersistentAgentMessageRead,
    PersistentAgentSecret,
    PersistentAgentStep,
    PersistentAgentToolCall,
    UserPhoneNumber,
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
        EmailAddress.objects.create(user=cls.user, email=cls.user.email, verified=True, primary=True)
        EmailAddress.objects.create(
            user=cls.collaborator_user,
            email=cls.collaborator_user.email,
            verified=True,
            primary=True,
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

    def _emit_outbound_message_notification_preview(self, body: str) -> str:
        with patch("console.agent_chat.signals.transition_agent_to_signup_preview_waiting", return_value=False):
            with self.captureOnCommitCallbacks(execute=True):
                PersistentAgentMessage.objects.create(
                    owner_agent=self.agent,
                    is_outbound=True,
                    from_endpoint=self.agent_endpoint,
                    to_endpoint=self.requester_endpoint,
                    body=body,
                    raw_payload={"source": "test"},
                )

        notification = self._receive_with_timeout(self.owner_profile_channel_name)
        self.assertEqual(notification.get("type"), "message_notification_event")
        return notification.get("payload", {}).get("message", {}).get("body_preview")

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
        self.assertTrue(owner_notification.get("payload", {}).get("has_unread_agent_message"))
        self.assertEqual(owner_notification.get("payload", {}).get("latest_agent_message_id"), str(message.id))
        self.assertIsNone(owner_notification.get("payload", {}).get("latest_agent_message_read_at"))
        self.assertEqual(
            owner_notification.get("payload", {}).get("workspace"),
            {
                "type": "personal",
                "id": str(self.user.id),
            },
        )

    @tag("batch_agent_chat")
    def test_outbound_message_notification_preview_strips_html(self):
        body_preview = self._emit_outbound_message_notification_preview(
            "<p>Hello <strong>there</strong></p><script>alert('x')</script>"
        )

        self.assertEqual(body_preview, "Hello there")

    @tag("batch_agent_chat")
    def test_outbound_message_notification_preview_strips_markdown(self):
        body_preview = self._emit_outbound_message_notification_preview(
            "# Done\n**Finished** [details](https://example.com/details)"
        )

        self.assertEqual(body_preview, "Done Finished details")

    @tag("batch_agent_chat")
    def test_outbound_message_notification_preview_falls_back_when_sanitized_empty(self):
        body_preview = self._emit_outbound_message_notification_preview(
            "<script>alert('x')</script><style>body { color: red; }</style>"
        )

        self.assertEqual(body_preview, "New agent message")

    @tag("batch_agent_chat")
    def test_outbound_message_notification_preview_truncates_after_sanitizing(self):
        body = "<p>**" + ("Finished task " * 20) + "**</p>"
        body_preview = self._emit_outbound_message_notification_preview(body)

        self.assertNotIn("<p>", body_preview)
        self.assertNotIn("**", body_preview)
        self.assertTrue(body_preview.startswith("Finished task Finished task"))
        self.assertLessEqual(len(body_preview), 160)

    @tag("batch_agent_chat")
    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
    @patch("console.agent_chat.signals.transition_agent_to_signup_preview_waiting", return_value=False)
    def test_roster_flags_latest_visible_unread_message(self, _mock_transition):
        visible_message = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=True,
            from_endpoint=self.agent_endpoint,
            to_endpoint=self.requester_endpoint,
            body="Visible unread",
            raw_payload={},
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=True,
            from_endpoint=self.agent_endpoint,
            to_endpoint=self.requester_endpoint,
            body="Hidden unread",
            raw_payload={"hide_in_chat": True},
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("console_agent_roster"))

        self.assertEqual(response.status_code, 200)
        agent_payload = next(item for item in response.json()["agents"] if item["id"] == str(self.agent.id))
        self.assertTrue(agent_payload["has_unread_agent_message"])
        self.assertEqual(agent_payload["latest_agent_message_id"], str(visible_message.id))
        self.assertIsNone(agent_payload["latest_agent_message_read_at"])

    @tag("batch_agent_chat")
    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
    @patch("console.agent_chat.signals.transition_agent_to_signup_preview_waiting", return_value=False)
    def test_mark_latest_read_endpoint_marks_visible_outbound_message_read(self, _mock_transition):
        message = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=True,
            from_endpoint=self.agent_endpoint,
            to_endpoint=self.requester_endpoint,
            body="Please review this",
            raw_payload={},
        )

        self.client.force_login(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("console_agent_latest_message_read", kwargs={"agent_id": self.agent.id}))

        self.assertEqual(response.status_code, 200)
        owner_profile = self._receive_with_timeout(self.owner_profile_channel_name)
        self.assertEqual(owner_profile.get("type"), "agent_profile_event")
        self.assertEqual(owner_profile.get("payload", {}).get("latest_agent_message_id"), str(message.id))
        self.assertFalse(owner_profile.get("payload", {}).get("has_unread_agent_message"))
        self.assertEqual(self._drain_channel_events(self.collaborator_profile_channel_name), [])
        read = PersistentAgentMessageRead.objects.get(message=message, user=self.user)
        self.assertEqual(read.read_source, "chat_open")
        self.assertFalse(PersistentAgentMessageRead.objects.filter(message=message, user=self.collaborator_user).exists())
        self.assertFalse(response.json()["has_unread_agent_message"])
        self.assertEqual(response.json()["latest_agent_message_id"], str(message.id))
        self.assertIsNotNone(response.json()["latest_agent_message_read_at"])

        self.client.force_login(self.collaborator_user)
        collaborator_response = self.client.get(reverse("console_agent_roster"))
        self.assertEqual(collaborator_response.status_code, 200)
        collaborator_agent = next(
            item for item in collaborator_response.json()["agents"] if item["id"] == str(self.agent.id)
        )
        self.assertTrue(collaborator_agent["has_unread_agent_message"])
        self.assertIsNone(collaborator_agent["latest_agent_message_read_at"])

    @tag("batch_agent_chat")
    @patch("console.agent_chat.signals.transition_agent_to_signup_preview_waiting", return_value=False)
    def test_inbound_human_message_marks_prior_agent_message_read(self, _mock_transition):
        outbound = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=True,
            from_endpoint=self.agent_endpoint,
            to_endpoint=self.requester_endpoint,
            body="Question for the user",
            raw_payload={},
        )
        collaborator_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel="web",
            address=build_web_user_address(self.collaborator_user.id, self.agent.id),
        )
        unrelated_newer_outbound = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=True,
            from_endpoint=self.agent_endpoint,
            to_endpoint=collaborator_endpoint,
            body="Newer question for someone else",
            raw_payload={},
        )

        ingest_inbound_message(
            CommsChannel.WEB,
            ParsedMessage(
                sender=self.requester_endpoint.address,
                recipient=self.agent_endpoint.address,
                subject=None,
                body="Here is my answer",
                attachments=[],
                raw_payload={"source": "test"},
                msg_channel=CommsChannel.WEB,
            ),
        )

        read = PersistentAgentMessageRead.objects.get(message=outbound, user=self.user)
        self.assertEqual(read.read_source, "inbound_reply")
        self.assertFalse(
            PersistentAgentMessageRead.objects.filter(
                message=unrelated_newer_outbound,
                user=self.user,
            ).exists()
        )
        self.assertFalse(PersistentAgentMessageRead.objects.filter(message=outbound, user=self.collaborator_user).exists())

    @tag("batch_agent_chat")
    @patch("console.agent_chat.signals.transition_agent_to_signup_preview_waiting", return_value=False)
    def test_inbound_email_marks_prior_agent_message_read_for_matching_user(self, _mock_transition):
        agent_email_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent-email@example.com",
        )
        collaborator_email_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address=self.collaborator_user.email,
        )
        outbound = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=True,
            from_endpoint=agent_email_endpoint,
            to_endpoint=collaborator_email_endpoint,
            body="Question for collaborator",
            raw_payload={},
        )

        ingest_inbound_message(
            CommsChannel.EMAIL,
            ParsedMessage(
                sender=self.collaborator_user.email,
                recipient=agent_email_endpoint.address,
                subject=None,
                body="Collaborator answer",
                attachments=[],
                raw_payload={"source": "test"},
                msg_channel=CommsChannel.EMAIL,
            ),
        )

        read = PersistentAgentMessageRead.objects.get(message=outbound, user=self.collaborator_user)
        self.assertEqual(read.read_source, "inbound_reply")
        self.assertFalse(PersistentAgentMessageRead.objects.filter(message=outbound, user=self.user).exists())

    @tag("batch_agent_chat")
    @patch("console.agent_chat.signals.transition_agent_to_signup_preview_waiting", return_value=False)
    def test_inbound_email_from_unknown_user_does_not_mark_read(self, _mock_transition):
        agent_email_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent-email-unknown@example.com",
        )
        outbound = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=True,
            from_endpoint=agent_email_endpoint,
            to_endpoint=PersistentAgentCommsEndpoint.objects.create(
                channel=CommsChannel.EMAIL,
                address=self.user.email,
            ),
            body="Question for owner",
            raw_payload={},
        )

        ingest_inbound_message(
            CommsChannel.EMAIL,
            ParsedMessage(
                sender="unknown@example.com",
                recipient=agent_email_endpoint.address,
                subject=None,
                body="Unknown answer",
                attachments=[],
                raw_payload={"source": "test"},
                msg_channel=CommsChannel.EMAIL,
            ),
        )

        self.assertFalse(PersistentAgentMessageRead.objects.filter(message=outbound).exists())

    @tag("batch_agent_chat")
    @patch("console.agent_chat.signals.transition_agent_to_signup_preview_waiting", return_value=False)
    def test_inbound_sms_marks_prior_agent_message_read_for_matching_user(self, _mock_transition):
        phone_number = "+15551234567"
        UserPhoneNumber.objects.create(
            user=self.collaborator_user,
            phone_number=phone_number,
            is_verified=True,
        )
        agent_sms_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15557654321",
        )
        collaborator_sms_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.SMS,
            address=phone_number,
        )
        outbound = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=True,
            from_endpoint=agent_sms_endpoint,
            to_endpoint=collaborator_sms_endpoint,
            body="SMS question",
            raw_payload={},
        )

        ingest_inbound_message(
            CommsChannel.SMS,
            ParsedMessage(
                sender=phone_number,
                recipient=agent_sms_endpoint.address,
                subject=None,
                body="SMS answer",
                attachments=[],
                raw_payload={"source": "test"},
                msg_channel=CommsChannel.SMS,
            ),
        )

        read = PersistentAgentMessageRead.objects.get(message=outbound, user=self.collaborator_user)
        self.assertEqual(read.read_source, "inbound_reply")
        self.assertFalse(PersistentAgentMessageRead.objects.filter(message=outbound, user=self.user).exists())

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
