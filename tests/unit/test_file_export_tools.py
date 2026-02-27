import base64
import sys
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase, tag, override_settings

from api.agent.files.filespace_service import write_bytes_to_dir
from api.agent.tools.agent_variables import clear_variables, set_agent_variable
from api.agent.tools.create_csv import execute_create_csv
from api.agent.tools.create_file import execute_create_file
from api.agent.tools.create_image import execute_create_image
from api.agent.tools.create_pdf import execute_create_pdf
from api.models import (
    AgentFsNode,
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentCompletion,
    LLMProvider,
    ImageGenerationModelEndpoint,
    ImageGenerationLLMTier,
    ImageGenerationTierEndpoint,
)


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

    def _seed_image_generation_tier(self, *, supports_image_to_image: bool = False):
        provider = LLMProvider.objects.create(
            key="img-provider",
            display_name="Image Provider",
            enabled=True,
        )
        endpoint = ImageGenerationModelEndpoint.objects.create(
            key="img-endpoint",
            provider=provider,
            enabled=True,
            litellm_model="gemini-2.5-flash-image",
            api_base="https://example.com/v1",
            supports_image_to_image=supports_image_to_image,
        )
        tier = ImageGenerationLLMTier.objects.create(order=1, description="Tier 1")
        ImageGenerationTierEndpoint.objects.create(
            tier=tier,
            endpoint=endpoint,
            weight=1.0,
        )

    def test_create_csv_writes_file(self):
        result = execute_create_csv(
            self.agent,
            {"csv_text": "col1,col2\n1,2\n", "file_path": "/exports/report.csv"},
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["file"], "$[/exports/report.csv]")
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
        self.assertEqual(first["file"], "$[/exports/report.csv]")
        self.assertEqual(second["file"], "$[/exports/report.csv]")
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
        self.assertEqual(first["file"], "$[/exports/report.csv]")
        self.assertEqual(second["file"], "$[/exports/report (2).csv]")
        # Verify two distinct nodes were created
        first_node = AgentFsNode.objects.get(created_by_agent=self.agent, path="/exports/report.csv")
        second_node = AgentFsNode.objects.get(created_by_agent=self.agent, path="/exports/report (2).csv")
        self.assertNotEqual(first_node.id, second_node.id)

    def test_create_csv_accepts_placeholder_wrapped_path(self):
        result = execute_create_csv(
            self.agent,
            {"csv_text": "col1,col2\n1,2\n", "file_path": "$[/exports/wrapped.csv]"},
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["file"], "$[/exports/wrapped.csv]")
        node = AgentFsNode.objects.get(created_by_agent=self.agent, path="/exports/wrapped.csv")
        with node.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"col1,col2\n1,2\n")

    def test_create_csv_rejects_invalid_path(self):
        result = execute_create_csv(
            self.agent,
            {"csv_text": "col1,col2\n1,2\n", "file_path": "$["},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("invalid", result["message"].lower())

    def test_create_file_writes_file(self):
        result = execute_create_file(
            self.agent,
            {"content": "hello\n", "file_path": "/exports/note.txt", "mime_type": "text/plain"},
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["file"], "$[/exports/note.txt]")
        node = AgentFsNode.objects.get(created_by_agent=self.agent, path="/exports/note.txt")
        self.assertEqual(node.mime_type, "text/plain")
        with node.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"hello\n")

    def test_create_file_infers_extension(self):
        result = execute_create_file(
            self.agent,
            {"content": "hello\n", "file_path": "/exports/note", "mime_type": "text/plain"},
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["file"], "$[/exports/note.txt]")
        node = AgentFsNode.objects.get(created_by_agent=self.agent, path="/exports/note.txt")
        self.assertEqual(node.mime_type, "text/plain")

    def test_create_file_blocks_csv_exports(self):
        result = execute_create_file(
            self.agent,
            {"content": "a,b\n1,2\n", "file_path": "/exports/report.csv", "mime_type": "text/csv"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("create_csv", result["message"])

    def test_create_file_blocks_pdf_exports(self):
        result = execute_create_file(
            self.agent,
            {"content": "<html></html>", "file_path": "/exports/report.pdf", "mime_type": "application/pdf"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("create_pdf", result["message"])

    def test_create_image_requires_configured_tier(self):
        result = execute_create_image(
            self.agent,
            {"prompt": "A minimal red circle icon", "file_path": "/exports/icon.png"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("No image generation model is configured", result["message"])

    @patch("api.agent.tools.create_image.run_completion")
    def test_create_image_writes_generated_file(self, mock_run_completion):
        self._seed_image_generation_tier()
        png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00"
        data_uri = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
        mock_run_completion.return_value = {
            "choices": [
                {
                    "message": {
                        "images": [
                            {
                                "image_url": {
                                    "url": data_uri,
                                }
                            }
                        ]
                    }
                }
            ]
        }

        result = execute_create_image(
            self.agent,
            {"prompt": "A minimal red circle icon", "file_path": "/exports/icon.png"},
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["file"], "$[/exports/icon.png]")
        self.assertIn("Generated image", result["inline"])
        node = AgentFsNode.objects.get(created_by_agent=self.agent, path="/exports/icon.png")
        self.assertEqual(node.mime_type, "image/png")
        with node.content.open("rb") as handle:
            self.assertEqual(handle.read(), png_bytes)
        completion = PersistentAgentCompletion.objects.get(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.IMAGE_GENERATION,
        )
        self.assertTrue((completion.llm_model or "").endswith("gemini-2.5-flash-image"))

    @patch("api.agent.tools.create_image.run_completion")
    def test_create_image_with_source_images_requires_supported_endpoint(self, mock_run_completion):
        self._seed_image_generation_tier(supports_image_to_image=False)
        write_result = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00",
            extension=".png",
            mime_type="image/png",
            path="/Inbox/source.png",
            overwrite=True,
        )
        self.assertEqual(write_result["status"], "ok")

        result = execute_create_image(
            self.agent,
            {
                "prompt": "Turn this into a neon version",
                "file_path": "/exports/neon.png",
                "source_images": ["/Inbox/source.png"],
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("does not support image-to-image", result["message"])
        mock_run_completion.assert_not_called()

    @patch("api.agent.tools.create_image.run_completion")
    def test_create_image_passes_source_images_for_image_to_image(self, mock_run_completion):
        self._seed_image_generation_tier(supports_image_to_image=True)
        source_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00"
        write_result = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=source_bytes,
            extension=".png",
            mime_type="image/png",
            path="/Inbox/source.png",
            overwrite=True,
        )
        self.assertEqual(write_result["status"], "ok")

        output_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x01"
        output_data_uri = "data:image/png;base64," + base64.b64encode(output_bytes).decode("ascii")
        mock_run_completion.return_value = {
            "choices": [
                {
                    "message": {
                        "images": [
                            {
                                "image_url": {
                                    "url": output_data_uri,
                                }
                            }
                        ]
                    }
                }
            ]
        }

        result = execute_create_image(
            self.agent,
            {
                "prompt": "Restyle this image in cyberpunk lighting",
                "file_path": "/exports/styled.png",
                "source_images": ["$[/Inbox/source.png]"],
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source_image_count"], 1)
        kwargs = mock_run_completion.call_args.kwargs
        message_content = kwargs["messages"][0]["content"]
        self.assertIsInstance(message_content, list)
        self.assertEqual(message_content[0]["type"], "text")
        self.assertEqual(message_content[1]["type"], "image_url")
        self.assertTrue(message_content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

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

    @patch.dict(sys.modules, {"weasyprint": _mock_weasyprint})
    def test_create_pdf_embeds_filespace_images(self):
        clear_variables()
        try:
            image_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00"
            write_result = write_bytes_to_dir(
                agent=self.agent,
                content_bytes=image_bytes,
                extension=".png",
                mime_type="image/png",
                path="/charts/logo.png",
                overwrite=True,
            )
            self.assertEqual(write_result["status"], "ok")

            set_agent_variable("/charts/logo.png", "https://example.com/logo.png")

            result = execute_create_pdf(
                self.agent,
                {"html": "<img src='$[/charts/logo.png]'>", "file_path": "/exports/logo.pdf"},
            )

            self.assertEqual(result["status"], "ok")
        finally:
            clear_variables()

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
        self.assertEqual(result["file"], "$[/exports/hello.pdf]")
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
        self.assertEqual(first["file"], "$[/exports/report.pdf]")
        self.assertEqual(second["file"], "$[/exports/report (2).pdf]")
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
        self.assertEqual(first["file"], "$[/exports/report.pdf]")
        self.assertEqual(second["file"], "$[/exports/report.pdf]")
        # Verify only one node exists (overwritten)
        nodes = AgentFsNode.objects.filter(created_by_agent=self.agent, path="/exports/report.pdf")
        self.assertEqual(nodes.count(), 1)

    @patch.dict(sys.modules, {"weasyprint": _mock_weasyprint})
    def test_create_pdf_with_cover_page_class(self):
        """Cover page class is accepted and generates PDF."""
        html = """
        <html>
        <body>
            <div class="cover-page">
                <h1>Annual Report</h1>
                <p class="subtitle">Financial Year 2024</p>
            </div>
            <div class="section">
                <h2>Executive Summary</h2>
                <p>Content here...</p>
            </div>
        </body>
        </html>
        """
        result = execute_create_pdf(
            self.agent,
            {"html": html, "file_path": "/exports/cover-test.pdf"},
        )
        self.assertEqual(result["status"], "ok")

    @patch.dict(sys.modules, {"weasyprint": _mock_weasyprint})
    def test_create_pdf_with_section_class(self):
        """Section class works for logical grouping."""
        html = """
        <html>
        <body>
            <section class="section">
                <h2>Section One</h2>
                <p>First section content.</p>
            </section>
            <section class="section">
                <h2>Section Two</h2>
                <p>Second section content.</p>
            </section>
        </body>
        </html>
        """
        result = execute_create_pdf(
            self.agent,
            {"html": html, "file_path": "/exports/section-test.pdf"},
        )
        self.assertEqual(result["status"], "ok")

    @patch.dict(sys.modules, {"weasyprint": _mock_weasyprint})
    def test_create_pdf_with_table_headers(self):
        """Tables with thead are accepted (for repeating headers)."""
        html = """
        <html>
        <body>
            <table>
                <thead>
                    <tr><th>Name</th><th>Value</th></tr>
                </thead>
                <tbody>
                    <tr><td>Item 1</td><td>100</td></tr>
                    <tr><td>Item 2</td><td>200</td></tr>
                </tbody>
            </table>
        </body>
        </html>
        """
        result = execute_create_pdf(
            self.agent,
            {"html": html, "file_path": "/exports/table-test.pdf"},
        )
        self.assertEqual(result["status"], "ok")

    @patch.dict(sys.modules, {"weasyprint": _mock_weasyprint})
    def test_create_pdf_with_doc_title(self):
        """Doc-title class for running headers."""
        html = """
        <html>
        <body>
            <h1 class="doc-title">My Document Title</h1>
            <p>Content here...</p>
        </body>
        </html>
        """
        result = execute_create_pdf(
            self.agent,
            {"html": html, "file_path": "/exports/title-test.pdf"},
        )
        self.assertEqual(result["status"], "ok")

    @patch.dict(sys.modules, {"weasyprint": _mock_weasyprint})
    def test_create_pdf_page_break_classes(self):
        """All page break utility classes work."""
        html = """
        <html>
        <body>
            <div class="no-break">
                <h2>Keep Together</h2>
                <p>This content should not be split.</p>
            </div>
            <div class="page-break">After this, new page.</div>
            <div class="page-break-before">This starts on a new page.</div>
        </body>
        </html>
        """
        result = execute_create_pdf(
            self.agent,
            {"html": html, "file_path": "/exports/break-test.pdf"},
        )
        self.assertEqual(result["status"], "ok")
