"""Tests for the result analysis module."""

import json

from django.test import SimpleTestCase, tag

from api.agent.core.result_analysis import (
    ArrayInfo,
    CsvInfo,
    EmbeddedContent,
    JsonAnalysis,
    PaginationInfo,
    ResultAnalysis,
    TextAnalysis,
    analyze_json,
    analyze_result,
    analyze_text,
    analysis_to_dict,
)


@tag("batch_result_analysis")
class JsonAnalysisTests(SimpleTestCase):
    """Tests for JSON structure analysis."""

    def test_analyzes_simple_array(self):
        data = [
            {"id": 1, "name": "Alice", "email": "alice@example.com"},
            {"id": 2, "name": "Bob", "email": "bob@example.com"},
        ]
        analysis = analyze_json(data, "test-id")

        self.assertEqual(analysis.pattern, "array")
        self.assertIsNotNone(analysis.primary_array)
        self.assertEqual(analysis.primary_array.path, "$")
        self.assertEqual(analysis.primary_array.length, 2)
        self.assertIn("id", analysis.primary_array.item_fields)
        self.assertIn("name", analysis.primary_array.item_fields)
        self.assertIn("email", analysis.primary_array.item_fields)

    def test_analyzes_nested_array_in_wrapper(self):
        data = {
            "status": "ok",
            "data": {
                "items": [
                    {"id": 1, "title": "First"},
                    {"id": 2, "title": "Second"},
                ]
            }
        }
        analysis = analyze_json(data, "test-id")

        self.assertIsNotNone(analysis.primary_array)
        self.assertIn("items", analysis.primary_array.path)
        self.assertEqual(analysis.primary_array.length, 2)
        self.assertIn("id", analysis.primary_array.item_fields)
        self.assertIn("title", analysis.primary_array.item_fields)

    def test_analyzes_content_wrapper(self):
        data = {
            "status": "ok",
            "content": [
                {"name": "Item 1"},
                {"name": "Item 2"},
            ]
        }
        analysis = analyze_json(data, "test-id")

        self.assertEqual(analysis.wrapper_path, "$.content")
        self.assertIsNotNone(analysis.primary_array)
        self.assertEqual(analysis.primary_array.length, 2)

    def test_detects_pagination_with_cursor(self):
        data = {
            "items": [{"id": 1}],
            "next_cursor": "abc123",
            "total_count": 100,
        }
        analysis = analyze_json(data, "test-id")

        self.assertIsNotNone(analysis.pagination)
        self.assertTrue(analysis.pagination.detected)
        self.assertEqual(analysis.pagination.pagination_type, "cursor")
        self.assertEqual(analysis.pagination.next_field, "$.next_cursor")
        self.assertEqual(analysis.pagination.total_field, "$.total_count")

    def test_detects_pagination_with_page(self):
        data = {
            "items": [{"id": 1}],
            "page": 1,
            "has_more": True,
            "total": 50,
        }
        analysis = analyze_json(data, "test-id")

        self.assertIsNotNone(analysis.pagination)
        self.assertTrue(analysis.pagination.detected)
        self.assertEqual(analysis.pagination.has_more_field, "$.has_more")

    def test_detects_single_object(self):
        data = {
            "id": 123,
            "name": "Single Item",
            "description": "A single object",
        }
        analysis = analyze_json(data, "test-id")

        self.assertEqual(analysis.pattern, "single_object")
        self.assertIsNone(analysis.primary_array)

    def test_detects_empty_result(self):
        data = {
            "status": "ok",
            "data": []
        }
        analysis = analyze_json(data, "test-id")

        self.assertIsNotNone(analysis.detected_patterns)
        self.assertTrue(analysis.detected_patterns.empty_result)

    def test_detects_error_response(self):
        data = {
            "status": "error",
            "error": "Something went wrong",
        }
        analysis = analyze_json(data, "test-id")

        self.assertIsNotNone(analysis.detected_patterns)
        self.assertTrue(analysis.detected_patterns.error_present)
        self.assertTrue(analysis.detected_patterns.api_response)

    def test_extracts_field_types(self):
        data = [
            {
                "id": 1,
                "email": "test@example.com",
                "created_at": "2024-01-15T10:30:00Z",
                "price": "19.99",
                "active": True,
            }
        ]
        analysis = analyze_json(data, "test-id")

        self.assertIsNotNone(analysis.primary_array)
        # Check that sample includes the item
        self.assertIsNotNone(analysis.primary_array.item_sample)

    def test_handles_nested_arrays(self):
        data = [
            {
                "id": 1,
                "name": "Order",
                "items": [
                    {"product": "Widget", "qty": 2},
                    {"product": "Gadget", "qty": 1},
                ]
            }
        ]
        analysis = analyze_json(data, "test-id")

        self.assertIsNotNone(analysis.primary_array)
        self.assertIn("items", analysis.primary_array.nested_arrays)


@tag("batch_result_analysis")
class TextAnalysisTests(SimpleTestCase):
    """Tests for text format analysis."""

    def test_detects_csv_format(self):
        text = """id,name,email,status
1,Alice,alice@example.com,active
2,Bob,bob@example.com,inactive
3,Charlie,charlie@example.com,active"""

        analysis = analyze_text(text)

        self.assertEqual(analysis.format, "csv")
        self.assertGreater(analysis.confidence, 0.8)
        self.assertIsNotNone(analysis.csv_info)
        self.assertEqual(analysis.csv_info.delimiter, ",")
        self.assertTrue(analysis.csv_info.has_header)
        self.assertIn("id", analysis.csv_info.columns)
        self.assertIn("name", analysis.csv_info.columns)
        self.assertIn("email", analysis.csv_info.columns)
        self.assertEqual(analysis.csv_info.row_count_estimate, 3)

    def test_detects_tab_delimited(self):
        text = "id\tname\temail\n1\tAlice\talice@example.com\n2\tBob\tbob@example.com"

        analysis = analyze_text(text)

        self.assertEqual(analysis.format, "csv")
        self.assertIsNotNone(analysis.csv_info)
        self.assertEqual(analysis.csv_info.delimiter, "\t")

    def test_csv_extracts_sample_rows_and_types(self):
        """CSV analysis should extract sample rows and infer column types."""
        text = """id,name,price,active
1,Widget,19.99,true
2,Gadget,29.50,false
3,Device,99.00,true"""

        analysis = analyze_text(text)

        self.assertEqual(analysis.format, "csv")
        self.assertIsNotNone(analysis.csv_info)

        # Should have sample rows (first 2-3 data rows)
        self.assertGreater(len(analysis.csv_info.sample_rows), 0)
        self.assertIn("Widget", analysis.csv_info.sample_rows[0])

        # Should have column types inferred
        self.assertEqual(len(analysis.csv_info.column_types), 4)
        self.assertEqual(analysis.csv_info.column_types[0], "int")  # id
        self.assertEqual(analysis.csv_info.column_types[1], "text")  # name
        self.assertEqual(analysis.csv_info.column_types[2], "float")  # price
        self.assertEqual(analysis.csv_info.column_types[3], "text")  # active (bool as text)

    def test_detects_markdown_format(self):
        text = """# Main Heading

Some introduction text.

## Section One

Content for section one.

- List item 1
- List item 2

## Section Two

More content here.

```python
code_block = True
```
"""

        analysis = analyze_text(text)

        self.assertEqual(analysis.format, "markdown")
        self.assertIsNotNone(analysis.doc_structure)
        self.assertGreater(len(analysis.doc_structure.sections), 0)
        self.assertTrue(analysis.doc_structure.has_code_blocks)
        self.assertTrue(analysis.doc_structure.has_lists)

    def test_detects_html_format(self):
        text = """<html>
<head><title>Test</title></head>
<body>
<h1>Main Title</h1>
<p>Some paragraph text.</p>
<h2>Subtitle</h2>
<table><tr><td>Cell</td></tr></table>
</body>
</html>"""

        analysis = analyze_text(text)

        self.assertEqual(analysis.format, "html")
        self.assertIsNotNone(analysis.doc_structure)
        self.assertGreater(len(analysis.doc_structure.sections), 0)
        self.assertTrue(analysis.doc_structure.has_tables)

    def test_detects_xml_format(self):
        text = """<?xml version="1.0" encoding="UTF-8"?>
<root>
  <item id="1">Alpha</item>
  <item id="2">Beta</item>
</root>"""

        analysis = analyze_text(text)

        self.assertEqual(analysis.format, "xml")
        self.assertIsNotNone(analysis.xml_info)
        self.assertEqual(analysis.xml_info.root_tag, "root")
        self.assertGreater(analysis.xml_info.element_count, 0)

    def test_detects_log_format(self):
        text = """2024-01-15T10:30:00Z INFO Starting application
2024-01-15T10:30:01Z DEBUG Loading configuration
2024-01-15T10:30:02Z INFO Server listening on port 8080
2024-01-15T10:30:03Z WARN High memory usage detected
2024-01-15T10:30:04Z ERROR Connection failed"""

        analysis = analyze_text(text)

        self.assertEqual(analysis.format, "log")
        self.assertIsNotNone(analysis.text_hints)
        self.assertGreater(analysis.text_hints.line_count, 0)

    def test_detects_json_lines_format(self):
        text = """{"id": 1, "name": "Alice"}
{"id": 2, "name": "Bob"}
{"id": 3, "name": "Charlie"}"""

        analysis = analyze_text(text)

        self.assertEqual(analysis.format, "json_lines")

    def test_extracts_text_hints(self):
        text = """Some text with an error message.
Contact us at support@example.com
Visit https://example.com for more info."""

        analysis = analyze_text(text)

        self.assertIsNotNone(analysis.text_hints)
        self.assertIn("error", analysis.text_hints.key_positions)
        self.assertIn("https", analysis.text_hints.key_positions)

    def test_plain_text_fallback(self):
        text = "Just some plain text without any special structure."

        analysis = analyze_text(text)

        self.assertEqual(analysis.format, "plain")
        self.assertIsNotNone(analysis.text_hints)


@tag("batch_result_analysis")
class FullAnalysisTests(SimpleTestCase):
    """Tests for the complete analyze_result function."""

    def test_analyzes_json_result(self):
        data = {
            "status": "ok",
            "content": {
                "items": [
                    {"id": 1, "name": "First"},
                    {"id": 2, "name": "Second"},
                ]
            }
        }
        result_text = json.dumps(data)

        analysis = analyze_result(result_text, "test-result-id")

        self.assertTrue(analysis.is_json)
        self.assertIsNotNone(analysis.json_analysis)
        self.assertIsNone(analysis.text_analysis)
        self.assertIsNotNone(analysis.size_strategy)
        self.assertIsNotNone(analysis.query_patterns)
        self.assertIsNotNone(analysis.compact_summary)

    def test_analyzes_text_result(self):
        text = """id,name,email
1,Alice,alice@example.com
2,Bob,bob@example.com"""

        analysis = analyze_result(text, "test-result-id")

        self.assertFalse(analysis.is_json)
        self.assertIsNone(analysis.json_analysis)
        self.assertIsNotNone(analysis.text_analysis)
        self.assertEqual(analysis.text_analysis.format, "csv")

    def test_size_strategy_small(self):
        data = {"id": 1, "name": "small"}
        result_text = json.dumps(data)

        analysis = analyze_result(result_text, "test-id")

        self.assertEqual(analysis.size_strategy.category, "small")
        self.assertEqual(analysis.size_strategy.recommendation, "direct_query")
        self.assertIsNone(analysis.size_strategy.warning)

    def test_size_strategy_large(self):
        # Create a large result (>50KB)
        items = [{"id": i, "name": f"Item {i}", "description": "x" * 100} for i in range(1000)]
        result_text = json.dumps(items)

        analysis = analyze_result(result_text, "test-id")

        self.assertIn(analysis.size_strategy.category, ["large", "huge"])
        self.assertIsNotNone(analysis.size_strategy.warning)

    def test_generates_query_patterns_for_array(self):
        data = [
            {"id": 1, "name": "First", "status": "active"},
            {"id": 2, "name": "Second", "status": "inactive"},
        ]
        result_text = json.dumps(data)

        analysis = analyze_result(result_text, "R1")

        self.assertIsNotNone(analysis.query_patterns)
        self.assertIsNotNone(analysis.query_patterns.list_all)
        self.assertIn("json_each", analysis.query_patterns.list_all)
        self.assertIn("R1", analysis.query_patterns.list_all)

    def test_generates_query_patterns_for_nested_array(self):
        data = {
            "content": {
                "items": [
                    {"id": 1, "title": "First"},
                ]
            }
        }
        result_text = json.dumps(data)

        analysis = analyze_result(result_text, "R2")

        self.assertIsNotNone(analysis.query_patterns)
        if analysis.query_patterns.list_all:
            self.assertIn("items", analysis.query_patterns.list_all)

    def test_compact_summary_includes_array_info(self):
        data = [
            {"id": 1, "name": "First"},
            {"id": 2, "name": "Second"},
        ]
        result_text = json.dumps(data)

        analysis = analyze_result(result_text, "test-id")

        self.assertIn("2 items", analysis.compact_summary)
        self.assertIn("json_each", analysis.compact_summary)
        self.assertIn("json_extract", analysis.compact_summary)

    def test_compact_summary_includes_csv_info(self):
        text = """id,name,email
1,Alice,alice@example.com
2,Bob,bob@example.com"""

        analysis = analyze_result(text, "test-id")

        self.assertIn("CSV", analysis.compact_summary)
        # When types are inferred, we use SCHEMA; otherwise COLUMNS
        self.assertTrue(
            "schema" in analysis.compact_summary.lower() or
            "columns" in analysis.compact_summary.lower()
        )


@tag("batch_result_analysis")
class AnalysisSerializationTests(SimpleTestCase):
    """Tests for analysis serialization."""

    def test_analysis_to_dict_json(self):
        data = {
            "content": [
                {"id": 1, "name": "First"},
                {"id": 2, "name": "Second"},
            ]
        }
        result_text = json.dumps(data)
        analysis = analyze_result(result_text, "test-id")

        result = analysis_to_dict(analysis)

        self.assertTrue(result["is_json"])
        self.assertIn("size", result)
        self.assertEqual(result["size"]["category"], "small")
        self.assertIn("json", result)
        self.assertIn("pattern", result["json"])

    def test_analysis_to_dict_text(self):
        text = """id,name,email
1,Alice,alice@example.com"""
        analysis = analyze_result(text, "test-id")

        result = analysis_to_dict(analysis)

        self.assertFalse(result["is_json"])
        self.assertIn("text", result)
        self.assertEqual(result["text"]["format"], "csv")
        self.assertIn("csv", result["text"])

    def test_serialized_analysis_is_json_safe(self):
        data = [{"id": 1, "unicode": "Hello \u2603 World"}]
        result_text = json.dumps(data)
        analysis = analyze_result(result_text, "test-id")

        result = analysis_to_dict(analysis)

        # Should not raise
        json_str = json.dumps(result, ensure_ascii=True)
        self.assertIsNotNone(json_str)
        # Should be parseable
        parsed = json.loads(json_str)
        self.assertEqual(parsed["is_json"], True)


@tag("batch_result_analysis")
class EdgeCaseTests(SimpleTestCase):
    """Tests for edge cases and error handling."""

    def test_handles_empty_json_array(self):
        result_text = json.dumps([])

        analysis = analyze_result(result_text, "test-id")

        self.assertTrue(analysis.is_json)
        self.assertIsNotNone(analysis.json_analysis)

    def test_handles_empty_json_object(self):
        result_text = json.dumps({})

        analysis = analyze_result(result_text, "test-id")

        self.assertTrue(analysis.is_json)
        self.assertEqual(analysis.json_analysis.pattern, "single_object")

    def test_handles_deeply_nested_json(self):
        data = {"a": {"b": {"c": {"d": {"e": {"f": [{"id": 1}]}}}}}}
        result_text = json.dumps(data)

        analysis = analyze_result(result_text, "test-id")

        self.assertTrue(analysis.is_json)
        # Should still find the array
        self.assertIsNotNone(analysis.json_analysis.primary_array)

    def test_handles_mixed_array_types(self):
        data = [
            {"id": 1, "name": "First"},
            {"id": 2},  # Missing name
            {"id": 3, "name": "Third", "extra": "field"},
        ]
        result_text = json.dumps(data)

        analysis = analyze_result(result_text, "test-id")

        self.assertIsNotNone(analysis.json_analysis.primary_array)
        self.assertIn("id", analysis.json_analysis.primary_array.item_fields)

    def test_handles_unicode_content(self):
        data = [{"name": "Hello \u2603 World", "emoji": "Test"}]
        result_text = json.dumps(data, ensure_ascii=False)

        analysis = analyze_result(result_text, "test-id")

        self.assertTrue(analysis.is_json)
        self.assertIsNotNone(analysis.compact_summary)

    def test_handles_large_array(self):
        # Should not timeout or crash
        data = [{"id": i} for i in range(10000)]
        result_text = json.dumps(data)

        analysis = analyze_result(result_text, "test-id")

        self.assertTrue(analysis.is_json)
        self.assertEqual(analysis.json_analysis.primary_array.length, 10000)

    def test_handles_binary_like_text(self):
        # Text with null bytes
        text = "normal text\x00with\x00nulls"

        analysis = analyze_result(text, "test-id")

        self.assertFalse(analysis.is_json)

    def test_handles_empty_string(self):
        analysis = analyze_result("", "test-id")

        self.assertFalse(analysis.is_json)
        self.assertEqual(analysis.size_strategy.bytes, 0)

    def test_detects_data_wrapper_pattern(self):
        """Reddit-style responses: items have {kind, data: {...actual fields...}}."""
        data = {
            "kind": "Listing",
            "data": {
                "children": [
                    {"kind": "t3", "data": {"title": "Post 1", "score": 100, "author": "user1"}},
                    {"kind": "t3", "data": {"title": "Post 2", "score": 50, "author": "user2"}},
                ]
            }
        }
        result_text = json.dumps(data)

        analysis = analyze_result(result_text, "reddit-test")

        # Should detect the primary array
        self.assertIsNotNone(analysis.json_analysis.primary_array)
        arr = analysis.json_analysis.primary_array
        self.assertEqual(arr.path, "$.data.children")
        self.assertEqual(arr.length, 2)

        # Should detect data wrapper and extract fields from inside it
        self.assertEqual(arr.item_data_key, "data")
        self.assertIn("title", arr.item_fields)
        self.assertIn("score", arr.item_fields)
        self.assertIn("author", arr.item_fields)
        # Should NOT include the wrapper keys as fields
        self.assertNotIn("kind", arr.item_fields)

        # Query patterns should use correct path
        self.assertIn("$.data.title", analysis.query_patterns.list_all)
        self.assertIn("fields in $.data", analysis.compact_summary)

    def test_detects_csv_in_json_content(self):
        """When http_request fetches CSV, content field contains CSV string."""
        # Simulate http_request response with CSV in $.content
        data = {
            "url": "https://example.com/data.csv",
            "status_code": 200,
            "content": "id,name,email,status\n1,Alice,alice@example.com,active\n2,Bob,bob@example.com,inactive\n3,Charlie,charlie@example.com,active"
        }
        result_text = json.dumps(data)

        analysis = analyze_result(result_text, "csv-test")

        # Should be detected as JSON
        self.assertTrue(analysis.is_json)
        self.assertIsNotNone(analysis.json_analysis)

        # Should detect embedded CSV content
        self.assertIsNotNone(analysis.json_analysis.embedded_content)
        emb = analysis.json_analysis.embedded_content
        self.assertEqual(emb.path, "$.content")
        self.assertEqual(emb.format, "csv")
        self.assertIsNotNone(emb.csv_info)
        self.assertIn("id", emb.csv_info.columns)
        self.assertIn("name", emb.csv_info.columns)
        self.assertIn("email", emb.csv_info.columns)
        self.assertEqual(emb.csv_info.row_count_estimate, 3)

        # Compact summary should include CSV hints
        self.assertIn("CSV DATA", analysis.compact_summary)
        self.assertIn("$.content", analysis.compact_summary)
        # When types are inferred, we use SCHEMA; otherwise COLUMNS
        self.assertTrue(
            "SCHEMA" in analysis.compact_summary or
            "COLUMNS" in analysis.compact_summary
        )
        self.assertIn("GET CSV", analysis.compact_summary)

    def test_detects_embedded_json_string(self):
        """Detect JSON embedded in string fields and expose query hints."""
        payload = {
            "status": "success",
            "result": "{\"items\": [{\"id\": 1, \"name\": \"Alpha\"}, {\"id\": 2, \"name\": \"Beta\"}]}",
        }
        analysis = analyze_result(json.dumps(payload), "json-embedded")

        self.assertTrue(analysis.is_json)
        self.assertIsNotNone(analysis.json_analysis.embedded_content)
        emb = analysis.json_analysis.embedded_content
        self.assertEqual(emb.path, "$.result")
        self.assertEqual(emb.format, "json")
        self.assertIsNotNone(emb.json_info)
        self.assertEqual(emb.json_info.primary_array_path, "$.items")
        self.assertIn("GET JSON", analysis.compact_summary)

    def test_detects_embedded_csv_in_nested_list(self):
        """Detect CSV embedded under list items with wildcard paths."""
        payload = {
            "results": [
                {"payload": "id,name\n1,Alice\n2,Bob"},
            ],
        }
        analysis = analyze_result(json.dumps(payload), "csv-nested")

        self.assertTrue(analysis.is_json)
        self.assertIsNotNone(analysis.json_analysis.embedded_content)
        emb = analysis.json_analysis.embedded_content
        self.assertEqual(emb.format, "csv")
        self.assertEqual(emb.path, "$.results[*].payload")

    def test_csv_in_json_serialization(self):
        """Verify embedded CSV info is serialized correctly."""
        data = {
            "status": "ok",
            "content": "col1,col2,col3\nvalue1,value2,value3\nvalue4,value5,value6"
        }
        result_text = json.dumps(data)
        analysis = analyze_result(result_text, "test-id")

        result = analysis_to_dict(analysis)

        self.assertIn("embedded_content", result["json"])
        ec = result["json"]["embedded_content"]
        self.assertEqual(ec["path"], "$.content")
        self.assertEqual(ec["format"], "csv")
        self.assertIn("csv", ec)
        self.assertIn("col1", ec["csv"]["columns"])

    def test_no_csv_detection_for_regular_json(self):
        """Regular JSON with string content that isn't CSV shouldn't trigger detection."""
        data = {
            "status": "ok",
            "content": "This is just some plain text content that is not CSV formatted."
        }
        result_text = json.dumps(data)

        analysis = analyze_result(result_text, "test-id")

        # Should not detect embedded CSV
        self.assertTrue(analysis.is_json)
        self.assertIsNone(analysis.json_analysis.embedded_content)
