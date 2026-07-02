from django.test import SimpleTestCase, tag

from api.agent.comms.chat_email_display_cache import (
    CHAT_BODY_HTML_CACHE_KEY,
    CHAT_BODY_HTML_SOURCE_HASH_KEY,
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
            get_cached_chat_body_html(
                payload,
                "Plain fallback",
                explicit_html="<p><strong>Hello</strong></p>",
            ),
            "<p><strong>Hello</strong></p>",
        )

    def test_cache_miss_when_source_changes(self):
        payload = merge_chat_body_html_cache(
            {},
            "Original body",
            explicit_html="<p>Original</p>",
        )

        self.assertIsNone(
            get_cached_chat_body_html(
                payload,
                "Edited body",
                explicit_html="<p>Original</p>",
            )
        )
        self.assertIn(CHAT_BODY_HTML_CACHE_KEY, payload)
        self.assertIn(CHAT_BODY_HTML_SOURCE_HASH_KEY, payload)

    def test_render_sanitizes_unsafe_html(self):
        rendered = render_chat_email_body_html(
            "Fallback",
            explicit_html="<p onclick='alert(1)'>Safe</p><script>alert(1)</script>",
        )

        self.assertIn("<p>Safe</p>", rendered)
        self.assertNotIn("onclick", rendered)
        self.assertNotIn("<script", rendered)
