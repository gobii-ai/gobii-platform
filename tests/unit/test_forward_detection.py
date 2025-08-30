import sys
import types
import unittest

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


class ForwardDetectionTests(unittest.TestCase):
    def test_is_forward_like_subject(self):
        subject = "Fwd: Meeting notes"
        self.assertTrue(_is_forward_like(subject, "", []))

    def test_is_forward_like_body_marker(self):
        body = "Hello\n-----Original Message-----\nFrom: a@example.com\n"
        self.assertTrue(_is_forward_like("", body, []))

    def test_is_forward_like_attachment(self):
        attachments = [{"ContentType": "message/rfc822"}]
        self.assertTrue(_is_forward_like("", "", attachments))

    def test_is_forward_like_header_block(self):
        body = (
            "Check this out\n"
            "From: Person <person@example.com>\n"
            "Sent: Monday, January 1, 2024 10:00 AM\n"
            "Subject: Interesting\n"
            "To: Other <other@example.com>\n"
        )
        self.assertTrue(_is_forward_like("", body, []))

    def test_is_forward_like_non_forward(self):
        subject = "Re: Follow up"
        body = "Just replying to your message"
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
