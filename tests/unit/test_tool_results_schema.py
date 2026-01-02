import json
from datetime import datetime, timezone

from django.test import SimpleTestCase, tag

from api.agent.core import tool_results


def _schema_type_matches(schema_type: object, expected: str) -> bool:
    if isinstance(schema_type, list):
        return expected in schema_type
    return schema_type == expected


@tag("batch_tool_results")
class ToolResultSchemaTests(SimpleTestCase):
    def test_infers_schema_for_object_result(self):
        payload = {"name": "Alice", "age": 30, "active": True}

        meta, stored_json, stored_text, schema_text = tool_results._summarize_result(json.dumps(payload))

        self.assertTrue(meta["is_json"])
        self.assertEqual(meta["json_type"], "object")
        self.assertIsNotNone(stored_json)
        self.assertIsNone(stored_text)
        self.assertGreater(meta["schema_bytes"], 0)
        self.assertFalse(meta["schema_truncated"])
        self.assertIsNotNone(schema_text)

        schema = json.loads(schema_text or "{}")
        self.assertEqual(schema.get("type"), "object")
        properties = schema.get("properties", {})
        self.assertIn("name", properties)
        self.assertIn("age", properties)
        self.assertIn("active", properties)
        self.assertTrue(_schema_type_matches(properties["name"].get("type"), "string"))
        self.assertTrue(_schema_type_matches(properties["age"].get("type"), "integer") or _schema_type_matches(properties["age"].get("type"), "number"))
        self.assertTrue(_schema_type_matches(properties["active"].get("type"), "boolean"))

    def test_infers_schema_for_array_result(self):
        payload = [{"id": 1, "name": "Alpha"}, {"id": 2, "name": "Beta"}]

        meta, stored_json, stored_text, schema_text = tool_results._summarize_result(json.dumps(payload))

        self.assertTrue(meta["is_json"])
        self.assertEqual(meta["json_type"], "array")
        self.assertIsNotNone(stored_json)
        self.assertIsNone(stored_text)
        self.assertGreater(meta["schema_bytes"], 0)
        self.assertIsNotNone(schema_text)

        schema = json.loads(schema_text or "{}")
        self.assertEqual(schema.get("type"), "array")
        items = schema.get("items", {})
        self.assertIsInstance(items, dict)
        properties = items.get("properties", {})
        self.assertIn("id", properties)
        self.assertIn("name", properties)
        self.assertTrue(_schema_type_matches(properties["id"].get("type"), "integer") or _schema_type_matches(properties["id"].get("type"), "number"))
        self.assertTrue(_schema_type_matches(properties["name"].get("type"), "string"))

    def test_no_schema_for_non_json_result(self):
        meta, stored_json, stored_text, schema_text = tool_results._summarize_result("not json")

        self.assertFalse(meta["is_json"])
        self.assertEqual(meta["schema_bytes"], 0)
        self.assertFalse(meta["schema_truncated"])
        self.assertIsNone(stored_json)
        self.assertIsNotNone(stored_text)
        self.assertIsNone(schema_text)

    def test_no_schema_for_json_string_result(self):
        result_text = json.dumps("plain text")
        meta, stored_json, stored_text, schema_text = tool_results._summarize_result(result_text)

        self.assertTrue(meta["is_json"])
        self.assertEqual(meta["json_type"], "string")
        self.assertEqual(meta["schema_bytes"], 0)
        self.assertFalse(meta["schema_truncated"])
        self.assertIsNotNone(stored_json)
        self.assertIsNone(schema_text)

    def test_infers_schema_from_double_encoded_json(self):
        payload = {"id": 7, "label": "alpha"}
        result_text = json.dumps(json.dumps(payload))

        meta, stored_json, stored_text, schema_text = tool_results._summarize_result(result_text)

        self.assertTrue(meta["is_json"])
        self.assertEqual(meta["json_type"], "object")
        self.assertGreater(meta["schema_bytes"], 0)
        self.assertIsNotNone(schema_text)
        schema = json.loads(schema_text or "{}")
        self.assertEqual(schema.get("type"), "object")
        self.assertIn("id", schema.get("properties", {}))
        self.assertIn("label", schema.get("properties", {}))

    def test_no_schema_for_sqlite_envelope(self):
        result_text = json.dumps({
            "status": "ok",
            "results": [
                {
                    "message": "Query 0 returned 1 rows.",
                    "result": [{"id": 1, "name": "Alpha"}],
                }
            ],
            "db_size_mb": 0.08,
            "message": "Executed 1 queries.",
        })

        meta, stored_json, stored_text, schema_text = tool_results._summarize_result(result_text)

        self.assertTrue(meta["is_json"])
        self.assertEqual(meta["schema_bytes"], 0)
        self.assertFalse(meta["schema_truncated"])
        self.assertIsNotNone(stored_json)
        self.assertIsNone(schema_text)

    def test_prompt_info_only_includes_schema_when_available(self):
        record = tool_results.ToolCallResultRecord(
            step_id="step-1",
            tool_name="http_request",
            created_at=datetime.now(timezone.utc),
            result_text=json.dumps({"status": "ok", "count": 2}),
        )
        info = tool_results.prepare_tool_results_for_prompt(
            [record],
            recency_positions={},
        )

        prompt_info = info.get("step-1")
        self.assertIsNotNone(prompt_info)
        self.assertIsNotNone(prompt_info.schema_text)
        self.assertIn('"type":"object"', prompt_info.schema_text)

        non_json_record = tool_results.ToolCallResultRecord(
            step_id="step-2",
            tool_name="http_request",
            created_at=datetime.now(timezone.utc),
            result_text="plain text",
        )
        info = tool_results.prepare_tool_results_for_prompt(
            [non_json_record],
            recency_positions={},
        )
        prompt_info = info.get("step-2")
        self.assertIsNotNone(prompt_info)
        self.assertIsNone(prompt_info.schema_text)
