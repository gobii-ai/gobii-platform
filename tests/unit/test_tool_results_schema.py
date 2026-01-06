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

    def test_fresh_text_result_adds_barbell_hint(self):
        long_text = (
            "Header: Intro\n"
            + ("Content line with punctuation, commas, and numbers 123. " * 400)
            + "\nFooter: End"
        )
        record = tool_results.ToolCallResultRecord(
            step_id="step-4",
            tool_name="http_request",
            created_at=datetime.now(timezone.utc),
            result_text=long_text,
        )
        info = tool_results.prepare_tool_results_for_prompt(
            [record],
            recency_positions={},
            fresh_tool_call_step_id="step-4",
        )

        prompt_info = info.get("step-4")
        self.assertIsNotNone(prompt_info)
        self.assertIn("FOCUS:", prompt_info.meta)
        self.assertIn("[...]", prompt_info.meta)

    def test_non_fresh_text_result_skips_barbell_hint(self):
        long_text = "Header\n" + ("Content " * 1200) + "\nFooter"
        record = tool_results.ToolCallResultRecord(
            step_id="step-5",
            tool_name="http_request",
            created_at=datetime.now(timezone.utc),
            result_text=long_text,
        )
        info = tool_results.prepare_tool_results_for_prompt(
            [record],
            recency_positions={},
        )

        prompt_info = info.get("step-5")
        self.assertIsNotNone(prompt_info)
        self.assertNotIn("FOCUS:", prompt_info.meta)

    def test_fresh_small_text_result_skips_barbell_hint(self):
        record = tool_results.ToolCallResultRecord(
            step_id="step-6",
            tool_name="http_request",
            created_at=datetime.now(timezone.utc),
            result_text="Small content",
        )
        info = tool_results.prepare_tool_results_for_prompt(
            [record],
            recency_positions={},
            fresh_tool_call_step_id="step-6",
        )

        prompt_info = info.get("step-6")
        self.assertIsNotNone(prompt_info)
        self.assertNotIn("FOCUS:", prompt_info.meta)

    def test_fresh_csv_text_skips_barbell_hint(self):
        csv_data = """id,name
1,Alice"""
        record = tool_results.ToolCallResultRecord(
            step_id="step-7",
            tool_name="http_request",
            created_at=datetime.now(timezone.utc),
            result_text=csv_data,
        )
        info = tool_results.prepare_tool_results_for_prompt(
            [record],
            recency_positions={},
            fresh_tool_call_step_id="step-7",
        )

        prompt_info = info.get("step-7")
        self.assertIsNotNone(prompt_info)
        self.assertIn("CSV", prompt_info.meta)
        self.assertNotIn("FOCUS:", prompt_info.meta)

    def test_fresh_non_eligible_tool_skips_barbell_hint(self):
        long_text = "Header\n" + ("Content " * 1200) + "\nFooter"
        record = tool_results.ToolCallResultRecord(
            step_id="step-8",
            tool_name="some_internal_tool",
            created_at=datetime.now(timezone.utc),
            result_text=long_text,
        )
        info = tool_results.prepare_tool_results_for_prompt(
            [record],
            recency_positions={},
            fresh_tool_call_step_id="step-8",
        )

        prompt_info = info.get("step-8")
        self.assertIsNotNone(prompt_info)
        self.assertNotIn("FOCUS:", prompt_info.meta)

    def test_fresh_json_result_skips_barbell_hint(self):
        record = tool_results.ToolCallResultRecord(
            step_id="step-9",
            tool_name="http_request",
            created_at=datetime.now(timezone.utc),
            result_text=json.dumps({"name": "Alice", "title": "Engineer"}),
        )
        info = tool_results.prepare_tool_results_for_prompt(
            [record],
            recency_positions={},
            fresh_tool_call_step_id="step-9",
        )

        prompt_info = info.get("step-9")
        self.assertIsNotNone(prompt_info)
        self.assertNotIn("FOCUS:", prompt_info.meta)
        self.assertNotIn("JSON_FOCUS:", prompt_info.meta)

    def test_fresh_large_json_adds_goldilocks_hint(self):
        payload = {
            "data": {
                "items": [
                    {"id": i, "name": f"Item {i}", "description": "x" * 200}
                    for i in range(120)
                ]
            }
        }
        record = tool_results.ToolCallResultRecord(
            step_id="step-10",
            tool_name="http_request",
            created_at=datetime.now(timezone.utc),
            result_text=json.dumps(payload),
        )
        info = tool_results.prepare_tool_results_for_prompt(
            [record],
            recency_positions={},
            fresh_tool_call_step_id="step-10",
        )

        prompt_info = info.get("step-10")
        self.assertIsNotNone(prompt_info)
        self.assertIn("JSON_FOCUS:", prompt_info.meta)

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


@tag("batch_tool_results")
class PreviewByteLimitTests(SimpleTestCase):
    """Tests for preview byte limits with large external results."""

    def test_large_result_preview_capped(self):
        """Results >= 5KB should have preview capped to 200 bytes."""
        from api.agent.core.tool_results import (
            _build_prompt_preview,
            LARGE_RESULT_THRESHOLD,
            LARGE_RESULT_PREVIEW_CAP,
        )

        large_text = "x" * 6000  # 6KB
        preview, is_inline = _build_prompt_preview(
            large_text,
            len(large_text),
            recency_position=0,
            tool_name="mcp_brightdata_scrape_as_markdown",
        )

        self.assertFalse(is_inline)
        # Preview should be capped around LARGE_RESULT_PREVIEW_CAP
        # (plus some truncation message)
        self.assertLess(len(preview), LARGE_RESULT_PREVIEW_CAP + 100)

    def test_huge_result_preview_minimal(self):
        """Results >= 15KB should have minimal preview (100 bytes)."""
        from api.agent.core.tool_results import (
            _build_prompt_preview,
            HUGE_RESULT_THRESHOLD,
            HUGE_RESULT_PREVIEW_CAP,
        )

        huge_text = "y" * 20000  # 20KB
        preview, is_inline = _build_prompt_preview(
            huge_text,
            len(huge_text),
            recency_position=0,
            tool_name="mcp_brightdata_search_engine",
        )

        self.assertFalse(is_inline)
        # Should include KB size in truncation message
        self.assertIn("KB", preview)
        self.assertIn("substr", preview)

    def test_sqlite_results_not_capped(self):
        """SQLite results should not have aggressive preview caps."""
        from api.agent.core.tool_results import _build_prompt_preview

        large_text = "z" * 20000  # 20KB
        preview, is_inline = _build_prompt_preview(
            large_text,
            len(large_text),
            recency_position=0,
            tool_name="sqlite_batch",
        )

        self.assertFalse(is_inline)
        # SQLite gets much more generous preview (16KB tier)
        self.assertGreater(len(preview), 10000)

    def test_small_result_shown_inline(self):
        """Small results should be shown fully inline."""
        from api.agent.core.tool_results import _build_prompt_preview

        small_text = "small content"
        preview, is_inline = _build_prompt_preview(
            small_text,
            len(small_text),
            recency_position=0,
            tool_name="mcp_brightdata_scrape_as_markdown",
        )

        self.assertTrue(is_inline)
        self.assertEqual(preview, small_text)

    def test_fresh_tool_call_under_threshold_shown_inline(self):
        """Fresh tool calls under 40KB should be shown fully inline with SQLite wrapper."""
        from api.agent.core.tool_results import (
            _build_prompt_preview,
            FRESH_RESULT_INLINE_THRESHOLD,
        )

        # 30KB text - under threshold
        medium_text = "x" * 30000
        preview, is_inline = _build_prompt_preview(
            medium_text,
            len(medium_text),
            recency_position=0,
            tool_name="mcp_brightdata_scrape_as_markdown",
            is_fresh_tool_call=True,
        )

        self.assertTrue(is_inline)
        # Should be wrapped as sqlite result
        self.assertIn("[Auto-inspected:", preview)
        self.assertIn("substr(result_text,1,30000)", preview)
        self.assertIn("30000 chars", preview)
        self.assertIn(medium_text, preview)

    def test_fresh_tool_call_over_threshold_truncated(self):
        """Fresh tool calls over 40KB should still be truncated."""
        from api.agent.core.tool_results import (
            _build_prompt_preview,
            FRESH_RESULT_INLINE_THRESHOLD,
        )

        # 50KB text - over threshold
        large_text = "y" * 50000
        preview, is_inline = _build_prompt_preview(
            large_text,
            len(large_text),
            recency_position=0,
            tool_name="mcp_brightdata_scrape_as_markdown",
            is_fresh_tool_call=True,
        )

        self.assertFalse(is_inline)
        self.assertLess(len(preview), len(large_text))

    def test_non_fresh_tool_call_still_truncated(self):
        """Non-fresh tool calls should follow normal truncation rules."""
        from api.agent.core.tool_results import _build_prompt_preview

        # 30KB text - under fresh threshold but not fresh
        medium_text = "z" * 30000
        preview, is_inline = _build_prompt_preview(
            medium_text,
            len(medium_text),
            recency_position=0,
            tool_name="mcp_brightdata_scrape_as_markdown",
            is_fresh_tool_call=False,
        )

        # Should be truncated since it's not fresh
        self.assertFalse(is_inline)
        self.assertLess(len(preview), len(medium_text))
