from __future__ import annotations

import email
from email.message import EmailMessage

from django.test import TestCase, tag

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

    def _build_with_attachment(self) -> bytes:
        m = EmailMessage()
        m["From"] = "Carol <carol@example.com>"
        m["To"] = "agent@example.org"
        m["Subject"] = "With attachment"
        m.set_content("See attachment")
        m.add_attachment(b"hello-bytes", maintype="application", subtype="octet-stream", filename="hello.bin")
        return m.as_bytes()

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

    def test_attachment_collected(self):
        raw = self._build_with_attachment()
        parsed = ImapEmailAdapter.parse_bytes(raw, recipient_address="agent@example.org")
        self.assertEqual(parsed.sender, "carol@example.com")
        self.assertEqual(len(parsed.attachments), 1)
        att = parsed.attachments[0]
        self.assertTrue(hasattr(att, "name"))
        self.assertTrue(hasattr(att, "size"))
        self.assertTrue(hasattr(att, "content_type"))
