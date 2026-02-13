from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, tag

from api.agent.files.filesystem_prompt import get_agent_filesystem_prompt


@tag("batch_event_processing")
class FilesystemPromptTests(SimpleTestCase):
    def test_returns_no_filespace_message_when_agent_has_no_filespace(self):
        agent = SimpleNamespace(id="agent-1")
        with patch("api.agent.files.filesystem_prompt._get_default_filespace_id", return_value=None):
            text = get_agent_filesystem_prompt(agent)
        self.assertIn("No filespace configured", text)

    def test_lists_only_most_recent_thirty_files(self):
        agent = SimpleNamespace(id="agent-1")
        now = datetime(2026, 2, 13, tzinfo=timezone.utc)
        nodes = [
            SimpleNamespace(
                path=f"/reports/file_{idx}.txt",
                size_bytes=idx,
                mime_type="text/plain",
                updated_at=now,
            )
            for idx in range(35)
        ]

        qs = MagicMock()
        qs.only.return_value = qs
        qs.order_by.return_value = nodes

        with patch("api.agent.files.filesystem_prompt._get_default_filespace_id", return_value="fs-1"), patch(
            "api.agent.files.filesystem_prompt.AgentFsNode.objects.filter",
            return_value=qs,
        ):
            text = get_agent_filesystem_prompt(agent)

        lines = text.splitlines()
        self.assertIn("Most recent files in agent filespace", lines[0])
        self.assertEqual(len(lines), 31)
        self.assertIn("$[/reports/file_0.txt]", text)
        self.assertIn("$[/reports/file_29.txt]", text)
        self.assertNotIn("$[/reports/file_30.txt]", text)
        self.assertIn("updated 2026-02-13T00:00:00+00:00", text)
        qs.order_by.assert_called_once_with("-updated_at", "-created_at", "path")

    def test_returns_no_files_message_when_filespace_is_empty(self):
        agent = SimpleNamespace(id="agent-1")
        qs = MagicMock()
        qs.only.return_value = qs
        qs.order_by.return_value = []

        with patch("api.agent.files.filesystem_prompt._get_default_filespace_id", return_value="fs-1"), patch(
            "api.agent.files.filesystem_prompt.AgentFsNode.objects.filter",
            return_value=qs,
        ):
            text = get_agent_filesystem_prompt(agent)

        self.assertIn("No files available in the agent filesystem", text)
