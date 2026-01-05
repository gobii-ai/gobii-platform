import base64
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.tools.create_chart import execute_create_chart, get_create_chart_tool
from api.models import BrowserUseAgent, PersistentAgent


def mock_query_data(data):
    """Helper to create a mock for _execute_query_for_data that returns given data."""
    return patch(
        "api.agent.tools.create_chart._execute_query_for_data",
        return_value=(data, None),
    )


def mock_filespace():
    """Helper to mock filespace operations."""
    return patch.multiple(
        "api.agent.tools.create_chart",
        write_bytes_to_dir=MagicMock(return_value={
            "status": "ok",
            "path": "/charts/test.svg",
            "node_id": "test-node-id",
        }),
        build_signed_filespace_download_url=MagicMock(
            return_value="https://example.com/signed/test.svg"
        ),
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
            return_value=([], None),
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
            return_value=([], "Query failed: no such table"),
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
            self.assertIn("url", result)
            self.assertEqual(result["url"], "https://example.com/bar.svg")
            self.assertIn("path", result)
            self.assertIn("node_id", result)
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
            self.assertIn("url", result)

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
            self.assertIn("url", result)

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
            self.assertIn("url", result)

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
            self.assertIn("url", result)

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
            self.assertIn("url", result)

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
            self.assertIn("url", result)

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
            self.assertIn("url", result)

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
            self.assertIn("url", result)

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
            self.assertIn("url", result)

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
            # No data_uri - we return url instead (doesn't flood LLM context)
            self.assertNotIn("data_uri", result)
            self.assertIn("url", result)
            self.assertEqual(result["url"], "https://example.com/signed/bar.svg")
            self.assertIn("path", result)
            self.assertIn("node_id", result)
            mock_write.assert_called_once()
            mock_signed_url.assert_called_once()
