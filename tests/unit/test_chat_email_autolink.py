"""Bare addresses and URLs in chat email HTML must be reachable, not just readable.

Messages rendered from markdown are autolinked on the client by remark-gfm, but anything that
reaches the timeline as HTML -- an agent's own HTML body, or a markdown body already converted
server side -- was emitted verbatim. A recipient shown "write to alice@example.com" could read the
address but not act on it, and the same text in a non-email channel was clickable. This pins the
two paths to the same contract.

Linkification runs inside the sanitizer so every caller inherits it: explicit agent HTML, cached
bodies, and cid-rewritten forwards all funnel through that one function.
"""
from __future__ import annotations

from django.test import SimpleTestCase, tag

from api.agent.comms.chat_email_display_cache import sanitize_chat_email_html


@tag("batch_agent_chat")
class ChatEmailAutolinkTests(SimpleTestCase):
    def test_bare_email_becomes_a_mailto_link(self):
        html = sanitize_chat_email_html("<p>Reach out to alice.smith@example.com about it.</p>")

        self.assertIn('href="mailto:alice.smith@example.com"', html)
        self.assertIn(">alice.smith@example.com<", html)

    def test_bare_url_becomes_a_link(self):
        """The markdown path already autolinks URLs; the HTML path must not silently differ."""
        html = sanitize_chat_email_html("<p>See https://example.com/docs for details.</p>")

        self.assertIn('href="https://example.com/docs"', html)

    def test_an_existing_mailto_link_is_left_alone(self):
        html = sanitize_chat_email_html('<p>Contact <a href="mailto:bob@example.com">bob@example.com</a>.</p>')

        self.assertEqual(html.count("<a "), 1)
        self.assertNotIn("<a href", html[html.index("<a ") + 3:])

    def test_an_address_used_as_link_text_keeps_its_own_target(self):
        """Rewriting the href here would silently redirect the reader somewhere else."""
        html = sanitize_chat_email_html('<p><a href="https://example.com/team">carol@example.com</a></p>')

        self.assertIn('href="https://example.com/team"', html)
        self.assertNotIn("mailto:", html)

    def test_addresses_inside_code_are_not_linkified(self):
        """Code shows text as written; turning a sample address into a link misrepresents it."""
        html = sanitize_chat_email_html("<p><code>alice@example.com</code></p>")

        self.assertNotIn("mailto:", html)

    def test_addresses_inside_preformatted_blocks_are_not_linkified(self):
        html = sanitize_chat_email_html("<pre>send to alice@example.com</pre>")

        self.assertNotIn("mailto:", html)

    def test_linkifying_does_not_reintroduce_unsafe_markup(self):
        html = sanitize_chat_email_html('<p>hi@example.com</p><script>alert(1)</script>')

        self.assertIn("mailto:hi@example.com", html)
        self.assertNotIn("<script", html)

    def test_javascript_urls_are_not_turned_into_links(self):
        html = sanitize_chat_email_html("<p>javascript:alert(1)</p>")

        self.assertNotIn("<a ", html)

    def test_empty_input_is_unchanged(self):
        self.assertEqual(sanitize_chat_email_html(""), "")
        self.assertEqual(sanitize_chat_email_html(None), "")

    def test_cid_images_still_survive_when_allowed(self):
        """allow_cid callers depend on cid: surviving; linkification must not disturb that."""
        html = sanitize_chat_email_html('<img src="cid:logo.png" alt="logo">', allow_cid=True)

        self.assertIn('src="cid:logo.png"', html)
