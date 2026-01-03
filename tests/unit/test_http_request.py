"""Unit tests for http_request tool functionality."""

import json
from io import BytesIO
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.models import BrowserUseAgent, PersistentAgent
from api.agent.tools.http_request import execute_http_request


def _make_mock_response(content: bytes, content_type: str, status_code: int = 200):
    """Create a mock requests.Response object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"Content-Type": content_type, "Content-Length": str(len(content))}

    # iter_content yields chunks
    def iter_content(chunk_size=1024):
        stream = BytesIO(content)
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            yield chunk

    resp.iter_content = iter_content
    resp.close = MagicMock()
    return resp


@tag("http_request_batch")
class HttpRequestJsonParsingTests(TestCase):
    """Tests for JSON content parsing in http_request tool."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="http-test@example.com",
            email="http-test@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="HTTP Test Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="HTTP Test Agent",
            charter="test http_request JSON parsing",
            browser_use_agent=self.browser_agent,
        )

    def tearDown(self):
        self.agent.delete()

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    def test_json_content_is_parsed_as_object(self, mock_request, mock_proxy):
        """When content-type is application/json, content should be a dict/list, not a string."""
        mock_proxy.return_value = None

        json_data = {"hits": [{"id": 1, "title": "Item 1"}, {"id": 2, "title": "Item 2"}]}
        json_bytes = json.dumps(json_data).encode("utf-8")

        mock_request.return_value = _make_mock_response(
            content=json_bytes,
            content_type="application/json",
        )

        result = execute_http_request(self.agent, {"method": "GET", "url": "https://api.example.com/data"})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["status_code"], 200)
        # The key assertion: content should be a dict, not a string
        self.assertIsInstance(result["content"], dict)
        self.assertEqual(result["content"], json_data)
        self.assertEqual(result["content"]["hits"][0]["title"], "Item 1")

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    def test_json_array_content_is_parsed(self, mock_request, mock_proxy):
        """JSON arrays should also be parsed correctly."""
        mock_proxy.return_value = None

        json_data = [{"id": 1}, {"id": 2}, {"id": 3}]
        json_bytes = json.dumps(json_data).encode("utf-8")

        mock_request.return_value = _make_mock_response(
            content=json_bytes,
            content_type="application/json; charset=utf-8",
        )

        result = execute_http_request(self.agent, {"method": "GET", "url": "https://api.example.com/items"})

        self.assertEqual(result["status"], "ok")
        self.assertIsInstance(result["content"], list)
        self.assertEqual(len(result["content"]), 3)
        self.assertEqual(result["content"][0]["id"], 1)

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    def test_headers_string_json_is_parsed(self, mock_request, mock_proxy):
        """JSON string headers should be parsed into a header dict."""
        mock_proxy.return_value = None

        mock_request.return_value = _make_mock_response(
            content=b"ok",
            content_type="text/plain",
        )

        headers = json.dumps({"User-Agent": "agent/1.0"})
        execute_http_request(
            self.agent,
            {"method": "GET", "url": "https://api.example.com/data", "headers": headers},
        )

        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["headers"]["User-Agent"], "agent/1.0")

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    def test_nested_json_is_directly_queryable(self, mock_request, mock_proxy):
        """Nested JSON structures should be directly accessible without json.loads."""
        mock_proxy.return_value = None

        # Simulating a real API response with nested data
        json_data = {
            "status": "ok",
            "data": {
                "users": [
                    {"name": "Alice", "email": "alice@example.com"},
                    {"name": "Bob", "email": "bob@example.com"},
                ],
                "total": 2,
            }
        }
        json_bytes = json.dumps(json_data).encode("utf-8")

        mock_request.return_value = _make_mock_response(
            content=json_bytes,
            content_type="application/json",
        )

        result = execute_http_request(self.agent, {"method": "GET", "url": "https://api.example.com/users"})

        # Verify deep nesting is directly accessible
        self.assertIsInstance(result["content"], dict)
        self.assertEqual(result["content"]["data"]["users"][0]["name"], "Alice")
        self.assertEqual(result["content"]["data"]["total"], 2)

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    def test_invalid_json_falls_back_to_string(self, mock_request, mock_proxy):
        """If JSON parsing fails, content should remain as string."""
        mock_proxy.return_value = None

        invalid_json = b"{'not': 'valid json'}"  # Single quotes = invalid JSON

        mock_request.return_value = _make_mock_response(
            content=invalid_json,
            content_type="application/json",
        )

        result = execute_http_request(self.agent, {"method": "GET", "url": "https://api.example.com/bad"})

        self.assertEqual(result["status"], "ok")
        # Falls back to string since JSON parsing failed
        self.assertIsInstance(result["content"], str)
        self.assertIn("not", result["content"])

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    def test_text_html_remains_string(self, mock_request, mock_proxy):
        """Non-JSON content types should remain as strings."""
        mock_proxy.return_value = None

        html_content = b"<html><body><h1>Hello</h1></body></html>"

        mock_request.return_value = _make_mock_response(
            content=html_content,
            content_type="text/html; charset=utf-8",
        )

        result = execute_http_request(self.agent, {"method": "GET", "url": "https://example.com/"})

        self.assertEqual(result["status"], "ok")
        self.assertIsInstance(result["content"], str)
        self.assertIn("<h1>Hello</h1>", result["content"])

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    def test_text_plain_with_json_content_is_parsed(self, mock_request, mock_proxy):
        """JSON content should be parsed regardless of content-type header."""
        mock_proxy.return_value = None

        json_looking_content = b'{"key": "value"}'

        mock_request.return_value = _make_mock_response(
            content=json_looking_content,
            content_type="text/plain",
        )

        result = execute_http_request(self.agent, {"method": "GET", "url": "https://example.com/file.txt"})

        self.assertEqual(result["status"], "ok")
        # Should be parsed as JSON since content starts with { regardless of content-type
        self.assertIsInstance(result["content"], dict)
        self.assertEqual(result["content"]["key"], "value")

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    def test_json_with_xssi_prefix_is_parsed(self, mock_request, mock_proxy):
        """Common XSSI prefixes should be stripped before JSON parsing."""
        mock_proxy.return_value = None

        json_bytes = b")]}',\n{\"ok\": true, \"items\": [1, 2, 3]}"

        mock_request.return_value = _make_mock_response(
            content=json_bytes,
            content_type="text/plain",
        )

        result = execute_http_request(self.agent, {"method": "GET", "url": "https://example.com/xssi"})

        self.assertEqual(result["status"], "ok")
        self.assertIsInstance(result["content"], dict)
        self.assertEqual(result["content"]["ok"], True)
        self.assertEqual(result["content"]["items"][0], 1)

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    def test_large_json_is_parsed(self, mock_request, mock_proxy):
        """Large JSON responses (under 5MB limit) should be parsed as objects."""
        mock_proxy.return_value = None

        # Create a large JSON response (~100KB, well under 5MB limit)
        large_data = {"items": [{"id": i, "data": "x" * 1000} for i in range(100)]}
        json_bytes = json.dumps(large_data).encode("utf-8")
        # Verify it's a decent size but under 5MB
        self.assertGreater(len(json_bytes), 100 * 1024)
        self.assertLess(len(json_bytes), 5 * 1024 * 1024)

        mock_request.return_value = _make_mock_response(
            content=json_bytes,
            content_type="application/json",
        )

        result = execute_http_request(self.agent, {"method": "GET", "url": "https://api.example.com/large"})

        self.assertEqual(result["status"], "ok")
        # Large JSON should be parsed as dict
        self.assertIsInstance(result["content"], dict)
        self.assertEqual(len(result["content"]["items"]), 100)

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    def test_octet_stream_csv_is_returned_as_text(self, mock_request, mock_proxy):
        """CSV data served as application/octet-stream should be returned as text."""
        mock_proxy.return_value = None

        # Simulating iris.data style CSV (no header, comma-separated)
        csv_content = b"5.1,3.5,1.4,0.2,Iris-setosa\n4.9,3.0,1.4,0.2,Iris-setosa\n"

        mock_request.return_value = _make_mock_response(
            content=csv_content,
            content_type="application/octet-stream",
        )

        result = execute_http_request(self.agent, {"method": "GET", "url": "https://archive.ics.uci.edu/ml/machine-learning-databases/iris/iris.data"})

        self.assertEqual(result["status"], "ok")
        # Should NOT be "[Binary content omitted...]"
        self.assertIsInstance(result["content"], str)
        self.assertIn("Iris-setosa", result["content"])
        self.assertNotIn("Binary content omitted", result["content"])

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    def test_actual_binary_is_omitted(self, mock_request, mock_proxy):
        """Actual binary content (images, etc) should still be omitted."""
        mock_proxy.return_value = None

        # PNG header + random binary garbage
        binary_content = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 10

        mock_request.return_value = _make_mock_response(
            content=binary_content,
            content_type="application/octet-stream",
        )

        result = execute_http_request(self.agent, {"method": "GET", "url": "https://example.com/image.png"})

        self.assertEqual(result["status"], "ok")
        # Should be omitted since it's actual binary
        self.assertIsInstance(result["content"], str)
        self.assertIn("Binary content omitted", result["content"])

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    def test_csv_content_type_is_handled(self, mock_request, mock_proxy):
        """CSV content-type should be treated as textual."""
        mock_proxy.return_value = None

        csv_content = b"name,age,city\nAlice,30,NYC\nBob,25,LA\n"

        mock_request.return_value = _make_mock_response(
            content=csv_content,
            content_type="text/csv",
        )

        result = execute_http_request(self.agent, {"method": "GET", "url": "https://example.com/data.csv"})

        self.assertEqual(result["status"], "ok")
        self.assertIsInstance(result["content"], str)
        self.assertIn("Alice", result["content"])

    @patch("api.agent.tools.http_request.select_proxy_for_persistent_agent")
    @patch("api.agent.tools.http_request.requests.request")
    def test_no_content_type_with_text_is_returned(self, mock_request, mock_proxy):
        """Missing content-type with valid text should still be returned."""
        mock_proxy.return_value = None

        text_content = b"This is plain text without a content-type header."

        mock_request.return_value = _make_mock_response(
            content=text_content,
            content_type="",  # No content type
        )

        result = execute_http_request(self.agent, {"method": "GET", "url": "https://example.com/file.txt"})

        self.assertEqual(result["status"], "ok")
        self.assertIsInstance(result["content"], str)
        self.assertIn("plain text", result["content"])
        self.assertNotIn("Binary content omitted", result["content"])
