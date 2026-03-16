from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, tag

from api.agent.tools.create_csv import execute_create_csv
from api.agent.tools.create_file import execute_create_file
from api.agent.tools.email_sender import get_send_email_tool


@tag("batch_attachment_guidance")
class AttachmentGuidanceTests(SimpleTestCase):
    def test_send_email_tool_requires_exact_attachment_value(self):
        tool = get_send_email_tool()

        description = tool["function"]["parameters"]["properties"]["attachments"]["description"]

        self.assertIn("only way to create an actual email attachment", description)
        self.assertIn("exact $[/path] value", description)
        self.assertIn("`attach` field", description)
        self.assertIn("does not attach anything", description)

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
        agent = SimpleNamespace(id="agent-123")

        result = execute_create_file(
            agent,
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
        agent = SimpleNamespace(id="agent-123")

        result = execute_create_csv(
            agent,
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
