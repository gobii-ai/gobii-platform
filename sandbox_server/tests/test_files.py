import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox_server.server.files import _handle_create_pdf


class SandboxFileToolTests(unittest.TestCase):
    def test_create_pdf_decodes_unicode_escapes_before_rendering(self):
        captured = {}

        class CapturingWeasyPrintHTML:
            def __init__(self, *args, **kwargs):
                captured["html"] = kwargs["string"]

            def write_pdf(self):
                return b"%PDF-1.4 test"

        mock_weasyprint = MagicMock()
        mock_weasyprint.HTML = CapturingWeasyPrintHTML

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(sys.modules, {"weasyprint": mock_weasyprint}):
                result = _handle_create_pdf(
                    Path(tmp_dir).resolve(),
                    {
                        "html": r"<h2>\u2615 Espresso</h2><strong>\ud83d\udca1 Insight</strong>",
                        "file_path": "/exports/escaped-unicode.pdf",
                    },
                )

        self.assertEqual(result["status"], "ok")
        self.assertIn("☕ Espresso", captured["html"])
        self.assertIn("💡 Insight", captured["html"])
        self.assertNotIn(r"\u2615", captured["html"])
        self.assertNotIn(r"\ud83d\udca1", captured["html"])

    def test_create_pdf_preserves_lone_surrogate_escape(self):
        captured = {}

        class CapturingWeasyPrintHTML:
            def __init__(self, *args, **kwargs):
                captured["html"] = kwargs["string"]

            def write_pdf(self):
                return b"%PDF-1.4 test"

        mock_weasyprint = MagicMock()
        mock_weasyprint.HTML = CapturingWeasyPrintHTML

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(sys.modules, {"weasyprint": mock_weasyprint}):
                result = _handle_create_pdf(
                    Path(tmp_dir).resolve(),
                    {
                        "html": r"<p>Broken emoji \ud83d stays literal</p>",
                        "file_path": "/exports/lone-surrogate.pdf",
                    },
                )

        self.assertEqual(result["status"], "ok")
        self.assertIn(r"\ud83d", captured["html"])
        captured["html"].encode("utf-8")

    def test_create_pdf_preserves_escaped_backslash_unicode_escape(self):
        captured = {}

        class CapturingWeasyPrintHTML:
            def __init__(self, *args, **kwargs):
                captured["html"] = kwargs["string"]

            def write_pdf(self):
                return b"%PDF-1.4 test"

        mock_weasyprint = MagicMock()
        mock_weasyprint.HTML = CapturingWeasyPrintHTML

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(sys.modules, {"weasyprint": mock_weasyprint}):
                result = _handle_create_pdf(
                    Path(tmp_dir).resolve(),
                    {
                        "html": r"<p>Literal escape \\u2615</p>",
                        "file_path": "/exports/escaped-backslash.pdf",
                    },
                )

        self.assertEqual(result["status"], "ok")
        self.assertIn(r"\u2615", captured["html"])
        self.assertNotIn("☕", captured["html"])
