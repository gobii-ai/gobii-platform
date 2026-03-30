import json
import sqlite3
from decimal import Decimal
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from api.agent.files.filespace_service import write_bytes_to_dir
from api.agent.tools.custom_tools import (
    CUSTOM_TOOL_RESULT_MARKER,
    build_custom_tool_bridge_token,
    execute_create_custom_tool,
    execute_custom_tool,
    get_create_custom_tool_tool,
    get_custom_tools_prompt_summary,
)
from api.agent.tools.file_str_replace import execute_file_str_replace
from api.agent.tools.search_tools import search_tools
from api.agent.tools.sqlite_state import agent_sqlite_db
from api.agent.tools.tool_manager import enable_tools, get_available_tool_ids, get_enabled_tool_definitions
from api.models import (
    AgentFsNode,
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentCustomTool,
    PersistentAgentEnabledTool,
    PersistentAgentSecret,
    PersistentAgentStep,
    TaskCredit,
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

    def _create_env_var_secret(self, key: str, value: str) -> PersistentAgentSecret:
        secret = PersistentAgentSecret(
            agent=self.agent,
            secret_type=PersistentAgentSecret.SecretType.ENV_VAR,
            domain_pattern=PersistentAgentSecret.ENV_VAR_DOMAIN_SENTINEL,
            name=key,
            key=key,
            requested=False,
        )
        secret.set_value(value)
        secret.save()
        return secret

    @staticmethod
    def _build_runnable_tool_source(run_body: str, *, imports: str = "") -> str:
        sections = []
        if imports:
            sections.append(imports.rstrip())
        sections.append("from _gobii_ctx import main")
        sections.append("")
        sections.append(run_body.rstrip())
        sections.append("")
        sections.append("if __name__ == '__main__':")
        sections.append("    main(run)")
        sections.append("")
        return "\n".join(sections)

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

    def test_create_custom_tool_definition_mentions_direct_tool_and_sqlite_access(self):
        definition = get_create_custom_tool_tool()
        description = definition["function"]["description"]

        self.assertIn("PEP 723", description)
        self.assertIn("from _gobii_ctx import main", description)
        self.assertIn("ctx.call_tool", description)
        self.assertIn("custom_*", description)
        self.assertIn("ctx.sqlite_db_path", description)
        self.assertIn("os.environ", description)
        self.assertIn("HTTP_PROXY", description)
        self.assertIn("HTTPS_PROXY", description)
        self.assertIn("ALL_PROXY", description)
        self.assertIn("NO_PROXY", description)
        self.assertIn("SOCKS5", description)
        self.assertIn("requests[socks]", description)
        self.assertIn("httpx", description)
        self.assertIn("curl", description)
        self.assertIn("tool-to-tool calls", description)

    @patch("api.agent.tools.tool_manager.get_enabled_tool_limit", return_value=2)
    @patch("api.agent.tools.tool_manager.is_custom_tools_available_for_agent", return_value=True)
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_enable_tools_enforces_lru_for_custom_tools(
        self,
        mock_get_manager,
        _mock_custom_available,
        _mock_limit,
    ):
        mock_manager = MagicMock()
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager

        for name in ("alpha", "beta", "gamma"):
            PersistentAgentCustomTool.objects.create(
                agent=self.agent,
                name=name.title(),
                tool_name=f"custom_{name}",
                description=f"{name.title()} tool",
                source_path=f"/tools/{name}.py",
                parameters_schema={"type": "object", "properties": {}},
            )

        older = PersistentAgentEnabledTool.objects.create(agent=self.agent, tool_full_name="custom_alpha")
        newer = PersistentAgentEnabledTool.objects.create(agent=self.agent, tool_full_name="custom_beta")
        older.last_used_at = timezone.now() - timedelta(minutes=10)
        older.save(update_fields=["last_used_at"])
        newer.last_used_at = timezone.now()
        newer.save(update_fields=["last_used_at"])

        result = enable_tools(self.agent, ["custom_gamma"])

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["enabled"], ["custom_gamma"])
        self.assertEqual(result["evicted"], ["custom_alpha"])
        self.assertEqual(
            set(PersistentAgentEnabledTool.objects.filter(agent=self.agent).values_list("tool_full_name", flat=True)),
            {"custom_beta", "custom_gamma"},
        )

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.agent.tools.custom_tools._resolve_bridge_base_url", return_value="https://example.com")
    @patch("api.agent.tools.custom_tools.SandboxComputeService")
    def test_execute_custom_tool_runs_in_sandbox_and_parses_result(
        self,
        mock_service_cls,
        _mock_bridge_url,
        _mock_sandbox,
    ):
        source = self._build_runnable_tool_source(
            "def run(params, ctx):\n"
            "    return {'value': params.get('value', 0) + 1}\n"
        )
        write_result = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=source.encode("utf-8"),
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
        mock_service.run_custom_tool_command.return_value = {
            "status": "ok",
            "stdout": f"debug line\n{CUSTOM_TOOL_RESULT_MARKER}{{\"result\": {{\"value\": 2}}}}\n",
            "stderr": "",
        }
        mock_service_cls.return_value = mock_service

        result = execute_custom_tool(self.agent, tool, {"value": 1})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], {"value": 2})
        self.assertEqual(result["stdout"], "debug line")
        mock_service.run_custom_tool_command.assert_called_once()
        call = mock_service.run_custom_tool_command.call_args
        self.assertEqual(call.kwargs["timeout"], 123)
        self.assertIn("SANDBOX_CUSTOM_TOOL_PARAMS_B64", call.kwargs["env"])
        self.assertEqual(call.kwargs["env"]["SANDBOX_CUSTOM_TOOL_SOURCE_PATH"], "/tools/increment.py")
        self.assertEqual(call.kwargs["sqlite_env_key"], "SANDBOX_CUSTOM_TOOL_SQLITE_DB_PATH")
        self.assertTrue(call.kwargs["local_sqlite_db_path"])
        self.assertIn('SOURCE_EXEC_PATH="${SANDBOX_CUSTOM_TOOL_EXEC_SOURCE_PATH:-/workspace$SANDBOX_CUSTOM_TOOL_SOURCE_PATH}"', call.args[1])
        self.assertIn('uv run "$SOURCE_EXEC_PATH"', call.args[1])

    @patch("api.agent.tools.custom_tools._resolve_bridge_base_url", return_value="https://example.com")
    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True)
    @patch("api.services.sandbox_compute.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.services.sandbox_compute._select_proxy_for_session", return_value=None)
    def test_execute_custom_tool_can_write_directly_to_agent_sqlite(
        self,
        _mock_select_proxy,
        _mock_service_tool_enabled,
        _mock_service_enabled,
        _mock_tool_enabled,
        _mock_bridge_url,
    ):
        source = self._build_runnable_tool_source(
            "def run(params, ctx):\n"
            "    conn = sqlite3.connect(ctx.sqlite_db_path)\n"
            "    try:\n"
            "        conn.execute('CREATE TABLE IF NOT EXISTS custom_tool_rows (value TEXT NOT NULL)')\n"
            "        conn.execute('INSERT INTO custom_tool_rows(value) VALUES (?)', (params['value'],))\n"
            "        conn.commit()\n"
            "    finally:\n"
            "        conn.close()\n"
            "    return {'stored': params['value']}\n"
            ,
            imports="import sqlite3",
        )
        write_result = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=source.encode("utf-8"),
            extension=".py",
            mime_type="text/x-python",
            path="/tools/store_value.py",
            overwrite=True,
        )
        self.assertEqual(write_result.get("status"), "ok")

        tool = PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Store Value",
            tool_name="custom_store_value",
            description="Store a value in SQLite.",
            source_path="/tools/store_value.py",
            parameters_schema={"type": "object", "properties": {"value": {"type": "string"}}},
            timeout_seconds=30,
        )

        result = execute_custom_tool(self.agent, tool, {"value": "hello"})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], {"stored": "hello"})

        with agent_sqlite_db(str(self.agent.id)) as db_path:
            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute("SELECT value FROM custom_tool_rows ORDER BY rowid").fetchall()
            finally:
                conn.close()
        self.assertEqual(rows, [("hello",)])

    @patch("api.agent.tools.custom_tools._resolve_bridge_base_url", return_value="https://example.com")
    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True)
    @patch("api.services.sandbox_compute.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.services.sandbox_compute._select_proxy_for_session", return_value=None)
    def test_execute_custom_tool_can_read_env_var_secret_from_os_environ(
        self,
        _mock_select_proxy,
        _mock_service_tool_enabled,
        _mock_service_enabled,
        _mock_tool_enabled,
        _mock_bridge_url,
    ):
        self._create_env_var_secret("OPENAI_API_KEY", "from-secret")
        source = self._build_runnable_tool_source(
            "def run(params, ctx):\n"
            "    return {'value': os.environ.get(params['key'])}\n"
            ,
            imports="import os",
        )
        write_result = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=source.encode("utf-8"),
            extension=".py",
            mime_type="text/x-python",
            path="/tools/read_env.py",
            overwrite=True,
        )
        self.assertEqual(write_result.get("status"), "ok")

        tool = PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Read Env",
            tool_name="custom_read_env",
            description="Read a sandbox env var.",
            source_path="/tools/read_env.py",
            parameters_schema={"type": "object", "properties": {"key": {"type": "string"}}},
            timeout_seconds=30,
        )

        result = execute_custom_tool(self.agent, tool, {"key": "OPENAI_API_KEY"})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], {"value": "from-secret"})

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("api.agent.core.event_processing._ensure_credit_for_tool")
    def test_custom_tool_bridge_tracks_nested_tool_calls_like_normal_tools(self, mock_ensure_credit):
        now = timezone.now()
        credit = TaskCredit.objects.create(
            user=self.user,
            credits=Decimal("5.000"),
            credits_used=Decimal("0.000"),
            granted_date=now - timedelta(days=1),
            expiration_date=now + timedelta(days=1),
            additional_task=True,
        )
        completion = PersistentAgentCompletion.objects.create(agent=self.agent)
        parent_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            completion=completion,
            description="Outer custom tool step",
            credits_cost=Decimal("0.000"),
            task_credit=credit,
        )
        custom_tool = PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Wrapper",
            tool_name="custom_wrapper",
            description="Calls nested tools.",
            source_path="/tools/wrapper.py",
            parameters_schema={"type": "object", "properties": {}},
        )
        mock_ensure_credit.return_value = {
            "cost": Decimal("0.040"),
            "credit": credit,
        }

        token = build_custom_tool_bridge_token(
            self.agent,
            custom_tool,
            parent_step_id=str(parent_step.id),
        )

        response = self.client.post(
            reverse("api:custom-tool-bridge-execute"),
            data=json.dumps(
                {
                    "tool_name": "update_charter",
                    "params": {
                        "new_charter": "Tracked nested charter",
                        "will_continue_work": False,
                    },
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

        nested_steps = (
            PersistentAgentStep.objects.filter(agent=self.agent)
            .exclude(id=parent_step.id)
            .select_related("tool_call", "completion", "task_credit")
        )
        self.assertEqual(nested_steps.count(), 1)
        nested_step = nested_steps.get()
        self.assertEqual(nested_step.completion_id, completion.id)
        self.assertEqual(nested_step.credits_cost, Decimal("0.040"))
        self.assertEqual(nested_step.task_credit_id, credit.id)
        self.assertEqual(nested_step.tool_call.tool_name, "update_charter")
        self.assertEqual(nested_step.tool_call.status, "complete")
        self.assertIn("Charter updated successfully.", nested_step.tool_call.result)

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.charter, "Tracked nested charter")

    @patch("api.custom_tool_bridge.execute_tracked_runtime_tool_call")
    def test_custom_tool_bridge_allows_other_custom_tools(self, mock_execute_tracked_runtime):
        custom_tool = PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Wrapper",
            tool_name="custom_wrapper",
            description="Calls nested tools.",
            source_path="/tools/wrapper.py",
            parameters_schema={"type": "object", "properties": {}},
        )
        PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Target",
            tool_name="custom_target",
            description="Nested target tool.",
            source_path="/tools/target.py",
            parameters_schema={"type": "object", "properties": {}},
        )
        mock_execute_tracked_runtime.return_value = ({"status": "ok", "result": {"ok": True}}, None)

        token = build_custom_tool_bridge_token(self.agent, custom_tool)
        response = self.client.post(
            reverse("api:custom-tool-bridge-execute"),
            data=json.dumps({"tool_name": "custom_target", "params": {"value": 1}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        mock_execute_tracked_runtime.assert_called_once_with(
            self.agent,
            tool_name="custom_target",
            exec_params={"value": 1},
            parent_step=None,
        )

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
        self.assertIn("DEV LOOP:", summary)
        self.assertIn("file_str_replace", summary)
        self.assertIn("ctx.sqlite_db_path", summary)
        self.assertIn("os.environ", summary)
        self.assertIn("HTTP_PROXY", summary)
        self.assertIn("HTTPS_PROXY", summary)
        self.assertIn("ALL_PROXY", summary)
        self.assertIn("NO_PROXY", summary)
        self.assertIn("SOCKS5", summary)
        self.assertIn("requests[socks]", summary)
        self.assertIn("httpx", summary)
        self.assertIn("curl", summary)
        self.assertIn("ctx.call_tool()", summary)
        self.assertIn("internal bridge transport", summary)
        self.assertIn("sqlite3", summary)
        self.assertIn("Data sync to SQLite:", summary)
        self.assertIn("Checkpointed orchestration:", summary)
        self.assertIn("Safe dev loop:", summary)
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
