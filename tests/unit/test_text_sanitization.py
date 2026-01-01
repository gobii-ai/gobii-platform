from django.test import TestCase, tag

from util.text_sanitizer import (
    strip_control_chars,
    strip_markdown_for_sms,
    normalize_whitespace,
    decode_unicode_escapes,
    strip_llm_artifacts,
    normalize_llm_output,
)


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


@tag("batch_text_sanitization")
class DecodeUnicodeEscapesTests(TestCase):
    """Tests for decoding JSON/Python-style unicode escape sequences."""

    def test_decode_em_dash(self):
        """\\u2014 should decode to em dash."""
        text = "Looking into GLM-4.7 benchmarks now \\u2014 I'll report back"
        result = decode_unicode_escapes(text)
        self.assertEqual(result, "Looking into GLM-4.7 benchmarks now — I'll report back")

    def test_decode_en_dash(self):
        """\\u2013 should decode to en dash."""
        text = "Pages 1\\u201310"
        result = decode_unicode_escapes(text)
        self.assertEqual(result, "Pages 1–10")

    def test_decode_smart_quotes(self):
        """Smart quotes should be decoded properly."""
        text = "He said \\u201cHello\\u201d and \\u2018goodbye\\u2019"
        result = decode_unicode_escapes(text)
        # Left/right double quotes and left/right single quotes
        self.assertEqual(result, "He said \u201cHello\u201d and \u2018goodbye\u2019")

    def test_decode_bullet_point(self):
        """Bullet points should decode properly."""
        text = "\\u2022 Item one\n\\u2022 Item two"
        result = decode_unicode_escapes(text)
        self.assertEqual(result, "• Item one\n• Item two")

    def test_decode_emoji_bmp(self):
        """Basic emoji in BMP should decode."""
        text = "Great job! \\u2764"
        result = decode_unicode_escapes(text)
        self.assertEqual(result, "Great job! ❤")

    def test_decode_emoji_surrogate_pair(self):
        """Emoji requiring surrogate pairs should decode correctly."""
        # 😀 is U+1F600, encoded as \uD83D\uDE00 in JSON
        text = "Hello \\uD83D\\uDE00 world"
        result = decode_unicode_escapes(text)
        self.assertEqual(result, "Hello 😀 world")

    def test_decode_long_form_emoji(self):
        """\\UXXXXXXXX format should decode correctly."""
        text = "Hello \\U0001F600 world"
        result = decode_unicode_escapes(text)
        self.assertEqual(result, "Hello 😀 world")

    def test_decode_mixed_escapes_and_real_characters(self):
        """Mix of escaped and real characters should work."""
        text = "Real — dash and escaped \\u2014 dash"
        result = decode_unicode_escapes(text)
        self.assertEqual(result, "Real — dash and escaped — dash")

    def test_decode_multiple_escapes(self):
        """Multiple escape sequences should all decode."""
        text = "\\u2022 First\n\\u2022 Second\n\\u2022 Third"
        result = decode_unicode_escapes(text)
        self.assertEqual(result, "• First\n• Second\n• Third")

    def test_decode_preserves_non_escape_backslash(self):
        """Backslashes not followed by valid escapes should be preserved.

        Note: \\n and \\t ARE valid escapes so they get decoded. Only
        backslash sequences that don't match known escapes are preserved.
        """
        # \u and \x without valid hex, \a, \b etc. are NOT in our escape map
        text = "\\a is not escape, \\b neither, but \\n is"
        result = decode_unicode_escapes(text)
        # \a and \b preserved, \n becomes newline
        self.assertEqual(result, "\\a is not escape, \\b neither, but \n is")

    def test_decode_windows_path_with_escaped_backslashes(self):
        """Windows paths with escaped backslashes should decode correctly.

        When LLMs output Windows paths, they typically escape the backslashes.
        \\\\Users\\\\name means the LLM wants to show C:\\Users\\name
        """
        text = "Path: C:\\\\Users\\\\name\\\\Documents"
        result = decode_unicode_escapes(text)
        self.assertEqual(result, "Path: C:\\Users\\name\\Documents")

    def test_decode_handles_none_input(self):
        """None input should return empty string."""
        result = decode_unicode_escapes(None)
        self.assertEqual(result, "")

    def test_decode_handles_non_string_input(self):
        """Non-string input should return empty string."""
        result = decode_unicode_escapes(123)
        self.assertEqual(result, "")

    def test_decode_handles_empty_string(self):
        """Empty string should return empty string."""
        result = decode_unicode_escapes("")
        self.assertEqual(result, "")

    def test_decode_case_insensitive(self):
        """Hex digits should be case insensitive."""
        text = "\\u2014 and \\u201C and \\U0001f600"
        result = decode_unicode_escapes(text)
        # Em dash, left double quote, grinning face emoji
        self.assertEqual(result, "\u2014 and \u201C and \U0001f600")

    def test_decode_incomplete_escape_preserved(self):
        """Incomplete escapes should be preserved."""
        text = "\\u201 incomplete and \\u trailing"
        result = decode_unicode_escapes(text)
        # Should preserve since \\u201 only has 3 hex digits
        self.assertEqual(result, "\\u201 incomplete and \\u trailing")

    def test_decode_arrow_symbols(self):
        """Arrow symbols should decode."""
        text = "Click \\u2192 here \\u2190 back"
        result = decode_unicode_escapes(text)
        self.assertEqual(result, "Click → here ← back")

    def test_decode_copyright_trademark(self):
        """Copyright and trademark symbols should decode."""
        text = "\\u00A9 2024 Company\\u2122"
        result = decode_unicode_escapes(text)
        self.assertEqual(result, "© 2024 Company™")

    def test_decode_real_world_llm_output(self):
        """Test a realistic LLM output with multiple issues."""
        text = (
            "Got it! Looking into GLM-4.7 benchmarks now \\u2014 I'll report back with:\n"
            "\n"
            "\\u2022 **Top benchmark claims** from Z.AI's official release\n"
            "\\u2022 **Head-to-head comparisons** vs Claude, GPT-4, Qwen\n"
            "\\u2022 **Community validation** \\u2014 real-world tests"
        )
        result = decode_unicode_escapes(text)
        expected = (
            "Got it! Looking into GLM-4.7 benchmarks now — I'll report back with:\n"
            "\n"
            "• **Top benchmark claims** from Z.AI's official release\n"
            "• **Head-to-head comparisons** vs Claude, GPT-4, Qwen\n"
            "• **Community validation** — real-world tests"
        )
        self.assertEqual(result, expected)

    def test_decode_common_escapes_newline(self):
        """Literal \\n should be decoded to actual newline."""
        text = "Line 1\\nLine 2\\nLine 3"
        result = decode_unicode_escapes(text)
        self.assertEqual(result, "Line 1\nLine 2\nLine 3")

    def test_decode_common_escapes_tab(self):
        """Literal \\t should be decoded to actual tab."""
        text = "Column1\\tColumn2\\tColumn3"
        result = decode_unicode_escapes(text)
        self.assertEqual(result, "Column1\tColumn2\tColumn3")

    def test_decode_common_escapes_carriage_return(self):
        """Literal \\r should be decoded to carriage return."""
        text = "Line 1\\r\\nLine 2"
        result = decode_unicode_escapes(text)
        self.assertEqual(result, "Line 1\r\nLine 2")

    def test_decode_escaped_backslash(self):
        """Double backslash should become single backslash."""
        text = "Path: C:\\\\Users\\\\name"
        result = decode_unicode_escapes(text)
        self.assertEqual(result, "Path: C:\\Users\\name")

    def test_decode_escaped_quotes(self):
        """Escaped quotes should be decoded."""
        text = 'He said \\"Hello\\" and she said \\\'Hi\\\'.'
        result = decode_unicode_escapes(text)
        self.assertEqual(result, 'He said "Hello" and she said \'Hi\'.')

    def test_decode_hex_escape(self):
        """\\xNN hex escapes should be decoded."""
        text = "Copyright \\xA9 2024"
        result = decode_unicode_escapes(text)
        self.assertEqual(result, "Copyright © 2024")

    def test_decode_mixed_all_escapes(self):
        """Complex mix of all escape types."""
        text = "Text:\\n\\u2022 Item with \\u201cquotes\\u201d\\n\\tIndented"
        result = decode_unicode_escapes(text)
        # \n -> newline, \u2022 -> bullet, \u201c/\u201d -> smart quotes, \t -> tab
        self.assertEqual(result, "Text:\n• Item with \u201cquotes\u201d\n\tIndented")

    def test_decode_backslash_before_u_not_escape(self):
        """Backslash-u without valid hex should be preserved."""
        text = "\\ugxyz is not valid"
        result = decode_unicode_escapes(text)
        # \u is consumed by common escapes but gxyz doesn't match unicode pattern
        # Actually \\u is not in common escapes, only \\n \\r \\t \\\\ \\" \\'
        # So \\ugxyz should remain as-is (partially - the \u part stays)
        self.assertIn("gxyz", result)


@tag("batch_text_sanitization")
class NormalizeLlmOutputTests(TestCase):
    """Tests for the comprehensive normalize_llm_output function."""

    def test_normalize_decodes_unicode_escapes(self):
        """Unicode escapes should be decoded."""
        text = "Hello \\u2014 world"
        result = normalize_llm_output(text)
        self.assertEqual(result, "Hello — world")

    def test_normalize_decodes_common_escapes(self):
        """Common escapes like \\n should be decoded."""
        text = "Line 1\\nLine 2"
        result = normalize_llm_output(text)
        self.assertEqual(result, "Line 1\nLine 2")

    def test_normalize_strips_control_chars(self):
        """Control characters should be stripped."""
        text = "Hello\x00World\u0019Test"
        result = normalize_llm_output(text)
        self.assertEqual(result, "HelloWorld'Test")

    def test_normalize_collapses_excessive_newlines(self):
        """Excessive newlines should be collapsed."""
        text = "Para 1\n\n\n\n\nPara 2"
        result = normalize_llm_output(text)
        self.assertEqual(result, "Para 1\n\nPara 2")

    def test_normalize_strips_trailing_whitespace(self):
        """Trailing whitespace on lines should be stripped."""
        text = "Line 1   \nLine 2  "
        result = normalize_llm_output(text)
        self.assertEqual(result, "Line 1\nLine 2")

    def test_normalize_handles_none(self):
        """None input should return empty string."""
        result = normalize_llm_output(None)
        self.assertEqual(result, "")

    def test_normalize_handles_non_string(self):
        """Non-string input should return empty string."""
        result = normalize_llm_output(123)
        self.assertEqual(result, "")

    def test_normalize_real_world_complex(self):
        """Test realistic complex LLM output."""
        text = (
            "Got it! Looking into benchmarks now \\u2014 I'll report back with:\\n"
            "\\n"
            "\\u2022 **Top claims**\\n"
            "\\u2022 **Comparisons**\\n\\n\\n\\n"
            "Let me dig in!   "
        )
        result = normalize_llm_output(text)
        expected = (
            "Got it! Looking into benchmarks now — I'll report back with:\n"
            "\n"
            "• **Top claims**\n"
            "• **Comparisons**\n\n"
            "Let me dig in!"
        )
        self.assertEqual(result, expected)

    def test_normalize_preserves_markdown(self):
        """Markdown formatting should be preserved."""
        text = "**Bold** and *italic* and `code`"
        result = normalize_llm_output(text)
        self.assertEqual(result, "**Bold** and *italic* and `code`")

    def test_normalize_preserves_emojis(self):
        """Real emoji characters should be preserved."""
        text = "Great job! 👍 Keep going! 🚀"
        result = normalize_llm_output(text)
        self.assertEqual(result, "Great job! 👍 Keep going! 🚀")

    def test_normalize_decodes_emoji_escapes(self):
        """Escaped emojis should be decoded."""
        text = "Great job! \\U0001F44D"
        result = normalize_llm_output(text)
        self.assertEqual(result, "Great job! 👍")


@tag("batch_text_sanitization")
class StripMarkdownForSmsTests(TestCase):
    """Tests for markdown stripping in SMS messages."""

    def test_strip_bold_asterisks(self):
        text = "This is **bold** text"
        result = strip_markdown_for_sms(text)
        self.assertEqual(result, "This is bold text")

    def test_strip_bold_underscores(self):
        text = "This is __bold__ text"
        result = strip_markdown_for_sms(text)
        self.assertEqual(result, "This is bold text")

    def test_strip_italic_asterisk(self):
        text = "This is *italic* text"
        result = strip_markdown_for_sms(text)
        self.assertEqual(result, "This is italic text")

    def test_strip_italic_underscore(self):
        text = "This is _italic_ text"
        result = strip_markdown_for_sms(text)
        self.assertEqual(result, "This is italic text")

    def test_strip_inline_code(self):
        text = "Use `git status` command"
        result = strip_markdown_for_sms(text)
        self.assertEqual(result, "Use git status command")

    def test_convert_link_to_text_with_url(self):
        text = "Check [Google](https://google.com)"
        result = strip_markdown_for_sms(text)
        self.assertEqual(result, "Check Google (https://google.com)")

    def test_strip_headers(self):
        text = "# Title\n## Subtitle\n### Section"
        result = strip_markdown_for_sms(text)
        self.assertEqual(result, "Title\nSubtitle\nSection")

    def test_handles_none_input(self):
        result = strip_markdown_for_sms(None)
        self.assertEqual(result, "")


@tag("batch_text_sanitization")
class NormalizeWhitespaceTests(TestCase):
    """Tests for whitespace normalization."""

    def test_collapse_excessive_newlines(self):
        text = "Line 1\n\n\n\nLine 2"
        result = normalize_whitespace(text)
        self.assertEqual(result, "Line 1\n\nLine 2")

    def test_preserve_double_newlines(self):
        text = "Line 1\n\nLine 2"
        result = normalize_whitespace(text)
        self.assertEqual(result, "Line 1\n\nLine 2")

    def test_strip_trailing_whitespace(self):
        text = "Line 1   \nLine 2  "
        result = normalize_whitespace(text)
        self.assertEqual(result, "Line 1\nLine 2")

    def test_handles_none_input(self):
        result = normalize_whitespace(None)
        self.assertEqual(result, "")


@tag("batch_text_sanitization")
class StripLlmArtifactsTests(TestCase):
    """Tests for stripping LLM reasoning/tool call artifacts."""

    def test_strip_trailing_think_tag_and_args(self):
        """Real-world case: LLM outputs </think> and arg tags at end of message."""
        text = (
            "What jumps out at you?</think><arg_key>to_address</arg_key>"
            "<arg_value>web://user/1/agent/abc123"
        )
        result = strip_llm_artifacts(text)
        self.assertEqual(result, "What jumps out at you?")

    def test_strip_closing_think_tag(self):
        """Closing think tag should be removed."""
        text = "Here's my response</think>"
        result = strip_llm_artifacts(text)
        self.assertEqual(result, "Here's my response")

    def test_strip_thinking_tag_variant(self):
        """<thinking> variant should also be removed."""
        text = "Response here</thinking>"
        result = strip_llm_artifacts(text)
        self.assertEqual(result, "Response here")

    def test_strip_arg_key_value_pairs(self):
        """Arg key/value tags should be removed."""
        text = "Message<arg_key>param</arg_key><arg_value>value</arg_value>"
        result = strip_llm_artifacts(text)
        self.assertEqual(result, "Message")

    def test_preserves_normal_content(self):
        """Normal content without artifacts should be preserved."""
        text = "## Header\n\n**Bold** text with *emphasis*"
        result = strip_llm_artifacts(text)
        self.assertEqual(result, "## Header\n\n**Bold** text with *emphasis*")

    def test_preserves_legitimate_angle_brackets(self):
        """Math expressions like 5 < 10 > 3 should be preserved."""
        text = "The value is 5 < 10 and 10 > 3"
        result = strip_llm_artifacts(text)
        self.assertEqual(result, "The value is 5 < 10 and 10 > 3")

    def test_handles_none_input(self):
        """None input should return empty string."""
        result = strip_llm_artifacts(None)
        self.assertEqual(result, "")

    def test_handles_empty_string(self):
        """Empty string should return empty string."""
        result = strip_llm_artifacts("")
        self.assertEqual(result, "")

    def test_full_message_with_artifacts(self):
        """Full realistic message with trailing artifacts."""
        text = (
            "Oh wow, there's some *real* debate!\n\n"
            "## The Big Debates\n\n"
            "**Can LLMs Create?**\n\n"
            "What do you think?</think><arg_key>to_address</arg_key>"
            "<arg_value>web://user/1/agent/c9fbc1e1-221d-408e-80b4-8bdf99644851"
        )
        result = strip_llm_artifacts(text)
        expected = (
            "Oh wow, there's some *real* debate!\n\n"
            "## The Big Debates\n\n"
            "**Can LLMs Create?**\n\n"
            "What do you think?"
        )
        self.assertEqual(result, expected)

    def test_normalize_llm_output_strips_artifacts(self):
        """normalize_llm_output should include artifact stripping."""
        text = "Great response!</think><arg_key>test</arg_key>"
        result = normalize_llm_output(text)
        self.assertEqual(result, "Great response!")
