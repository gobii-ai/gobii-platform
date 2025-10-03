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

    def test_strip_control_chars_normalizes_known_sequences(self):
        dirty = "We\x00b9re seeing 50\x1390% and DCSEU\x00B9s letter \x14 final draft."

        cleaned = strip_control_chars(dirty)

        self.assertEqual(cleaned, "We're seeing 50-90% and DCSEU's letter - final draft.")

    def test_strip_control_chars_decodes_control_hex_sequences(self):
        dirty = "Zbyn\u00011bk Roubal\u0000edk I\u00019ll and It\u00019s ready"

        cleaned = strip_control_chars(dirty)

        self.assertEqual(cleaned, "Zbyněk Roubalík I'll and It's ready")
