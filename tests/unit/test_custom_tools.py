from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.files.filespace_service import write_bytes_to_dir
from api.agent.tools.custom_tools import (
    CUSTOM_TOOL_RESULT_MARKER,
    execute_create_custom_tool,
    execute_custom_tool,
    get_custom_tools_prompt_summary,
)
from api.agent.tools.file_str_replace import execute_file_str_replace
from api.agent.tools.search_tools import search_tools
from api.agent.tools.tool_manager import get_available_tool_ids, get_enabled_tool_definitions
from api.models import (
    AgentFsNode,
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentCustomTool,
    PersistentAgentEnabledTool,
    UserQuota,
)


@tag("batch_agent_tools")
class CustomToolsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="custom-tools@example.com",
            email="custom-tools@example.com",
            password="secret",
        )
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100
        quota.save(update_fields=["agent_limit"])

        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Custom Tools Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Custom Tools Agent",
            charter="Build sandbox tools",
            browser_use_agent=cls.browser_agent,
        )

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.agent.tools.tool_manager.enable_tools")
    def test_create_custom_tool_writes_source_and_enables_tool(self, mock_enable_tools, _mock_sandbox):
        mock_enable_tools.return_value = {
            "status": "success",
            "enabled": ["custom_greeter"],
            "already_enabled": [],
            "evicted": [],
            "invalid": [],
        }

        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "Greeter",
                "description": "Return a greeting.",
                "source_path": "/tools/greeter.py",
                "source_code": "def run(params, ctx):\n    return {'message': 'hi'}\n",
                "parameters_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                    },
                },
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["tool_name"], "custom_greeter")
        mock_enable_tools.assert_called_once_with(self.agent, ["custom_greeter"])

        tool = PersistentAgentCustomTool.objects.get(agent=self.agent, tool_name="custom_greeter")
        self.assertEqual(tool.source_path, "/tools/greeter.py")
        self.assertEqual(tool.entrypoint, "run")
        self.assertEqual(tool.timeout_seconds, 300)

        node = AgentFsNode.objects.get(path="/tools/greeter.py")
        with node.content.open("rb") as handle:
            self.assertIn(b"def run", handle.read())

    def test_file_str_replace_updates_source_and_touches_custom_tool(self):
        write_result = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=b"def run(params, ctx):\n    return {'message': 'hi'}\n",
            extension=".py",
            mime_type="text/x-python",
            path="/tools/greeter.py",
            overwrite=True,
        )
        self.assertEqual(write_result.get("status"), "ok")

        tool = PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Greeter",
            tool_name="custom_greeter",
            description="Return a greeting.",
            source_path="/tools/greeter.py",
            parameters_schema={"type": "object", "properties": {}},
        )
        later = tool.updated_at + timedelta(minutes=5)

        with patch("api.agent.tools.file_str_replace.timezone.now", return_value=later):
            result = execute_file_str_replace(
                self.agent,
                {
                    "path": "/tools/greeter.py",
                    "old_text": "'hi'",
                    "new_text": "'hello'",
                    "expected_replacements": 1,
                },
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["replacements"], 1)

        node = AgentFsNode.objects.get(path="/tools/greeter.py")
        with node.content.open("rb") as handle:
            self.assertIn(b"hello", handle.read())

        tool.refresh_from_db()
        self.assertEqual(tool.updated_at, later)

    @patch("api.agent.tools.tool_manager.is_custom_tools_available_for_agent", return_value=True)
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_tool_manager_surfaces_custom_tools(self, mock_get_manager, _mock_custom_available):
        mock_manager = MagicMock()
        mock_manager.get_tools_for_agent.return_value = []
        mock_manager.get_enabled_tools_definitions.return_value = []
        mock_get_manager.return_value = mock_manager

        PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Greeter",
            tool_name="custom_greeter",
            description="Return a greeting.",
            source_path="/tools/greeter.py",
            parameters_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
        )
        PersistentAgentEnabledTool.objects.create(agent=self.agent, tool_full_name="custom_greeter")

        available = get_available_tool_ids(self.agent)
        self.assertIn("custom_greeter", available)

        definitions = get_enabled_tool_definitions(self.agent)
        tool_names = [definition["function"]["name"] for definition in definitions]
        self.assertIn("custom_greeter", tool_names)

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.agent.tools.custom_tools._resolve_bridge_base_url", return_value="https://example.com")
    @patch("api.agent.tools.custom_tools.SandboxComputeService")
    def test_execute_custom_tool_runs_in_sandbox_and_parses_result(
        self,
        mock_service_cls,
        _mock_bridge_url,
        _mock_sandbox,
    ):
        write_result = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=b"def run(params, ctx):\n    return {'value': params.get('value', 0) + 1}\n",
            extension=".py",
            mime_type="text/x-python",
            path="/tools/increment.py",
            overwrite=True,
        )
        self.assertEqual(write_result.get("status"), "ok")

        tool = PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Increment",
            tool_name="custom_increment",
            description="Increment a value.",
            source_path="/tools/increment.py",
            parameters_schema={"type": "object", "properties": {"value": {"type": "integer"}}},
            timeout_seconds=123,
        )

        mock_service = MagicMock()
        mock_service.run_command.return_value = {
            "status": "ok",
            "stdout": f"debug line\n{CUSTOM_TOOL_RESULT_MARKER}{{\"result\": {{\"value\": 2}}}}\n",
            "stderr": "",
        }
        mock_service_cls.return_value = mock_service

        result = execute_custom_tool(self.agent, tool, {"value": 1})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], {"value": 2})
        self.assertEqual(result["stdout"], "debug line")
        mock_service.run_command.assert_called_once()
        call = mock_service.run_command.call_args
        self.assertEqual(call.kwargs["timeout"], 123)
        self.assertIn("SANDBOX_CUSTOM_TOOL_SOURCE_B64", call.kwargs["env"])
        self.assertEqual(call.kwargs["env"]["SANDBOX_CUSTOM_TOOL_SOURCE_PATH"], "/tools/increment.py")

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_prompt_summary_reports_saved_and_enabled_custom_tools(self, _mock_sandbox):
        PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Alpha",
            tool_name="custom_alpha",
            description="Alpha tool",
            source_path="/tools/alpha.py",
            parameters_schema={"type": "object", "properties": {}},
        )
        PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Beta",
            tool_name="custom_beta",
            description="Beta tool",
            source_path="/tools/beta.py",
            parameters_schema={"type": "object", "properties": {}},
        )
        PersistentAgentEnabledTool.objects.create(agent=self.agent, tool_full_name="custom_beta")

        summary = get_custom_tools_prompt_summary(self.agent, recent_limit=2)

        self.assertIn("Custom tools: 2 saved, 1 enabled.", summary)
        self.assertIn("Dev loop:", summary)
        self.assertIn("file_str_replace", summary)
        self.assertIn("custom_alpha", summary)
        self.assertIn("custom_beta", summary)

    @patch("api.agent.tools.search_tools.get_llm_config_with_failover", return_value=[("openai", "gpt-4o-mini", {})])
    @patch("api.agent.tools.search_tools.run_completion")
    @patch("api.agent.tools.search_tools.get_mcp_manager")
    @patch("api.agent.tools.tool_manager.is_custom_tools_available_for_agent", return_value=True)
    def test_search_tools_includes_custom_tool_catalog(
        self,
        _mock_custom_available,
        mock_get_manager,
        mock_run_completion,
        _mock_get_config,
    ):
        PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Greeter",
            tool_name="custom_greeter",
            description="Return a greeting.",
            source_path="/tools/greeter.py",
            parameters_schema={"type": "object", "properties": {}},
        )

        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager

        message = MagicMock()
        message.content = "No relevant tools."
        setattr(message, "tool_calls", [])
        choice = MagicMock()
        choice.message = message
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "greet someone")

        self.assertEqual(result["status"], "success")
        user_message = mock_run_completion.call_args.kwargs["messages"][1]["content"]
        self.assertIn("custom_greeter", user_message)
        self.assertIn("Return a greeting.", user_message)
