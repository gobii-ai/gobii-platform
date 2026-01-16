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
