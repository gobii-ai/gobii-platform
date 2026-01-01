"""
Unit tests for outbound delivery functionality, including email delivery
and HTML-to-plaintext conversion using inscriptis.
"""

import os
from unittest.mock import patch, MagicMock
from datetime import timedelta
from django.test import TestCase, override_settings, tag
from django.contrib.auth import get_user_model
from django.utils import timezone

from api.models import (
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    OutboundMessageAttempt,
    CommsChannel,
    DeliveryStatus,
    BrowserUseAgent,
)
from api.agent.comms.outbound_delivery import deliver_agent_email, deliver_agent_sms, _convert_sms_body_to_plaintext
from api.agent.comms.email_content import convert_body_to_html_and_plaintext
from config import settings
from inscriptis import get_text

User = get_user_model()


@tag("batch_outbound_delivery")
class HTMLToPlaintextConversionTests(TestCase):
    """Test HTML to plaintext conversion using inscriptis."""

    def test_simple_html_conversion(self):
        """Test basic HTML to plaintext conversion."""
        html = "<p>Hello world!</p>"
        expected = "Hello world!"
        result = get_text(html).strip()
        self.assertEqual(result, expected)

    def test_complex_html_conversion(self):
        """Test complex HTML structures are properly converted."""
        html = """
        <h1>Welcome Email</h1>
        <p>Dear user,</p>
        <p>Here are your next steps:</p>
        <ul>
            <li>Complete your profile</li>
            <li>Verify your email</li>
            <li>Start using the service</li>
        </ul>
        <p>Best regards,<br>The Team</p>
        """
        result = get_text(html).strip()
        
        # Check that key elements are preserved
        self.assertIn("Welcome Email", result)
        self.assertIn("Dear user,", result)
        self.assertIn("Complete your profile", result)
        self.assertIn("Verify your email", result)
        self.assertIn("Start using the service", result)
        self.assertIn("Best regards,", result)
        self.assertIn("The Team", result)
        
        # Check that list formatting is preserved
        self.assertIn("* Complete your profile", result)
        self.assertIn("* Verify your email", result)
        self.assertIn("* Start using the service", result)

    def test_table_conversion(self):
        """Test that HTML tables are properly converted to plaintext."""
        html = """
        <table>
            <tr>
                <th>Name</th>
                <th>Value</th>
            </tr>
            <tr>
                <td>Total</td>
                <td>$100.00</td>
            </tr>
        </table>
        """
        result = get_text(html).strip()
        
        # inscriptis should preserve table structure
        self.assertIn("Name", result)
        self.assertIn("Value", result)
        self.assertIn("Total", result)
        self.assertIn("$100.00", result)

    def test_link_conversion(self):
        """Test that HTML links are handled properly."""
        html = '<p>Visit <a href="https://example.com">our website</a> for more info.</p>'
        result = get_text(html).strip()
        
        # The link text should be preserved
        self.assertIn("Visit our website for more info.", result)

    def test_empty_html(self):
        """Test handling of empty or whitespace-only HTML."""
        self.assertEqual(get_text("").strip(), "")
        self.assertEqual(get_text("   ").strip(), "")
        self.assertEqual(get_text("<p></p>").strip(), "")

    def test_malformed_html(self):
        """Test that malformed HTML doesn't break the conversion."""
        html = "<p>Unclosed paragraph<div>Nested content"
        result = get_text(html).strip()
        self.assertIn("Unclosed paragraph", result)
        self.assertIn("Nested content", result)


@tag("batch_outbound_delivery")
class EmailDeliveryTests(TestCase):
    """Test email delivery functionality."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="testuser@example.com",
            email="testuser@example.com",
            password="password"
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Test Browser Agent"
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Test Agent",
            charter="Test charter",
            browser_use_agent=self.browser_agent
        )
        self.default_domain = settings.DEFAULT_AGENT_EMAIL_DOMAIN
        self.from_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=f"agent@{self.default_domain}",
            is_primary=True
        )
        self.to_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="user@example.com"
        )

    def test_non_email_message_skipped(self):
        """Test that non-email messages are skipped."""
        message = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.from_endpoint,
            to_endpoint=self.to_endpoint,
            is_outbound=True,
            body="Test message",
            raw_payload={"subject": "Test"},
            latest_status=DeliveryStatus.QUEUED
        )
        # Change channel to SMS to test skipping
        message.from_endpoint.channel = CommsChannel.SMS
        message.from_endpoint.save()
        
        with patch('api.agent.comms.outbound_delivery.logger') as mock_logger:
            deliver_agent_email(message)
            mock_logger.warning.assert_called_once()

    def test_non_queued_message_skipped(self):
        """Test that non-queued messages are skipped."""
        message = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.from_endpoint,
            to_endpoint=self.to_endpoint,
            is_outbound=True,
            body="Test message",
            raw_payload={"subject": "Test"},
            latest_status=DeliveryStatus.SENT  # Already sent
        )
        
        with patch('api.agent.comms.outbound_delivery.logger') as mock_logger:
            deliver_agent_email(message)
            mock_logger.info.assert_called_once()

    @override_settings(GOBII_RELEASE_ENV="test")
    @patch.dict(os.environ, {"POSTMARK_SERVER_TOKEN": ""}, clear=False)
    def test_simulated_email_delivery(self):
        """Test email delivery in test environment (simulation mode)."""
        html_body = """
        <h1>Test Email</h1>
        <p>This is a <strong>test</strong> email with <em>formatting</em>.</p>
        <ul>
            <li>Item 1</li>
            <li>Item 2</li>
        </ul>
        """
        
        message = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.from_endpoint,
            to_endpoint=self.to_endpoint,
            is_outbound=True,
            body=html_body,
            raw_payload={"subject": "Test Subject"},
            latest_status=DeliveryStatus.QUEUED
        )
        
        with patch('api.agent.comms.outbound_delivery.logger') as mock_logger:
            deliver_agent_email(message)
            
            # Verify simulation was logged
            mock_logger.info.assert_any_call(
                "Running in non-prod environment without POSTMARK_SERVER_TOKEN. "
                "Simulating email delivery for message %s.",
                message.id,
            )
            
            # Verify email content was logged
            logged_calls = [call[0] for call in mock_logger.info.call_args_list]
            email_log_found = any("--- SIMULATED EMAIL ---" in str(call) for call in logged_calls)
            self.assertTrue(email_log_found, "Simulated email content should be logged")
        
        # Verify message status was updated
        message.refresh_from_db()
        self.assertEqual(message.latest_status, DeliveryStatus.DELIVERED)
        
        # Verify attempt was created
        attempt = OutboundMessageAttempt.objects.filter(message=message).first()
        self.assertIsNotNone(attempt)
        self.assertEqual(attempt.status, DeliveryStatus.DELIVERED)
        self.assertEqual(attempt.provider, "postmark_simulation")

    def test_html_to_plaintext_conversion_output(self):
        """Test that inscriptis produces the expected plaintext output."""
        html_body = """
        <h1>Test Email</h1>
        <p>This is a test with <strong>bold text</strong>.</p>
        <ul>
            <li>First item</li>
            <li>Second item</li>
        </ul>
        """
        
        # Verify the actual conversion produces expected output
        actual_plaintext = get_text(html_body).strip()
        self.assertIn("Test Email", actual_plaintext)
        self.assertIn("This is a test with bold text.", actual_plaintext)
        self.assertIn("* First item", actual_plaintext)
        self.assertIn("* Second item", actual_plaintext)

        # Verify it handles different HTML structures correctly
        complex_html = """
        <div>
            <h2>Important Notice</h2>
            <p>Please review the following:</p>
            <table>
                <tr><th>Item</th><th>Status</th></tr>
                <tr><td>Task 1</td><td>Complete</td></tr>
                <tr><td>Task 2</td><td>Pending</td></tr>
            </table>
        </div>
        """
        
        table_result = get_text(complex_html).strip()
        self.assertIn("Important Notice", table_result)
        self.assertIn("Please review", table_result)
        self.assertIn("Item", table_result)
        self.assertIn("Status", table_result)
        self.assertIn("Task 1", table_result)
        self.assertIn("Complete", table_result)

    @override_settings(GOBII_RELEASE_ENV="prod", POSTMARK_ENABLED=True)
    @patch.dict(os.environ, {"POSTMARK_SERVER_TOKEN": "test-token"}, clear=False)
    @patch('api.agent.comms.outbound_delivery.AnymailMessage')
    @patch('api.agent.comms.email_content.get_text')
    def test_production_email_delivery_uses_inscriptis(self, mock_get_text, mock_anymail):
        """Test that inscriptis conversion is used in production email delivery."""
        # Setup mock email message
        mock_msg = MagicMock()
        mock_anymail.return_value = mock_msg
        mock_msg.anymail_status.message_id = "test-message-id"
        
        # Setup inscriptis mock
        mock_get_text.return_value = "Production Test\n\nTest message"
        
        html_body = "<h1>Production Test</h1><p>Test message</p>"
        
        message = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.from_endpoint,
            to_endpoint=self.to_endpoint,
            is_outbound=True,
            body=html_body,
            raw_payload={"subject": "Production Test"},
            latest_status=DeliveryStatus.QUEUED
        )
        
        with patch('api.agent.comms.outbound_delivery.render_to_string') as mock_render:
            mock_render.return_value = "<html><body>Full HTML</body></html>"
            
            deliver_agent_email(message)
            
            # Verify inscriptis get_text was called with the message body and config
            mock_get_text.assert_called_once()
            args, kwargs = mock_get_text.call_args
            self.assertEqual(args[0], html_body)  # First argument should be the HTML body
            # Second argument should be a ParserConfig object
            from inscriptis.model.config import ParserConfig
            self.assertIsInstance(args[1], ParserConfig)
            
            # Verify AnymailMessage was created with inscriptis plaintext
            mock_anymail.assert_called_once()
            call_kwargs = mock_anymail.call_args[1]
            
            # The body should be the plaintext version from inscriptis
            self.assertEqual(call_kwargs['body'], "Production Test\n\nTest message")
            
            # Verify send was called
            mock_msg.send.assert_called_once_with(fail_silently=False)


@tag("batch_outbound_delivery")
class EmailContentRenderingTests(TestCase):
    def test_markdown_with_inline_html_renders_cleanly(self):
        body = (
            "Today's scan\n\n"
            "**Part A:** No relevant funding.\n\n"
            "Sources: <a href='https://example.com'>Example</a>"
        )
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        self.assertIn("<strong>Part A:</strong>", html_snippet)
        self.assertNotIn("**Part A:", html_snippet)
        self.assertRegex(
            html_snippet,
            r"<p>Sources:\s*<a href='https://example.com'>Example</a></p>",
        )
        self.assertIn("Part A:", plaintext)

    def test_html_with_markdown_inside_tags_is_repaired(self):
        body = "<p>**bold** inside</p>"
        html_snippet, _ = convert_body_to_html_and_plaintext(body)

        self.assertIn("<strong>bold</strong>", html_snippet)
        self.assertNotIn("**bold**", html_snippet)

    def test_html_block_with_markdown_list_is_converted(self):
        body = "<div>\n- First\n- Second\n</div>"
        html_snippet, _ = convert_body_to_html_and_plaintext(body)

        self.assertIn("<ul>", html_snippet)
        self.assertIn("<li>First</li>", html_snippet)
        self.assertIn("<li>Second</li>", html_snippet)
        self.assertNotIn("- First", html_snippet)

    def test_plaintext_paragraphs_preserve_line_breaks(self):
        body = "Line 1\nLine 2\n\nLine 3"
        html_snippet, _ = convert_body_to_html_and_plaintext(body)

        self.assertIn("<p>Line 1<br />Line 2</p>", html_snippet)
        self.assertIn("<p>Line 3</p>", html_snippet)

    def test_horizontal_rules_are_removed(self):
        body = "Section 1\n\n---\n\nSection 2"
        html_snippet, _ = convert_body_to_html_and_plaintext(body)

        self.assertNotIn("<hr", html_snippet.lower())
        self.assertIn("Section 1", html_snippet)
        self.assertIn("Section 2", html_snippet)


@tag("batch_outbound_delivery")
class SMSThrottleNoticeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="sms-throttle@example.com",
            email="sms-throttle@example.com",
            password="password",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="SMS Throttle Browser Agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="SMS Throttle Agent",
            charter="Test cron throttle sms suffix",
            browser_use_agent=self.browser_agent,
            schedule="@daily",
        )
        # Age the agent past the throttle threshold
        PersistentAgent.objects.filter(pk=self.agent.pk).update(
            created_at=timezone.now() - timedelta(days=20)
        )
        self.agent.refresh_from_db()

        self.from_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15550001111",
            is_primary=True,
        )
        self.to_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.SMS,
            address="+15550002222",
        )

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("api.agent.comms.outbound_delivery.switch_is_active", return_value=True)
    @patch("config.redis_client.get_redis_client")
    @patch("api.agent.comms.outbound_delivery.sms.send_sms", return_value="sms123")
    def test_deliver_agent_sms_appends_throttle_notice_when_pending(self, _mock_send, mock_get_redis, _mock_switch):
        class _FakeRedis:
            def __init__(self):
                self._store = {}

            def get(self, key):
                return self._store.get(key)

            def set(self, key, value, ex=None, nx=None):
                if nx and key in self._store:
                    return False
                self._store[key] = value
                return True

            def delete(self, key):
                self._store.pop(key, None)
                return 1

        fake_redis = _FakeRedis()
        mock_get_redis.return_value = fake_redis

        from api.services.cron_throttle import (
            cron_throttle_footer_cooldown_key,
            cron_throttle_pending_footer_key,
        )

        pending_key = cron_throttle_pending_footer_key(str(self.agent.id))
        fake_redis.set(pending_key, "1")

        message = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.from_endpoint,
            to_endpoint=self.to_endpoint,
            is_outbound=True,
            body="Hello there",
            raw_payload={},
        )

        deliver_agent_sms(message)

        # Ensure the throttle notice was appended to the outbound SMS body.
        last_call = _mock_send.call_args_list[-1]
        sent_body = last_call.kwargs.get("body", "")
        self.assertIn("Hello there", sent_body)
        self.assertIn("🥺", sent_body)
        self.assertIn("/subscribe/pro/", sent_body)

        self.assertFalse(fake_redis.get(pending_key))
        self.assertTrue(fake_redis.get(cron_throttle_footer_cooldown_key(str(self.agent.id))))


@tag("batch_outbound_delivery")
class SMSContentConversionTests(TestCase):
    """Test SMS content conversion functionality."""

    def test_html_content_conversion(self):
        """Test HTML content is properly converted to plaintext for SMS."""
        html_body = """
        <h1>Meeting Reminder</h1>
        <p>Hi there!</p>
        <p>Just a quick reminder about our meeting tomorrow at <strong>2:00 PM</strong>.</p>
        <ul>
            <li>Bring your laptop</li>
            <li>Review the agenda</li>
        </ul>
        <p>Thanks!<br>John</p>
        """
        
        result = _convert_sms_body_to_plaintext(html_body)
        
        # Verify key content is preserved
        self.assertIn("Meeting Reminder", result)
        self.assertIn("Hi there!", result)
        self.assertIn("2:00 PM", result)
        self.assertIn("Bring your laptop", result)
        self.assertIn("Review the agenda", result)
        self.assertIn("Thanks!", result)
        self.assertIn("John", result)
        
        # Verify no HTML tags remain
        self.assertNotIn("<", result)
        self.assertNotIn(">", result)

    def test_markdown_content_conversion(self):
        """Test Markdown content is properly converted to plaintext for SMS."""
        markdown_body = """# Meeting Update

Hi **team**!

Here's what we discussed:

- Project timeline moved to next week
- Budget approved for new tools
- Next meeting: `Friday 3pm`

For more details, check [our docs](https://example.com/docs).

Thanks!
"""
        
        result = _convert_sms_body_to_plaintext(markdown_body)
        
        # Verify content is converted to plaintext
        self.assertIn("Meeting Update", result)
        self.assertIn("Hi team!", result)
        self.assertIn("Project timeline", result)
        self.assertIn("Budget approved", result)
        self.assertIn("Friday 3pm", result)
        self.assertIn("our docs", result)
        self.assertIn("Thanks!", result)
        
        # Verify Markdown formatting is converted to plaintext
        self.assertNotIn("**", result)  # Bold formatting removed
        self.assertNotIn("# ", result)  # Heading formatting removed  
        self.assertNotIn("`", result)   # Code formatting removed
        self.assertNotIn("](", result)  # Link syntax removed
        
        # URLs should be preserved (either inline or as references)
        self.assertIn("https://example.com/docs", result)

    def test_plaintext_content_passthrough(self):
        """Test plaintext content passes through unchanged."""
        plaintext_body = """Hey there!

Just wanted to let you know the meeting is moved to tomorrow at 3pm.

Let me know if you have any questions.

Thanks!
John"""
        
        result = _convert_sms_body_to_plaintext(plaintext_body)
        
        # Should be identical to input (just stripped)
        self.assertEqual(result, plaintext_body.strip())

    def test_mixed_content_with_html_priority(self):
        """Test that HTML detection takes priority over Markdown."""
        mixed_body = """<p>This has both <strong>HTML</strong> and **markdown** formatting.</p>"""
        
        result = _convert_sms_body_to_plaintext(mixed_body)
        
        # Should process as HTML (strip tags) not Markdown
        self.assertIn("This has both HTML and **markdown** formatting.", result)
        self.assertNotIn("<", result)
        self.assertNotIn(">", result)

    def test_markdown_patterns_detection(self):
        """Test various Markdown patterns are properly detected."""
        test_cases = [
            ("# Heading", "Heading"),
            ("**bold text**", "bold text"),
            ("__bold text__", "bold text"),
            ("`code block`", "code block"),
            ("```\ncode fence\n```", "code fence"),
            ("[link text](https://example.com)", "link text"),
            ("- list item", "list item"),
            ("* list item", "list item"),
            ("+ list item", "list item"),
            ("1. ordered item", "ordered item"),
        ]
        
        for markdown_input, expected_content in test_cases:
            with self.subTest(markdown=markdown_input):
                result = _convert_sms_body_to_plaintext(markdown_input)
                self.assertIn(expected_content, result)
                # Ensure no markdown syntax remains
                self.assertNotIn("**", result)
                self.assertNotIn("__", result)
                self.assertNotIn("`", result)
                self.assertNotIn("](", result)  # Link syntax should be removed
                
                # For links, also check URL is preserved
                if "https://example.com" in markdown_input:
                    self.assertIn("https://example.com", result)

    def test_empty_and_whitespace_content(self):
        """Test handling of empty or whitespace-only content."""
        self.assertEqual(_convert_sms_body_to_plaintext(""), "")
        self.assertEqual(_convert_sms_body_to_plaintext("   "), "")
        self.assertEqual(_convert_sms_body_to_plaintext("\n\n\n"), "")

    def test_special_characters_preserved(self):
        """Test that special characters and emojis are preserved."""
        content = "Meeting @ 2pm! 🎉 Cost: $50.00 (50% off)"
        result = _convert_sms_body_to_plaintext(content)
        self.assertEqual(result, content)

    def test_multi_item_lists_preserve_structure(self):
        """Test that multi-item lists preserve structure and newlines."""
        # Test unordered list
        unordered_list = """Here are the items:

- First item
- Second item  
- Third item

That's all!"""

        result = _convert_sms_body_to_plaintext(unordered_list)
        
        # Should contain all items
        self.assertIn("First item", result)
        self.assertIn("Second item", result)
        self.assertIn("Third item", result)
        
        # Should preserve structure with newlines AND bullet markers
        lines = result.split('\n')
        list_lines = [line.strip() for line in lines if line.strip().startswith('-') and 'item' in line.lower()]
        self.assertEqual(len(list_lines), 3, "Should have 3 separate list item lines")
        
        # Each list item should be on its own line with markers preserved
        self.assertIn("-   First item", list_lines)
        self.assertIn("-   Second item", list_lines)
        self.assertIn("-   Third item", list_lines)
        
        # Test ordered list
        ordered_list = """Steps to follow:

1. First step
2. Second step
3. Third step

Done!"""

        result = _convert_sms_body_to_plaintext(ordered_list)
        
        # Should contain all steps
        self.assertIn("First step", result)
        self.assertIn("Second step", result) 
        self.assertIn("Third step", result)
        
        # Should preserve structure with newlines AND number markers
        lines = result.split('\n')
        step_lines = [line.strip() for line in lines if line.strip() and line.strip()[0].isdigit() and 'step' in line.lower()]
        self.assertEqual(len(step_lines), 3, "Should have 3 separate step lines")
        
        # Each step should be on its own line with markers preserved
        self.assertIn("1.  First step", step_lines)
        self.assertIn("2.  Second step", step_lines) 
        self.assertIn("3.  Third step", step_lines)

    def test_markdown_links_preserve_urls(self):
        """Test that markdown links preserve the full URL in plaintext for SMS."""
        test_cases = [
            {
                "markdown": "Check out [our website](https://example.com) for more info.",
                "expected_url": "https://example.com",
                "expected_text": "our website",
                "description": "Simple link with descriptive text"
            },
            {
                "markdown": "Visit [https://docs.example.com](https://docs.example.com) for docs.",
                "expected_url": "https://docs.example.com", 
                "expected_text": "https://docs.example.com",
                "description": "Link where text and URL are the same"
            },
            {
                "markdown": "Multiple links: [Google](https://google.com) and [GitHub](https://github.com).",
                "expected_urls": ["https://google.com", "https://github.com"],
                "expected_texts": ["Google", "GitHub"],
                "description": "Multiple links in one message"
            },
            {
                "markdown": "Email us at [support@example.com](mailto:support@example.com) or call.",
                "expected_text": "support@example.com",
                "description": "Email link with mailto protocol"
            },
            {
                "markdown": "Download from [here](https://files.example.com/app.zip?v=1.2.3&ref=sms).",
                "expected_url": "https://files.example.com/app.zip?v=1.2.3&ref=sms",
                "expected_text": "here",
                "description": "URL with query parameters"
            },
        ]
        
        for case in test_cases:
            with self.subTest(description=case["description"]):
                result = _convert_sms_body_to_plaintext(case["markdown"])
                
                # Handle single URL case
                if "expected_url" in case:
                    # Text should appear in main content
                    self.assertIn(case['expected_text'], result)
                    # URL should be preserved somewhere in the result
                    self.assertIn(case['expected_url'], result)
                
                # Handle multiple URLs case  
                if "expected_urls" in case:
                    for url, text in zip(case["expected_urls"], case["expected_texts"]):
                        # Both text and URL should be preserved
                        self.assertIn(text, result)
                        self.assertIn(url, result)
                
                # Ensure markdown link syntax is cleaned up
                self.assertNotIn("](", result, "Markdown link syntax should be removed")

    def test_markdown_links_with_mixed_content(self):
        """Test that URLs are preserved when markdown links are mixed with other content types."""
        mixed_content = """# Meeting Notes

Thanks for joining today's **important** meeting!

Key points discussed:
- Project status: On track
- Next steps: Review [the documentation](https://docs.company.com/project-x)
- Deadline: `March 15th, 2024`

Please check our [company portal](https://portal.company.com) for updates.

Contact me at [john@company.com](mailto:john@company.com) if you have questions."""
        
        result = _convert_sms_body_to_plaintext(mixed_content)
        
        # Verify all URLs and text are preserved
        expected_urls = [
            "https://docs.company.com/project-x",
            "https://portal.company.com"
        ]
        
        expected_texts = [
            "the documentation",
            "company portal",
            "john@company.com"
        ]
        
        for url in expected_urls:
            self.assertIn(url, result, f"URL '{url}' should be preserved")
            
        for text in expected_texts:
            self.assertIn(text, result, f"Link text '{text}' should be preserved")
        
        # Verify markdown syntax is removed but content is preserved
        self.assertIn("Meeting Notes", result)
        self.assertIn("important meeting", result)  # **bold** should become plain text
        self.assertIn("March 15th, 2024", result)  # `code` should become plain text
        
        # Verify no markdown syntax remains
        self.assertNotIn("**", result)
        self.assertNotIn("`", result)  
        self.assertNotIn("](", result)
        self.assertNotIn("# ", result)

    def test_long_content_handling(self):
        """Test handling of long content typical in SMS scenarios."""
        long_content = "This is a very long message that might be sent via SMS. " * 10
        result = _convert_sms_body_to_plaintext(long_content)
        
        # Should handle long content without issues
        self.assertEqual(result, long_content.strip())
        self.assertGreater(len(result), 100)  # Ensure it's actually long

    def test_markdown_links_deduplicate_identical_text_url(self):
        """Test that links preserve URLs in SMS-friendly format."""
        test_cases = [
            {
                "markdown": "Visit [https://example.com](https://example.com) for more info.",
                "expected_url": "https://example.com",
                "expected_text": "Visit https://example.com for more info.",
                "description": "Simple URL link with identical text and href"
            },
            {
                "markdown": "Check out [https://docs.python.org](https://docs.python.org) and [our site](https://company.com).",
                "expected_urls": ["https://docs.python.org", "https://company.com"],
                "expected_texts": ["our site"],
                "description": "Mixed case: one identical, one different"
            },
            {
                "markdown": "Three links: [https://a.com](https://a.com), [site B](https://b.com), [https://c.com](https://c.com).",
                "expected_urls": ["https://a.com", "https://b.com", "https://c.com"],
                "expected_texts": ["site B"],
                "description": "Multiple links with some identical text/URL pairs"
            }
        ]
        
        for case in test_cases:
            with self.subTest(case=case["description"]):
                result = _convert_sms_body_to_plaintext(case["markdown"])
                
                # URLs should be preserved
                if "expected_url" in case:
                    self.assertIn(case["expected_url"], result)
                
                if "expected_urls" in case:
                    for url in case["expected_urls"]:
                        self.assertIn(url, result)
                        
                # Link text should be preserved
                if "expected_texts" in case:
                    for text in case["expected_texts"]:
                        self.assertIn(text, result)
                
                # Ensure no markdown syntax remains
                self.assertNotIn("](", result)

    def test_markdown_links_edge_cases_deduplication(self):
        """Test edge cases for URL handling in SMS."""
        test_cases = [
            {
                "markdown": "URL with fragment: [https://example.com#section](https://example.com#section)",
                "expected_url": "https://example.com#section",
                "description": "URL with fragment identifier"
            },
            {
                "markdown": "URL with params: [https://api.com?param=value](https://api.com?param=value)",
                "expected_url": "https://api.com?param=value", 
                "description": "URL with query parameters"
            },
            {
                "markdown": "Case sensitive: [HTTPS://EXAMPLE.COM](https://example.com)",
                "expected_text": "HTTPS://EXAMPLE.COM",
                "expected_url": "https://example.com",
                "description": "Case sensitivity in URL handling"
            },
            {
                "markdown": "Different protocols: [ftp://files.com](ftp://files.com) and [ssh://server.com](ssh://server.com)",
                "expected_urls": ["ftp://files.com", "ssh://server.com"],
                "description": "Non-HTTP protocols should be preserved"
            }
        ]
        
        for case in test_cases:
            with self.subTest(case=case["description"]):
                result = _convert_sms_body_to_plaintext(case["markdown"])
                
                # Single URL case
                if "expected_url" in case:
                    self.assertIn(case["expected_url"], result)
                
                # Text should be preserved if specified
                if "expected_text" in case:
                    self.assertIn(case["expected_text"], result)
                
                # Multiple URLs case
                if "expected_urls" in case:
                    for url in case["expected_urls"]:
                        self.assertIn(url, result)
                
                # Ensure no markdown syntax remains
                self.assertNotIn("](", result)
