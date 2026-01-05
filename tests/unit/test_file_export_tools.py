import base64
import sys
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase, tag, override_settings

from api.agent.tools.create_csv import execute_create_csv
from api.agent.tools.create_pdf import execute_create_pdf
from api.models import AgentFsNode, BrowserUseAgent, PersistentAgent


class _MockWeasyPrintHTML:
    """Mock WeasyPrint HTML class that returns test PDF bytes."""
    def __init__(self, *args, **kwargs):
        pass

    def write_pdf(self):
        return b"%PDF-1.4 test"


# Create a mock weasyprint module for environments without system dependencies
_mock_weasyprint = MagicMock()
_mock_weasyprint.HTML = _MockWeasyPrintHTML
_mock_weasyprint.default_url_fetcher = MagicMock()


@tag("batch_agent_filesystem")
class FileExportToolTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="exports@example.com",
            email="exports@example.com",
            password="secret",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Export Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Export Agent",
            charter="export files",
            browser_use_agent=cls.browser_agent,
        )

    def test_create_csv_writes_file(self):
        result = execute_create_csv(
            self.agent,
            {"csv_text": "col1,col2\n1,2\n", "file_path": "/exports/report.csv"},
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["path"], "/exports/report.csv")
        node = AgentFsNode.objects.get(created_by_agent=self.agent, path="/exports/report.csv")
        self.assertEqual(node.mime_type, "text/csv")
        with node.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"col1,col2\n1,2\n")

    def test_create_csv_overwrites_exports_path(self):
        first = execute_create_csv(
            self.agent,
            {"csv_text": "col1,col2\n1,2\n", "file_path": "/exports/report.csv", "overwrite": True},
        )
        second = execute_create_csv(
            self.agent,
            {"csv_text": "col1,col2\n3,4\n", "file_path": "/exports/report.csv", "overwrite": True},
        )

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "ok")
        self.assertEqual(first["path"], "/exports/report.csv")
        self.assertEqual(second["path"], "/exports/report.csv")
        # Verify only one node exists (overwritten)
        nodes = AgentFsNode.objects.filter(created_by_agent=self.agent, path="/exports/report.csv")
        self.assertEqual(nodes.count(), 1)
        with nodes.first().content.open("rb") as handle:
            self.assertEqual(handle.read(), b"col1,col2\n3,4\n")

    def test_create_csv_path_dedupes_when_overwrite_false(self):
        first = execute_create_csv(
            self.agent,
            {"csv_text": "col1,col2\n1,2\n", "file_path": "/exports/report.csv"},
        )
        second = execute_create_csv(
            self.agent,
            {"csv_text": "col1,col2\n3,4\n", "file_path": "/exports/report.csv"},
        )

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "ok")
        self.assertEqual(first["path"], "/exports/report.csv")
        self.assertEqual(second["path"], "/exports/report (2).csv")
        # Verify two distinct nodes were created
        first_node = AgentFsNode.objects.get(created_by_agent=self.agent, path="/exports/report.csv")
        second_node = AgentFsNode.objects.get(created_by_agent=self.agent, path="/exports/report (2).csv")
        self.assertNotEqual(first_node.id, second_node.id)

    def test_create_pdf_blocks_external_assets(self):
        result = execute_create_pdf(
            self.agent,
            {"html": "<img src='https://example.com/x.png'>", "file_path": "/exports/block.pdf"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("asset", result["message"].lower())

    def test_create_pdf_blocks_object_data(self):
        result = execute_create_pdf(
            self.agent,
            {"html": "<object data='https://example.com/file.pdf'></object>", "file_path": "/exports/block.pdf"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("asset", result["message"].lower())

    def test_create_pdf_blocks_meta_refresh(self):
        result = execute_create_pdf(
            self.agent,
            {"html": "<meta http-equiv='refresh' content='0; url=https://example.com'>", "file_path": "/exports/block.pdf"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("asset", result["message"].lower())

    def test_create_pdf_blocks_data_css_import(self):
        css_payload = "@import url('https://example.com/x.css');"
        css_b64 = base64.b64encode(css_payload.encode("utf-8")).decode("ascii")
        result = execute_create_pdf(
            self.agent,
            {"html": f"<link rel='stylesheet' href='data:text/css;base64,{css_b64}'>", "file_path": "/exports/block.pdf"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("asset", result["message"].lower())

    def test_create_pdf_blocks_data_svg_external(self):
        svg_payload = (
            "<svg xmlns='http://www.w3.org/2000/svg'>"
            "<image href='https://example.com/x.png' />"
            "</svg>"
        )
        svg_b64 = base64.b64encode(svg_payload.encode("utf-8")).decode("ascii")
        result = execute_create_pdf(
            self.agent,
            {"html": f"<img src='data:image/svg+xml;base64,{svg_b64}'>", "file_path": "/exports/block.pdf"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("asset", result["message"].lower())

    @override_settings(MAX_FILE_SIZE=10)
    def test_create_pdf_rejects_oversized_html(self):
        result = execute_create_pdf(
            self.agent,
            {"html": "<html><body>this is too large</body></html>", "file_path": "/exports/block.pdf"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("maximum", result["message"].lower())

    @patch.dict(sys.modules, {"weasyprint": _mock_weasyprint})
    def test_create_pdf_allows_data_srcset(self):
        result = execute_create_pdf(
            self.agent,
            {
                "html": (
                    "<img srcset='data:image/png;base64,AAAA 1x, "
                    "data:image/png;base64,BBBB 2x'>"
                ),
                "file_path": "/exports/srcset.pdf",
            },
        )

        self.assertEqual(result["status"], "ok")

    @patch.dict(sys.modules, {"weasyprint": _mock_weasyprint})
    def test_create_pdf_writes_file(self):
        result = execute_create_pdf(
            self.agent,
            {"html": "<html><body>Hello</body></html>", "file_path": "/exports/hello.pdf"},
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["path"], "/exports/hello.pdf")
        node = AgentFsNode.objects.get(created_by_agent=self.agent, path="/exports/hello.pdf")
        self.assertEqual(node.mime_type, "application/pdf")
        with node.content.open("rb") as handle:
            self.assertTrue(handle.read().startswith(b"%PDF-1.4"))

    @patch.dict(sys.modules, {"weasyprint": _mock_weasyprint})
    def test_create_pdf_path_dedupes_when_overwrite_false(self):
        first = execute_create_pdf(
            self.agent,
            {"html": "<html><body>Hello</body></html>", "file_path": "/exports/report.pdf"},
        )
        second = execute_create_pdf(
            self.agent,
            {"html": "<html><body>Updated</body></html>", "file_path": "/exports/report.pdf"},
        )

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "ok")
        self.assertEqual(first["path"], "/exports/report.pdf")
        self.assertEqual(second["path"], "/exports/report (2).pdf")
        # Verify two distinct nodes were created
        first_node = AgentFsNode.objects.get(created_by_agent=self.agent, path="/exports/report.pdf")
        second_node = AgentFsNode.objects.get(created_by_agent=self.agent, path="/exports/report (2).pdf")
        self.assertNotEqual(first_node.id, second_node.id)

    @patch.dict(sys.modules, {"weasyprint": _mock_weasyprint})
    def test_create_pdf_overwrites_exports_path(self):
        first = execute_create_pdf(
            self.agent,
            {"html": "<html><body>Hello</body></html>", "file_path": "/exports/report.pdf", "overwrite": True},
        )
        second = execute_create_pdf(
            self.agent,
            {"html": "<html><body>Updated</body></html>", "file_path": "/exports/report.pdf", "overwrite": True},
        )

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "ok")
        self.assertEqual(first["path"], "/exports/report.pdf")
        self.assertEqual(second["path"], "/exports/report.pdf")
        # Verify only one node exists (overwritten)
        nodes = AgentFsNode.objects.filter(created_by_agent=self.agent, path="/exports/report.pdf")
        self.assertEqual(nodes.count(), 1)
