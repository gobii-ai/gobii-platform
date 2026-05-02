from django.test import SimpleTestCase, tag

from util.sms_encoding import (
    estimate_sms_segments,
    normalize_sms_text,
    optimize_sms_for_cost,
    sms_encoding,
)


@tag("batch_sms")
class SmsEncodingTests(SimpleTestCase):
    def test_sms_encoding_allows_gsm7_extension_table(self):
        self.assertEqual(sms_encoding("Use {code} and €"), "GSM-7")

    def test_estimate_sms_segments_counts_extension_chars_as_two_septets(self):
        self.assertEqual(estimate_sms_segments("{" * 80), 1)
        self.assertEqual(estimate_sms_segments("{" * 81), 2)

    def test_estimate_sms_segments_counts_emoji_as_utf16_code_units(self):
        self.assertEqual(estimate_sms_segments("😀" * 35), 1)
        self.assertEqual(estimate_sms_segments("😀" * 36), 2)

    def test_normalize_sms_text_replaces_common_unicode_with_gsm7_text(self):
        text = "Quick update — “done” 😊"

        normalized = normalize_sms_text(text)

        self.assertEqual(normalized, 'Quick update - "done" :)')
        self.assertEqual(sms_encoding(normalized), "GSM-7")

    def test_normalize_sms_text_replaces_laughing_emoji_with_short_gsm7_text(self):
        self.assertEqual(normalize_sms_text("Funny 😂🤣"), "Funny :'):')")

    def test_optimize_sms_for_cost_switches_ucs2_to_gsm7_even_without_segment_savings(self):
        result = optimize_sms_for_cost("Quick update — done 😊")

        self.assertTrue(result["changed"])
        self.assertEqual(result["text"], "Quick update - done :)")
        self.assertEqual(result["original_encoding"], "UCS-2")
        self.assertEqual(result["final_encoding"], "GSM-7")
        self.assertEqual(result["segments_saved"], 0)

    def test_optimize_sms_for_cost_keeps_original_when_normalization_increases_segments(self):
        text = "👍" * 18

        result = optimize_sms_for_cost(text)

        self.assertFalse(result["changed"])
        self.assertEqual(result["text"], text)
        self.assertEqual(result["original_segments"], 1)
        self.assertEqual(result["normalized_segments"], 2)

    def test_optimize_sms_for_cost_keeps_original_when_normalization_exceeds_max_length(self):
        text = "😂" * 18

        result = optimize_sms_for_cost(text, max_length=40)

        self.assertFalse(result["changed"])
        self.assertEqual(result["text"], text)
        self.assertGreater(len(normalize_sms_text(text)), 40)
