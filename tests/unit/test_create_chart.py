from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.tools.create_chart import (
    _LONG_LABEL_CHAR_THRESHOLD,
    _MANY_CATEGORIES_THRESHOLD,
    _build_chart_save_path,
    _compute_horizontal_bar_left_margin,
    _generate_chart,
    _max_label_len,
    _should_swap_to_horizontal,
    execute_create_chart,
    get_create_chart_tool,
)
from api.models import BrowserUseAgent, PersistentAgent


def mock_query_data(data, cols=None, error=None):
    """Helper to mock _execute_query_for_data with optional explicit columns."""
    if cols is None and data and isinstance(data[0], dict):
        cols = list(data[0].keys())
    return patch(
        "api.agent.tools.create_chart._execute_query_for_data",
        return_value=(data, cols, error),
    )


@tag("batch_agent_tools")
class CreateChartToolTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="chart@example.com",
            email="chart@example.com",
            password="secret",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Chart Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Chart Agent",
            charter="create charts",
            browser_use_agent=cls.browser_agent,
        )

    def _assert_missing_columns_error(self, params, data, expected_missing, expected_available):
        with (
            patch("api.agent.files.filespace_service.write_bytes_to_dir") as mock_write,
            patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url") as mock_signed_url,
            mock_query_data(data, cols=expected_available),
        ):
            result = execute_create_chart(self.agent, params)

        self.assertEqual(result["status"], "error")
        message = result["message"].lower()
        self.assertIn(f"available: {', '.join(expected_available)}", message)
        self.assertIn("aliases exactly match", message)

        missing_part = message.split("missing: ", 1)[1].split(".", 1)[0]
        found_missing_set = {column.strip() for column in missing_part.split(",")}
        expected_missing_set = {column.strip() for column in expected_missing.split(",")}
        self.assertSetEqual(found_missing_set, expected_missing_set)
        mock_write.assert_not_called()
        mock_signed_url.assert_not_called()

    def test_tool_definition_has_required_fields(self):
        tool = get_create_chart_tool()
        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["function"]["name"], "create_chart")
        self.assertIn("parameters", tool["function"])
        self.assertIn("type", tool["function"]["parameters"]["properties"])
        self.assertIn("query", tool["function"]["parameters"]["properties"])
        self.assertIn("query", tool["function"]["parameters"]["required"])

    def test_missing_type_returns_error(self):
        result = execute_create_chart(
            self.agent,
            {"query": "SELECT 1"},
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("type", result["message"].lower())

    def test_missing_query_returns_error(self):
        result = execute_create_chart(
            self.agent,
            {"type": "bar"},
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("query", result["message"].lower())

    def test_empty_query_result_returns_error(self):
        with patch(
            "api.agent.tools.create_chart._execute_query_for_data",
            return_value=([], None, None),
        ):
            result = execute_create_chart(
                self.agent,
                {"type": "bar", "query": "SELECT * FROM empty", "x": "a", "y": "b"},
            )
            self.assertEqual(result["status"], "error")
            self.assertIn("no rows", result["message"].lower())

    def test_query_error_returns_error(self):
        with patch(
            "api.agent.tools.create_chart._execute_query_for_data",
            return_value=([], None, "Query failed: no such table"),
        ):
            result = execute_create_chart(
                self.agent,
                {"type": "bar", "query": "SELECT * FROM missing", "x": "a", "y": "b"},
            )
            self.assertEqual(result["status"], "error")
            self.assertIn("query failed", result["message"].lower())

    def test_invalid_chart_type_returns_error(self):
        result = execute_create_chart(
            self.agent,
            {"type": "invalid_chart", "query": "SELECT 1", "x": "x", "y": "y"},
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("invalid", result["message"].lower())

    def test_bar_chart_requires_x_and_y(self):
        with mock_query_data([{"month": "Jan", "value": 100}]):
            result = execute_create_chart(
                self.agent,
                {"type": "bar", "query": "SELECT month, value FROM t"},
            )
            self.assertEqual(result["status"], "error")
            self.assertIn("x", result["message"].lower())

    def test_pie_chart_requires_values_and_labels(self):
        with mock_query_data([{"category": "A", "amount": 100}]):
            result = execute_create_chart(
                self.agent,
                {"type": "pie", "query": "SELECT category, amount FROM t"},
            )
            self.assertEqual(result["status"], "error")
            self.assertIn("values", result["message"].lower())

    def test_non_pie_chart_missing_columns_returns_error(self):
        self._assert_missing_columns_error(
            params={
                "type": "line",
                "query": "SELECT period, amount FROM t",
                "x": "month",
                "y": "revenue",
            },
            data=[
                {"period": "Jan", "amount": 100},
                {"period": "Feb", "amount": 120},
            ],
            expected_missing="month, revenue",
            expected_available=["period", "amount"],
        )

    def test_pie_chart_missing_columns_returns_error(self):
        self._assert_missing_columns_error(
            params={
                "type": "pie",
                "query": "SELECT category_name, total_amount FROM t",
                "labels": "category",
                "values": "amount",
            },
            data=[
                {"category_name": "Sales", "total_amount": 300},
                {"category_name": "Marketing", "total_amount": 200},
            ],
            expected_missing="amount, category",
            expected_available=["category_name", "total_amount"],
        )

    def test_multi_series_chart_reports_only_missing_series(self):
        with (
            patch("api.agent.files.filespace_service.write_bytes_to_dir") as mock_write,
            patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url") as mock_signed_url,
            mock_query_data(
                [
                    {"month": "Jan", "revenue": 100, "costs": 80},
                    {"month": "Feb", "revenue": 150, "costs": 90},
                ],
                cols=["month", "revenue", "costs"],
            ),
        ):
            result = execute_create_chart(
                self.agent,
                {
                    "type": "line",
                    "query": "SELECT month, revenue, costs FROM t",
                    "x": "month",
                    "y": ["revenue", "profit"],
                },
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("missing: profit", result["message"].lower())
        self.assertNotIn("missing: revenue", result["message"].lower())
        self.assertIn("available: month, revenue, costs", result["message"].lower())
        mock_write.assert_not_called()
        mock_signed_url.assert_not_called()

    def test_malformed_multi_series_y_returns_structured_error(self):
        with mock_query_data(
            [
                {"month": "Jan", "revenue": 100},
                {"month": "Feb", "revenue": 120},
            ],
            cols=["month", "revenue"],
        ):
            result = execute_create_chart(
                self.agent,
                {
                    "type": "line",
                    "query": "SELECT month, revenue FROM t",
                    "x": "month",
                    "y": ["revenue", ["bad"]],
                },
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("failed to generate chart", result["message"].lower())

    @patch("api.agent.files.filespace_service.write_bytes_to_dir")
    @patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url")
    def test_chart_succeeds_when_aliases_match_requested_columns(self, mock_signed_url, mock_write):
        mock_write.return_value = {"status": "ok", "path": "/charts/alias.svg", "node_id": "id1"}
        mock_signed_url.return_value = "https://example.com/alias.svg"
        with mock_query_data([
            {"month": "Jan", "revenue": 100},
            {"month": "Feb", "revenue": 120},
        ]):
            result = execute_create_chart(
                self.agent,
                {
                    "type": "line",
                    "query": "SELECT period AS month, amount AS revenue FROM t",
                    "x": "month",
                    "y": "revenue",
                },
            )
        self.assertEqual(result["status"], "ok")
        self.assertIn("inline", result)
        mock_write.assert_called_once()
        mock_signed_url.assert_called_once()

    @patch("api.agent.files.filespace_service.write_bytes_to_dir")
    @patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url")
    def test_bar_chart_generates_svg(self, mock_signed_url, mock_write):
        mock_write.return_value = {"status": "ok", "path": "/charts/bar.svg", "node_id": "id1"}
        mock_signed_url.return_value = "https://example.com/bar.svg"

        with mock_query_data([
            {"month": "Jan", "revenue": 100},
            {"month": "Feb", "revenue": 150},
            {"month": "Mar", "revenue": 200},
        ]):
            result = execute_create_chart(
                self.agent,
                {
                    "type": "bar",
                    "query": "SELECT month, revenue FROM sales",
                    "x": "month",
                    "y": "revenue",
                    "title": "Monthly Revenue",
                },
            )
            self.assertEqual(result["status"], "ok")
            # Returns inline/attach hints for embedding
            self.assertIn("inline", result)
            self.assertIn("attach", result)
            self.assertIn("file", result)
            # inline uses path-based variable
            self.assertIn("$[/charts/bar.svg]", result["inline"])
            self.assertEqual(result["attach"], "$[/charts/bar.svg]")
            self.assertEqual(result["file"], "$[/charts/bar.svg]")
            # No url exposed - prevents LLM from copying it
            self.assertNotIn("url", result)
            # No data_uri - we don't flood LLM context with base64
            self.assertNotIn("data_uri", result)

    @patch("api.agent.files.filespace_service.write_bytes_to_dir")
    @patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url")
    def test_line_chart_generates_svg(self, mock_signed_url, mock_write):
        mock_write.return_value = {"status": "ok", "path": "/charts/line.svg", "node_id": "id1"}
        mock_signed_url.return_value = "https://example.com/line.svg"

        with mock_query_data([
            {"day": "Mon", "visitors": 100},
            {"day": "Tue", "visitors": 120},
            {"day": "Wed", "visitors": 90},
        ]):
            result = execute_create_chart(
                self.agent,
                {"type": "line", "query": "SELECT day, visitors FROM t", "x": "day", "y": "visitors"},
            )
            self.assertEqual(result["status"], "ok")
            self.assertIn("inline", result)

    @patch("api.agent.files.filespace_service.write_bytes_to_dir")
    @patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url")
    def test_pie_chart_generates_svg(self, mock_signed_url, mock_write):
        mock_write.return_value = {"status": "ok", "path": "/charts/pie.svg", "node_id": "id1"}
        mock_signed_url.return_value = "https://example.com/pie.svg"

        with mock_query_data([
            {"category": "Sales", "amount": 300},
            {"category": "Marketing", "amount": 200},
            {"category": "Engineering", "amount": 500},
        ]):
            result = execute_create_chart(
                self.agent,
                {
                    "type": "pie",
                    "query": "SELECT category, amount FROM budget",
                    "values": "amount",
                    "labels": "category",
                    "title": "Budget Distribution",
                },
            )
            self.assertEqual(result["status"], "ok")
            self.assertIn("inline", result)

    @patch("api.agent.files.filespace_service.write_bytes_to_dir")
    @patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url")
    def test_donut_chart_generates_svg(self, mock_signed_url, mock_write):
        mock_write.return_value = {"status": "ok", "path": "/charts/donut.svg", "node_id": "id1"}
        mock_signed_url.return_value = "https://example.com/donut.svg"

        with mock_query_data([
            {"source": "Organic", "visitors": 1500},
            {"source": "Paid", "visitors": 800},
            {"source": "Social", "visitors": 400},
        ]):
            result = execute_create_chart(
                self.agent,
                {
                    "type": "donut",
                    "query": "SELECT source, visitors FROM traffic",
                    "values": "visitors",
                    "labels": "source",
                },
            )
            self.assertEqual(result["status"], "ok")
            self.assertIn("inline", result)

    @patch("api.agent.files.filespace_service.write_bytes_to_dir")
    @patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url")
    def test_scatter_chart_generates_svg(self, mock_signed_url, mock_write):
        mock_write.return_value = {"status": "ok", "path": "/charts/scatter.svg", "node_id": "id1"}
        mock_signed_url.return_value = "https://example.com/scatter.svg"

        with mock_query_data([
            {"age": 25, "income": 40000},
            {"age": 35, "income": 60000},
            {"age": 45, "income": 80000},
        ]):
            result = execute_create_chart(
                self.agent,
                {"type": "scatter", "query": "SELECT age, income FROM t", "x": "age", "y": "income"},
            )
            self.assertEqual(result["status"], "ok")
            self.assertIn("inline", result)

    @patch("api.agent.files.filespace_service.write_bytes_to_dir")
    @patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url")
    def test_area_chart_generates_svg(self, mock_signed_url, mock_write):
        mock_write.return_value = {"status": "ok", "path": "/charts/area.svg", "node_id": "id1"}
        mock_signed_url.return_value = "https://example.com/area.svg"

        with mock_query_data([
            {"quarter": "Q1", "sales": 100},
            {"quarter": "Q2", "sales": 150},
            {"quarter": "Q3", "sales": 180},
        ]):
            result = execute_create_chart(
                self.agent,
                {"type": "area", "query": "SELECT quarter, sales FROM t", "x": "quarter", "y": "sales"},
            )
            self.assertEqual(result["status"], "ok")
            self.assertIn("inline", result)

    @patch("api.agent.files.filespace_service.write_bytes_to_dir")
    @patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url")
    def test_horizontal_bar_chart_generates_svg(self, mock_signed_url, mock_write):
        mock_write.return_value = {"status": "ok", "path": "/charts/hbar.svg", "node_id": "id1"}
        mock_signed_url.return_value = "https://example.com/hbar.svg"

        with mock_query_data([
            {"product": "Widget A", "sales": 500},
            {"product": "Widget B", "sales": 300},
            {"product": "Widget C", "sales": 700},
        ]):
            result = execute_create_chart(
                self.agent,
                {"type": "horizontal_bar", "query": "SELECT product, sales FROM t", "x": "product", "y": "sales"},
            )
            self.assertEqual(result["status"], "ok")
            self.assertIn("inline", result)

    @patch("api.agent.files.filespace_service.write_bytes_to_dir")
    @patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url")
    def test_multi_series_line_chart(self, mock_signed_url, mock_write):
        mock_write.return_value = {"status": "ok", "path": "/charts/multi.svg", "node_id": "id1"}
        mock_signed_url.return_value = "https://example.com/multi.svg"

        with mock_query_data([
            {"month": "Jan", "revenue": 100, "costs": 80},
            {"month": "Feb", "revenue": 150, "costs": 90},
            {"month": "Mar", "revenue": 200, "costs": 100},
        ]):
            result = execute_create_chart(
                self.agent,
                {
                    "type": "line",
                    "query": "SELECT month, revenue, costs FROM t",
                    "x": "month",
                    "y": ["revenue", "costs"],
                    "title": "Revenue vs Costs",
                },
            )
            self.assertEqual(result["status"], "ok")
            self.assertIn("inline", result)

    @patch("api.agent.files.filespace_service.write_bytes_to_dir")
    @patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url")
    def test_stacked_bar_chart(self, mock_signed_url, mock_write):
        mock_write.return_value = {"status": "ok", "path": "/charts/stacked.svg", "node_id": "id1"}
        mock_signed_url.return_value = "https://example.com/stacked.svg"

        with mock_query_data([
            {"quarter": "Q1", "product_a": 100, "product_b": 50},
            {"quarter": "Q2", "product_a": 120, "product_b": 60},
            {"quarter": "Q3", "product_a": 140, "product_b": 70},
        ]):
            result = execute_create_chart(
                self.agent,
                {
                    "type": "stacked_bar",
                    "query": "SELECT quarter, product_a, product_b FROM t",
                    "x": "quarter",
                    "y": ["product_a", "product_b"],
                    "title": "Product Sales by Quarter",
                },
            )
            self.assertEqual(result["status"], "ok")
            self.assertIn("inline", result)

    @patch("api.agent.files.filespace_service.write_bytes_to_dir")
    @patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url")
    def test_stacked_area_chart(self, mock_signed_url, mock_write):
        mock_write.return_value = {"status": "ok", "path": "/charts/stacked_area.svg", "node_id": "id1"}
        mock_signed_url.return_value = "https://example.com/stacked_area.svg"

        with mock_query_data([
            {"month": "Jan", "mobile": 100, "desktop": 200},
            {"month": "Feb", "mobile": 120, "desktop": 180},
            {"month": "Mar", "mobile": 150, "desktop": 160},
        ]):
            result = execute_create_chart(
                self.agent,
                {
                    "type": "stacked_area",
                    "query": "SELECT month, mobile, desktop FROM t",
                    "x": "month",
                    "y": ["mobile", "desktop"],
                    "title": "Traffic by Device",
                },
            )
            self.assertEqual(result["status"], "ok")
            self.assertIn("inline", result)

    @patch("api.agent.files.filespace_service.write_bytes_to_dir")
    @patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url")
    def test_custom_colors(self, mock_signed_url, mock_write):
        mock_write.return_value = {"status": "ok", "path": "/charts/colors.svg", "node_id": "id1"}
        mock_signed_url.return_value = "https://example.com/colors.svg"

        with mock_query_data([
            {"item": "A", "value": 10},
            {"item": "B", "value": 20},
        ]):
            result = execute_create_chart(
                self.agent,
                {
                    "type": "bar",
                    "query": "SELECT item, value FROM t",
                    "x": "item",
                    "y": "value",
                    "colors": ["#FF5733", "#33FF57"],
                },
            )
            self.assertEqual(result["status"], "ok")

    @patch("api.agent.files.filespace_service.write_bytes_to_dir")
    @patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url")
    def test_custom_labels(self, mock_signed_url, mock_write):
        mock_write.return_value = {"status": "ok", "path": "/charts/labels.svg", "node_id": "id1"}
        mock_signed_url.return_value = "https://example.com/labels.svg"

        with mock_query_data([
            {"x": 1, "y": 10},
            {"x": 2, "y": 20},
        ]):
            result = execute_create_chart(
                self.agent,
                {
                    "type": "bar",
                    "query": "SELECT x, y FROM t",
                    "x": "x",
                    "y": "y",
                    "xlabel": "Custom X Label",
                    "ylabel": "Custom Y Label",
                },
            )
            self.assertEqual(result["status"], "ok")

    @patch("api.agent.files.filespace_service.write_bytes_to_dir")
    @patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url")
    def test_always_saves_to_filespace(self, mock_signed_url, mock_write):
        mock_write.return_value = {
            "status": "ok",
            "path": "/charts/bar_20240101_120000.svg",
            "node_id": "test-node-id",
        }
        mock_signed_url.return_value = "https://example.com/signed/bar.svg"

        with mock_query_data([{"x": "A", "val": 10}]):
            result = execute_create_chart(
                self.agent,
                {
                    "type": "bar",
                    "query": "SELECT x, val FROM t",
                    "x": "x",
                    "y": "val",
                },
            )

            self.assertEqual(result["status"], "ok")
            # No data_uri or url - we return inline/attach hints (LLM uses path-based variables)
            self.assertNotIn("data_uri", result)
            self.assertNotIn("url", result)
            self.assertIn("inline", result)
            self.assertIn("attach", result)
            self.assertIn("file", result)
            # Variable name is the path wrapped in guillemets
            self.assertIn(result["file"], result["inline"])
            mock_write.assert_called_once()
            mock_signed_url.assert_called_once()

    def test_build_chart_save_path_is_unique_within_same_second(self):
        with patch("api.agent.tools.create_chart.datetime") as mock_datetime, \
                patch("api.agent.tools.create_chart.uuid.uuid4") as mock_uuid4:
            mock_datetime.now.return_value = datetime(2026, 2, 8, 17, 53, 56)
            mock_uuid4.side_effect = [
                SimpleNamespace(hex="a1b2c3d4feedbeef"),
                SimpleNamespace(hex="e5f60718cafebabe"),
            ]

            first_path = _build_chart_save_path("bar")
            second_path = _build_chart_save_path("bar")

        self.assertTrue(first_path.startswith("/charts/bar_20260208_175356_"))
        self.assertTrue(second_path.startswith("/charts/bar_20260208_175356_"))
        self.assertNotEqual(first_path, second_path)


@tag("batch_agent_tools")
class MaxLabelLenTests(TestCase):
    """Unit tests for _max_label_len helper."""

    def test_empty_list(self):
        self.assertEqual(_max_label_len([]), 0)

    def test_all_none(self):
        self.assertEqual(_max_label_len([None, None]), 0)

    def test_short_strings(self):
        self.assertEqual(_max_label_len(["Jan", "Feb", "Mar"]), 3)

    def test_mixed_types_converted_to_str(self):
        self.assertEqual(_max_label_len([1, 10, 100]), 3)

    def test_long_label_dominates(self):
        labels = ["A", "B", "Very Long Label Name Here"]
        self.assertEqual(_max_label_len(labels), len("Very Long Label Name Here"))

    def test_none_values_skipped(self):
        labels = [None, "Short", "A Longer Label"]
        self.assertEqual(_max_label_len(labels), len("A Longer Label"))


@tag("batch_agent_tools")
class ShouldSwapToHorizontalTests(TestCase):
    """Unit tests for _should_swap_to_horizontal."""

    def test_non_bar_type_never_swaps(self):
        long_labels = ["A Very Long Label Name Here"] * 3
        for chart_type in ("horizontal_bar", "line", "area", "scatter", "pie", "donut"):
            self.assertFalse(
                _should_swap_to_horizontal(chart_type, long_labels),
                f"Expected no swap for chart_type={chart_type!r}",
            )

    def test_bar_with_short_labels_does_not_swap(self):
        short_labels = ["Jan", "Feb", "Mar"]
        self.assertFalse(_should_swap_to_horizontal("bar", short_labels))

    def test_bar_with_labels_exactly_at_char_threshold_does_not_swap(self):
        labels = ["x" * _LONG_LABEL_CHAR_THRESHOLD]
        self.assertFalse(_should_swap_to_horizontal("bar", labels))

    def test_bar_with_labels_one_over_char_threshold_swaps(self):
        labels = ["x" * (_LONG_LABEL_CHAR_THRESHOLD + 1)]
        self.assertTrue(_should_swap_to_horizontal("bar", labels))

    def test_bar_with_many_categories_swaps(self):
        labels = [f"c{i}" for i in range(_MANY_CATEGORIES_THRESHOLD + 1)]
        self.assertTrue(_should_swap_to_horizontal("bar", labels))

    def test_bar_at_category_threshold_does_not_swap(self):
        labels = [f"c{i}" for i in range(_MANY_CATEGORIES_THRESHOLD)]
        self.assertFalse(_should_swap_to_horizontal("bar", labels))

    def test_bar_with_long_labels_swaps(self):
        labels = ["Very Long Category Name", "Another Long Name", "Short"]
        self.assertTrue(_should_swap_to_horizontal("bar", labels))

    def test_bar_with_empty_labels_does_not_swap(self):
        self.assertFalse(_should_swap_to_horizontal("bar", []))

    def test_bar_with_all_none_labels_does_not_swap(self):
        self.assertFalse(_should_swap_to_horizontal("bar", [None, None]))

    def test_bar_with_numeric_labels_below_threshold(self):
        self.assertFalse(_should_swap_to_horizontal("bar", [1, 2, 3]))


@tag("batch_agent_tools")
class ComputeHorizontalBarLeftMarginTests(TestCase):
    """Unit tests for _compute_horizontal_bar_left_margin."""

    def test_empty_labels_returns_minimum(self):
        self.assertAlmostEqual(_compute_horizontal_bar_left_margin([]), 0.15)

    def test_short_labels_clamp_to_minimum(self):
        self.assertAlmostEqual(_compute_horizontal_bar_left_margin(["Jan", "Feb"]), 0.15)

    def test_moderate_labels(self):
        labels = ["x" * 20]
        self.assertAlmostEqual(_compute_horizontal_bar_left_margin(labels), 0.20)

    def test_long_labels_clamp_to_maximum(self):
        labels = ["x" * 100]
        self.assertAlmostEqual(_compute_horizontal_bar_left_margin(labels), 0.45)

    def test_margin_increases_with_label_length(self):
        short_margin = _compute_horizontal_bar_left_margin(["Short"])
        long_margin = _compute_horizontal_bar_left_margin(["A Much Longer Label That Needs Space"])
        self.assertGreaterEqual(long_margin, short_margin)

    def test_result_within_valid_bounds(self):
        for length in range(0, 60, 5):
            labels = ["x" * length]
            margin = _compute_horizontal_bar_left_margin(labels)
            self.assertGreaterEqual(margin, 0.15)
            self.assertLessEqual(margin, 0.45)


@tag("batch_agent_tools")
class AxisSwapIntegrationTests(TestCase):
    """Integration tests: verify auto-swap and margin behaviour inside _generate_chart."""

    SHORT_DATA = [
        {"cat": "Jan", "val": 10},
        {"cat": "Feb", "val": 20},
        {"cat": "Mar", "val": 30},
    ]

    LONG_LABEL_DATA = [
        {"cat": "North America Operations", "val": 100},
        {"cat": "European Union Markets", "val": 200},
        {"cat": "Asia Pacific Region", "val": 150},
    ]

    MANY_CATEGORIES_DATA = [
        {"cat": f"Cat {i}", "val": i * 10}
        for i in range(_MANY_CATEGORIES_THRESHOLD + 1)
    ]

    def _make_svg(self, chart_type, data, x="cat", y="val", **kwargs):
        svg_bytes = _generate_chart(chart_type=chart_type, data=data, x=x, y=y, **kwargs)
        return svg_bytes.decode("utf-8")

    def test_bar_with_short_labels_renders_vertical_bars(self):
        svg = self._make_svg("bar", self.SHORT_DATA)
        self.assertIn("<svg", svg)

    def test_bar_with_long_labels_renders_horizontal_bars(self):
        svg = self._make_svg("bar", self.LONG_LABEL_DATA)
        self.assertIn("<svg", svg)
        self.assertIn("rect", svg)

    def test_bar_with_long_labels_svg_contains_label_text(self):
        """Long labels must appear in the SVG (not truncated)."""
        svg = self._make_svg("bar", self.LONG_LABEL_DATA)
        self.assertIn("North America", svg)

    def test_bar_with_many_categories_auto_swaps(self):
        svg = self._make_svg("bar", self.MANY_CATEGORIES_DATA)
        self.assertIn("<svg", svg)
        self.assertIn("rect", svg)

    def test_explicit_horizontal_bar_with_long_labels_preserves_labels(self):
        svg = self._make_svg("horizontal_bar", self.LONG_LABEL_DATA)
        self.assertIn("North America", svg)

    def test_horizontal_bar_with_short_labels_succeeds(self):
        svg = self._make_svg("horizontal_bar", self.SHORT_DATA)
        self.assertIn("<svg", svg)

    def test_line_chart_with_long_labels_is_not_swapped(self):
        """Non-bar chart types must not be affected by auto-swap logic."""
        svg = self._make_svg("line", self.LONG_LABEL_DATA)
        self.assertIn("<svg", svg)

    def test_generate_chart_returns_bytes(self):
        svg_bytes = _generate_chart(
            chart_type="bar", data=self.SHORT_DATA, x="cat", y="val",
        )
        self.assertIsInstance(svg_bytes, bytes)
        self.assertTrue(svg_bytes.startswith(b"<?xml") or svg_bytes.startswith(b"<svg"))

    @patch("api.agent.files.filespace_service.write_bytes_to_dir")
    @patch("api.agent.files.attachment_helpers.build_signed_filespace_download_url")
    def test_execute_create_chart_succeeds_with_long_labels(self, mock_signed_url, mock_write):
        """execute_create_chart with a bar chart and long labels succeeds via auto-swap."""
        mock_write.return_value = {"status": "ok", "path": "/charts/hbar.svg", "node_id": "id1"}
        mock_signed_url.return_value = "https://example.com/hbar.svg"

        User = get_user_model()
        user = User.objects.create_user(
            username="swap@example.com", email="swap@example.com", password="secret",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Swap Browser")
        agent = PersistentAgent.objects.create(
            user=user, name="Swap Agent", charter="test", browser_use_agent=browser_agent,
        )

        with mock_query_data(self.LONG_LABEL_DATA):
            result = execute_create_chart(agent, {
                "type": "bar",
                "query": "SELECT cat, val FROM t",
                "x": "cat",
                "y": "val",
                "title": "Long Label Bar",
            })

        self.assertEqual(result["status"], "ok")
        self.assertIn("inline", result)
