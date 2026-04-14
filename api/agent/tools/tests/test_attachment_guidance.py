from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, tag

from api.agent.tools.create_csv import execute_create_csv
from api.agent.tools.create_file import execute_create_file, get_create_file_tool
from api.agent.tools.email_sender import get_send_email_tool


@tag("batch_attachment_guidance")
class AttachmentGuidanceTests(SimpleTestCase):
    agent = SimpleNamespace(id="agent-123")

    def _execute_create_file_query(
        self,
        params,
        *,
        rows=None,
        columns=None,
        query_error=None,
        write_path=None,
        node_id="node-file",
        signed_url=None,
    ):
        write_path = write_path or params["file_path"]
        signed_url = signed_url or f"https://example.com{write_path}"
        with (
            patch(
                "api.agent.tools.create_file.run_sqlite_select",
                return_value=(rows or [], columns, query_error),
            ) as run_sqlite_select_mock,
            patch(
                "api.agent.tools.create_file.write_bytes_to_dir",
                return_value={"status": "ok", "path": write_path, "node_id": node_id},
            ) as write_bytes_to_dir_mock,
            patch(
                "api.agent.tools.create_file.build_signed_filespace_download_url",
                return_value=signed_url,
            ) as build_signed_url_mock,
            patch("api.agent.tools.create_file.get_max_file_size", return_value=None) as get_max_file_size_mock,
            patch("api.agent.tools.create_file.set_agent_variable") as set_agent_variable_mock,
        ):
            result = execute_create_file(self.agent, params)
        return {
            "result": result,
            "run_sqlite_select_mock": run_sqlite_select_mock,
            "write_bytes_to_dir_mock": write_bytes_to_dir_mock,
            "build_signed_url_mock": build_signed_url_mock,
            "get_max_file_size_mock": get_max_file_size_mock,
            "set_agent_variable_mock": set_agent_variable_mock,
        }

    def test_send_email_tool_requires_exact_attachment_value(self):
        tool = get_send_email_tool()

        description = tool["function"]["parameters"]["properties"]["attachments"]["description"]

        self.assertIn("only way to create an actual email attachment", description)
        self.assertIn("exact $[/path] value", description)
        self.assertIn("`attach` field", description)
        self.assertIn("does not attach anything", description)

    def test_create_file_tool_schema_requires_content_or_query(self):
        tool = get_create_file_tool()
        parameters = tool["function"]["parameters"]

        self.assertEqual(parameters["required"], ["file_path", "mime_type"])
        self.assertEqual(
            parameters["oneOf"],
            [
                {"required": ["content"]},
                {"required": ["query"]},
            ],
        )

    @patch("api.agent.tools.create_file.set_agent_variable")
    @patch("api.agent.tools.create_file.get_max_file_size", return_value=None)
    @patch(
        "api.agent.tools.create_file.build_signed_filespace_download_url",
        return_value="https://example.com/exports/report.txt",
    )
    @patch(
        "api.agent.tools.create_file.write_bytes_to_dir",
        return_value={"status": "ok", "path": "/exports/report.txt", "node_id": "node-file"},
    )
    def test_create_file_returns_attachment_followup_message(
        self,
        write_bytes_to_dir_mock,
        build_signed_url_mock,
        get_max_file_size_mock,
        set_agent_variable_mock,
    ):
        result = execute_create_file(
            self.agent,
            {
                "content": "hello",
                "file_path": "/exports/report.txt",
                "mime_type": "text/plain",
            },
        )

        self.assertEqual(result["attach"], "$[/exports/report.txt]")
        self.assertIn("send_email.attachments", result["message"])
        self.assertIn("$[/exports/report.txt]", result["message"])
        self.assertIn("does not attach anything", result["message"])
        write_bytes_to_dir_mock.assert_called_once()
        build_signed_url_mock.assert_called_once_with(
            agent_id="agent-123",
            node_id="node-file",
        )
        get_max_file_size_mock.assert_called_once_with()
        set_agent_variable_mock.assert_called_once_with(
            "/exports/report.txt",
            "https://example.com/exports/report.txt",
        )

    @patch("api.agent.tools.create_csv.set_agent_variable")
    @patch("api.agent.tools.create_csv.get_max_file_size", return_value=None)
    @patch(
        "api.agent.tools.create_csv.build_signed_filespace_download_url",
        return_value="https://example.com/exports/report.csv",
    )
    @patch(
        "api.agent.tools.create_csv.write_bytes_to_dir",
        return_value={"status": "ok", "path": "/exports/report.csv", "node_id": "node-csv"},
    )
    def test_create_csv_returns_attachment_followup_message(
        self,
        write_bytes_to_dir_mock,
        build_signed_url_mock,
        get_max_file_size_mock,
        set_agent_variable_mock,
    ):
        result = execute_create_csv(
            self.agent,
            {
                "csv_text": "name\nGobii\n",
                "file_path": "/exports/report.csv",
            },
        )

        self.assertEqual(result["attach"], "$[/exports/report.csv]")
        self.assertIn("send_email.attachments", result["message"])
        self.assertIn("$[/exports/report.csv]", result["message"])
        self.assertIn("does not attach anything", result["message"])
        write_bytes_to_dir_mock.assert_called_once()
        build_signed_url_mock.assert_called_once_with(
            agent_id="agent-123",
            node_id="node-csv",
        )
        get_max_file_size_mock.assert_called_once_with()
        set_agent_variable_mock.assert_called_once_with(
            "/exports/report.csv",
            "https://example.com/exports/report.csv",
        )

    def test_create_file_query_writes_scalar_text_value_and_attachment_message(self):
        execution = self._execute_create_file_query(
            {
                "query": "SELECT transcript FROM report_data",
                "file_path": "/exports/transcript.txt",
                "mime_type": "text/plain",
            },
            rows=[{"transcript": "Title: Gobii\n\nfirst line\nsecond\tline"}],
            columns=["transcript"],
        )

        result = execution["result"]
        self.assertEqual(result["attach"], "$[/exports/transcript.txt]")
        self.assertIn("send_email.attachments", result["message"])
        self.assertEqual(result["inline"], "[Download]($[/exports/transcript.txt])")
        self.assertEqual(result["inline_html"], "<a href='$[/exports/transcript.txt]'>Download</a>")

        write_call = execution["write_bytes_to_dir_mock"].call_args.kwargs
        self.assertEqual(write_call["extension"], ".txt")
        self.assertEqual(write_call["mime_type"], "text/plain")
        self.assertEqual(
            write_call["content_bytes"].decode("utf-8"),
            "Title: Gobii\n\nfirst line\nsecond\tline",
        )

        execution["run_sqlite_select_mock"].assert_called_once_with("SELECT transcript FROM report_data")
        execution["build_signed_url_mock"].assert_called_once_with(
            agent_id="agent-123",
            node_id="node-file",
        )
        execution["get_max_file_size_mock"].assert_called_once_with()
        execution["set_agent_variable_mock"].assert_called_once_with(
            "/exports/transcript.txt",
            "https://example.com/exports/transcript.txt",
        )

    def test_create_file_query_writes_scalar_xml_value(self):
        execution = self._execute_create_file_query(
            {
                "query": "SELECT xml_body FROM report_data",
                "file_path": "/exports/report.xml",
                "mime_type": "application/xml",
            },
            rows=[{"xml_body": "<root>\n  <item>Gobii</item>\n</root>"}],
            columns=["xml_body"],
        )

        write_call = execution["write_bytes_to_dir_mock"].call_args.kwargs
        self.assertEqual(write_call["extension"], ".xml")
        self.assertEqual(write_call["mime_type"], "application/xml")
        self.assertEqual(
            write_call["content_bytes"].decode("utf-8"),
            "<root>\n  <item>Gobii</item>\n</root>",
        )

    def test_create_file_query_decodes_utf8_bytes(self):
        execution = self._execute_create_file_query(
            {
                "query": "SELECT transcript_blob FROM report_data",
                "file_path": "/exports/transcript.txt",
                "mime_type": "text/plain",
            },
            rows=[{"transcript_blob": b"Title: Gobii\n"}],
            columns=["transcript_blob"],
        )

        write_call = execution["write_bytes_to_dir_mock"].call_args.kwargs
        self.assertEqual(write_call["content_bytes"].decode("utf-8"), "Title: Gobii\n")

    def test_create_file_query_errors_for_non_utf8_bytes(self):
        execution = self._execute_create_file_query(
            {
                "query": "SELECT transcript_blob FROM report_data",
                "file_path": "/exports/transcript.txt",
                "mime_type": "text/plain",
            },
            rows=[{"transcript_blob": b"\xff\xfe"}],
            columns=["transcript_blob"],
        )

        self.assertEqual(
            execution["result"],
            {
                "status": "error",
                "message": "Query returned binary data that is not valid UTF-8 text.",
            },
        )
        execution["write_bytes_to_dir_mock"].assert_not_called()

    def test_create_file_errors_when_both_content_and_query_are_provided(self):
        result = execute_create_file(
            self.agent,
            {
                "content": "hello",
                "query": "SELECT 1 AS value",
                "file_path": "/exports/report.txt",
                "mime_type": "text/plain",
            },
        )

        self.assertEqual(result, {"status": "error", "message": "Use content OR query, not both."})

    def test_create_file_errors_when_neither_content_nor_query_is_provided(self):
        result = execute_create_file(
            self.agent,
            {
                "file_path": "/exports/report.txt",
                "mime_type": "text/plain",
            },
        )

        self.assertEqual(
            result,
            {"status": "error", "message": "Provide exactly one of content or query."},
        )

    def test_create_file_query_still_directs_csv_and_pdf_to_specialized_tools(self):
        csv_result = execute_create_file(
            self.agent,
            {
                "query": "SELECT 1 AS value",
                "file_path": "/exports/report.csv",
                "mime_type": "text/csv",
            },
        )
        pdf_result = execute_create_file(
            self.agent,
            {
                "query": "SELECT 1 AS value",
                "file_path": "/exports/report.pdf",
                "mime_type": "application/pdf",
            },
        )

        self.assertEqual(csv_result, {"status": "error", "message": "Use create_csv to write CSV files."})
        self.assertEqual(pdf_result, {"status": "error", "message": "Use create_pdf to generate PDFs from HTML."})

    def test_create_file_query_errors_when_query_returns_multiple_rows(self):
        execution = self._execute_create_file_query(
            {
                "query": "SELECT transcript FROM report_data",
                "file_path": "/exports/transcript.txt",
                "mime_type": "text/plain",
            },
            rows=[
                {"transcript": "first"},
                {"transcript": "second"},
            ],
            columns=["transcript"],
        )

        self.assertEqual(
            execution["result"],
            {
                "status": "error",
                "message": "Query must return exactly 1 row and 1 column for create_file query exports.",
            },
        )
        execution["write_bytes_to_dir_mock"].assert_not_called()

    def test_create_file_query_errors_when_query_returns_multiple_columns(self):
        execution = self._execute_create_file_query(
            {
                "query": "SELECT title, transcript FROM report_data",
                "file_path": "/exports/transcript.txt",
                "mime_type": "text/plain",
            },
            rows=[{"title": "Gobii", "transcript": "body"}],
            columns=["title", "transcript"],
        )

        self.assertEqual(
            execution["result"],
            {
                "status": "error",
                "message": "Query must return exactly 1 row and 1 column for create_file query exports.",
            },
        )
        execution["write_bytes_to_dir_mock"].assert_not_called()

    def test_create_file_query_errors_when_query_returns_no_rows(self):
        execution = self._execute_create_file_query(
            {
                "query": "SELECT transcript FROM report_data WHERE 1 = 0",
                "file_path": "/exports/transcript.txt",
                "mime_type": "text/plain",
            },
            rows=[],
            columns=["transcript"],
        )

        self.assertEqual(
            execution["result"],
            {
                "status": "error",
                "message": "Query must return exactly 1 row and 1 column for create_file query exports.",
            },
        )
        execution["write_bytes_to_dir_mock"].assert_not_called()

    def test_create_file_query_propagates_sqlite_error(self):
        execution = self._execute_create_file_query(
            {
                "query": "SELECT * FROM missing_table",
                "file_path": "/exports/transcript.txt",
                "mime_type": "text/plain",
            },
            query_error="Query failed: no such table: missing_table",
        )

        self.assertEqual(
            execution["result"],
            {
                "status": "error",
                "message": "Query failed: no such table: missing_table",
            },
        )
        execution["write_bytes_to_dir_mock"].assert_not_called()
