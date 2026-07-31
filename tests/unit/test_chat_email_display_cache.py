from django.test import SimpleTestCase, tag

from api.agent.comms.chat_email_display_cache import (
    CHAT_BODY_HTML_CACHE_KEY,
    get_cached_chat_body_html,
    merge_chat_body_html_cache,
    render_chat_email_body_html,
)


@tag("batch_agent_chat")
class ChatEmailDisplayCacheTests(SimpleTestCase):
    def test_cache_hit_returns_stored_html(self):
        payload = merge_chat_body_html_cache(
            {},
            "Plain fallback",
            explicit_html="<p><strong>Hello</strong></p>",
        )

        self.assertEqual(
            get_cached_chat_body_html(payload),
            "<p><strong>Hello</strong></p>",
        )

    def test_cache_miss_without_cached_html(self):
        self.assertIsNone(get_cached_chat_body_html({}))

    def test_render_sanitizes_unsafe_html(self):
        rendered = render_chat_email_body_html(
            "Fallback",
            explicit_html="<p onclick='alert(1)'>Safe</p><script>alert(1)</script>",
        )

        self.assertIn("<p>Safe</p>", rendered)
        self.assertNotIn("onclick", rendered)
        self.assertNotIn("<script", rendered)


@tag("batch_agent_chat")
class ChatEmailStyleLeakTests(SimpleTestCase):
    """Bug #504: bleach strip=True removes disallowed tags but keeps their text, so email
    <style>/<title> contents rendered as prose walls in the chat timeline."""

    def test_style_block_contents_are_removed(self):
        rendered = render_chat_email_body_html(
            "Fallback",
            explicit_html=(
                "<style>body { padding:0 !important; } .btn a { width:2% !important; }</style>"
                "<p>You have a new message</p>"
            ),
        )
        self.assertIn("You have a new message", rendered)
        self.assertNotIn("!important", rendered)
        self.assertNotIn(".btn", rendered)

    def test_title_and_head_contents_are_removed(self):
        rendered = render_chat_email_body_html(
            "Fallback",
            explicit_html="<html><head><title>Email Template</title></head><body><p>hi</p></body></html>",
        )
        self.assertIn("hi", rendered)
        self.assertNotIn("Email Template", rendered)
