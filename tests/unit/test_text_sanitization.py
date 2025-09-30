from django.test import TestCase, tag

from util.text_sanitizer import strip_control_chars

@tag("batch_text_sanitization")
class TextSanitizationTests(TestCase):
    def test_strip_control_chars_removes_disallowed_characters(self):
        dirty = "Hello\x00World\u0019"

        cleaned = strip_control_chars(dirty)

        self.assertEqual(cleaned, "HelloWorld'")

    def test_strip_control_chars_allows_basic_whitespace(self):
        text = "Line1\nLine2\tTabbed\rCarriage"

        cleaned = strip_control_chars(text)

        self.assertEqual(cleaned, text)

    def test_strip_control_chars_handles_non_string_input(self):
        self.assertEqual(strip_control_chars(None), "")
        self.assertEqual(strip_control_chars(123), "")
