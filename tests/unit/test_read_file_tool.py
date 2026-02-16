from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.files.filespace_service import write_bytes_to_dir
from api.agent.tools.read_file import execute_read_file, get_read_file_tool
from api.models import BrowserUseAgent, PersistentAgent


@tag("batch_agent_tools")
class ReadFileToolTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(
            username="read-file@example.com",
            email="read-file@example.com",
            password="secret",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Read File Browser")
        cls.agent = PersistentAgent.objects.create(
            user=user,
            name="Read File Agent",
            charter="read files",
            browser_use_agent=browser_agent,
        )

    def _write_file(self, *, path: str, content: bytes, mime_type: str) -> None:
        result = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=content,
            path=path,
            mime_type=mime_type,
            overwrite=True,
        )
        self.assertEqual(result.get("status"), "ok")

    def test_tool_definition_exposes_response_format_and_recommends_markdown(self):
        tool = get_read_file_tool()
        properties = tool["function"]["parameters"]["properties"]

        self.assertIn("response_format", properties)
        self.assertEqual(properties["response_format"]["enum"], ["markdown", "raw_text"])
        self.assertIn("recommended", properties["response_format"]["description"].lower())
        self.assertIn("pdf", tool["function"]["description"].lower())

    @patch("api.agent.tools.read_file.MarkItDown")
    def test_default_response_format_uses_markdown_converter(self, mock_markitdown):
        self._write_file(path="/exports/note.txt", content=b"hello world\n", mime_type="text/plain")
        mock_markitdown.return_value.convert.return_value = SimpleNamespace(markdown="## Converted")

        result = execute_read_file(self.agent, {"path": "/exports/note.txt"})

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("format"), "markdown")
        self.assertEqual(result.get("markdown"), "## Converted")
        self.assertNotIn("text", result)
        mock_markitdown.assert_called_once()

    @patch("api.agent.tools.read_file.MarkItDown")
    def test_raw_text_returns_plain_text_without_markdown_converter(self, mock_markitdown):
        self._write_file(path="/exports/raw.txt", content=b"line 1\nline 2\n", mime_type="text/plain")

        result = execute_read_file(
            self.agent,
            {"path": "/exports/raw.txt", "response_format": "raw_text", "max_chars": 6},
        )

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("format"), "raw_text")
        self.assertEqual(result.get("text"), "line 1\n\n... (truncated to 6 characters)")
        self.assertNotIn("markdown", result)
        mock_markitdown.assert_not_called()

    def test_raw_text_allows_utf8_text_when_mime_is_octet_stream(self):
        self._write_file(
            path="/exports/octet.txt",
            content=b"octet-stream text\n",
            mime_type="application/octet-stream",
        )

        result = execute_read_file(
            self.agent,
            {"path": "/exports/octet.txt", "response_format": "raw_text"},
        )

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("format"), "raw_text")
        self.assertEqual(result.get("text"), "octet-stream text\n")

    def test_raw_text_allows_vendor_json_mime_type(self):
        self._write_file(
            path="/exports/vendor.json",
            content=b'{"hello":"world"}\n',
            mime_type="application/vnd.api+json",
        )

        result = execute_read_file(
            self.agent,
            {"path": "/exports/vendor.json", "response_format": "raw_text"},
        )

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("format"), "raw_text")
        self.assertEqual(result.get("text"), "{\"hello\":\"world\"}\n")

    def test_raw_text_rejects_office_vnd_mime_even_without_extension(self):
        self._write_file(
            path="/exports/word_blob",
            content=b"PK\x03\x04",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

        result = execute_read_file(
            self.agent,
            {"path": "/exports/word_blob", "response_format": "raw_text"},
        )

        self.assertEqual(result.get("status"), "error")
        self.assertIn("raw_text", result.get("message", ""))
        self.assertIn("markdown", result.get("message", ""))

    def test_raw_text_rejects_pdf_and_points_to_markdown(self):
        self._write_file(
            path="/exports/report.pdf",
            content=b"%PDF-1.4 fake",
            mime_type="application/pdf",
        )

        result = execute_read_file(
            self.agent,
            {"path": "/exports/report.pdf", "response_format": "raw_text"},
        )

        self.assertEqual(result.get("status"), "error")
        self.assertIn("raw_text", result.get("message", ""))
        self.assertIn("markdown", result.get("message", ""))

    def test_invalid_response_format_returns_error(self):
        self._write_file(path="/exports/note.txt", content=b"hello", mime_type="text/plain")

        result = execute_read_file(
            self.agent,
            {"path": "/exports/note.txt", "response_format": "binary"},
        )

        self.assertEqual(result.get("status"), "error")
        self.assertIn("invalid response_format", result.get("message", "").lower())
