from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, tag

from api.agent.tools.agent_variables import (
    clear_variables,
    set_agent_variable,
    substitute_variables_as_data_uris,
    substitute_variables_with_filespace,
)
from api.agent.tools.create_pdf import (
    _coerce_markdown_images_to_html,
    _contains_unrenderable_escaped_html,
    _normalize_escaped_html_input,
    execute_create_pdf,
)


@tag("context_hints_batch")
class ImageEmbeddingHelperTests(SimpleTestCase):
    def setUp(self):
        clear_variables()
        self.agent = SimpleNamespace(id="agent-test")

    def tearDown(self):
        clear_variables()

    def test_placeholder_without_leading_slash_resolves_in_markdown_image(self):
        set_agent_variable("/charts/foo.svg", "https://example.com/foo.svg")
        text = "Chart: ![]($[charts/foo.svg])"
        result = substitute_variables_with_filespace(text, self.agent)

        self.assertIn("![](https://example.com/foo.svg)", result)

    def test_raw_filespace_path_resolves_in_markdown_image(self):
        set_agent_variable("/charts/foo.svg", "https://example.com/foo.svg")
        text = "Chart: ![](/charts/foo.svg)"
        result = substitute_variables_with_filespace(text, self.agent)

        self.assertIn("![](https://example.com/foo.svg)", result)

    def test_raw_filespace_path_resolves_in_html_image(self):
        set_agent_variable("/charts/foo.svg", "https://example.com/foo.svg")
        text = "<img src='/charts/foo.svg'>"
        result = substitute_variables_with_filespace(text, self.agent)

        self.assertIn("<img src='https://example.com/foo.svg'>", result)

    def test_data_uri_fallback_handles_missing_slash(self):
        set_agent_variable("/charts/foo.svg", "data:image/svg+xml;base64,abc")
        text = "<img src='$[charts/foo.svg]'>"
        result = substitute_variables_as_data_uris(text, self.agent)

        self.assertIn("data:image/svg+xml;base64,abc", result)

    def test_markdown_images_convert_to_html_for_pdf(self):
        html = "See ![Sales]($[/charts/foo.svg])"
        result = _coerce_markdown_images_to_html(html)

        self.assertIn("<img", result)
        self.assertIn("src=\"$[/charts/foo.svg]\"", result)
        self.assertIn("alt=\"Sales\"", result)

    def test_escaped_pdf_markup_is_normalized_to_raw_html(self):
        html = "&lt;h1&gt;Title&lt;/h1&gt;&lt;p&gt;Body&lt;/p&gt;"
        result = _normalize_escaped_html_input(html)

        self.assertEqual(result, "<h1>Title</h1><p>Body</p>")

    def test_literal_angle_bracket_text_is_not_normalized(self):
        html = "Use &lt; and &gt; symbols, or mention &lt;div&gt; and &lt;p&gt; in prose."
        result = _normalize_escaped_html_input(html)

        self.assertEqual(result, html)
        self.assertFalse(_contains_unrenderable_escaped_html(result))

    @patch("api.agent.tools.create_pdf.get_max_file_size", return_value=None)
    def test_double_escaped_pdf_markup_is_rejected(self, get_max_file_size_mock):
        result = execute_create_pdf(
            self.agent,
            {
                "html": "&amp;lt;h1&amp;gt;Title&amp;lt;/h1&amp;gt;",
                "file_path": "/exports/report.pdf",
            },
        )

        self.assertEqual(
            result,
            {
                "status": "error",
                "message": "HTML appears entity-escaped. Pass raw tags like <div>, not &lt;div&gt;.",
            },
        )
        get_max_file_size_mock.assert_not_called()

    @patch("api.agent.tools.create_pdf.get_max_file_size", return_value=None)
    def test_escaped_external_asset_is_blocked_after_normalization(self, get_max_file_size_mock):
        result = execute_create_pdf(
            self.agent,
            {
                "html": "&lt;img src='https://example.com/chart.png'&gt;",
                "file_path": "/exports/report.pdf",
            },
        )

        self.assertEqual(
            result,
            {
                "status": "error",
                "message": (
                    "HTML contains external or local asset references (URLs are not allowed). "
                    "To embed charts: use <img src='$[/charts/...]'> with the $[path] from create_chart's inline_html field. "
                    "The $[path] syntax is required—it gets replaced with embedded data."
                ),
            },
        )
        get_max_file_size_mock.assert_called_once_with()
