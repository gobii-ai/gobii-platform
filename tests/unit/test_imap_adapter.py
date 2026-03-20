from __future__ import annotations

import email
from email.message import EmailMessage

from django.test import TestCase, override_settings, tag

from api.agent.comms.imap_adapter import ImapEmailAdapter, ImapParsedContext


@tag("batch_email")
class ImapAdapterTests(TestCase):
    def _build_plain(self, frm: str = "Alice <alice@example.com>") -> bytes:
        m = EmailMessage()
        m["From"] = frm
        m["To"] = "agent@example.org"
        m["Subject"] = "Hello"
        m.set_content("Hello world!")
        return m.as_bytes()

    def _build_html_only(self) -> bytes:
        m = EmailMessage()
        m["From"] = "Bob <bob@example.com>"
        m["To"] = "agent@example.org"
        m["Subject"] = "HTML only"
        m.add_alternative("<p><b>Hi</b> there</p>", subtype="html")
        return m.as_bytes()

    def _build_multipart(self) -> bytes:
        m = EmailMessage()
        m["From"] = "Dana <dana@example.com>"
        m["To"] = "agent@example.org"
        m["Subject"] = "Multipart"
        m.set_content("Plain fallback line")
        m.add_alternative("<table><tr><td><strong>Rich</strong></td></tr></table>", subtype="html")
        return m.as_bytes()

    def _build_with_attachment(self) -> bytes:
        m = EmailMessage()
        m["From"] = "Carol <carol@example.com>"
        m["To"] = "agent@example.org"
        m["Subject"] = "With attachment"
        m.set_content("See attachment")
        m.add_attachment(b"hello-bytes", maintype="application", subtype="octet-stream", filename="hello.bin")
        return m.as_bytes()

    def _build_with_attached_message(self) -> bytes:
        nested = EmailMessage()
        nested["From"] = "Forwarded <forwarded@example.com>"
        nested["To"] = "agent@example.org"
        nested["Subject"] = "Forwarded email"
        nested.add_alternative("<p><strong>Nested html</strong></p>", subtype="html")

        outer = EmailMessage()
        outer["From"] = "Erin <erin@example.com>"
        outer["To"] = "agent@example.org"
        outer["Subject"] = "Top-level body with attached message"
        outer.set_content("Top-level plain body")
        outer.add_attachment(
            nested.as_bytes(),
            maintype="message",
            subtype="rfc822",
            filename="forwarded.eml",
        )
        return outer.as_bytes()

    def test_plain_text_parse(self):
        raw = self._build_plain()
        parsed = ImapEmailAdapter.parse_bytes(raw, recipient_address="agent@example.org", ctx=ImapParsedContext(uid="1", folder="INBOX"))
        self.assertEqual(parsed.sender, "alice@example.com")
        self.assertEqual(parsed.recipient, "agent@example.org")
        self.assertIn("Hello world!", parsed.body)
        self.assertEqual(parsed.raw_payload.get("imap_uid"), "1")
        self.assertEqual(parsed.raw_payload.get("imap_folder"), "INBOX")

    def test_html_only_parse_to_text(self):
        raw = self._build_html_only()
        parsed = ImapEmailAdapter.parse_bytes(raw, recipient_address="agent@example.org")
        self.assertEqual(parsed.sender, "bob@example.com")
        self.assertIn("Hi", parsed.body)
        self.assertGreater(len(parsed.body.strip()), 0)
        self.assertEqual(parsed.raw_payload.get("body_html"), "<p><b>Hi</b> there</p>\n")

    def test_multipart_parse_preserves_html_in_raw_payload(self):
        raw = self._build_multipart()

        parsed = ImapEmailAdapter.parse_bytes(raw, recipient_address="agent@example.org")

        self.assertEqual(parsed.sender, "dana@example.com")
        self.assertEqual(parsed.body, "Plain fallback line\n")
        self.assertEqual(
            parsed.raw_payload.get("body_html"),
            "<table><tr><td><strong>Rich</strong></td></tr></table>\n",
        )

    def test_attached_message_html_is_not_preserved_as_top_level_body(self):
        raw = self._build_with_attached_message()

        parsed = ImapEmailAdapter.parse_bytes(raw, recipient_address="agent@example.org")

        self.assertEqual(parsed.sender, "erin@example.com")
        self.assertEqual(parsed.body, "Top-level plain body\n")
        self.assertIsNone(parsed.raw_payload.get("body_html"))

    def test_attachment_collected(self):
        raw = self._build_with_attachment()
        parsed = ImapEmailAdapter.parse_bytes(raw, recipient_address="agent@example.org")
        self.assertEqual(parsed.sender, "carol@example.com")
        self.assertEqual(len(parsed.attachments), 1)
        att = parsed.attachments[0]
        self.assertTrue(hasattr(att, "name"))
        self.assertTrue(hasattr(att, "size"))
        self.assertTrue(hasattr(att, "content_type"))

    @override_settings(MAX_FILE_SIZE=5)
    def test_oversize_attachment_is_reported_in_raw_payload(self):
        raw = self._build_with_attachment()

        parsed = ImapEmailAdapter.parse_bytes(raw, recipient_address="agent@example.org")

        self.assertEqual(parsed.attachments, [])
        self.assertEqual(
            parsed.raw_payload.get("rejected_attachments"),
            [
                {
                    "filename": "hello.bin",
                    "limit_bytes": 5,
                    "reason_code": "too_large",
                    "channel": "email",
                    "size_bytes": len(b"hello-bytes"),
                }
            ],
        )
