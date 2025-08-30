import re

from django.test import TestCase, tag

from api.agent.comms.outbound_delivery import _convert_body_to_html_and_plaintext


@tag("batch_email_body")
class EmailBodyRenderingTestCase(TestCase):
    """Test email body content detection and conversion."""

    @tag("batch_email_body")
    def test_html_stays_as_is(self):
        """HTML content should be preserved as-is."""
        body = "<p>Hello</p><p>Thanks</p>"
        html_snippet, plaintext = _convert_body_to_html_and_plaintext(body)
        
        self.assertEqual(html_snippet, "<p>Hello</p><p>Thanks</p>")
        # inscriptis adds some whitespace when converting HTML to text
        self.assertIn("Hello", plaintext)
        self.assertIn("Thanks", plaintext)

    @tag("batch_email_body")
    def test_plaintext_converted_to_br(self):
        """Plaintext newlines should be converted to <br> tags."""
        body = "Hello\n\nThanks"
        html_snippet, plaintext = _convert_body_to_html_and_plaintext(body)
        
        self.assertEqual(html_snippet, "Hello<br><br>Thanks")
        self.assertEqual(plaintext.strip(), "Hello\n\nThanks")

    @tag("batch_email_body")
    def test_markdown_rendered_to_html(self):
        """Markdown content should be rendered to HTML."""
        body = "# Title\n\n- one\n- two"
        html_snippet, plaintext = _convert_body_to_html_and_plaintext(body)
        
        # Check that markdown was converted to HTML
        self.assertIn("<h1>Title</h1>", html_snippet)
        self.assertIn("<li>one</li>", html_snippet)
        self.assertIn("<li>two</li>", html_snippet)
        
        # Plaintext should start with "Title"
        self.assertTrue(plaintext.startswith("Title"))

    def test_bold_markdown_converted(self):
        """Bold markdown should be converted to HTML."""
        body = "This is **bold** text"
        html_snippet, plaintext = _convert_body_to_html_and_plaintext(body)
        
        self.assertIn("<strong>bold</strong>", html_snippet)
        self.assertIn("bold", plaintext)

    @tag("batch_email_body")
    def test_link_markdown_converted(self):
        """Markdown links should be converted to HTML."""
        body = "Check out [Google](https://google.com)"
        html_snippet, plaintext = _convert_body_to_html_and_plaintext(body)
        
        self.assertIn('<a href="https://google.com">Google</a>', html_snippet)
        self.assertIn("Google", plaintext)

    def test_links_preserved_in_plaintext(self):
        """URLs should be preserved in plaintext conversion."""
        body = "Check out [Google](https://google.com) and [GitHub](https://github.com)"
        html_snippet, plaintext = _convert_body_to_html_and_plaintext(body)
        
        # HTML should contain proper links
        self.assertIn('<a href="https://google.com">Google</a>', html_snippet)
        self.assertIn('<a href="https://github.com">GitHub</a>', html_snippet)
        
        # Plaintext should now preserve the URLs
        self.assertIn("https://google.com", plaintext)
        self.assertIn("https://github.com", plaintext)
        
    def test_html_links_preserved_in_plaintext(self):
        """URLs in HTML should be preserved in plaintext conversion."""
        body = '<p>Visit <a href="https://example.com">Example</a> and <a href="https://test.org">Test Site</a></p>'
        html_snippet, plaintext = _convert_body_to_html_and_plaintext(body)
        
        # HTML should be preserved as-is
        self.assertEqual(html_snippet, body)
        
        # Plaintext should preserve the URLs
        self.assertIn("https://example.com", plaintext)
        self.assertIn("https://test.org", plaintext)

    def test_code_markdown_converted(self):
        """Inline code markdown should be converted to HTML."""
        body = "Use `git status` command"
        html_snippet, plaintext = _convert_body_to_html_and_plaintext(body)
        
        self.assertIn("<code>git status</code>", html_snippet)
        self.assertIn("git status", plaintext)

    def test_mixed_html_not_converted(self):
        """Content with real HTML tags should not be processed as markdown."""
        body = "# Title\n\n<p>This is HTML</p>"
        html_snippet, plaintext = _convert_body_to_html_and_plaintext(body)
        
        # Should preserve the original content since it contains real HTML tags
        self.assertEqual(html_snippet, "# Title\n\n<p>This is HTML</p>")
        self.assertIn("Title", plaintext)
        self.assertIn("This is HTML", plaintext)

    def test_fake_html_gets_escaped(self):
        """Content with angle brackets but no real HTML tags should be escaped."""
        body = "Check if 5 < 10 and 10 > 5"
        html_snippet, plaintext = _convert_body_to_html_and_plaintext(body)
        
        # Should be treated as plaintext and escaped
        self.assertIn("&lt;", html_snippet)
        self.assertIn("&gt;", html_snippet)
        self.assertEqual(plaintext.strip(), "Check if 5 < 10 and 10 > 5")

    def test_html_escape_in_plaintext(self):
        """Special characters in plaintext should be escaped."""
        body = "Use <script> tag & other < > symbols"
        html_snippet, plaintext = _convert_body_to_html_and_plaintext(body)
        
        self.assertIn("&lt;script&gt;", html_snippet)
        self.assertIn("&amp;", html_snippet)
        self.assertIn("&lt;", html_snippet)
        self.assertIn("&gt;", html_snippet)
        self.assertEqual(plaintext.strip(), "Use <script> tag & other < > symbols")
