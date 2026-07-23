from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

from api.agent.comms.outbound_content_policy import contains_raw_html, markdown_only_error
from api.agent.peer_comm import PeerMessagingError, PeerMessagingService
from api.agent.tools.agent_variables import clear_variables, set_agent_variable
from api.agent.tools.peer_dm import execute_send_agent_message, get_send_agent_message_tool
from api.agent.tools.send_discord_message import execute_send_discord_message, get_send_discord_message_tool
from api.agent.tools.web_chat_sender import execute_send_chat_message, get_send_chat_tool
from api.models import AgentPeerLink, BrowserUseAgent, PersistentAgent, PersistentAgentMessage, build_web_user_address


@tag("batch_text_sanitization")
class OutboundContentPolicyTests(SimpleTestCase):
    def test_rejects_renderable_html(self):
        values = (
            "<strong>Bug title</strong>",
            "<span style='color:red'>MINOR</span>",
            "<div>\nBlock content\n</div>",
            "<!-- hidden comment -->",
            "first<br>second",
        )

        for value in values:
            with self.subTest(value=value):
                self.assertTrue(contains_raw_html(value))

    def test_accepts_markdown_and_literal_html_examples(self):
        values = (
            "**Bold Markdown**",
            "- one\n- two",
            "| Status | Value |\n| --- | --- |\n| Open | 2 |",
            "<https://example.com>",
            "2 < 3 and 4 > 1",
            "The type is List<String> here.",
            "The input uses Optional<Input> here.",
            "Compare a<b and c>d values.",
            "&lt;strong&gt;literal&lt;/strong&gt;",
            "`<strong>literal</strong>`",
            "```html\n<strong>literal</strong>\n```",
        )

        for value in values:
            with self.subTest(value=value):
                self.assertFalse(contains_raw_html(value))

    def test_builds_standard_retryable_error(self):
        self.assertEqual(
            markdown_only_error("<strong>Title</strong>", surface="Web chat"),
            {
                "status": "error",
                "error_type": "unsupported_markup",
                "retryable": True,
                "message": (
                    "Web chat supports Markdown, not raw HTML. Replace HTML formatting with Markdown and retry; "
                    "use code formatting to show HTML literally."
                ),
            },
        )
        self.assertIsNone(markdown_only_error("**Title**", surface="Web chat"))

    def test_native_tool_schemas_describe_markdown_only_contract(self):
        descriptions = (
            get_send_chat_tool()["function"]["parameters"]["properties"]["body"]["description"],
            get_send_discord_message_tool()["function"]["parameters"]["properties"]["message"]["description"],
            get_send_agent_message_tool()["function"]["parameters"]["properties"]["message"]["description"],
        )

        for description in descriptions:
            with self.subTest(description=description):
                self.assertIn("Markdown only", description)
                self.assertIn("raw HTML is rejected", description)
                self.assertIn("code formatting", description)


@tag("batch_text_sanitization")
class NativeMarkdownSenderTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="markdown-sender@example.com")
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Markdown Sender Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Markdown Sender",
            charter="Test native Markdown sends.",
            browser_use_agent=browser_agent,
            execution_environment="eval",
        )
        peer_browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Markdown Peer Browser")
        self.peer_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Markdown Peer",
            charter="Receive native Markdown sends.",
            browser_use_agent=peer_browser_agent,
            execution_environment="eval",
        )
        AgentPeerLink.objects.create(
            agent_a=self.agent,
            agent_b=self.peer_agent,
            created_by=self.user,
        )

    def tearDown(self):
        clear_variables()

    def assert_unsupported_markup(self, result, surface):
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "unsupported_markup")
        self.assertIs(result["retryable"], True)
        self.assertIn(f"{surface} supports Markdown, not raw HTML", result["message"])

    def test_web_chat_rejects_html_without_persisting_message(self):
        result = execute_send_chat_message(
            self.agent,
            {
                "body": "<strong>Bug title</strong>",
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "will_continue_work": False,
            },
        )

        self.assert_unsupported_markup(result, "Web chat")
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=self.agent).exists())

    def test_web_chat_delivers_valid_markdown(self):
        result = execute_send_chat_message(
            self.agent,
            {
                "body": "**Bug title**",
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "ok")
        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertEqual(message.body, "**Bug title**")

    def test_web_chat_rejects_html_introduced_by_variable_substitution(self):
        set_agent_variable("formatted_title", "<strong>Bug title</strong>")

        result = execute_send_chat_message(
            self.agent,
            {
                "body": "$[formatted_title]",
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "will_continue_work": False,
            },
        )

        self.assert_unsupported_markup(result, "Web chat")
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=self.agent).exists())

    @patch("api.agent.tools.send_discord_message.send_channel_message")
    @patch("api.agent.tools.send_discord_message.resolve_filespace_attachments")
    def test_discord_rejects_html_before_attachment_or_delivery_work(
        self,
        resolve_filespace_attachments_mock,
        send_channel_message_mock,
    ):
        result = execute_send_discord_message(
            self.agent,
            {
                "channel_id": "123",
                "message": "<span style='color:red'>MINOR</span>",
                "attachments": ["/exports/report.pdf"],
                "will_continue_work": False,
            },
        )

        self.assert_unsupported_markup(result, "Discord")
        resolve_filespace_attachments_mock.assert_not_called()
        send_channel_message_mock.assert_not_called()

    @patch("api.agent.tools.send_discord_message.send_channel_message")
    @patch("api.agent.tools.send_discord_message.resolve_filespace_attachments", return_value=[])
    def test_discord_delivers_valid_markdown(
        self,
        _resolve_filespace_attachments_mock,
        send_channel_message_mock,
    ):
        send_channel_message_mock.return_value = SimpleNamespace(
            id="message-id",
            raw_payload={"discord_message_id": "discord-id"},
        )

        result = execute_send_discord_message(
            self.agent,
            {
                "channel_id": "123",
                "message": "**MINOR**",
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "success")
        send_channel_message_mock.assert_called_once_with(
            self.agent,
            channel_id="123",
            body="**MINOR**",
            attachments=[],
        )

    def test_peer_message_rejects_html_before_delivery(self):
        result = execute_send_agent_message(
            self.agent,
            {
                "peer_agent_id": str(self.peer_agent.id),
                "message": "<strong>Handoff</strong>",
                "will_continue_work": False,
            },
        )

        self.assert_unsupported_markup(result, "Peer messaging")
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=self.agent).exists())

    def test_peer_service_rejects_html_for_direct_callers(self):
        service = PeerMessagingService(self.agent, self.peer_agent)

        with self.assertRaises(PeerMessagingError) as raised:
            service.send_message("<strong>Spawn handoff</strong>")

        self.assertEqual(raised.exception.error_type, "unsupported_markup")
        self.assertIs(raised.exception.retryable, True)
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=self.agent).exists())

    @patch("api.agent.tools.peer_dm.PeerMessagingService")
    @patch("api.agent.tools.peer_dm.resolve_filespace_attachments", return_value=[])
    def test_peer_message_delivers_valid_markdown(
        self,
        _resolve_filespace_attachments_mock,
        peer_messaging_service_mock,
    ):
        peer_messaging_service_mock.return_value.send_message.return_value = SimpleNamespace(
            status="ok",
            message="Peer message sent.",
            remaining_credits=10,
            window_reset_at=None,
        )

        result = execute_send_agent_message(
            self.agent,
            {
                "peer_agent_id": str(self.peer_agent.id),
                "message": "**Handoff**",
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "ok")
        peer_messaging_service_mock.return_value.send_message.assert_called_once_with(
            "**Handoff**",
            attachments=[],
        )
