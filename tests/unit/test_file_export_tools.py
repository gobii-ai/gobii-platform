from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag, override_settings

from api.agent.tools.create_csv import execute_create_csv
from api.agent.tools.create_pdf import execute_create_pdf
from api.models import AgentFsNode, BrowserUseAgent, PersistentAgent


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

    def test_create_csv_writes_exports_file(self):
        result = execute_create_csv(
            self.agent,
            {"csv_text": "col1,col2\n1,2\n", "filename": "report.csv"},
        )

        self.assertEqual(result["status"], "ok")
        node = AgentFsNode.objects.get(id=result["node_id"])
        self.assertTrue(node.path.startswith("/exports/"))
        self.assertEqual(node.mime_type, "text/csv")
        with node.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"col1,col2\n1,2\n")

    def test_create_pdf_blocks_external_assets(self):
        result = execute_create_pdf(
            self.agent,
            {"html": "<img src='https://example.com/x.png'>"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("asset", result["message"].lower())

    def test_create_pdf_blocks_object_data(self):
        result = execute_create_pdf(
            self.agent,
            {"html": "<object data='https://example.com/file.pdf'></object>"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("asset", result["message"].lower())

    def test_create_pdf_blocks_meta_refresh(self):
        result = execute_create_pdf(
            self.agent,
            {"html": "<meta http-equiv='refresh' content='0; url=https://example.com'>"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("asset", result["message"].lower())

    @override_settings(MAX_FILE_SIZE=10)
    def test_create_pdf_rejects_oversized_html(self):
        result = execute_create_pdf(
            self.agent,
            {"html": "<html><body>this is too large</body></html>"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("maximum", result["message"].lower())

    @patch("api.agent.tools.create_pdf.pdfkit.from_string", return_value=b"%PDF-1.4 test")
    def test_create_pdf_allows_data_srcset(self, mock_pdf):
        result = execute_create_pdf(
            self.agent,
            {
                "html": (
                    "<img srcset='data:image/png;base64,AAAA 1x, "
                    "data:image/png;base64,BBBB 2x'>"
                )
            },
        )

        self.assertEqual(result["status"], "ok")

    @patch("api.agent.tools.create_pdf.pdfkit.from_string", return_value=b"%PDF-1.4 test")
    def test_create_pdf_writes_exports_file(self, mock_pdf):
        result = execute_create_pdf(
            self.agent,
            {"html": "<html><body>Hello</body></html>", "filename": "hello.pdf"},
        )

        self.assertEqual(result["status"], "ok")
        node = AgentFsNode.objects.get(id=result["node_id"])
        self.assertTrue(node.path.startswith("/exports/"))
        self.assertEqual(node.mime_type, "application/pdf")
        with node.content.open("rb") as handle:
            self.assertTrue(handle.read().startswith(b"%PDF-1.4"))
