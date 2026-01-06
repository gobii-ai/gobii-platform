from types import SimpleNamespace

from django.test import SimpleTestCase, tag

from api.agent.tools.agent_variables import (
    clear_variables,
    set_agent_variable,
    substitute_variables_as_data_uris,
    substitute_variables_with_filespace,
)
from api.agent.tools.create_pdf import _coerce_markdown_images_to_html


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
