import re

from django.test import TestCase, tag

from api.agent.comms.email_content import convert_body_to_html_and_plaintext


@tag("batch_email_body")
class EmailBodyRenderingTestCase(TestCase):
    """Test email body content detection and conversion."""

    @tag("batch_email_body")
    def test_html_stays_as_is(self):
        """HTML content should be preserved as-is."""
        body = "<p>Hello</p><p>Thanks</p>"
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        self.assertEqual(html_snippet.replace("\n", ""), "<p>Hello</p><p>Thanks</p>")
        # inscriptis adds some whitespace when converting HTML to text
        self.assertIn("Hello", plaintext)
        self.assertIn("Thanks", plaintext)

    @tag("batch_email_body")
    def test_plaintext_converted_to_br(self):
        """Plaintext newlines should be converted to <br> tags."""
        body = "Hello\n\nThanks"
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        self.assertEqual(html_snippet, "<p>Hello</p><p>Thanks</p>")
        self.assertEqual(plaintext.strip(), "Hello\n\nThanks")

    @tag("batch_email_body")
    def test_markdown_rendered_to_html(self):
        """Markdown content should be rendered to HTML."""
        body = "# Title\n\n- one\n- two"
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        # Check that markdown was converted to HTML
        self.assertIn("<h1>Title</h1>", html_snippet)
        self.assertIn("<li>one</li>", html_snippet)
        self.assertIn("<li>two</li>", html_snippet)
        
        # Plaintext should start with "Title"
        self.assertTrue(plaintext.startswith("Title"))

    def test_bold_markdown_converted(self):
        """Bold markdown should be converted to HTML."""
        body = "This is **bold** text"
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        self.assertIn("<strong>bold</strong>", html_snippet)
        self.assertIn("bold", plaintext)

    @tag("batch_email_body")
    def test_link_markdown_converted(self):
        """Markdown links should be converted to HTML."""
        body = "Check out [Google](https://google.com)"
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        self.assertIn('<a href="https://google.com">Google</a>', html_snippet)
        self.assertIn("Google", plaintext)

    def test_links_preserved_in_plaintext(self):
        """URLs should be preserved in plaintext conversion."""
        body = "Check out [Google](https://google.com) and [GitHub](https://github.com)"
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        # HTML should contain proper links
        self.assertIn('<a href="https://google.com">Google</a>', html_snippet)
        self.assertIn('<a href="https://github.com">GitHub</a>', html_snippet)
        
        # Plaintext should now preserve the URLs
        self.assertIn("https://google.com", plaintext)
        self.assertIn("https://github.com", plaintext)

    def test_html_links_preserved_in_plaintext(self):
        """URLs in HTML should be preserved in plaintext conversion."""
        body = '<p>Visit <a href="https://example.com">Example</a> and <a href="https://test.org">Test Site</a></p>'
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        # HTML should be preserved as-is
        self.assertEqual(html_snippet, body)
        
        # Plaintext should preserve the URLs
        self.assertIn("https://example.com", plaintext)
        self.assertIn("https://test.org", plaintext)

    def test_code_markdown_converted(self):
        """Inline code markdown should be converted to HTML."""
        body = "Use `git status` command"
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        self.assertIn("<code>git status</code>", html_snippet)
        self.assertIn("git status", plaintext)

    def test_mixed_html_not_converted(self):
        """Content with HTML tags should still render markdown safely."""
        body = "# Title\n\n<p>This is HTML</p>"
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        # Markdown should render while preserving HTML tags
        self.assertIn("<h1>Title</h1>", html_snippet)
        self.assertIn("<p>This is HTML</p>", html_snippet)
        self.assertNotIn("# Title", html_snippet)
        self.assertIn("Title", plaintext)
        self.assertIn("This is HTML", plaintext)

    def test_fake_html_gets_escaped(self):
        """Content with angle brackets but no real HTML tags should be escaped."""
        body = "Check if 5 < 10 and 10 > 5"
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        # Should be treated as plaintext and escaped
        self.assertIn("&lt;", html_snippet)
        self.assertIn("&gt;", html_snippet)
        self.assertEqual(plaintext.strip(), "Check if 5 < 10 and 10 > 5")

    def test_html_escape_in_plaintext(self):
        """Special characters in plaintext should be escaped."""
        body = "Use <script> tag & other < > symbols"
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        self.assertIn("&lt;script&gt;", html_snippet)
        self.assertIn("&amp;", html_snippet)
        self.assertIn("&lt;", html_snippet)
        self.assertIn("&gt;", html_snippet)
        self.assertEqual(plaintext.strip(), "Use <script> tag & other < > symbols")

    def test_unicode_escape_em_dash_decoded(self):
        """Unicode escape \\u2014 should be decoded to em dash."""
        body = "Looking into benchmarks now \\u2014 I'll report back"
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        # Em dash should be in the output, not the escape sequence
        self.assertIn("—", html_snippet)
        self.assertIn("—", plaintext)
        self.assertNotIn("\\u2014", html_snippet)
        self.assertNotIn("\\u2014", plaintext)

    def test_unicode_escape_bullet_with_markdown(self):
        """Unicode bullet escapes combined with markdown should render correctly."""
        body = (
            "Got it! I'll report back with:\\n"
            "\\u2022 **Top benchmark claims**\\n"
            "\\u2022 **Head-to-head comparisons**"
        )
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        # Bullets should be decoded
        self.assertIn("•", html_snippet)
        self.assertIn("•", plaintext)
        # Markdown bold should be rendered
        self.assertIn("<strong>", html_snippet)
        # Escape sequences should not appear
        self.assertNotIn("\\u2022", html_snippet)

    def test_unicode_escape_smart_quotes_decoded(self):
        """Smart quote escapes should be decoded properly."""
        body = "He said \\u201cHello\\u201d"
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        # Check that left and right double quotes are present (decoded from escapes)
        self.assertIn("\u201c", html_snippet)  # left double quote
        self.assertIn("\u201d", html_snippet)  # right double quote
        self.assertNotIn("\\u201c", html_snippet)

    def test_unicode_escape_with_html_tags(self):
        """Unicode escapes in HTML content should be decoded."""
        body = "<p>Check this \\u2014 important</p>"
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        self.assertIn("—", html_snippet)
        self.assertIn("—", plaintext)

    def test_real_world_llm_email_output(self):
        """Test realistic LLM output that triggered the original bug."""
        body = (
            "Got it! Looking into GLM-4.7 benchmarks now \\u2014 I'll report back with:\\n"
            "\\n"
            "\\u2022 **Top benchmark claims** from Z.AI's official release\\n"
            "\\u2022 **Head-to-head comparisons** vs Claude, GPT-4, Qwen, DeepSeek\\n"
            "\\u2022 **Community validation** \\u2014 real-world tests from the r/LocalLLaMA crowd\\n"
            "\\u2022 **Methodology notes** \\u2014 what benchmarks, how they were run\\n"
            "\\n"
            "I'll dig into the data and get you a comprehensive breakdown shortly.\\n"
            "\\n"
            "Roxie"
        )
        html_snippet, plaintext = convert_body_to_html_and_plaintext(body)

        # All unicode escapes should be decoded
        self.assertNotIn("\\u2014", html_snippet)
        self.assertNotIn("\\u2022", html_snippet)
        self.assertNotIn("\\u2014", plaintext)
        self.assertNotIn("\\u2022", plaintext)

        # Actual characters should be present
        self.assertIn("—", plaintext)  # em dash
        self.assertIn("•", plaintext)  # bullet

        # Markdown should be rendered in HTML
        self.assertIn("<strong>", html_snippet)
