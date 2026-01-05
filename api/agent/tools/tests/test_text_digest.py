import base64

from django.test import SimpleTestCase, tag

from ..text_digest import digest


@tag("context_hints_batch")
class TextDigestTests(SimpleTestCase):
    def test_digest_classifies_prose(self):
        text = (
            "This is a simple paragraph about data quality and analysis. "
            "However, the system should still recognize this as prose. "
            "Therefore the classification should prefer prose over noise."
        )
        result = digest(text)

        self.assertEqual(result.primary_type, "prose")
        self.assertNotEqual(result.action, "skip")
        self.assertTrue(result.best_sample)

    def test_digest_classifies_html(self):
        text = (
            "<html><body>"
            "<div><h1>Title</h1></div>"
            "<p>Paragraph content with details.</p>"
            "<ul><li>Item one</li><li>Item two</li></ul>"
            "</body></html>"
        )
        result = digest(text)

        self.assertEqual(result.primary_type, "html")
        self.assertIn("type=html", result.summary_line())

    def test_digest_flags_base64(self):
        payload = base64.b64encode(b"0123456789abcdef" * 10).decode("ascii")
        result = digest(payload)

        self.assertIn("base64", result.flags)
