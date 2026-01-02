import base64
import json
from datetime import datetime, timezone

from django.test import SimpleTestCase, tag

from api.agent.core import tool_results


@tag("batch_tool_results")
class ToolResultSchemaTests(SimpleTestCase):
    """Tests for tool result summarization with rich analysis."""

    def test_analyzes_object_result(self):
        payload = {"name": "Alice", "age": 30, "active": True}

        meta, stored_json, stored_text, analysis = tool_results._summarize_result(
            json.dumps(payload), "test-id"
        )

        self.assertTrue(meta["is_json"])
        # Pattern is now from analysis, not raw json_type
        self.assertEqual(meta["json_type"], "single_object")
        self.assertIsNotNone(stored_json)
        self.assertIsNone(stored_text)
        self.assertIsNotNone(analysis)
        self.assertTrue(analysis.is_json)

    def test_analyzes_array_result(self):
        payload = [{"id": 1, "name": "Alpha"}, {"id": 2, "name": "Beta"}]

        meta, stored_json, stored_text, analysis = tool_results._summarize_result(
            json.dumps(payload), "test-id"
        )

        self.assertTrue(meta["is_json"])
        self.assertEqual(meta["json_type"], "array")
        self.assertIsNotNone(stored_json)
        self.assertIsNone(stored_text)
        self.assertIsNotNone(analysis)
        self.assertIsNotNone(analysis.json_analysis)
        self.assertIsNotNone(analysis.json_analysis.primary_array)
        self.assertEqual(analysis.json_analysis.primary_array.length, 2)
        self.assertIn("id", analysis.json_analysis.primary_array.item_fields)
        self.assertIn("name", analysis.json_analysis.primary_array.item_fields)

    def test_no_analysis_json_for_non_json_result(self):
        meta, stored_json, stored_text, analysis = tool_results._summarize_result(
            "not json", "test-id"
        )

        self.assertFalse(meta["is_json"])
        self.assertIsNone(stored_json)
        self.assertIsNotNone(stored_text)
        self.assertIsNotNone(analysis)
        self.assertFalse(analysis.is_json)
        self.assertIsNotNone(analysis.text_analysis)

    def test_json_string_result(self):
        result_text = json.dumps("plain text")
        meta, stored_json, stored_text, analysis = tool_results._summarize_result(
            result_text, "test-id"
        )

        self.assertTrue(meta["is_json"])
        self.assertIsNotNone(stored_json)

    def test_double_encoded_json(self):
        payload = {"id": 7, "label": "alpha"}
        result_text = json.dumps(json.dumps(payload))

        meta, stored_json, stored_text, analysis = tool_results._summarize_result(
            result_text, "test-id"
        )

        self.assertTrue(meta["is_json"])
        self.assertIsNotNone(analysis)

    def test_sqlite_envelope_detection(self):
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

        meta, stored_json, stored_text, analysis = tool_results._summarize_result(
            result_text, "test-id"
        )

        self.assertTrue(meta["is_json"])
        self.assertIsNotNone(stored_json)
        # Analysis should detect API response pattern
        self.assertIsNotNone(analysis)

    def test_prompt_info_includes_analysis_in_meta(self):
        record = tool_results.ToolCallResultRecord(
            step_id="step-1",
            tool_name="http_request",
            created_at=datetime.now(timezone.utc),
            result_text=json.dumps({
                "content": [
                    {"id": 1, "name": "First"},
                    {"id": 2, "name": "Second"},
                ]
            }),
        )
        info = tool_results.prepare_tool_results_for_prompt(
            [record],
            recency_positions={},
        )

        prompt_info = info.get("step-1")
        self.assertIsNotNone(prompt_info)
        # Meta should contain rich analysis info
        self.assertIn("result_id=step-1", prompt_info.meta)
        # Should have query pattern with path
        self.assertIn("QUERY:", prompt_info.meta)
        self.assertIn("PATH:", prompt_info.meta)
        self.assertIn("items", prompt_info.meta.lower())

    def test_prompt_info_for_text_result(self):
        csv_data = """id,name,email
1,Alice,alice@example.com
2,Bob,bob@example.com"""

        record = tool_results.ToolCallResultRecord(
            step_id="step-2",
            tool_name="http_request",
            created_at=datetime.now(timezone.utc),
            result_text=csv_data,
        )
        info = tool_results.prepare_tool_results_for_prompt(
            [record],
            recency_positions={},
        )

        prompt_info = info.get("step-2")
        self.assertIsNotNone(prompt_info)
        # Should have text analysis hints
        self.assertIn("CSV", prompt_info.meta)

    def test_non_eligible_tool_gets_basic_meta(self):
        """Tools not in SCHEMA_ELIGIBLE_TOOL_PREFIXES get basic meta only."""
        record = tool_results.ToolCallResultRecord(
            step_id="step-3",
            tool_name="some_internal_tool",
            created_at=datetime.now(timezone.utc),
            result_text=json.dumps({"data": [1, 2, 3]}),
        )
        info = tool_results.prepare_tool_results_for_prompt(
            [record],
            recency_positions={},
        )

        prompt_info = info.get("step-3")
        self.assertIsNotNone(prompt_info)
        self.assertIn("result_id=step-3", prompt_info.meta)
        # Should not have rich analysis for non-eligible tools
        # The compact summary is only added for eligible tools

    def test_extracts_top_keys_from_array_items(self):
        payload = [
            {"user_id": 1, "username": "alice", "email": "a@b.com"},
            {"user_id": 2, "username": "bob", "email": "b@c.com"},
        ]

        meta, _, _, analysis = tool_results._summarize_result(
            json.dumps(payload), "test-id"
        )

        # top_keys should come from array item fields
        self.assertIn("user_id", meta["top_keys"])
        self.assertIn("username", meta["top_keys"])
        self.assertIn("email", meta["top_keys"])

    def test_json5_is_normalized_for_storage(self):
        result_text = "{'id': 1,}"

        meta, stored_json, stored_text, analysis = tool_results._summarize_result(
            result_text, "test-id"
        )

        self.assertTrue(meta["is_json"])
        self.assertIsNotNone(stored_json)
        parsed = json.loads(stored_json)
        self.assertEqual(parsed["id"], 1)
        self.assertIsNotNone(analysis.parse_info)
        self.assertEqual(analysis.parse_info.mode, "json5")

    def test_base64_csv_stores_decoded_text(self):
        csv_text = "id,name\n1,Alice\n2,Bob"
        encoded = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")
        result_text = f"data:text/csv;base64,{encoded}"

        meta, stored_json, stored_text, analysis = tool_results._summarize_result(
            result_text, "test-id"
        )

        self.assertFalse(meta["is_json"])
        self.assertIsNone(stored_json)
        self.assertIsNotNone(stored_text)
        self.assertIn("id,name", stored_text)
        self.assertIsNotNone(analysis.decode_info)
        self.assertIn("base64", analysis.decode_info.steps)


@tag("batch_tool_results")
class MetaTextFormattingTests(SimpleTestCase):
    """Tests for the _format_meta_text function."""

    def test_basic_meta_format(self):
        meta = {
            "bytes": 1000,
            "line_count": 10,
            "is_json": True,
            "json_type": "array",
            "top_keys": "id,name",
            "is_binary": False,
            "has_images": False,
            "has_base64": False,
            "is_truncated": False,
            "truncated_bytes": 0,
        }

        result = tool_results._format_meta_text(
            "test-id",
            meta,
            analysis=None,
            stored_in_db=True,
        )

        self.assertIn("result_id=test-id", result)
        self.assertIn("in_db=1", result)
        self.assertIn("bytes=1000", result)

    def test_meta_with_analysis(self):
        from api.agent.core.result_analysis import analyze_result

        data = [{"id": 1, "name": "Test"}]
        analysis = analyze_result(json.dumps(data), "test-id")

        meta = {
            "bytes": 50000,  # Large enough to show analysis
            "line_count": 1,
            "is_json": True,
            "json_type": "array",
            "top_keys": "id,name",
            "is_binary": False,
            "has_images": False,
            "has_base64": False,
            "is_truncated": False,
            "truncated_bytes": 0,
        }

        result = tool_results._format_meta_text(
            "test-id",
            meta,
            analysis=analysis,
            stored_in_db=True,
        )

        # Should include compact summary with query pattern
        self.assertIn("QUERY:", result)
        self.assertIn("json_each", result)
        self.assertIn("PATH:", result)

    def test_meta_fallback_without_analysis(self):
        meta = {
            "bytes": 50000,  # Large enough to trigger hints
            "line_count": 100,
            "is_json": True,
            "json_type": "array",
            "top_keys": "id,name,email",
            "is_binary": False,
            "has_images": False,
            "has_base64": False,
            "is_truncated": False,
            "truncated_bytes": 0,
        }

        result = tool_results._format_meta_text(
            "test-id",
            meta,
            analysis=None,
            stored_in_db=True,
        )

        # Should have fallback hints
        self.assertIn("json_extract", result)
        self.assertIn("test-id", result)

    def test_meta_for_small_result_no_hints(self):
        meta = {
            "bytes": 100,  # Small result
            "line_count": 1,
            "is_json": True,
            "json_type": "object",
            "top_keys": "id",
            "is_binary": False,
            "has_images": False,
            "has_base64": False,
            "is_truncated": False,
            "truncated_bytes": 0,
        }

        result = tool_results._format_meta_text(
            "test-id",
            meta,
            analysis=None,
            stored_in_db=True,
        )

        # Small results don't need query hints
        self.assertIn("result_id=test-id", result)
        self.assertNotIn("json_extract", result)

    def test_meta_includes_decode_and_parse_info(self):
        meta = {
            "bytes": 1000,
            "line_count": 10,
            "is_json": True,
            "json_type": "array",
            "top_keys": "id,name",
            "is_binary": False,
            "has_images": False,
            "has_base64": False,
            "is_truncated": False,
            "truncated_bytes": 0,
            "decoded_from": "base64+gzip",
            "decoded_encoding": "utf-8",
            "parsed_from": "jsonp",
            "parsed_with": "json5",
        }

        result = tool_results._format_meta_text(
            "test-id",
            meta,
            analysis=None,
            stored_in_db=True,
        )

        self.assertIn("decoded_from=base64+gzip", result)
        self.assertIn("decoded_encoding=utf-8", result)
        self.assertIn("parsed_from=jsonp", result)
        self.assertIn("parsed_with=json5", result)
