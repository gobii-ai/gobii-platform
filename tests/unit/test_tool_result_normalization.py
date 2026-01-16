import json

from django.test import SimpleTestCase, tag

from api.agent.core.event_processing import _normalize_tool_result_content


@tag("batch_event_processing")
class ToolResultNormalizationTests(SimpleTestCase):
    def test_normalizes_stringified_json_array(self):
        raw = json.dumps({"status": "success", "result": json.dumps([{"name": "Alice"}])})
        normalized = _normalize_tool_result_content(raw)
        parsed = json.loads(normalized)

        self.assertIsInstance(parsed["result"], list)
        self.assertEqual(parsed["result"][0]["name"], "Alice")

    def test_leaves_plain_text_unchanged(self):
        raw = "plain text response"
        normalized = _normalize_tool_result_content(raw)
        self.assertEqual(normalized, raw)

    def test_leaves_invalid_json_like_string_unchanged(self):
        raw = "{'key': 'value'}"
        normalized = _normalize_tool_result_content(raw)
        self.assertEqual(normalized, raw)

    def test_leaves_json_scalar_strings_unchanged(self):
        scalar_strings = ("123", "true", "\"a string\"")

        for raw in scalar_strings:
            with self.subTest(raw=raw):
                normalized = _normalize_tool_result_content(raw)
                self.assertEqual(normalized, raw)

    def test_respects_max_depth_for_nested_stringified_json(self):
        deep_string = json.dumps({"final": "value"})
        raw = json.dumps(
            {
                "level1": {
                    "level2": {
                        "level3": {
                            "level4": {
                                "level5": deep_string,
                            }
                        }
                    }
                }
            }
        )

        normalized = _normalize_tool_result_content(raw)
        parsed = json.loads(normalized)

        self.assertEqual(parsed["level1"]["level2"]["level3"]["level4"]["level5"], deep_string)

    def test_respects_max_bytes_for_large_string_payloads(self):
        long_payload = "x" * 500_001
        raw = json.dumps({"result": long_payload})

        normalized = _normalize_tool_result_content(raw)
        parsed = json.loads(normalized)

        self.assertEqual(parsed["result"], long_payload)

    def test_non_string_inputs_are_returned(self):
        samples = [{"result": "value"}, ["value"], None]

        for raw in samples:
            with self.subTest(raw=raw):
                normalized = _normalize_tool_result_content(raw)
                self.assertIs(normalized, raw)
