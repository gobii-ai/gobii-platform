import sys
import types
import unittest
from django.test import tag

# Provide minimal stubs for django modules used by adapters
django = types.ModuleType("django")
http = types.ModuleType("http")
request = types.ModuleType("request")


class QueryDict(dict):
    pass


class HttpRequest:  # pragma: no cover - stub
    pass


request.QueryDict = QueryDict
http.request = request
http.HttpRequest = HttpRequest
django.http = http

sys.modules.setdefault("django", django)
sys.modules.setdefault("django.http", http)
sys.modules.setdefault("django.http.request", request)

# Stub opentelemetry.trace used by adapters
opentelemetry = types.ModuleType("opentelemetry")


class DummyTracer:
    def start_as_current_span(self, name):  # pragma: no cover - stub
        def decorator(func):
            return func

        return decorator


def get_tracer(name):  # pragma: no cover - stub
    return DummyTracer()


trace = types.ModuleType("trace")
trace.get_tracer = get_tracer
opentelemetry.trace = trace
sys.modules.setdefault("opentelemetry", opentelemetry)
sys.modules.setdefault("opentelemetry.trace", trace)

# Stub api.models to avoid Django model imports
api_models = types.ModuleType("api.models")


class CommsChannel:  # pragma: no cover - stub
    pass


api_models.CommsChannel = CommsChannel
sys.modules.setdefault("api.models", api_models)

# Import the adapters module directly from file to avoid heavy package imports
import importlib.util
from pathlib import Path

ADAPTERS_PATH = Path(__file__).resolve().parents[2] / "api" / "agent" / "comms" / "adapters.py"
spec = importlib.util.spec_from_file_location("forward_adapters", ADAPTERS_PATH)
adapters = importlib.util.module_from_spec(spec)
sys.modules.setdefault("forward_adapters", adapters)
spec.loader.exec_module(adapters)

_is_forward_like = adapters._is_forward_like
_extract_forward_sections = adapters._extract_forward_sections
_html_to_text = adapters._html_to_text
_has_forwarded_header_block = adapters._has_forwarded_header_block


@tag("batch_forward_detection")
class ForwardDetectionTests(unittest.TestCase):
    def test_is_forward_like_subject(self):
        subject = "Fwd: Meeting notes"
        self.assertTrue(_is_forward_like(subject, "", []))

    def test_is_forward_like_subject_fw(self):
        """Test 'Fw:' prefix (common in some clients)."""
        subject = "Fw: Meeting notes"
        self.assertTrue(_is_forward_like(subject, "", []))

    def test_is_forward_like_body_marker(self):
        body = "Hello\n-----Original Message-----\nFrom: a@example.com\n"
        self.assertTrue(_is_forward_like("", body, []))

    def test_is_forward_like_body_marker_underscore_line(self):
        """Test Outlook web style underscore separator."""
        body = "Check this out\n________________________________\nFrom: sender@example.com\n"
        self.assertTrue(_is_forward_like("", body, []))

    def test_is_forward_like_attachment(self):
        attachments = [{"ContentType": "message/rfc822"}]
        self.assertTrue(_is_forward_like("", "", attachments))

    def test_is_forward_like_header_block(self):
        """Test Gmail-style header order: From, Date, Subject, To."""
        body = (
            "Check this out\n"
            "From: Person <person@example.com>\n"
            "Sent: Monday, January 1, 2024 10:00 AM\n"
            "Subject: Interesting\n"
            "To: Other <other@example.com>\n"
        )
        self.assertTrue(_is_forward_like("", body, []))

    def test_is_forward_like_header_block_outlook_order(self):
        """Test Outlook-style header order: From, Sent, To, Subject."""
        body = (
            "FYI\n\n"
            "From: Boss <boss@company.com>\n"
            "Sent: Tuesday, January 2, 2024 3:00 PM\n"
            "To: Team <team@company.com>\n"
            "Subject: Q1 Planning\n"
            "\n"
            "Please review the attached.\n"
        )
        self.assertTrue(_is_forward_like("", body, []))

    def test_is_forward_like_header_block_minimal(self):
        """Test detection with only 3 headers (From, Date, To - no Subject)."""
        body = (
            "See below\n"
            "From: alice@example.com\n"
            "Date: Jan 1, 2024\n"
            "To: bob@example.com\n"
        )
        self.assertTrue(_is_forward_like("", body, []))

    def test_is_forward_like_non_forward(self):
        subject = "Re: Follow up"
        body = "Just replying to your message"
        self.assertFalse(_is_forward_like(subject, body, []))

    def test_is_forward_like_non_forward_with_from_in_body(self):
        """A single 'From:' line in body shouldn't trigger forward detection."""
        body = "I heard from: the team that things are going well."
        self.assertFalse(_is_forward_like("", body, []))

    def test_is_forward_like_reply_with_quoted_headers(self):
        """Outlook-style reply with quoted header block should NOT be a forward.

        This is a regression test: replies include quoted header blocks
        (From/Sent/To/Subject) but should still use stripped-reply logic.
        """
        subject = "Re: Q1 Planning"
        body = (
            "Sounds good, let's proceed.\n\n"
            "From: Boss <boss@company.com>\n"
            "Sent: Tuesday, January 2, 2024 3:00 PM\n"
            "To: Team <team@company.com>\n"
            "Subject: Re: Q1 Planning\n"
            "\n"
            "What do you think?\n"
        )
        self.assertFalse(_is_forward_like(subject, body, []))

    def test_is_forward_like_reply_with_explicit_forward_marker(self):
        """Even with Re: subject, explicit forward marker should trigger forward detection."""
        subject = "Re: Check this out"
        body = (
            "Here's that email I mentioned.\n\n"
            "Begin forwarded message:\n"
            "From: someone@example.com\n"
            "Subject: Original topic\n"
        )
        self.assertTrue(_is_forward_like(subject, body, []))

    def test_is_forward_like_reply_with_underscore_separator(self):
        """Outlook reply with underscore separator should NOT be a forward.

        Regression test: Outlook uses ________________________________ for both
        forwards AND replies, so it shouldn't trigger forward detection for Re: subjects.
        """
        subject = "RE: Weekly sync"
        body = (
            "Sounds good!\n\n"
            "________________________________\n"
            "From: Manager <manager@company.com>\n"
            "Sent: Monday, January 6, 2025 9:00 AM\n"
            "To: Team <team@company.com>\n"
            "Subject: RE: Weekly sync\n"
            "\n"
            "Let's meet at 2pm.\n"
        )
        self.assertFalse(_is_forward_like(subject, body, []))

    def test_is_forward_like_reply_with_original_message_marker(self):
        """Outlook reply with -----Original Message----- should NOT be a forward."""
        subject = "RE: Project update"
        body = (
            "Will do.\n\n"
            "-----Original Message-----\n"
            "From: Boss <boss@company.com>\n"
            "Sent: Tuesday, January 7, 2025 10:00 AM\n"
            "To: Employee <employee@company.com>\n"
            "Subject: RE: Project update\n"
            "\n"
            "Please send me the report.\n"
        )
        self.assertFalse(_is_forward_like(subject, body, []))

    def test_extract_forward_sections_with_marker(self):
        body = (
            "Intro line\n\n"
            "-----Original Message-----\n"
            "From: a@example.com\n"
            "To: b@example.com\n"
            "Subject: Hi\n"
        )
        preamble, forwarded = _extract_forward_sections(body)
        self.assertEqual(preamble, "Intro line")
        self.assertTrue(forwarded.startswith("-----Original Message-----"))

    def test_extract_forward_sections_without_marker(self):
        body = "Just a normal message"
        preamble, forwarded = _extract_forward_sections(body)
        self.assertEqual(preamble, body)
        self.assertEqual(forwarded, "")

    def test_html_to_text(self):
        html = "<p>Hello<br>World</p>"
        text = _html_to_text(html)
        self.assertIn("Hello", text)
        self.assertIn("World", text)
        self.assertNotIn("<", text)
        self.assertNotIn(">", text)

    def test_html_to_text_empty(self):
        self.assertEqual(_html_to_text(""), "")

    def test_extract_forward_sections_outlook_style(self):
        """Test extraction with Outlook-style header order."""
        body = (
            "Please handle this.\n\n"
            "From: Client <client@example.com>\n"
            "Sent: Wednesday, January 3, 2024 9:00 AM\n"
            "To: Support <support@company.com>\n"
            "Subject: Help needed\n"
            "\n"
            "I need assistance with my account.\n"
        )
        preamble, forwarded = _extract_forward_sections(body)
        self.assertEqual(preamble, "Please handle this.")
        self.assertIn("From: Client", forwarded)
        self.assertIn("Help needed", forwarded)

    def test_extract_forward_sections_header_block_only(self):
        """Test extraction when forward has no explicit marker, just headers."""
        body = (
            "From: sender@example.com\n"
            "Date: January 1, 2024\n"
            "Subject: Test\n"
            "To: recipient@example.com\n"
            "\n"
            "Original message content.\n"
        )
        preamble, forwarded = _extract_forward_sections(body)
        self.assertEqual(preamble, "")
        self.assertIn("From: sender@example.com", forwarded)

    def test_has_forwarded_header_block_true(self):
        """Direct test of _has_forwarded_header_block with valid block."""
        text = (
            "From: person@example.com\n"
            "Date: Jan 1, 2024\n"
            "Subject: Test\n"
            "To: other@example.com\n"
        )
        self.assertTrue(_has_forwarded_header_block(text))

    def test_has_forwarded_header_block_false_insufficient(self):
        """Only 2 headers shouldn't trigger detection."""
        text = (
            "From: person@example.com\n"
            "Subject: Test\n"
        )
        self.assertFalse(_has_forwarded_header_block(text))

    def test_has_forwarded_header_block_false_scattered(self):
        """Headers too far apart shouldn't trigger detection."""
        text = (
            "From: person@example.com\n"
            "line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\nline9\nline10\n"
            "Date: Jan 1, 2024\n"
            "line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\nline9\nline10\n"
            "Subject: Test\n"
        )
        self.assertFalse(_has_forwarded_header_block(text))
