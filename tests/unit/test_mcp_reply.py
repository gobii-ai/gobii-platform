from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.comms.message_reads import build_latest_agent_message_read_state
from api.agent.comms.message_service import inject_internal_web_message
from api.agent.comms.routing import (
    agent_has_recent_mcp_inbound,
    bind_inbound_routing_scope,
    capture_inbound_routing_scope,
    get_recent_mcp_inbound_message,
    reset_inbound_routing_scope,
)
from api.agent.core.daily_limit_mode import is_credit_message_only_allowed_tool
from api.agent.core.event_processing import (
    _filter_incompatible_reply_tools,
    _same_channel_reply_tool_name,
)
from api.agent.core.prompt_context import _get_implied_send_context
from api.agent.tools.mcp_sender import execute_send_mcp_message, get_send_mcp_message_tool
from api.agent.tools.static_tools import get_static_tool_definitions
from api.models import BrowserUseAgent, DeliveryStatus, PersistentAgent, PersistentAgentMessage
from api.services.remote_mcp import _serialize_message as serialize_remote_mcp_message
from console.agent_chat.timeline import serialize_message_event


@tag("mcp_reply_batch")
class McpReplyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="mcp-reply@example.test",
            email="mcp-reply@example.test",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Aisha",
            charter="Maintain operational state precisely.",
            planning_state=PersistentAgent.PlanningState.SKIPPED,
            browser_use_agent=BrowserUseAgent.objects.create(user=self.user, name="browser"),
        )
        self.inbound, _ = inject_internal_web_message(
            self.agent.id,
            "Delete exactly onboarding_checkin and report back here.",
            sender_user_id=self.user.id,
            trigger_processing=False,
            source="remote_mcp",
            source_kind="mcp",
            source_label="Gobii MCP",
        )

    def _bind(self, message=None):
        inbound = message or self.inbound
        scope = capture_inbound_routing_scope(
            self.agent,
            message_id=inbound.id,
        )
        return bind_inbound_routing_scope(scope)

    @staticmethod
    def _tool_names(tools):
        return {
            tool.get("function", {}).get("name")
            for tool in tools
            if isinstance(tool, dict)
        }

    def test_mcp_tool_is_exposed_for_recent_activity_without_hiding_other_tools(self):
        tools = get_static_tool_definitions(self.agent)
        filtered = _filter_incompatible_reply_tools(
            tools,
            self.inbound,
            mcp_available=True,
        )

        self.assertEqual(
            self._tool_names(filtered) - {"send_mcp_message"},
            self._tool_names(tools),
        )
        self.assertIn("send_mcp_message", self._tool_names(filtered))
        self.assertIn("send_chat_message", self._tool_names(filtered))
        self.assertIn("send_email", self._tool_names(filtered))

        web_inbound, _ = inject_internal_web_message(
            self.agent.id,
            "Ordinary web request",
            sender_user_id=self.user.id,
            trigger_processing=False,
        )
        filtered_without_recent_mcp = _filter_incompatible_reply_tools(
            tools,
            web_inbound,
            mcp_available=False,
        )
        self.assertNotIn("send_mcp_message", self._tool_names(filtered_without_recent_mcp))

        filtered_web = _filter_incompatible_reply_tools(
            tools,
            web_inbound,
            mcp_available=True,
        )
        self.assertIn("send_mcp_message", self._tool_names(filtered_web))
        self.assertIn("send_chat_message", self._tool_names(filtered_web))
        self.assertIn("send_email", self._tool_names(filtered_web))

    def test_same_channel_reply_uses_mcp_tool(self):
        self.assertEqual(
            _same_channel_reply_tool_name(self.inbound),
            "send_mcp_message",
        )

    def test_active_mcp_inbound_exposes_tool_when_recency_query_lags(self):
        filtered = _filter_incompatible_reply_tools(
            get_static_tool_definitions(self.agent),
            self.inbound,
            mcp_available=False,
        )

        self.assertIn("send_mcp_message", self._tool_names(filtered))

    def test_tool_schema_has_no_recipient_and_supports_attachments(self):
        function = get_send_mcp_message_tool()["function"]
        properties = function["parameters"]["properties"]

        self.assertEqual(function["name"], "send_mcp_message")
        self.assertEqual(set(function["parameters"]["required"]), {"body", "will_continue_work"})
        self.assertIn("attachments", properties)
        self.assertNotIn("to_address", properties)

    def test_success_persists_correlated_delivered_mcp_timeline_reply(self):
        result = execute_send_mcp_message(
            self.agent,
            {"body": "The requested schedule was deleted.", "will_continue_work": False},
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["auto_sleep_ok"])
        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertTrue(message.is_outbound)
        self.assertEqual(message.parent_id, self.inbound.id)
        self.assertEqual(message.conversation_id, self.inbound.conversation_id)
        self.assertEqual(message.to_endpoint_id, self.inbound.from_endpoint_id)
        self.assertEqual(message.raw_payload["source_kind"], "mcp")
        self.assertEqual(message.raw_payload["source"], "mcp_reply_tool")
        self.assertEqual(message.latest_status, DeliveryStatus.DELIVERED)
        self.assertIsNotNone(message.latest_sent_at)
        self.assertIsNotNone(message.latest_delivered_at)

        timeline_message = serialize_message_event(message)["message"]
        self.assertEqual(timeline_message["channel"], "mcp")
        self.assertEqual(timeline_message["inReplyToMessageId"], str(self.inbound.id))
        self.assertTrue(timeline_message["isOutbound"])
        self.assertEqual(timeline_message["sourceLabel"], "Gobii MCP")
        self.assertEqual(
            serialize_remote_mcp_message(message)["parent_id"],
            str(self.inbound.id),
        )

    def test_progress_reply_keeps_work_active(self):
        result = execute_send_mcp_message(
            self.agent,
            {"body": "The target is deleted; I am verifying the sentinel.", "will_continue_work": True},
        )

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["auto_sleep_ok"])
        self.assertTrue(is_credit_message_only_allowed_tool("send_mcp_message"))

    def test_stale_mcp_activity_hides_tool_and_writes_nothing(self):
        stale_timestamp = timezone.now() - timedelta(days=settings.WEB_SESSION_RETENTION_DAYS + 1)
        PersistentAgentMessage.objects.filter(id=self.inbound.id).update(timestamp=stale_timestamp)

        self.assertFalse(agent_has_recent_mcp_inbound(self.agent))
        self.assertIsNone(get_recent_mcp_inbound_message(self.agent))
        tools = _filter_incompatible_reply_tools(
            get_static_tool_definitions(self.agent),
            None,
            mcp_available=get_recent_mcp_inbound_message(self.agent) is not None,
        )
        self.assertNotIn("send_mcp_message", self._tool_names(tools))

        result = execute_send_mcp_message(
            self.agent,
            {"body": "Should not persist.", "will_continue_work": False},
        )
        self.assertEqual(result["status"], "error")
        self.assertFalse(result["retryable"])
        self.assertEqual(PersistentAgentMessage.objects.filter(is_outbound=True).count(), 0)

    def test_reply_uses_most_recent_mcp_conversation(self):
        other_user = get_user_model().objects.create_user(
            username="second-mcp-user@example.test",
            email="second-mcp-user@example.test",
        )
        newer_inbound, _ = inject_internal_web_message(
            self.agent.id,
            "A newer MCP request from another user.",
            sender_user_id=other_user.id,
            trigger_processing=False,
            source="remote_mcp",
            source_kind="mcp",
            source_label="Gobii MCP",
        )
        web_inbound, _ = inject_internal_web_message(
            self.agent.id,
            "An even newer ordinary web message.",
            sender_user_id=self.user.id,
            trigger_processing=False,
        )
        filtered = _filter_incompatible_reply_tools(
            get_static_tool_definitions(self.agent),
            web_inbound,
            mcp_available=get_recent_mcp_inbound_message(self.agent) is not None,
        )
        result = execute_send_mcp_message(
            self.agent,
            {"body": "Reply through the newest MCP conversation.", "will_continue_work": False},
        )

        self.assertIn("send_mcp_message", self._tool_names(filtered))
        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertEqual(message.parent_id, newer_inbound.id)
        self.assertEqual(message.conversation_id, newer_inbound.conversation_id)

    def test_active_mcp_request_wins_over_newer_concurrent_request(self):
        other_user = get_user_model().objects.create_user(
            username="concurrent-mcp-user@example.test",
            email="concurrent-mcp-user@example.test",
        )
        newer_inbound, _ = inject_internal_web_message(
            self.agent.id,
            "A newer concurrent MCP request.",
            sender_user_id=other_user.id,
            trigger_processing=False,
            source="remote_mcp",
            source_kind="mcp",
            source_label="Gobii MCP",
        )
        self.assertNotEqual(newer_inbound.conversation_id, self.inbound.conversation_id)

        token = self._bind(self.inbound)
        try:
            result = execute_send_mcp_message(
                self.agent,
                {"body": "Reply to the active MCP request.", "will_continue_work": False},
            )
        finally:
            reset_inbound_routing_scope(token)

        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertEqual(message.parent_id, self.inbound.id)
        self.assertEqual(message.conversation_id, self.inbound.conversation_id)

    @patch("api.agent.tools.mcp_sender.broadcast_message_attachment_update")
    @patch("api.agent.tools.mcp_sender.create_message_attachments")
    @patch("api.agent.tools.mcp_sender.resolve_filespace_attachments")
    def test_attachments_use_standard_message_attachment_flow(
        self,
        mock_resolve,
        mock_create,
        mock_broadcast,
    ):
        resolved_attachment = object()
        mock_resolve.return_value = [resolved_attachment]
        result = execute_send_mcp_message(
            self.agent,
            {
                "body": "The requested artifact is attached.",
                "attachments": ["/reports/result.csv"],
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "ok")
        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        mock_resolve.assert_called_once_with(self.agent, ["/reports/result.csv"])
        mock_create.assert_called_once_with(message, [resolved_attachment])
        mock_broadcast.assert_called_once_with(str(message.id))

    def test_duplicate_reply_is_rejected_without_second_message(self):
        params = {"body": "Completed safely.", "will_continue_work": False}
        first = execute_send_mcp_message(self.agent, params)
        second = execute_send_mcp_message(self.agent, params)

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "error")
        self.assertTrue(second["duplicate_detected"])
        self.assertEqual(
            PersistentAgentMessage.objects.filter(is_outbound=True).count(),
            1,
        )

    @patch("api.agent.core.prompt_context.get_deliverable_web_sessions")
    def test_mcp_context_disables_implied_owner_web_send(self, mock_sessions):
        mock_sessions.return_value = [SimpleNamespace(user_id=self.user.id)]
        token = self._bind()
        try:
            context = _get_implied_send_context(self.agent)
        finally:
            reset_inbound_routing_scope(token)

        description = get_send_mcp_message_tool()["function"]["description"]
        self.assertIsNone(context)
        self.assertIn("Human-channel tools remain available", description)
        self.assertIn("make zero human-channel calls", description)
        self.assertIn("Never claim that no human contact occurred", description)

    @patch("console.agent_chat.signals.emit_message_notification")
    @patch("console.agent_chat.signals.transition_agent_to_signup_preview_waiting")
    def test_mcp_reply_is_timeline_visible_without_owner_notification_or_unread_state(
        self,
        mock_preview_transition,
        mock_notification,
    ):
        with self.captureOnCommitCallbacks(execute=True):
            result = execute_send_mcp_message(
                self.agent,
                {"body": "Recorded only on the MCP timeline.", "will_continue_work": False},
            )

        self.assertEqual(result["status"], "ok")
        mock_preview_transition.assert_not_called()
        mock_notification.assert_not_called()
        state = build_latest_agent_message_read_state([self.agent.id], self.user)[str(self.agent.id)]
        self.assertFalse(state["has_unread_agent_message"])
        self.assertIsNone(state["latest_agent_message_id"])
