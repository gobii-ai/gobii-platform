from __future__ import annotations

from django.test import SimpleTestCase, tag

from console.templatetags.agent_extras import agent_message_html
from util.markdown_render import render_agent_markdown


@tag("console_message_formatting_batch")
class MarkdownRenderingTests(SimpleTestCase):
    def test_numbered_list_without_blank_line_renders_ordered_list(self):
        markdown = "Intro paragraph\n1. First item\n2. Second item"

        html = render_agent_markdown(markdown)

        self.assertIn("<ol>", html)
        self.assertIn("<li>First item</li>", html)
        self.assertIn("<li>Second item</li>", html)
        self.assertNotIn("1. First item", html)

    def test_existing_list_not_duplicated(self):
        markdown = "1. Alpha\n2. Beta"

        html = render_agent_markdown(markdown)

        self.assertEqual(html.count("<li>"), 2)
        self.assertIn("<li>Alpha</li>", html)
        self.assertIn("<li>Beta</li>", html)

    def test_strikethrough_renders_del_tag(self):
        html = render_agent_markdown("Use ~~deprecated~~ feature")

        self.assertIn("<del>deprecated</del>", html)

    def test_task_list_renders_checkbox_inputs(self):
        html = render_agent_markdown("- [x] Done\n- [ ] Todo")

        self.assertGreaterEqual(html.count("<input"), 2)
        self.assertIn('type="checkbox"', html)
        self.assertIn('checked', html)
        self.assertIn("Done", html)
        self.assertIn("Todo", html)


@tag("console_message_formatting_batch")
class AgentMessageHTMLTests(SimpleTestCase):
    def test_inline_html_preserves_newlines(self):
        html = str(agent_message_html("<span>Hello</span>\nSecond line"))

        self.assertIn("<span>Hello</span><br />Second line", html)

    def test_plaintext_html_detection_preserves_breaks(self):
        html = str(agent_message_html("Visit <https://example.com>\nThanks"))

        self.assertIn('<a href="https://example.com">https://example.com</a>', html)
        self.assertIn("<br", html)

    def test_block_html_remains_unchanged(self):
        html = str(agent_message_html("<p>Hello</p>\n<p>World</p>"))

        self.assertIn("<p>Hello</p>", html)
        self.assertIn("<p>World</p>", html)
        self.assertNotIn("<br />", html)
