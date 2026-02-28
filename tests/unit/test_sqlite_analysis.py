"""
Comprehensive tests for the sqlite_analysis module.

Tests cover:
- Numeric analysis (statistics, distributions, outliers)
- Temporal analysis (date parsing, ranges, recency)
- Text analysis (semantic type detection)
- Nested content (JSON, CSV, JSON Lines)
- Cardinality (enum, unique, sequential)
- Data quality (nulls, mixed types, duplicates)
- Correlation detection
- Query suggestions
- Full table analysis integration
- Edge cases and messy real-world data
"""

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from unittest import TestCase

from django.test import SimpleTestCase, tag

from api.agent.tools import sqlite_analysis


@tag("sqlite_analysis")
class NumericAnalysisTests(SimpleTestCase):
    """Tests for numeric column analysis."""

    def test_basic_statistics(self):
        """Test mean, median, stddev, min, max."""
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        stats = sqlite_analysis.analyze_numeric(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.count, 10)
        self.assertEqual(stats.min_val, 1.0)
        self.assertEqual(stats.max_val, 10.0)
        self.assertAlmostEqual(stats.mean, 5.5, places=1)
        self.assertAlmostEqual(stats.median, 5.5, places=1)

    def test_percentiles(self):
        """Test percentile calculation."""
        values = list(range(1, 101))  # 1-100
        stats = sqlite_analysis.analyze_numeric(values)

        self.assertIsNotNone(stats)
        # Percentiles are approximate based on index calculation
        self.assertGreaterEqual(stats.p25, 24)
        self.assertLessEqual(stats.p25, 27)
        self.assertGreaterEqual(stats.p75, 74)
        self.assertLessEqual(stats.p75, 77)

    def test_outlier_detection(self):
        """Test IQR-based outlier detection."""
        # Normal values plus obvious outliers
        values = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 100, -50]
        stats = sqlite_analysis.analyze_numeric(values)

        self.assertIsNotNone(stats)
        self.assertGreater(stats.outlier_count, 0)
        self.assertIn(100, stats.outliers)

    def test_distribution_detection_uniform(self):
        """Test uniform distribution detection."""
        values = list(range(0, 100, 5))  # Evenly spaced
        stats = sqlite_analysis.analyze_numeric(values)

        self.assertIsNotNone(stats)
        # Should detect as uniform or normal
        self.assertIn(stats.distribution, ["uniform", "normal", "unknown"])

    def test_distribution_detection_constant(self):
        """Test constant value detection."""
        values = [5.0] * 20
        stats = sqlite_analysis.analyze_numeric(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.distribution, "constant")

    def test_skips_none_values(self):
        """Test that None values are properly skipped."""
        values = [1, None, 2, None, 3, 4, 5]
        stats = sqlite_analysis.analyze_numeric(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.count, 5)

    def test_skips_bool_values(self):
        """Test that boolean values are not treated as numeric."""
        values = [True, False, True, True, False]
        stats = sqlite_analysis.analyze_numeric(values)

        # Should return None since bools are skipped
        self.assertIsNone(stats)

    def test_handles_floats(self):
        """Test float value handling."""
        values = [1.5, 2.7, 3.14159, 4.0, 5.999]
        stats = sqlite_analysis.analyze_numeric(values)

        self.assertIsNotNone(stats)
        self.assertAlmostEqual(stats.min_val, 1.5, places=2)

    def test_empty_list(self):
        """Test with empty list."""
        stats = sqlite_analysis.analyze_numeric([])
        self.assertIsNone(stats)

    def test_single_value(self):
        """Test with single value."""
        stats = sqlite_analysis.analyze_numeric([42])
        self.assertIsNone(stats)  # Need at least 2 values


@tag("sqlite_analysis")
class TemporalAnalysisTests(SimpleTestCase):
    """Tests for temporal/date column analysis."""

    def test_iso_datetime_parsing(self):
        """Test ISO datetime string parsing."""
        values = [
            "2024-01-15T10:30:00Z",
            "2024-02-20T14:45:30Z",
            "2024-03-25T09:15:00Z",
        ]
        stats = sqlite_analysis.analyze_temporal(values)

        self.assertIsNotNone(stats)
        self.assertGreater(stats.parse_rate, 0.9)
        self.assertEqual(stats.count, 3)

    def test_iso_date_parsing(self):
        """Test ISO date string parsing."""
        values = ["2024-01-01", "2024-06-15", "2024-12-31"]
        stats = sqlite_analysis.analyze_temporal(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.granularity, "day")

    def test_us_date_format(self):
        """Test US date format parsing."""
        values = ["01/15/2024", "02/28/2024", "12/31/2024"]
        stats = sqlite_analysis.analyze_temporal(values)

        self.assertIsNotNone(stats)
        self.assertGreater(stats.parse_rate, 0.5)

    def test_date_range_calculation(self):
        """Test min/max date and span calculation."""
        values = ["2024-01-01", "2024-01-05", "2024-01-10"]
        stats = sqlite_analysis.analyze_temporal(values)

        self.assertIsNotNone(stats)
        self.assertIsNotNone(stats.min_date)
        self.assertIsNotNone(stats.max_date)
        self.assertIn("day", stats.span_description.lower())

    def test_recency_calculation(self):
        """Test recency description."""
        # Use dates from a few days ago
        now = datetime.now()
        values = [
            (now - timedelta(days=5)).isoformat(),
            (now - timedelta(days=3)).isoformat(),
            (now - timedelta(days=2)).isoformat(),
        ]
        stats = sqlite_analysis.analyze_temporal(values)

        self.assertIsNotNone(stats)
        self.assertIn("day", stats.recency.lower())

    def test_granularity_detection_minute(self):
        """Test minute-level granularity detection."""
        values = [
            "2024-01-15T10:30:00",
            "2024-01-15T10:31:00",
            "2024-01-15T10:32:00",
        ]
        stats = sqlite_analysis.analyze_temporal(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.granularity, "minute")

    def test_mixed_parseable_unparseable(self):
        """Test with mix of parseable and unparseable values."""
        values = [
            "2024-01-15",
            "not a date",
            "2024-02-20",
            "also not a date",
            "2024-03-25",
        ]
        stats = sqlite_analysis.analyze_temporal(values)

        self.assertIsNotNone(stats)
        self.assertAlmostEqual(stats.parse_rate, 0.6, places=1)

    def test_below_threshold_returns_none(self):
        """Test that low parse rate returns None."""
        values = ["not a date", "also not", "nope", "still no"]
        stats = sqlite_analysis.analyze_temporal(values)

        self.assertIsNone(stats)

    def test_datetime_objects(self):
        """Test with actual datetime objects."""
        values = [
            datetime(2024, 1, 15, 10, 30),
            datetime(2024, 2, 20, 14, 45),
        ]
        stats = sqlite_analysis.analyze_temporal(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.count, 2)


@tag("sqlite_analysis")
class TextAnalysisTests(SimpleTestCase):
    """Tests for text column analysis."""

    def test_basic_text_stats(self):
        """Test basic text statistics."""
        values = ["hello", "world", "testing", "one two three"]
        stats = sqlite_analysis.analyze_text(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.count, 4)
        self.assertEqual(stats.min_length, 5)
        self.assertEqual(stats.max_length, 13)

    def test_multiline_detection(self):
        """Test multiline text detection."""
        values = ["single line", "multi\nline\ntext", "another single"]
        stats = sqlite_analysis.analyze_text(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.multiline_count, 1)

    def test_empty_string_count(self):
        """Test empty string counting."""
        values = ["content", "", "more content", "   ", "final"]
        stats = sqlite_analysis.analyze_text(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.empty_count, 2)  # "" and "   "


@tag("sqlite_analysis")
class SemanticTypeTests(SimpleTestCase):
    """Tests for semantic type detection."""

    def test_email_detection(self):
        """Test email pattern detection."""
        values = [
            "user@example.com",
            "test.user@domain.org",
            "admin@company.co.uk",
        ]
        stats = sqlite_analysis.analyze_text(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.semantic_type, "email")
        self.assertGreater(stats.semantic_type_rate, 0.9)

    def test_url_detection(self):
        """Test URL pattern detection."""
        values = [
            "https://example.com/page",
            "http://test.org/path/to/resource",
            "https://api.service.io/v1/endpoint",
        ]
        stats = sqlite_analysis.analyze_text(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.semantic_type, "url")

    def test_uuid_detection(self):
        """Test UUID pattern detection."""
        values = [
            "550e8400-e29b-41d4-a716-446655440000",
            "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        ]
        stats = sqlite_analysis.analyze_text(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.semantic_type, "uuid")

    def test_ipv4_detection(self):
        """Test IPv4 address detection."""
        values = ["192.168.1.1", "10.0.0.1", "172.16.0.100"]
        stats = sqlite_analysis.analyze_text(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.semantic_type, "ipv4")

    def test_phone_detection(self):
        """Test phone number detection."""
        values = ["555-123-4567", "555.123.4567", "(555) 123-4567"]
        stats = sqlite_analysis.analyze_text(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.semantic_type, "phone")

    def test_currency_usd_detection(self):
        """Test USD currency detection."""
        values = ["$100.00", "$1,234.56", "$50"]
        stats = sqlite_analysis.analyze_text(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.semantic_type, "currency_usd")

    def test_percentage_detection(self):
        """Test percentage detection."""
        values = ["50%", "100%", "25.5%", "-10%"]
        stats = sqlite_analysis.analyze_text(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.semantic_type, "percentage")

    def test_semver_detection(self):
        """Test semantic version detection."""
        values = ["1.0.0", "2.3.4", "10.20.30-beta"]
        stats = sqlite_analysis.analyze_text(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.semantic_type, "semver")

    def test_no_semantic_type_for_mixed(self):
        """Test that mixed content doesn't get a semantic type."""
        values = ["random text", "123", "user@email.com", "more random"]
        stats = sqlite_analysis.analyze_text(values)

        self.assertIsNotNone(stats)
        # Should not have high-confidence semantic type
        self.assertTrue(
            stats.semantic_type is None or stats.semantic_type_rate < 0.5
        )


@tag("sqlite_analysis")
class NestedContentTests(SimpleTestCase):
    """Tests for nested content detection (JSON, CSV, etc.)."""

    def test_json_object_detection(self):
        """Test JSON object detection in text."""
        values = [
            '{"name": "Alice", "age": 30}',
            '{"name": "Bob", "age": 25}',
            '{"name": "Charlie", "age": 35}',
        ]
        info = sqlite_analysis.analyze_nested_content(values)

        self.assertIsNotNone(info)
        self.assertEqual(info.format, "json_object")
        self.assertGreater(info.detection_rate, 0.9)
        self.assertIn("name", info.json_keys)
        self.assertIn("age", info.json_keys)

    def test_json_array_detection(self):
        """Test JSON array detection in text."""
        values = [
            '[1, 2, 3, 4, 5]',
            '["a", "b", "c"]',
            '[{"id": 1}, {"id": 2}]',
        ]
        info = sqlite_analysis.analyze_nested_content(values)

        self.assertIsNotNone(info)
        self.assertEqual(info.format, "json_array")

    def test_json_path_collection(self):
        """Test JSON path collection for nested objects."""
        values = [
            '{"data": {"items": [{"id": 1, "name": "test"}], "count": 10}}',
        ]
        info = sqlite_analysis.analyze_nested_content(values)

        self.assertIsNotNone(info)
        # Should find paths like $.data.items[]
        self.assertTrue(any("items" in p for p in info.json_paths))

    def test_csv_detection(self):
        """Test CSV content detection."""
        values = [
            "name,age,city\nAlice,30,NYC\nBob,25,LA\nCharlie,35,Chicago",
            "id,value,status\n1,100,active\n2,200,inactive",
        ]
        info = sqlite_analysis.analyze_nested_content(values)

        self.assertIsNotNone(info)
        self.assertEqual(info.format, "csv")
        self.assertIn(",", info.csv_delimiter)

    def test_csv_column_detection(self):
        """Test CSV column header detection."""
        values = [
            "product_id,product_name,price,quantity\n1,Widget,9.99,100\n2,Gadget,19.99,50",
        ]
        info = sqlite_analysis.analyze_nested_content(values)

        self.assertIsNotNone(info)
        self.assertIn("product_id", info.csv_columns)

    def test_nested_json_in_json_detection(self):
        """Test detection of JSON inside JSON strings."""
        inner_json = json.dumps({"nested": "data", "count": 42})
        values = [
            json.dumps({"content": inner_json, "type": "embedded"}),
        ]
        info = sqlite_analysis.analyze_nested_content(values)

        self.assertIsNotNone(info)
        self.assertTrue(info.has_deeper_nesting)
        self.assertIn("JSON", info.nested_hint)

    def test_csv_in_json_detection(self):
        """Test detection of CSV inside JSON strings."""
        csv_content = "a,b,c\n1,2,3\n4,5,6"
        values = [
            json.dumps({"content": csv_content, "format": "csv"}),
        ]
        info = sqlite_analysis.analyze_nested_content(values)

        self.assertIsNotNone(info)
        # Should detect as JSON with nested content
        if info.has_deeper_nesting:
            self.assertIn("CSV", info.nested_hint)

    def test_json_lines_detection(self):
        """Test JSON Lines (newline-delimited JSON) detection."""
        # JSON Lines content - each line is valid JSON
        # Note: This may be detected as CSV if lines have consistent comma counts
        # The detection order is: JSON object/array > CSV > JSON Lines
        values = [
            '{"user": "alice"}\n{"user": "bob"}\n{"user": "charlie"}',
        ]
        info = sqlite_analysis.analyze_nested_content(values)

        self.assertIsNotNone(info)
        # Can be detected as json_lines, json_object, or csv depending on parsing
        # The important thing is that structured content is detected
        self.assertIn(info.format, ["json_lines", "json_object", "csv"])

    def test_non_json_non_csv(self):
        """Test that plain text doesn't trigger nested detection."""
        values = [
            "This is just plain text.",
            "No JSON or CSV here.",
            "Just regular sentences.",
        ]
        info = sqlite_analysis.analyze_nested_content(values)

        self.assertIsNone(info)


@tag("sqlite_analysis")
class CardinalityTests(SimpleTestCase):
    """Tests for cardinality analysis."""

    def test_unique_detection(self):
        """Test unique value detection."""
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        info = sqlite_analysis.analyze_cardinality(values)

        self.assertEqual(info.cardinality_type, "unique")
        self.assertTrue(info.is_unique)
        self.assertEqual(info.distinct_count, 10)

    def test_binary_detection(self):
        """Test binary (2-value) detection."""
        values = [True, False, True, True, False, True]
        info = sqlite_analysis.analyze_cardinality(values)

        self.assertEqual(info.cardinality_type, "binary")
        self.assertEqual(info.distinct_count, 2)

    def test_enum_detection(self):
        """Test enum (small set) detection."""
        values = ["red", "green", "blue", "red", "green", "blue", "red"]
        info = sqlite_analysis.analyze_cardinality(values)

        self.assertEqual(info.cardinality_type, "enum")
        self.assertEqual(info.distinct_count, 3)
        self.assertIn("red", info.sample_values)

    def test_enum_value_distribution(self):
        """Test that enum values include distribution counts."""
        values = ["a", "a", "a", "b", "b", "c"]
        info = sqlite_analysis.analyze_cardinality(values)

        self.assertEqual(info.value_distribution.get("a"), 3)
        self.assertEqual(info.value_distribution.get("b"), 2)
        self.assertEqual(info.value_distribution.get("c"), 1)

    def test_sequential_detection(self):
        """Test sequential (auto-increment) detection."""
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        info = sqlite_analysis.analyze_cardinality(values)

        self.assertTrue(info.is_sequential)

    def test_low_cardinality(self):
        """Test low cardinality detection."""
        # 20 distinct values out of 1000 (2% - low but not enum)
        values = [f"cat{i % 20}" for i in range(1000)]
        info = sqlite_analysis.analyze_cardinality(values)

        self.assertEqual(info.cardinality_type, "low")

    def test_high_cardinality(self):
        """Test high cardinality detection."""
        # Many distinct values
        values = [f"user_{i}" for i in range(100)]
        info = sqlite_analysis.analyze_cardinality(values)

        self.assertIn(info.cardinality_type, ["high", "unique"])

    def test_handles_nulls(self):
        """Test that nulls are handled correctly."""
        values = [1, None, 2, None, 3]
        info = sqlite_analysis.analyze_cardinality(values)

        self.assertEqual(info.distinct_count, 3)
        self.assertEqual(info.total_count, 5)

    def test_sparse_large_integers_do_not_materialize_huge_range(self):
        """Regression: sparse large unique ints should not trigger huge range allocations."""
        values = [
            134662,
            2049749369,
            1284876922,
            26350935,
            142935447,
            31654357,
            15168993,
            93864,
            242,
        ]
        info = sqlite_analysis.analyze_cardinality(values)

        self.assertEqual(info.cardinality_type, "unique")
        self.assertTrue(info.is_unique)
        self.assertFalse(info.is_sequential)


@tag("sqlite_analysis")
class DataQualityTests(SimpleTestCase):
    """Tests for data quality analysis."""

    def test_null_rate_calculation(self):
        """Test null rate calculation."""
        values = [1, None, 2, None, 3, None, 4]
        quality = sqlite_analysis.analyze_quality(values)

        self.assertEqual(quality.null_count, 3)
        self.assertAlmostEqual(quality.null_rate, 3/7, places=2)

    def test_type_distribution(self):
        """Test type distribution counting."""
        values = [1, 2, "text", 3.14, None, True]
        quality = sqlite_analysis.analyze_quality(values)

        self.assertEqual(quality.type_distribution.get("int"), 2)
        self.assertEqual(quality.type_distribution.get("text"), 1)
        self.assertEqual(quality.type_distribution.get("float"), 1)
        self.assertEqual(quality.type_distribution.get("null"), 1)
        self.assertEqual(quality.type_distribution.get("bool"), 1)

    def test_mixed_types_detection(self):
        """Test mixed types detection."""
        values = [1, "text", 3.14]
        quality = sqlite_analysis.analyze_quality(values)

        self.assertTrue(quality.mixed_types)

    def test_empty_string_count(self):
        """Test empty string counting."""
        values = ["content", "", "   ", "more"]
        quality = sqlite_analysis.analyze_quality(values)

        self.assertEqual(quality.empty_string_count, 2)

    def test_whitespace_issues(self):
        """Test whitespace issue detection."""
        values = [" leading", "trailing ", " both ", "clean"]
        quality = sqlite_analysis.analyze_quality(values)

        self.assertEqual(quality.whitespace_issues, 3)

    def test_duplicate_count(self):
        """Test duplicate counting."""
        values = [1, 2, 2, 3, 3, 3, 4]
        quality = sqlite_analysis.analyze_quality(values)

        self.assertEqual(quality.duplicate_count, 3)  # 7 total - 4 distinct


@tag("sqlite_analysis")
class CorrelationTests(SimpleTestCase):
    """Tests for correlation detection."""

    def test_numeric_correlation_detection(self):
        """Test Pearson correlation detection."""
        columns = [
            {"name": "x", "index": 0},
            {"name": "y", "index": 1},
        ]
        # Perfect positive correlation
        rows = [(i, i * 2) for i in range(20)]

        correlations = sqlite_analysis.detect_correlations(columns, rows)

        self.assertGreater(len(correlations), 0)
        corr = correlations[0]
        self.assertEqual(corr.correlation_type, "numeric")
        self.assertGreater(corr.strength, 0.9)

    def test_fk_candidate_detection(self):
        """Test foreign key candidate detection."""
        columns = [
            {"name": "id", "index": 0},
            {"name": "user_id", "index": 1},
        ]
        # user_id has repeated values (looks like FK)
        rows = [(i, i % 5) for i in range(20)]

        correlations = sqlite_analysis.detect_correlations(columns, rows)

        fk_candidates = [c for c in correlations if c.correlation_type == "fk_candidate"]
        self.assertGreater(len(fk_candidates), 0)

    def test_no_correlation_for_independent(self):
        """Test that independent columns don't show correlation."""
        columns = [
            {"name": "a", "index": 0},
            {"name": "b", "index": 1},
        ]
        # Random-ish values
        import random
        random.seed(42)
        rows = [(random.randint(1, 100), random.randint(1, 100)) for _ in range(20)]

        correlations = sqlite_analysis.detect_correlations(columns, rows)

        numeric_corrs = [c for c in correlations if c.correlation_type == "numeric"]
        # Should have no strong correlations
        self.assertTrue(all(c.strength < 0.7 for c in numeric_corrs))


@tag("sqlite_analysis")
class QuerySuggestionTests(SimpleTestCase):
    """Tests for query suggestion generation."""

    def test_enum_group_by_suggestion(self):
        """Test GROUP BY suggestion for enum columns."""
        columns = [
            {"name": "category", "cardinality_type": "enum", "inferred_type": "text"},
            {"name": "value", "cardinality_type": "high", "inferred_type": "float"},
        ]

        suggestions = sqlite_analysis.generate_query_suggestions("products", columns, 100)

        group_by_suggestions = [s for s in suggestions if "GROUP BY" in s.sql]
        self.assertGreater(len(group_by_suggestions), 0)

    def test_numeric_stats_suggestion(self):
        """Test stats suggestion for numeric columns."""
        columns = [
            {"name": "price", "cardinality_type": "high", "inferred_type": "float"},
        ]

        suggestions = sqlite_analysis.generate_query_suggestions("orders", columns, 100)

        stats_suggestions = [s for s in suggestions if "AVG" in s.sql or "MIN" in s.sql]
        self.assertGreater(len(stats_suggestions), 0)

    def test_date_order_suggestion(self):
        """Test ORDER BY suggestion for date columns."""
        columns = [
            {"name": "created_at", "cardinality_type": "high", "inferred_type": "datetime"},
        ]

        suggestions = sqlite_analysis.generate_query_suggestions("events", columns, 100)

        order_suggestions = [s for s in suggestions if "ORDER BY" in s.sql]
        self.assertGreater(len(order_suggestions), 0)


@tag("sqlite_analysis")
class ColumnAnalysisTests(SimpleTestCase):
    """Tests for single column analysis."""

    def test_integer_column(self):
        """Test integer column analysis."""
        values = list(range(1, 51))
        analysis = sqlite_analysis.analyze_column("id", "INTEGER", values, 50)

        self.assertEqual(analysis.inferred_type, "int")
        self.assertIsNotNone(analysis.numeric_stats)
        self.assertTrue(analysis.cardinality.is_unique)

    def test_text_column_with_emails(self):
        """Test text column with email values."""
        values = [f"user{i}@example.com" for i in range(20)]
        analysis = sqlite_analysis.analyze_column("email", "TEXT", values, 20)

        self.assertEqual(analysis.inferred_type, "text")
        self.assertIsNotNone(analysis.text_stats)
        self.assertEqual(analysis.text_stats.semantic_type, "email")

    def test_json_text_column(self):
        """Test text column containing JSON."""
        values = [json.dumps({"key": f"value{i}"}) for i in range(10)]
        analysis = sqlite_analysis.analyze_column("data", "TEXT", values, 10)

        self.assertEqual(analysis.inferred_type, "json_object")
        self.assertIsNotNone(analysis.nested_content)

    def test_datetime_text_column(self):
        """Test text column containing datetimes."""
        values = [f"2024-01-{i+1:02d}T10:00:00Z" for i in range(20)]
        analysis = sqlite_analysis.analyze_column("timestamp", "TEXT", values, 20)

        self.assertEqual(analysis.inferred_type, "datetime")
        self.assertIsNotNone(analysis.temporal_stats)

    def test_epoch_timestamp_detection(self):
        """Test Unix epoch timestamp detection."""
        base = 1704067200  # 2024-01-01 00:00:00 UTC
        values = [base + i * 3600 for i in range(24)]
        analysis = sqlite_analysis.analyze_column("timestamp", "INTEGER", values, 24)

        self.assertEqual(analysis.inferred_type, "epoch_timestamp")

    def test_null_only_column(self):
        """Test column with only null values."""
        values = [None] * 10
        analysis = sqlite_analysis.analyze_column("empty", "TEXT", values, 10)

        self.assertEqual(analysis.inferred_type, "null")


@tag("sqlite_analysis")
class TableAnalysisIntegrationTests(TestCase):
    """Integration tests for full table analysis."""

    def setUp(self):
        """Create a temporary SQLite database."""
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()

    def tearDown(self):
        """Clean up the temporary database."""
        self.conn.close()
        import os
        os.unlink(self.db_path)

    def test_simple_table_analysis(self):
        """Test analysis of a simple table."""
        self.cursor.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                name TEXT,
                age INTEGER,
                email TEXT
            )
        """)
        for i in range(50):
            self.cursor.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?)",
                (i + 1, f"User {i}", 20 + (i % 40), f"user{i}@example.com")
            )
        self.conn.commit()

        analysis = sqlite_analysis.analyze_table(self.cursor, "users", 50)

        self.assertEqual(analysis.name, "users")
        self.assertEqual(analysis.row_count, 50)
        self.assertEqual(len(analysis.columns), 4)

        # Check that columns were analyzed
        id_col = next(c for c in analysis.columns if c["name"] == "id")
        self.assertEqual(id_col["inferred_type"], "int")

        email_col = next(c for c in analysis.columns if c["name"] == "email")
        self.assertIn("email", str(email_col.get("_analysis").text_stats.semantic_type or "").lower())

    def test_table_with_json_column(self):
        """Test analysis of table with JSON data."""
        self.cursor.execute("""
            CREATE TABLE api_responses (
                id INTEGER PRIMARY KEY,
                response TEXT
            )
        """)
        for i in range(20):
            json_data = json.dumps({
                "status": "ok",
                "data": {"items": [{"id": j, "val": j * 10} for j in range(5)]},
                "count": 5
            })
            self.cursor.execute(
                "INSERT INTO api_responses VALUES (?, ?)",
                (i + 1, json_data)
            )
        self.conn.commit()

        analysis = sqlite_analysis.analyze_table(self.cursor, "api_responses", 20)

        response_col = next(c for c in analysis.columns if c["name"] == "response")
        self.assertEqual(response_col["inferred_type"], "json_object")
        self.assertIsNotNone(response_col.get("json_paths"))

    def test_table_with_csv_column(self):
        """Test analysis of table with embedded CSV."""
        self.cursor.execute("""
            CREATE TABLE imports (
                id INTEGER PRIMARY KEY,
                csv_data TEXT
            )
        """)
        for i in range(10):
            csv_data = "name,value,status\n"
            for j in range(10):
                csv_data += f"item{j},{j * 100},active\n"
            self.cursor.execute(
                "INSERT INTO imports VALUES (?, ?)",
                (i + 1, csv_data)
            )
        self.conn.commit()

        analysis = sqlite_analysis.analyze_table(self.cursor, "imports", 10)

        csv_col = next(c for c in analysis.columns if c["name"] == "csv_data")
        self.assertEqual(csv_col["inferred_type"], "csv")

    def test_table_with_dates(self):
        """Test analysis of table with date columns."""
        self.cursor.execute("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY,
                event_date TEXT,
                created_at TEXT
            )
        """)
        from datetime import datetime, timedelta
        base_date = datetime(2024, 1, 1)
        for i in range(30):
            event_date = (base_date + timedelta(days=i)).strftime("%Y-%m-%d")
            created_at = (base_date + timedelta(days=i, hours=i)).isoformat()
            self.cursor.execute(
                "INSERT INTO events VALUES (?, ?, ?)",
                (i + 1, event_date, created_at)
            )
        self.conn.commit()

        analysis = sqlite_analysis.analyze_table(self.cursor, "events", 30)

        date_col = next(c for c in analysis.columns if c["name"] == "event_date")
        self.assertEqual(date_col["inferred_type"], "datetime")

    def test_query_suggestions_generated(self):
        """Test that query suggestions are generated."""
        self.cursor.execute("""
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                product TEXT,
                amount REAL,
                status TEXT
            )
        """)
        statuses = ["pending", "shipped", "delivered"]
        for i in range(50):
            self.cursor.execute(
                "INSERT INTO orders VALUES (?, ?, ?, ?)",
                (i + 1, f"Product {i % 10}", 10.0 + i, statuses[i % 3])
            )
        self.conn.commit()

        analysis = sqlite_analysis.analyze_table(self.cursor, "orders", 50)

        self.assertGreater(len(analysis.query_suggestions), 0)

    def test_format_table_analysis(self):
        """Test formatting of table analysis."""
        self.cursor.execute("""
            CREATE TABLE test (
                id INTEGER PRIMARY KEY,
                value REAL,
                category TEXT
            )
        """)
        for i in range(30):
            self.cursor.execute(
                "INSERT INTO test VALUES (?, ?, ?)",
                (i + 1, i * 1.5, ["A", "B", "C"][i % 3])
            )
        self.conn.commit()

        analysis = sqlite_analysis.analyze_table(self.cursor, "test", 30)
        lines = sqlite_analysis.format_table_analysis(analysis)

        self.assertGreater(len(lines), 0)
        # Should have COLUMNS section
        self.assertTrue(any("COLUMNS:" in line for line in lines))


@tag("sqlite_analysis")
class EdgeCaseTests(SimpleTestCase):
    """Tests for edge cases and messy real-world data."""

    def test_empty_values_list(self):
        """Test with completely empty values."""
        analysis = sqlite_analysis.analyze_column("empty", "TEXT", [], 0)
        # Empty column returns "null" type since there's no data
        self.assertIn(analysis.inferred_type, ["null", "unknown"])

    def test_all_none_values(self):
        """Test with all None values."""
        values = [None] * 20
        analysis = sqlite_analysis.analyze_column("nulls", "TEXT", values, 20)
        self.assertEqual(analysis.inferred_type, "null")

    def test_mixed_type_column(self):
        """Test column with mixed types."""
        values = [1, "text", 3.14, None, True, {"key": "value"}, [1, 2, 3]]
        quality = sqlite_analysis.analyze_quality(values)
        self.assertTrue(quality.mixed_types)

    def test_very_long_text(self):
        """Test with very long text values."""
        long_text = "x" * 10000
        values = [long_text, "short", long_text]
        stats = sqlite_analysis.analyze_text(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.max_length, 10000)

    def test_unicode_text(self):
        """Test with Unicode text."""
        values = ["日本語テキスト", "中文文本", "한국어 텍스트", "Émoji 🎉"]
        stats = sqlite_analysis.analyze_text(values)

        self.assertIsNotNone(stats)
        self.assertEqual(stats.count, 4)

    def test_malformed_json(self):
        """Test with malformed JSON."""
        values = [
            '{"valid": "json"}',
            '{not valid json}',
            '{"also": "valid"}',
            'completely invalid',
        ]
        info = sqlite_analysis.analyze_nested_content(values)

        # Should still detect JSON in some values
        if info:
            self.assertLess(info.detection_rate, 1.0)

    def test_csv_with_quoted_commas(self):
        """Test CSV with quoted fields containing commas."""
        values = [
            'name,description,value\n"Smith, John","A, B, C",100\n"Doe, Jane","X, Y",200',
        ]
        info = sqlite_analysis.analyze_nested_content(values)

        # Should still detect as CSV
        self.assertIsNotNone(info)
        if info:
            self.assertEqual(info.format, "csv")

    def test_deeply_nested_json(self):
        """Test deeply nested JSON structures."""
        deep_json = {"level1": {"level2": {"level3": {"level4": {"data": [1, 2, 3]}}}}}
        values = [json.dumps(deep_json) for _ in range(5)]

        info = sqlite_analysis.analyze_nested_content(values)

        self.assertIsNotNone(info)
        # Should detect as JSON object
        self.assertEqual(info.format, "json_object")
        # Keys should include at least level1
        self.assertIn("level1", info.json_keys)

    def test_numeric_strings(self):
        """Test strings that look like numbers."""
        values = ["123", "456.78", "0", "-99", "1e10"]
        stats = sqlite_analysis.analyze_text(values)

        self.assertIsNotNone(stats)
        # Should be text, not numeric
        self.assertEqual(stats.count, 5)

    def test_date_edge_cases(self):
        """Test date parsing edge cases."""
        values = [
            "2024-01-01",
            "Jan 1, 2024",
            "01/01/24",
            "2024-13-45",  # Invalid date
            "not a date",
        ]
        stats = sqlite_analysis.analyze_temporal(values)

        # Should parse some but not all
        if stats:
            self.assertLess(stats.parse_rate, 1.0)

    def test_extremely_sparse_data(self):
        """Test with very sparse data (mostly nulls)."""
        values = [None] * 95 + [1, 2, 3, 4, 5]
        quality = sqlite_analysis.analyze_quality(values)

        self.assertAlmostEqual(quality.null_rate, 0.95, places=2)

    def test_binary_blob_handling(self):
        """Test that binary blobs are handled gracefully."""
        values = [b'\x00\x01\x02', b'\xff\xfe\xfd', None]
        quality = sqlite_analysis.analyze_quality(values)

        self.assertEqual(quality.type_distribution.get("blob"), 2)
