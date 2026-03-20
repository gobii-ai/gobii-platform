from allauth.account.models import EmailAddress
from django.test import TransactionTestCase, TestCase, tag
from django.contrib.auth import get_user_model
from unittest.mock import patch, MagicMock
from django.db.utils import OperationalError

from django.utils import timezone

from api.models import (
    PersistentAgent,
    BrowserUseAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    CommsChannel,
    DeliveryStatus,
)
from api.agent.tools.email_sender import (
    execute_send_email,
    get_send_email_tool,
    convert_markdown_pipe_tables_to_html,
)
from config import settings


User = get_user_model()


def create_browser_agent_without_proxy(user, name):
    """Helper to create BrowserUseAgent without triggering proxy selection."""
    with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
        return BrowserUseAgent.objects.create(user=user, name=name)


@tag("batch_email_sender_db")
class EmailSenderDbConnectionTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="sender@example.com",
            email="sender@example.com",
            password="secret",
        )
        # Email verification is required for outbound email sending
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        self.browser_agent = create_browser_agent_without_proxy(self.user, "BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="EmailAgent",
            charter="send emails",
            browser_use_agent=self.browser_agent,
        )
        self.default_domain = settings.DEFAULT_AGENT_EMAIL_DOMAIN
        # Primary from endpoint for the agent
        self.from_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel="email",
            address=f"ricardo.kingsley@{self.default_domain}",
            is_primary=True,
        )

    def _mark_message_delivered(self, message):
        message.latest_status = DeliveryStatus.DELIVERED
        message.latest_sent_at = timezone.now()
        message.latest_error_message = ""
        message.save(update_fields=["latest_status", "latest_sent_at", "latest_error_message"])

    def test_send_email_tool_requires_html_tables(self):
        description = get_send_email_tool()["function"]["description"]

        self.assertIn("<table>", description)
        self.assertIn("<thead>", description)
        self.assertIn("<tbody>", description)
        self.assertIn("<tr>", description)
        self.assertIn("<th>", description)
        self.assertIn("<td>", description)
        self.assertIn("Do NOT use Markdown pipe tables", description)

    def test_execute_send_email_retries_on_operational_error(self):
        """
        Test that execute_send_email properly retries on OperationalError.
        
        IMPORTANT: This test specifically tests the retry logic that depends on
        close_old_connections() working properly, so we must NOT mock it here.
        """
        # Ensure close_old_connections is not mocked for this test
        # (in case it was mocked globally or in a parent class)
        from django.db import close_old_connections
        if hasattr(close_old_connections, '_mock_name'):
            # It's a mock, we need to use the real function
            from importlib import reload
            import django.db
            reload(django.db)
            from django.db import close_old_connections
        
        params = {
            "to_address": self.user.email,  # allowed by whitelist
            "subject": "Hello",
            "mobile_first_html": "<p>Hi!</p>",
        }

        # First get_or_create call raises OperationalError; second succeeds
        original_get_or_create = PersistentAgentCommsEndpoint.objects.get_or_create

        def _flaky_get_or_create(*args, **kwargs):
            if not getattr(_flaky_get_or_create, "called", False):
                _flaky_get_or_create.called = True  # type: ignore[attr-defined]
                raise OperationalError("simulated stale connection")
            return original_get_or_create(*args, **kwargs)

        # First message create raises OperationalError; second succeeds
        from api.models import PersistentAgentMessage
        original_create_msg = PersistentAgentMessage.objects.create

        def _flaky_create_msg(*args, **kwargs):
            if not getattr(_flaky_create_msg, "called", False):
                _flaky_create_msg.called = True  # type: ignore[attr-defined]
                raise OperationalError("simulated stale connection on create")
            return original_create_msg(*args, **kwargs)

        with patch(
            "api.agent.tools.email_sender.PersistentAgentCommsEndpoint.objects.get_or_create",
            side_effect=_flaky_get_or_create,
        ), patch(
            "api.agent.tools.email_sender.PersistentAgentMessage.objects.create",
            side_effect=_flaky_create_msg,
        ), patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_message_delivered,
        ):
            result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "ok")
        self.assertTrue(result.get("message_id"))

    def test_execute_send_email_strips_control_characters(self):
        params = {
            "to_address": self.user.email,
            "subject": "Hello Team",
            "mobile_first_html": "<p>It\u0019s great to chat</p>",
            "cc_addresses": [self.user.email],
        }

        with patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_message_delivered,
        ):
            result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "ok")
        self.assertTrue(result.get("message_id"))

        message = PersistentAgentMessage.objects.get(owner_agent=self.agent)
        self.assertEqual(str(message.id), result.get("message_id"))
        self.assertNotIn("\u0019", message.body)
        self.assertIn("It's", message.body)
        self.assertEqual(message.raw_payload.get("subject", ""), params["subject"])
        self.assertEqual(message.to_endpoint.address, params["to_address"])
        self.assertListEqual(
            list(message.cc_endpoints.values_list("address", flat=True)),
            params["cc_addresses"],
        )

    def test_execute_send_email_self_send_uses_default_alias_sender(self):
        self.from_ep.is_primary = False
        self.from_ep.save(update_fields=["is_primary"])
        custom_primary = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=self.user.email,
            is_primary=True,
        )

        params = {
            "to_address": self.user.email,
            "subject": "Self send test",
            "mobile_first_html": "<p>Hello</p>",
        }

        with patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_message_delivered,
        ):
            result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "ok")
        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertEqual(message.from_endpoint_id, self.from_ep.id)
        self.assertEqual(message.to_endpoint_id, custom_primary.id)

    def test_execute_send_email_self_send_with_cc_keeps_custom_sender(self):
        self.from_ep.is_primary = False
        self.from_ep.save(update_fields=["is_primary"])
        custom_primary = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=self.user.email,
            is_primary=True,
        )

        params = {
            "to_address": self.user.email,
            "cc_addresses": ["another@example.com"],
            "subject": "Self send with cc",
            "mobile_first_html": "<p>Hello with cc</p>",
        }

        with patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_message_delivered,
        ):
            result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "error")
        self.assertIn("Recipient address 'another@example.com' not allowed", result.get("message", ""))

        # Make CC allowed by using owner email and retry to confirm sender selection.
        params["cc_addresses"] = [self.user.email]
        with patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_message_delivered,
        ):
            result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "ok")
        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertEqual(message.from_endpoint_id, custom_primary.id)


@tag("batch_email_sender_db")
class MarkdownTableConversionTests(TestCase):
    """Unit tests for convert_markdown_pipe_tables_to_html."""

    def test_basic_table_converted_to_html(self):
        """A standard markdown pipe table should become a valid HTML table."""
        body = "| Name | Score |\n| --- | --- |\n| Alice | 95 |\n| Bob | 87 |"
        result = convert_markdown_pipe_tables_to_html(body)

        self.assertIn("<table", result)
        self.assertIn("<thead>", result)
        self.assertIn("<tbody>", result)
        self.assertIn("<th", result)
        self.assertIn("Name", result)
        self.assertIn("Score", result)
        self.assertIn("<td", result)
        self.assertIn("Alice", result)
        self.assertIn("95", result)
        self.assertIn("Bob", result)
        self.assertNotIn("| Name |", result)
        self.assertNotIn("| --- |", result)

    def test_no_pipe_returns_body_unchanged(self):
        """Bodies without pipe characters are returned as-is (fast path)."""
        body = "<p>Hello world</p>"
        self.assertEqual(convert_markdown_pipe_tables_to_html(body), body)

    def test_empty_string_returns_empty(self):
        """Empty string input returns empty string."""
        self.assertEqual(convert_markdown_pipe_tables_to_html(""), "")

    def test_none_returns_none(self):
        """None input is returned unchanged (function is a no-op for falsy)."""
        self.assertIsNone(convert_markdown_pipe_tables_to_html(None))

    def test_surrounding_html_preserved(self):
        """HTML content before and after the table is kept intact."""
        body = (
            "<p>See the results below:</p>\n"
            "| Metric | Value |\n"
            "| ------ | ----- |\n"
            "| CPU    | 42%   |\n"
            "<p>Let me know if you need more details.</p>"
        )
        result = convert_markdown_pipe_tables_to_html(body)

        self.assertIn("<p>See the results below:</p>", result)
        self.assertIn("<p>Let me know if you need more details.</p>", result)
        self.assertIn("<table", result)
        self.assertIn("Metric", result)
        self.assertIn("CPU", result)
        self.assertNotIn("| Metric |", result)

    def test_multiple_tables_both_converted(self):
        """Multiple pipe tables in one body are each converted."""
        body = (
            "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
            "| X | Y |\n| --- | --- |\n| 3 | 4 |"
        )
        result = convert_markdown_pipe_tables_to_html(body)

        self.assertEqual(result.count("<table"), 2)
        self.assertNotIn("| A |", result)
        self.assertNotIn("| X |", result)

    def test_special_chars_in_cells_are_html_escaped(self):
        """Cell values with HTML-special characters must be escaped."""
        body = "| Tag | Example |\n| --- | --- |\n| script | <script>alert(1)</script> |"
        result = convert_markdown_pipe_tables_to_html(body)

        self.assertIn("&lt;script&gt;", result)
        self.assertNotIn("<script>", result)

    def test_alignment_colons_in_separator_accepted(self):
        """Separator rows with alignment colons are still recognised."""
        body = "| Left | Center | Right |\n| :--- | :---: | ---: |\n| a | b | c |"
        result = convert_markdown_pipe_tables_to_html(body)

        self.assertIn("<table", result)
        self.assertIn("Left", result)
        self.assertIn("a", result)

    def test_inline_styles_applied_to_table_elements(self):
        """Converted tables include inline CSS for email-client compatibility."""
        body = "| Col |\n| --- |\n| val |"
        result = convert_markdown_pipe_tables_to_html(body)

        self.assertIn("border-collapse", result)
        # th and td should also have inline styles
        self.assertIn("<th style=", result)
        self.assertIn("<td style=", result)

    def test_pipe_in_non_table_context_not_converted(self):
        """A single pipe character that isn't a table row must not be touched."""
        body = "<p>Choose option A | option B</p>"
        result = convert_markdown_pipe_tables_to_html(body)
        # No table was produced, body unchanged
        self.assertNotIn("<table", result)
        self.assertIn("option A | option B", result)

    def test_header_only_table_no_body_rows(self):
        """A table with no data rows (header + separator only) is still valid HTML."""
        body = "| Name |\n| ---- |"
        result = convert_markdown_pipe_tables_to_html(body)

        self.assertIn("<table", result)
        self.assertIn("<thead>", result)
        self.assertIn("<tbody>", result)
        self.assertIn("Name", result)
        # tbody should have no rows
        self.assertNotIn("<tr><td", result)


@tag("batch_email_sender_db")
class SendEmailToolSchemaTests(TestCase):
    """Tests that verify the get_send_email_tool schema enforces HTML tables."""

    def _schema(self):
        return get_send_email_tool()

    def test_tool_name_is_send_email(self):
        self.assertEqual(self._schema()["function"]["name"], "send_email")

    def test_description_forbids_markdown_tables(self):
        desc = self._schema()["function"]["description"]
        # Must explicitly forbid markdown pipe tables
        self.assertIn("| Col | Col |", desc)
        lowered = desc.lower()
        self.assertTrue(
            "do not use markdown" in lowered
            or "forbidden" in lowered
            or "never" in lowered,
            "Description should strongly forbid markdown pipe tables",
        )

    def test_description_requires_html_table_elements(self):
        desc = self._schema()["function"]["description"]
        for tag_name in ("<table>", "<thead>", "<tbody>", "<tr>", "<th>", "<td>"):
            self.assertIn(
                tag_name,
                desc,
                f"Tool description must reference {tag_name}",
            )

    def test_mobile_first_html_param_forbids_markdown(self):
        param_desc = (
            self._schema()["function"]["parameters"]["properties"]["mobile_first_html"]["description"]
        )
        lowered = param_desc.lower()
        self.assertTrue(
            "never" in lowered or "no pipe" in lowered or "forbidden" in lowered,
            "mobile_first_html description must forbid markdown/pipe tables",
        )

    def test_required_fields_present(self):
        required = self._schema()["function"]["parameters"]["required"]
        for field in ("to_address", "subject", "mobile_first_html", "will_continue_work"):
            self.assertIn(field, required)

    def test_schema_type_is_function(self):
        self.assertEqual(self._schema()["type"], "function")


@tag("batch_email_sender_db")
class ExecuteSendEmailMarkdownTableTests(TransactionTestCase):
    """Integration tests: markdown tables in execute_send_email are stored as HTML."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tabletest@example.com",
            email="tabletest@example.com",
            password="secret",
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        self.browser_agent = create_browser_agent_without_proxy(self.user, "TableBA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="TableAgent",
            charter="send emails with tables",
            browser_use_agent=self.browser_agent,
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=f"table-agent@{settings.DEFAULT_AGENT_EMAIL_DOMAIN}",
            is_primary=True,
        )

    def _mark_delivered(self, message):
        message.latest_status = DeliveryStatus.DELIVERED
        message.latest_sent_at = timezone.now()
        message.latest_error_message = ""
        message.save(update_fields=["latest_status", "latest_sent_at", "latest_error_message"])

    def test_markdown_table_stored_as_html_table(self):
        """When an agent sends markdown pipe tables, the stored body uses HTML."""
        body = (
            "<p>Here are your results:</p>\n"
            "| Metric | Value |\n"
            "| ------ | ----- |\n"
            "| Speed  | 100ms |\n"
            "| Memory | 256MB |"
        )
        params = {
            "to_address": self.user.email,
            "subject": "Results",
            "mobile_first_html": body,
        }

        with patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_delivered,
        ):
            result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "ok")
        message = PersistentAgentMessage.objects.get(id=result["message_id"])

        # Stored body must contain HTML table tags, not raw markdown
        self.assertIn("<table", message.body)
        self.assertIn("<th", message.body)
        self.assertIn("<td", message.body)
        self.assertNotIn("| Metric |", message.body)
        self.assertNotIn("| --- |", message.body)
        # Surrounding HTML is still present
        self.assertIn("<p>Here are your results:</p>", message.body)

    def test_pure_html_table_body_unchanged(self):
        """Bodies that already use HTML tables are stored without modification."""
        body = (
            "<p>Summary:</p>"
            '<table><thead><tr><th>Col</th></tr></thead>'
            "<tbody><tr><td>Val</td></tr></tbody></table>"
        )
        params = {
            "to_address": self.user.email,
            "subject": "HTML Table",
            "mobile_first_html": body,
        }

        with patch(
            "api.agent.tools.email_sender.deliver_agent_email",
            side_effect=self._mark_delivered,
        ):
            result = execute_send_email(self.agent, params)

        self.assertEqual(result.get("status"), "ok")
        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        # Pure HTML tables must be preserved as-is
        self.assertIn("<table>", message.body)
        self.assertIn("<th>Col</th>", message.body)
