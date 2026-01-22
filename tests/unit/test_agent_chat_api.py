from __future__ import annotations

import json
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings, tag

from api.agent.tools.sqlite_kanban import KanbanBoardSnapshot, KanbanCardChange
from api.models import (
    BrowserUseAgent,
    BrowserUseAgentTask,
    CommsChannel,
    DeliveryStatus,
    PersistentAgent,
    PersistentAgentKanbanCard,
    PersistentAgentKanbanEvent,
    PersistentAgentCompletion,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentToolCall,
    build_web_agent_address,
    build_web_user_address,
)
from api.agent.core.processing_flags import clear_processing_queued_flag, set_processing_queued_flag
from api.agent.tools.web_chat_sender import execute_send_chat_message
from api.services.web_sessions import start_web_session
from console.agent_chat.kanban_events import persist_kanban_event
from console.agent_chat.timeline import build_processing_snapshot
from console.agent_chat.timeline import serialize_kanban_event
from util.analytics import AnalyticsEvent

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

        cls.user_address = build_web_user_address(cls.user.id, cls.agent.id)
        cls.agent_address = build_web_agent_address(cls.agent.id)

        cls.agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=cls.agent,
            channel=CommsChannel.WEB,
            address=cls.agent_address,
            is_primary=True,
        )
        cls.user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.WEB,
            address=cls.user_address,
            is_primary=False,
        )
        cls.conversation = PersistentAgentConversation.objects.create(
            owner_agent=cls.agent,
            channel=CommsChannel.WEB,
            address=cls.user_address,
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
        self.assertEqual(message_event["message"]["senderUserId"], self.user.id)
        self.assertEqual(message_event["message"]["senderName"], self.user.email)
        self.assertEqual(message_event["message"]["senderAddress"], self.user_address)

        tool_cluster = next(event for event in events if event["kind"] == "steps")
        self.assertEqual(tool_cluster["entries"][0]["toolName"], "send_email")
        self.assertTrue(payload.get("newest_cursor"))
        self.assertIsNotNone(payload.get("processing_active"))
        snapshot = payload.get("processing_snapshot")
        self.assertIsInstance(snapshot, dict)
        self.assertIn("active", snapshot)
        self.assertIn("webTasks", snapshot)
        self.assertIsInstance(snapshot.get("webTasks"), list)

    @tag("batch_agent_chat")
    def test_timeline_has_no_older_when_under_limit(self):
        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertFalse(payload.get("has_more_older"))

    @tag("batch_agent_chat")
    @patch("console.agent_chat.timeline.get_processing_heartbeat")
    def test_processing_snapshot_uses_heartbeat(self, mock_get_heartbeat):
        mock_get_heartbeat.return_value = {"last_seen": 123.0}

        snapshot = build_processing_snapshot(self.agent)

        self.assertTrue(snapshot.active)

    @tag("batch_agent_chat")
    def test_timeline_includes_thinking_events(self):
        completion = PersistentAgentCompletion.objects.create(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.ORCHESTRATOR,
            thinking_content="Reasoned path",
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        events = payload.get("events", [])
        thinking_event = next(event for event in events if event.get("kind") == "thinking")

        self.assertEqual(thinking_event.get("reasoning"), "Reasoned path")
        self.assertEqual(thinking_event.get("completionId"), str(completion.id))

    @tag("batch_agent_chat")
    def test_timeline_includes_kanban_events(self):
        card = PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Investigate kanban persistence",
            description="Ensure kanban survives refresh.",
            status=PersistentAgentKanbanCard.Status.TODO,
        )
        snapshot = KanbanBoardSnapshot(
            todo_count=1,
            doing_count=0,
            done_count=0,
            todo_titles=[card.title],
            doing_titles=[],
            done_titles=[],
        )
        changes = [
            KanbanCardChange(
                card_id=str(card.id),
                title=card.title,
                action="created",
                to_status=PersistentAgentKanbanCard.Status.TODO,
            )
        ]
        agent_name = (self.agent.name or "Agent").split()[0]
        kanban_payload = serialize_kanban_event(agent_name, changes, snapshot)
        persist_kanban_event(self.agent, kanban_payload)

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        timeline_payload = response.json()
        events = timeline_payload.get("events", [])
        kanban_event = next(event for event in events if event.get("kind") == "kanban")

        self.assertEqual(kanban_event.get("displayText"), kanban_payload.get("displayText"))
        self.assertEqual(kanban_event.get("primaryAction"), kanban_payload.get("primaryAction"))
        snapshot_payload = kanban_event.get("snapshot", {})
        self.assertEqual(snapshot_payload.get("todoCount"), 1)
        self.assertEqual(snapshot_payload.get("todoTitles"), [card.title])
        self.assertEqual(kanban_event.get("changes")[0].get("cardId"), str(card.id))

    @tag("batch_agent_chat")
    def test_timeline_creates_baseline_kanban_event(self):
        card = PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Baseline task",
            description="Baseline snapshot coverage.",
            status=PersistentAgentKanbanCard.Status.TODO,
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        events = response.json().get("events", [])
        kanban_event = next(event for event in events if event.get("kind") == "kanban")

        snapshot_payload = kanban_event.get("snapshot", {})
        self.assertEqual(snapshot_payload.get("todoCount"), 1)
        self.assertEqual(snapshot_payload.get("todoTitles"), [card.title])
        self.assertTrue(PersistentAgentKanbanEvent.objects.filter(agent=self.agent).exists())

    @tag("batch_agent_chat")
    def test_timeline_preserves_html_email_body(self):
        html_body = "<p>Email intro</p><p><strong>Bold</strong> value</p><ul><li>Bullet</li></ul>"
        email_address = "louise@example.com"

        email_sender = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.EMAIL,
            address=email_address,
            is_primary=False,
        )
        email_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=email_address,
        )
        PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=email_sender,
            conversation=email_conversation,
            body=html_body,
            owner_agent=self.agent,
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        html_event = next(
            event
            for event in payload.get("events", [])
            if event.get("kind") == "message" and event["message"].get("bodyText") == html_body
        )

        rendered_html = html_event["message"]["bodyHtml"]
        self.assertIn("<strong>Bold</strong>", rendered_html)
        self.assertIn("<li>Bullet</li>", rendered_html)
        self.assertNotIn("&lt;", rendered_html)

    @tag("batch_agent_chat")
    def test_plaintext_and_markdown_prefer_body_text(self):
        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        original_event = next(
            event
            for event in payload.get("events", [])
            if event.get("kind") == "message" and event["message"].get("bodyText") == "Hello from the owner"
        )

        self.assertEqual(original_event["message"].get("bodyHtml"), "")

    @tag("batch_agent_chat")
    def test_web_session_api_flow(self):
        start_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/start/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(start_response.status_code, 200)
        start_payload = start_response.json()
        session_key = start_payload["session_key"]

        heartbeat_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/heartbeat/",
            data=json.dumps({"session_key": session_key}),
            content_type="application/json",
        )
        self.assertEqual(heartbeat_response.status_code, 200)

        end_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/end/",
            data=json.dumps({"session_key": session_key}),
            content_type="application/json",
        )
        self.assertEqual(end_response.status_code, 200)
        end_payload = end_response.json()
        self.assertIn("ended_at", end_payload)

        # Ending an already-deleted session should still succeed idempotently.
        repeat_end = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/end/",
            data=json.dumps({"session_key": session_key}),
            content_type="application/json",
        )
        self.assertEqual(repeat_end.status_code, 200)
        repeat_payload = repeat_end.json()
        self.assertTrue(
            repeat_payload.get("ended") or repeat_payload.get("ended_at"),
            repeat_payload,
        )

    @tag("batch_agent_chat")
    @patch("console.api_views.Analytics.track_event")
    def test_session_analytics_emitted(self, mock_track_event):
        start_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/start/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(start_response.status_code, 200)
        start_payload = start_response.json()
        session_key = start_payload["session_key"]

        heartbeat_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/heartbeat/",
            data=json.dumps({"session_key": session_key}),
            content_type="application/json",
        )
        self.assertEqual(heartbeat_response.status_code, 200)

        end_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/end/",
            data=json.dumps({"session_key": session_key}),
            content_type="application/json",
        )
        self.assertEqual(end_response.status_code, 200)

        self.assertEqual(mock_track_event.call_count, 2)
        event_names = {record.kwargs.get("event") for record in mock_track_event.call_args_list}
        self.assertIn(AnalyticsEvent.WEB_CHAT_SESSION_STARTED, event_names)
        self.assertIn(AnalyticsEvent.WEB_CHAT_SESSION_ENDED, event_names)

        start_call_record = next(
            record for record in mock_track_event.call_args_list if record.kwargs.get("event") == AnalyticsEvent.WEB_CHAT_SESSION_STARTED
        )
        end_call_record = next(
            record for record in mock_track_event.call_args_list if record.kwargs.get("event") == AnalyticsEvent.WEB_CHAT_SESSION_ENDED
        )

        self.assertEqual(start_call_record.kwargs["properties"].get("agent_id"), str(self.agent.id))
        self.assertEqual(end_call_record.kwargs["properties"].get("agent_id"), str(self.agent.id))
        self.assertEqual(end_call_record.kwargs["properties"].get("session_key"), session_key)

    @tag("batch_agent_chat")
    @patch("console.api_views.Analytics.track_event")
    def test_message_post_records_analytics(self, mock_track_event):
        with patch("api.agent.tasks.process_agent_events_task.delay") as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    f"/console/api/agents/{self.agent.id}/messages/",
                    data=json.dumps({"body": "Hello agent"}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 201)

        mock_delay.assert_called()

        self.assertEqual(mock_track_event.call_count, 1)
        self.assertEqual(mock_track_event.call_args.kwargs.get("event"), AnalyticsEvent.WEB_CHAT_MESSAGE_SENT)

        message_call = next(
            record for record in mock_track_event.call_args_list if record.kwargs.get("event") == AnalyticsEvent.WEB_CHAT_MESSAGE_SENT
        )
        props = message_call.kwargs["properties"]
        self.assertEqual(props.get("agent_id"), str(self.agent.id))
        self.assertIn("message_id", props)
        self.assertEqual(props.get("message_length"), len("Hello agent"))

    @tag("batch_agent_chat")
    def test_processing_status_endpoint_includes_active_web_tasks(self):
        task = BrowserUseAgentTask.objects.create(
            agent=self.browser_agent,
            user=self.user,
            prompt="Visit example.com",
            status=BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/processing/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        snapshot = payload.get("processing_snapshot")

        self.assertIsInstance(snapshot, dict)
        self.assertTrue(snapshot.get("active"))

        web_tasks = snapshot.get("webTasks") or []
        self.assertEqual(len(web_tasks), 1)
        web_task = web_tasks[0]

        self.assertEqual(web_task.get("id"), str(task.id))
        self.assertEqual(web_task.get("status"), BrowserUseAgentTask.StatusChoices.IN_PROGRESS)
        self.assertEqual(web_task.get("statusLabel"), task.get_status_display())
        self.assertEqual(web_task.get("promptPreview"), "Visit example.com")

    @tag("batch_agent_chat")
    def test_processing_status_reports_active_when_only_queued(self):
        set_processing_queued_flag(self.agent.id)
        try:
            response = self.client.get(f"/console/api/agents/{self.agent.id}/processing/")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload.get("processing_active"))

            snapshot = payload.get("processing_snapshot") or {}
            self.assertTrue(snapshot.get("active"))
            self.assertEqual(snapshot.get("webTasks"), [])
        finally:
            clear_processing_queued_flag(self.agent.id)


    @tag("batch_agent_chat")
    def test_web_chat_tool_requires_active_session(self):
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.com",
            is_primary=True,
        )
        result = execute_send_chat_message(
            self.agent,
            {"body": "Ping", "to_address": self.user_address},
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("No active web chat session", result["message"])

        start_web_session(self.agent, self.user)
        success = execute_send_chat_message(
            self.agent,
            {"body": "Ping", "to_address": self.user_address},
        )
        self.assertEqual(success["status"], "ok")

        markdown_body = "# Heading\n\n- Item"
        PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=self.user_endpoint,
            conversation=self.conversation,
            body=markdown_body,
            owner_agent=self.agent,
        )

        refreshed = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(refreshed.status_code, 200)
        payload = refreshed.json()

        markdown_event = next(
            event
            for event in payload.get("events", [])
            if event.get("kind") == "message" and event["message"].get("bodyText") == markdown_body
        )

        self.assertEqual(markdown_event["message"].get("bodyHtml"), "")

    @tag("batch_agent_chat")
    def test_web_chat_tool_allows_without_session_when_no_other_channels(self):
        result = execute_send_chat_message(
            self.agent,
            {"body": "Ping", "to_address": self.user_address},
        )
        self.assertEqual(result["status"], "ok")
        message = PersistentAgentMessage.objects.filter(
            owner_agent=self.agent,
            is_outbound=True,
            body="Ping",
        ).first()
        self.assertIsNotNone(message)

    @tag("batch_agent_chat")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_message_post_creates_console_message(self, mock_delay):
        body = "Run weekly summary"
        with self.captureOnCommitCallbacks(execute=True):
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
        self.assertEqual(event["message"]["channel"], CommsChannel.WEB)
        relative_ts = event["message"].get("relativeTimestamp")
        self.assertIsInstance(relative_ts, str)

        stored = (
            PersistentAgentMessage.objects.filter(owner_agent=self.agent, body=body)
            .order_by("-timestamp")
            .first()
        )
        self.assertIsNotNone(stored)
        self.assertEqual(stored.from_endpoint.address, self.user_address)
        self.assertEqual(stored.conversation.address, self.user_address)
        mock_delay.assert_called_once()

    @tag("batch_agent_chat")
    def test_send_chat_tool_creates_outbound_message(self):
        start_web_session(self.agent, self.user)
        params = {"body": "Tool says hi", "to_address": self.user_address}
        result = execute_send_chat_message(self.agent, params)
        self.assertEqual(result["status"], "ok")

        message = PersistentAgentMessage.objects.get(owner_agent=self.agent, is_outbound=True, body="Tool says hi")
        self.assertEqual(message.from_endpoint.channel, CommsChannel.WEB)
        self.assertEqual(message.conversation.channel, CommsChannel.WEB)
        self.assertEqual(message.latest_status, DeliveryStatus.DELIVERED)

    @tag("batch_agent_chat")
    def test_send_chat_tool_can_mark_continuation(self):
        start_web_session(self.agent, self.user)
        params = {
            "body": "I'll keep working",
            "to_address": self.user_address,
            "will_continue_work": True,
        }
        result = execute_send_chat_message(self.agent, params)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result.get("auto_sleep_ok"))

    @tag("batch_agent_chat")
    def test_send_chat_tool_rejects_unlisted_address(self):
        start_web_session(self.agent, self.user)
        stranger_address = build_web_user_address(self.user.id + 999, self.agent.id)
        params = {"body": "Nope", "to_address": stranger_address}
        result = execute_send_chat_message(self.agent, params)
        self.assertEqual(result["status"], "error")
        self.assertIn("no active web chat session", result["message"].lower())
