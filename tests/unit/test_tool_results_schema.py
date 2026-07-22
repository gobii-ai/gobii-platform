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
        # result_text is always populated for robust querying
        self.assertIsNotNone(stored_text)
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
        # result_text is always populated for robust querying
        self.assertIsNotNone(stored_text)
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
        self.assertIn("result_id=step-1", prompt_info.meta)
        self.assertIn("result_json_path=$.content", prompt_info.meta)
        self.assertTrue(prompt_info.is_inline)
        self.assertIn("First", prompt_info.preview_text)
        self.assertNotIn("QUERY:", prompt_info.meta)
        self.assertNotIn("PATH:", prompt_info.meta)

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
        self.assertTrue(prompt_info.is_inline)
        self.assertIn("alice@example.com", prompt_info.preview_text)
        self.assertNotIn("QUERY:", prompt_info.meta)

    def test_fresh_text_result_adds_barbell_hint(self):
        long_text = (
            "Header: Intro "
            + ("Content line with punctuation and numbers 123. " * 400)
            + "Footer: End"
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
        self.assertTrue(prompt_info.is_inline)
        self.assertIn("Alice", prompt_info.preview_text)
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

    def test_uuid_result_id_is_shortened(self):
        record = tool_results.ToolCallResultRecord(
            step_id="7f3a2b1c-1234-5678-9abc-def012345678",
            tool_name="http_request",
            created_at=datetime.now(timezone.utc),
            result_text=json.dumps({"name": "Alice"}),
        )
        info = tool_results.prepare_tool_results_for_prompt(
            [record],
            recency_positions={},
        )

        prompt_info = info.get(record.step_id)
        self.assertIsNotNone(prompt_info)
        self.assertIn("result_id=7f3a2b", prompt_info.meta)
        self.assertNotIn(record.step_id, prompt_info.meta)

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

    def test_scrape_as_markdown_stores_plain_markdown_in_result_text(self):
        markdown = "# Gemma 4\n\nBenchmark table"
        payload = {"status": "success", "result": markdown}

        meta, stored_json, stored_text, analysis = tool_results._summarize_result(
            json.dumps(payload), "test-id", "mcp_brightdata_scrape_as_markdown"
        )

        self.assertTrue(meta["is_json"])
        self.assertIsNotNone(stored_json)
        self.assertEqual(json.loads(stored_json)["result"], markdown)
        self.assertEqual(stored_text, markdown)
        self.assertIsNotNone(analysis)

    def test_scrape_as_markdown_preview_uses_plain_markdown_and_meta_guidance(self):
        markdown = "# Gemma 4\n\nBenchmark table"
        record = tool_results.ToolCallResultRecord(
            step_id="step-scrape",
            tool_name="mcp_brightdata_scrape_as_markdown",
            created_at=datetime.now(timezone.utc),
            result_text=json.dumps({"status": "success", "result": markdown}),
        )

        info = tool_results.prepare_tool_results_for_prompt(
            [record],
            recency_positions={},
            fresh_tool_call_step_id="step-scrape",
        )

        prompt_info = info.get("step-scrape")
        self.assertIsNotNone(prompt_info)
        self.assertIn("SCRAPE MARKDOWN:", prompt_info.meta)
        self.assertIn("first query all needed rows", prompt_info.meta)
        self.assertIn("substr(result_text,-1500)", prompt_info.meta)
        self.assertIn("facts may be at the end", prompt_info.meta)
        self.assertIn("grep_context_all", prompt_info.meta)
        self.assertIn("inspect analysis_json/result_json", prompt_info.meta)
        self.assertIn("Never read_file", prompt_info.meta)
        self.assertNotIn("CSV DATA", prompt_info.meta)
        self.assertIn("# Gemma 4", prompt_info.preview_text)
        self.assertNotIn('"status":"success"', prompt_info.preview_text)

    def test_scrape_as_markdown_meta_does_not_misclassify_comma_heavy_page_as_csv(self):
        markdown = "# Operations report\n\n" + "\n".join(
            f"Section {index}: implementation, onboarding, controls, and support context."
            for index in range(900)
        )
        record = tool_results.ToolCallResultRecord(
            step_id="step-comma-heavy-scrape",
            tool_name="mcp_brightdata_scrape_as_markdown",
            created_at=datetime.now(timezone.utc),
            result_text=json.dumps({"status": "success", "result": markdown}),
        )

        info = tool_results.prepare_tool_results_for_prompt(
            [record],
            recency_positions={},
            fresh_tool_call_step_id="step-comma-heavy-scrape",
        )

        prompt_info = info["step-comma-heavy-scrape"]
        self.assertNotIn("CSV DATA", prompt_info.meta)
        self.assertNotIn("[JSON", prompt_info.meta)
        self.assertNotIn("json_extract(result_json", prompt_info.meta)
        self.assertIn("SCRAPE MARKDOWN", prompt_info.meta)
        self.assertIn("result_text is the page text", prompt_info.meta)


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

    def test_meta_with_inline_result_suppresses_analysis_query_hints(self):
        from api.agent.core.result_analysis import analyze_result

        data = [{"id": 1, "name": "Test"}]
        analysis = analyze_result(json.dumps(data), "test-id")

        meta = {
            "bytes": 500,
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
            result_is_inline=True,
        )

        self.assertIn("result_id=test-id", result)
        self.assertIn("in_db=1", result)
        self.assertNotIn("QUERY:", result)
        self.assertNotIn("PATH:", result)
        self.assertNotIn("SAMPLE:", result)
        self.assertNotIn("JSON_DIGEST:", result)

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

    @staticmethod
    def _prepare_http_result(step_id, payload, *, recency=0, fresh=True, **kwargs):
        record = tool_results.ToolCallResultRecord(
            step_id=step_id,
            tool_name="http_request",
            created_at=datetime.now(timezone.utc),
            result_text=json.dumps(payload),
        )
        params = {"recency_positions": {step_id: recency}, **kwargs}
        if fresh:
            params["fresh_tool_call_step_id"] = step_id
        return tool_results.prepare_tool_results_for_prompt([record], **params)[step_id], record

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

        huge_text = "y" * 50000  # 50KB - must exceed HUGE_RESULT_THRESHOLD
        preview, is_inline = _build_prompt_preview(
            huge_text,
            len(huge_text),
            recency_position=0,
            tool_name="mcp_brightdata_search_engine",
        )

        self.assertFalse(is_inline)
        # Should include KB size in truncation message
        self.assertIn("KB", preview)
        self.assertIn("search or sample both ends", preview)
        self.assertNotIn("substr(col,1", preview)

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
            tool_name="send_chat_message",
        )

        self.assertTrue(is_inline)
        self.assertEqual(preview, small_text)

    def test_fresh_small_inline_http_result_includes_source_model_choice(self):
        payload = {
            "status": "ok",
            "status_code": 206,
            "content": {
                "date": "2026-05-17",
                "provider_warnings": ["Prediction-market provider returned only partial odds."],
                "items": [
                    {
                        "headline": "Central bank signals rate hold",
                        "summary": "The policy committee signaled a wait-and-see stance.",
                        "source_url": "https://news.example.test/rate-hold",
                    },
                    {
                        "headline": "Election coalition talks continue",
                        "summary": "Market odds were unavailable in this partial feed response.",
                        "source_url": "https://news.example.test/coalition-talks",
                    },
                ],
            },
        }
        info, record = self._prepare_http_result(
            "step-http", payload,
            named_model_tables={"items"},
        )

        self.assertTrue(info.is_inline)
        self.assertIn("result_id=step-http", info.meta)
        self.assertIn("parsed_with=json", info.meta)
        self.assertIn("Central bank signals rate hold", info.preview_text)
        self.assertIn("SOURCE ARRAYS result_id=step-http; stored paths", info.preview_text)
        self.assertLess(
            info.preview_text.index("SOURCE ARRAYS result_id=step-http"),
            info.preview_text.index("Central bank signals rate hold"),
        )
        for expected in (
            "$.content.items", '"content":{', "one sqlite_batch only", "every listed entity array",
            "INSERT ... SELECT/json_each", "Derive all facts, URLs, and write keys from j.value",
            "never changes __agent_config/__agent_skills", "No pre-read, refetch, blob inspection, copied literals",
        ):
            self.assertIn(expected, info.preview_text)
        self.assertEqual(info.preview_text.count("[SOURCE ARRAYS"), 1)
        self.assertNotIn("SOURCE WRITE HINT", info.preview_text)
        self.assertTrue(info.source_reconciliation_directive)
        self.assertIn(info.source_reconciliation_directive, info.preview_text)
        aliased = tool_results.prepare_tool_results_for_prompt(
            [record], recency_positions={"step-http": 0}, fresh_tool_call_step_id="step-http",
            named_model_tables={"news_items"},
        )["step-http"]
        self.assertTrue(aliased.source_reconciliation_directive)
        compact_payload = json.dumps(payload, separators=(",", ":"))
        self.assertLess(len(info.preview_text) - len(compact_payload), 1_000)
        for absent in ("QUERY:", "PATH:", "SAMPLE:", "JSON_DIGEST:", "__tool_results"):
            self.assertNotIn(absent, info.meta)

        linked = tool_results.prepare_tool_results_for_prompt(
            [record],
            recency_positions={"step-http": 0},
            fresh_tool_call_step_id="step-http",
            paired_url_rewriter=lambda text, _record: text.replace(
                "https://news.example.test/rate-hold",
                "https://news.example.test/rate-hold [link_ref: $[link:LEXACT]]",
            ),
            paired_url_step_ids={"step-http"},
        )["step-http"]
        for expected in (
            "VERIFIED LINK PRESENTATION", "anchor each token on its exact entity name",
            "<a href='token'>entity</a>", "No separate URL/link column unless requested", "owner report with 4+ items",
            "Say Not returned where a requested URL is absent", "Follow any preceding source-write directive",
            "does not change the requested audience or action",
        ):
            self.assertIn(expected, linked.preview_text)
        self.assertIn("SOURCE WRITE HINT", linked.preview_text)
        self.assertNotIn("Without a preceding SOURCE ARRAYS directive", linked.preview_text)
        self.assertNotIn("NEVER OUTREACH", linked.preview_text)
        self.assertNotIn("[SOURCE ARRAYS result_id=", linked.preview_text)

        linked_model = tool_results.prepare_tool_results_for_prompt(
            [record],
            recency_positions={"step-http": 0},
            fresh_tool_call_step_id="step-http",
            paired_url_rewriter=lambda text, _record: text.replace(
                "https://news.example.test/rate-hold",
                "https://news.example.test/rate-hold [link_ref: $[link:LEXACT]]",
            ),
            paired_url_step_ids={"step-http"},
            named_model_tables={"items"},
        )["step-http"]
        self.assertTrue(linked_model.source_reconciliation_directive)
        self.assertIn("https://news.example.test/rate-hold", linked_model.preview_text)
        self.assertNotIn("$[link:LEXACT]", linked_model.preview_text)

        recent = tool_results.prepare_tool_results_for_prompt(
            [record], recency_positions={"step-http": 1}
        )["step-http"]
        self.assertNotIn("SOURCE ARRAYS result_id=step-http", recent.preview_text)
        self.assertIsNone(recent.source_reconciliation_directive)
        self.assertIn("Central bank signals rate hold", recent.preview_text)

    def test_fresh_relational_http_result_requires_one_source_derived_model_batch(self):
        payload = {
            "status": "ok",
            "content": {
                "alerts": [{"message": "Account data refreshed"}],
                "accounts": [{"account_id": "acct-1", "name": "Acme"}],
                "workstreams": [{"workstream_id": "ws-1", "account_id": "acct-1", "status": "open"}],
            },
        }
        info, _record = self._prepare_http_result(
            "step-relational", payload,
            named_model_tables={"accounts"},
        )
        preview = info.preview_text

        self.assertIn("$.content.accounts(account_id,name)", preview)
        self.assertIn("$.content.workstreams(workstream_id,account_id,status)", preview)
        self.assertNotIn("$.content.alerts", preview)
        self.assertIn("every listed entity array", preview)
        self.assertIn("INSERT ... SELECT/json_each", preview)
        self.assertEqual(preview.count("[SOURCE ARRAYS"), 1)
        self.assertLess(preview.index("SOURCE ARRAYS"), preview.index('"workstreams"'))

        payload["content"]["notes"] = "context " * 10_000
        large, _record = self._prepare_http_result(
            "step-large-relational", payload,
            named_model_tables={"accounts"},
        )
        self.assertFalse(large.is_inline)
        self.assertIn("SOURCE ARRAYS", large.preview_text)
        self.assertTrue(large.source_reconciliation_directive)

    def test_fresh_source_without_matching_entity_array_does_not_force_import(self):
        cases = (
            ("object", {"answer": "ready"}, set(), '"answer":"ready"'),
            ("weather", {"forecasts": [{"temperature": 72}]}, {"accounts"}, '"temperature":72'),
        )
        for step, content, model_tables, expected in cases:
            with self.subTest(step=step):
                info, _record = self._prepare_http_result(
                    f"step-{step}", {"status": "ok", "content": content},
                    named_model_tables=model_tables,
                )
                self.assertIn(expected, info.preview_text)
                self.assertNotIn("SOURCE ARRAY", info.preview_text)
                self.assertIsNone(info.source_reconciliation_directive)

        scalar, _record = self._prepare_http_result(
            "step-scalar",
            {"status": "ok", "content": {"answer": "ready", "count": 4}},
        )
        self.assertNotIn("SOURCE WRITE HINT", scalar.preview_text)

    def test_fresh_source_array_without_model_gets_optional_safe_write_shape(self):
        payload = {
            "status": "ok",
            "content": {
                "prospects": [
                    {
                        "name": "Ari Bell",
                        "title": "VP Sales",
                        "profile_url": "https://example.test/ari",
                    },
                    {
                        "name": "Dee Chen",
                        "title": "CRO",
                        "profile_url": "https://example.test/dee",
                    },
                ]
            },
        }
        info, _record = self._prepare_http_result(
            "step-first-model",
            payload,
            named_model_tables=set(),
        )

        for expected in (
            "[SOURCE WRITE HINT result_id=step-first-model",
            "exact stored arrays: $.content.prospects(name,title,profile_url)",
            "If modeling/persisting this evidence",
            "INSERT ... SELECT json_extract(j.value,'$.field')",
            "FROM __tool_results AS t, json_each(t.result_json,'$.content.prospects') AS j",
            "WHERE t.tool_name='http_request'",
            "derive stable keys and fields from j.value",
            "ordinary one-off answer",
        ):
            self.assertIn(expected, info.preview_text)
        self.assertNotIn("[SOURCE ARRAYS", info.preview_text)
        self.assertNotIn(" VALUES ", info.preview_text)
        self.assertEqual(info.preview_text.count("[SOURCE WRITE HINT"), 1)
        self.assertIsNone(info.source_reconciliation_directive)

    def test_optional_source_write_hint_has_bounded_overhead_and_array_count(self):
        payload = {
            "status": "ok",
            "content": {
                f"entities_{index}": [
                    {
                        "entity_name": f"Entity {index}",
                        "profile_url": f"https://example.test/{index}",
                        "role": "Owner",
                    }
                ]
                for index in range(12)
            },
        }
        info, _record = self._prepare_http_result(
            "step-many-arrays",
            payload,
            named_model_tables=set(),
        )

        hint = info.preview_text.split("]\n", 1)[0] + "]\n"
        schema_list = hint.split("exact stored arrays: ", 1)[1].split(
            ". If modeling/persisting", 1
        )[0]
        self.assertLessEqual(len(schema_list.split("; ")), tool_results.MAX_OPTIONAL_SOURCE_ARRAYS)
        self.assertLessEqual(len(hint), tool_results.MAX_OPTIONAL_SOURCE_HINT_CHARS)
        compact_payload = json.dumps(payload, separators=(",", ":"))
        self.assertLessEqual(
            len(info.preview_text) - len(compact_payload),
            tool_results.MAX_OPTIONAL_SOURCE_HINT_CHARS,
        )

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
        # Should be wrapped with one-time view warning
        self.assertIn("[FULL RESULT (30000 chars) - ONE-TIME VIEW", preview)
        self.assertIn("later turns show a preview", preview)
        self.assertNotIn("SOURCE ARRAYS", preview)
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
        self.assertIn("search or sample both ends", preview)
        self.assertNotIn("substr(col,1,2000)", preview)

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

    def test_fresh_tool_call_step_ids_inline_multiple_records(self):
        """Every record in the fresh step set should get the one-time full inline preview."""
        from api.agent.core.tool_results import (
            FRESH_RESULT_INLINE_THRESHOLD,
            ToolCallResultRecord,
            prepare_tool_results_for_prompt,
        )

        first_text = "a" * (FRESH_RESULT_INLINE_THRESHOLD - 10000)
        second_text = "b" * (FRESH_RESULT_INLINE_THRESHOLD - 9000)
        records = [
            ToolCallResultRecord(
                step_id="step-a",
                tool_name="http_request",
                created_at=datetime.now(timezone.utc),
                result_text=first_text,
            ),
            ToolCallResultRecord(
                step_id="step-b",
                tool_name="http_request",
                created_at=datetime.now(timezone.utc),
                result_text=second_text,
            ),
        ]

        info = prepare_tool_results_for_prompt(
            records,
            recency_positions={},
            fresh_tool_call_step_ids={"step-a", "step-b"},
        )

        self.assertTrue(info["step-a"].is_inline)
        self.assertTrue(info["step-b"].is_inline)
        self.assertIn("[FULL RESULT", info["step-a"].preview_text)
        self.assertIn("[FULL RESULT", info["step-b"].preview_text)
        self.assertIn("ONE-TIME VIEW", info["step-a"].preview_text)
        self.assertIn("ONE-TIME VIEW", info["step-b"].preview_text)

    def test_non_fresh_step_id_without_recency_gets_meta_only(self):
        """Fresh step sets should not change old result preview behavior."""
        from api.agent.core.tool_results import (
            FRESH_RESULT_INLINE_THRESHOLD,
            ToolCallResultRecord,
            prepare_tool_results_for_prompt,
        )

        result_text = "z" * (FRESH_RESULT_INLINE_THRESHOLD - 10000)
        record = ToolCallResultRecord(
            step_id="old-step",
            tool_name="http_request",
            created_at=datetime.now(timezone.utc),
            result_text=result_text,
        )

        info = prepare_tool_results_for_prompt(
            [record],
            recency_positions={},
            fresh_tool_call_step_ids={"new-step"},
        )

        self.assertFalse(info["old-step"].is_inline)
        self.assertIsNone(info["old-step"].preview_text)


@tag("batch_tool_results")
class CsvAutoLoadTests(SimpleTestCase):
    """Tests for CSV auto-loading helper functions."""

    def test_sanitize_column_name_with_dot(self):
        """Dots should be replaced with underscores."""
        from api.agent.core.tool_results import _sanitize_column_name

        self.assertEqual(_sanitize_column_name("sepal.length"), "sepal_length")
        self.assertEqual(_sanitize_column_name("a.b.c"), "a_b_c")

    def test_sanitize_column_name_with_space(self):
        """Spaces should be replaced with underscores."""
        from api.agent.core.tool_results import _sanitize_column_name

        self.assertEqual(_sanitize_column_name("first name"), "first_name")
        self.assertEqual(_sanitize_column_name("user id"), "user_id")

    def test_sanitize_column_name_with_multiple_specials(self):
        """Multiple special characters should be collapsed."""
        from api.agent.core.tool_results import _sanitize_column_name

        self.assertEqual(_sanitize_column_name("col...name"), "col_name")
        self.assertEqual(_sanitize_column_name("a  b  c"), "a_b_c")
        self.assertEqual(_sanitize_column_name("user.first name"), "user_first_name")

    def test_sanitize_column_name_leading_digit(self):
        """Column names starting with digits should be prefixed."""
        from api.agent.core.tool_results import _sanitize_column_name

        self.assertEqual(_sanitize_column_name("123"), "col_123")
        self.assertEqual(_sanitize_column_name("1st_column"), "col_1st_column")

    def test_sanitize_column_name_empty(self):
        """Empty column names should have a fallback."""
        from api.agent.core.tool_results import _sanitize_column_name

        self.assertEqual(_sanitize_column_name(""), "col")
        self.assertEqual(_sanitize_column_name("..."), "col")

    def test_dedupe_column_names(self):
        """Duplicate column names should be numbered."""
        from api.agent.core.tool_results import _dedupe_column_names

        result = _dedupe_column_names(["name", "name", "name"])
        self.assertEqual(result, ["name", "name_2", "name_3"])

    def test_dedupe_column_names_mixed(self):
        """Mixed column names should only dedupe duplicates."""
        from api.agent.core.tool_results import _dedupe_column_names

        result = _dedupe_column_names(["id", "name", "id", "value", "name"])
        self.assertEqual(result, ["id", "name", "id_2", "value", "name_2"])

    def test_dedupe_column_names_unique(self):
        """Unique column names should pass through unchanged."""
        from api.agent.core.tool_results import _dedupe_column_names

        result = _dedupe_column_names(["id", "name", "value"])
        self.assertEqual(result, ["id", "name", "value"])
