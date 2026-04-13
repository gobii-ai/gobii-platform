import base64
import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
import zipfile
from decimal import Decimal
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from api.agent.files.filespace_service import write_bytes_to_dir
from api.agent.tools.custom_tools import (
    CUSTOM_TOOL_RESULT_MARKER,
    _sync_workspace_source,
    build_custom_tool_bridge_token,
    execute_create_custom_tool,
    execute_custom_tool,
    get_create_custom_tool_tool,
    get_custom_tools_prompt_summary,
    normalize_custom_tool_name,
    normalize_custom_tool_parameters_schema,
)
from api.agent.tools.file_str_replace import execute_file_str_replace
from api.agent.tools.search_tools import search_tools
from api.agent.tools.sqlite_batch import execute_sqlite_batch
from api.agent.tools.sqlite_state import agent_sqlite_db
from api.agent.tools.tool_manager import (
    enable_tools,
    execute_enabled_tool,
    get_available_tool_ids,
    get_enabled_tool_definitions,
)
from api.services.system_skill_profiles import set_default_system_skill_profile, upsert_system_skill_profile_values
from api.services.sandbox_internal_paths import sandbox_workspace_root_for_agent
from api.services.sandbox_compute import SandboxComputeService, SandboxSessionUpdate, LocalSandboxBackend
from api.models import (
    AgentComputeSession,
    AgentFsNode,
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentCustomTool,
    PersistentAgentEnabledTool,
    PersistentAgentSecret,
    PersistentAgentStep,
    SystemSkillProfile,
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

    def _create_meta_ads_profile(self, *, profile_key: str = "default", is_default: bool = True) -> SystemSkillProfile:
        profile = SystemSkillProfile.objects.create(
            user=self.user,
            skill_key="meta_ads_platform",
            profile_key=profile_key,
            label="Meta Ads Profile",
            is_default=False,
        )
        upsert_system_skill_profile_values(
            profile,
            {
                "META_APP_ID": "app-123",
                "META_APP_SECRET": "secret-123",
                "META_SYSTEM_USER_TOKEN": "token-123",
                "META_AD_ACCOUNT_ID": "act_123",
                "META_API_VERSION": "v25.0",
            },
        )
        if is_default:
            set_default_system_skill_profile(profile)
        return profile

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

    @staticmethod
    def _write_local_wheel(wheel_path: str) -> None:
        dist_info = "helper_pkg-0.1.0.dist-info"
        files = {
            "helper_pkg/__init__.py": "def ping():\n    return 'pong'\n",
            f"{dist_info}/METADATA": (
                "Metadata-Version: 2.1\n"
                "Name: helper-pkg\n"
                "Version: 0.1.0\n"
                "Summary: Local helper package for uv smoke tests\n"
            ),
            f"{dist_info}/WHEEL": (
                "Wheel-Version: 1.0\n"
                "Generator: gobii-tests\n"
                "Root-Is-Purelib: true\n"
                "Tag: py3-none-any\n"
            ),
            f"{dist_info}/top_level.txt": "helper_pkg\n",
        }

        records = []
        with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative_path, text in files.items():
                payload = text.encode("utf-8")
                archive.writestr(relative_path, payload)
                digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode("ascii")
                records.append(f"{relative_path},sha256={digest},{len(payload)}")
            records.append(f"{dist_info}/RECORD,,")
            archive.writestr(f"{dist_info}/RECORD", "\n".join(records) + "\n")

    def test_normalize_custom_tool_name_is_idempotent_for_custom_prefix(self):
        normalized = normalize_custom_tool_name("custom_weather_tool")

        self.assertEqual(normalized, ("custom_weather_tool", "custom_weather_tool"))

    def test_normalize_custom_tool_parameters_schema_drops_missing_required_fields(self):
        schema = normalize_custom_tool_parameters_schema(
            {
                "type": "object",
                "properties": {
                    "result_id": {"type": "string"},
                    "file_path": {"type": "string"},
                },
                "required": ["result_id flocks", "file_path", "file_path"],
            }
        )

        self.assertEqual(
            schema,
            {
                "type": "object",
                "properties": {
                    "result_id": {"type": "string"},
                    "file_path": {"type": "string"},
                },
                "required": ["file_path"],
            },
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
                "source_code": self._build_runnable_tool_source(
                    "def run(params, ctx):\n"
                    "    return {'message': 'hi'}\n"
                ),
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

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.agent.tools.custom_tools.SandboxComputeService")
    @patch("api.agent.tools.tool_manager.enable_tools")
    def test_create_custom_tool_syncs_workspace_source_before_registering(
        self,
        mock_enable_tools,
        mock_service_cls,
        _mock_sandbox,
    ):
        source = self._build_runnable_tool_source(
            "def run(params, ctx):\n"
            "    return {'message': 'hi'}\n"
        )
        write_result = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=source.encode("utf-8"),
            extension=".py",
            mime_type="text/x-python",
            path="/tools/workspace_tool.py",
            overwrite=True,
        )
        self.assertEqual(write_result.get("status"), "ok")

        mock_enable_tools.return_value = {
            "status": "success",
            "enabled": ["custom_workspace_tool"],
            "already_enabled": [],
            "evicted": [],
            "invalid": [],
        }
        mock_service = MagicMock()
        mock_service._backend = object()
        mock_service._sync_workspace_push.return_value = {"status": "ok"}
        mock_service_cls.return_value = mock_service

        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "Workspace Tool",
                "description": "Loads source from workspace path.",
                "source_path": "/tools/workspace_tool.py",
                "parameters_schema": {"type": "object", "properties": {}},
            },
        )

        self.assertEqual(result["status"], "ok")
        mock_service._ensure_session.assert_called_once_with(self.agent, source="custom_tool_source_sync")
        mock_service._sync_workspace_push.assert_called_once()

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

    @patch("api.agent.tools.tool_manager.is_custom_tools_available_for_agent", return_value=True)
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_tool_manager_sanitizes_invalid_persisted_custom_tool_required_fields(
        self,
        mock_get_manager,
        _mock_custom_available,
    ):
        mock_manager = MagicMock()
        mock_manager.get_tools_for_agent.return_value = []
        mock_manager.get_enabled_tools_definitions.return_value = []
        mock_get_manager.return_value = mock_manager

        PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Transcript",
            tool_name="custom_process_youtube_transcript",
            description="Process a transcript.",
            source_path="/tools/transcript.py",
            parameters_schema={
                "type": "object",
                "properties": {
                    "result_id": {"type": "string"},
                    "file_path": {"type": "string"},
                },
                "required": ["result_id flocks", "file_path"],
            },
        )
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="custom_process_youtube_transcript",
        )

        definitions = get_enabled_tool_definitions(self.agent)
        tool_def = next(
            definition
            for definition in definitions
            if definition["function"]["name"] == "custom_process_youtube_transcript"
        )

        self.assertEqual(
            tool_def["function"]["parameters"]["required"],
            ["file_path"],
        )

    def test_create_custom_tool_definition_mentions_direct_tool_and_sqlite_access(self):
        definition = get_create_custom_tool_tool()
        description = definition["function"]["description"]
        properties = definition["function"]["parameters"]["properties"]

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
        self.assertIn("httpx[socks]", description)
        self.assertIn('dependencies = ["requests[socks]"]', description)
        self.assertIn("ctx.requests_proxies()", description)
        self.assertIn("ctx.proxy_url()", description)
        self.assertIn("curl", description)
        self.assertIn("tool-to-tool calls", description)
        self.assertIn("secret_type='env_var'", description)
        self.assertIn("domain-scoped credential", description)
        self.assertIn("not bare `requests`/`httpx`", description)
        self.assertIn("direct HTTPS tunneling", description)
        self.assertIn("write `/tools/my_tool.py`", description)
        self.assertIn("Latest workspace edits are synced automatically", description)
        self.assertIn("Small disposable tools are good", description)
        self.assertIn("Those triggers are not exhaustive", description)
        self.assertIn("err on the side of creating and using one", description)
        self.assertIn("Do not manually repeat MCP/tool/API calls", description)
        self.assertIn("Prefer patching the same file", description)
        self.assertIn("with ctx.sqlite() as db", description)
        self.assertIn("same durable agent SQLite DB that sqlite_batch reads", description)
        self.assertIn("Do not ATTACH sandbox file paths in sqlite_batch", description)
        self.assertEqual(
            properties["source_path"]["description"],
            "Workspace path to the Python source file, for example `/tools/my_tool.py`.",
        )
        self.assertNotIn("entrypoint", properties)

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_create_custom_tool_rejects_non_run_entrypoint(self, _mock_sandbox):
        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "Greeter",
                "description": "Return a greeting.",
                "source_path": "/tools/greeter.py",
                "source_code": self._build_runnable_tool_source(
                    "def run(params, ctx):\n"
                    "    return {'message': 'hi'}\n"
                ),
                "parameters_schema": {"type": "object", "properties": {}},
                "entrypoint": "other",
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("entrypoint is no longer configurable", result["message"])

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_create_custom_tool_rejects_source_without_main_run(self, _mock_sandbox):
        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "Greeter",
                "description": "Return a greeting.",
                "source_path": "/tools/greeter.py",
                "source_code": "from _gobii_ctx import main\n\ndef run(params, ctx):\n    return {'message': 'hi'}\n",
                "parameters_schema": {"type": "object", "properties": {}},
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("main(run)", result["message"])

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
        mock_service._sync_workspace_push.return_value = {"status": "ok"}
        mock_service.run_custom_tool_command.return_value = {
            "status": "ok",
            "stdout": f"debug line\n{CUSTOM_TOOL_RESULT_MARKER}{{\"result\": {{\"value\": 2}}}}\n",
            "stderr": "",
            "shared_sqlite_db": {
                "available": True,
                "same_db_as_sqlite_batch": True,
                "transport": "sandbox_sync",
                "sync_back": "ok",
                "deleted": False,
                "size_bytes": 123,
            },
        }
        mock_service_cls.return_value = mock_service

        result = execute_custom_tool(self.agent, tool, {"value": 1})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], {"value": 2})
        self.assertEqual(
            result["shared_sqlite_db"],
            {
                "available": True,
                "same_db_as_sqlite_batch": True,
                "transport": "sandbox_sync",
                "sync_back": "ok",
                "deleted": False,
                "size_bytes": 123,
            },
        )
        self.assertEqual(result["stdout"], "debug line")
        mock_service._ensure_session.assert_called_once_with(self.agent, source="custom_tool_source_sync")
        mock_service._sync_workspace_push.assert_called_once()
        mock_service.run_custom_tool_command.assert_called_once()
        call = mock_service.run_custom_tool_command.call_args
        self.assertEqual(call.kwargs["timeout"], 123)
        self.assertIn("SANDBOX_CUSTOM_TOOL_PARAMS_B64", call.kwargs["env"])
        self.assertEqual(call.kwargs["env"]["SANDBOX_CUSTOM_TOOL_SOURCE_PATH"], "/tools/increment.py")
        self.assertEqual(
            call.kwargs["env"]["SANDBOX_CUSTOM_TOOL_EXEC_SOURCE_PATH"],
            f"{sandbox_workspace_root_for_agent(self.agent.id)}/tools/increment.py",
        )
        self.assertEqual(call.kwargs["sqlite_env_key"], "SANDBOX_CUSTOM_TOOL_SQLITE_DB_PATH")
        self.assertTrue(call.kwargs["local_sqlite_db_path"])
        self.assertIn('RUNTIME_CACHE_ROOT="${SANDBOX_RUNTIME_CACHE_ROOT:-/tmp}"', call.args[1])
        self.assertIn('XDG_CACHE_HOME="${XDG_CACHE_HOME:-$RUNTIME_CACHE_ROOT/xdg-cache}"', call.args[1])
        self.assertIn('PIP_CACHE_DIR="${PIP_CACHE_DIR:-$RUNTIME_CACHE_ROOT/pip-cache}"', call.args[1])
        self.assertIn('UV_TOOL_DIR="${UV_TOOL_DIR:-$RUNTIME_CACHE_ROOT/uv-tools}"', call.args[1])
        self.assertIn('UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$RUNTIME_CACHE_ROOT/uv-project-env}"', call.args[1])
        self.assertIn('SOURCE_EXEC_PATH="${SANDBOX_CUSTOM_TOOL_EXEC_SOURCE_PATH:-.$SANDBOX_CUSTOM_TOOL_SOURCE_PATH}"', call.args[1])
        self.assertIn('UV_CACHE_DIR="${SANDBOX_CUSTOM_TOOL_UV_CACHE_DIR:-$RUNTIME_CACHE_ROOT/uv-cache}"', call.args[1])
        self.assertIn('UV_INSTALL_DIR="${SANDBOX_CUSTOM_TOOL_UV_INSTALL_DIR:-$RUNTIME_CACHE_ROOT/uv-bin}"', call.args[1])
        self.assertIn('mkdir -p "$UV_CACHE_DIR" "$UV_INSTALL_DIR" "$XDG_CACHE_HOME" "$PIP_CACHE_DIR" "$UV_TOOL_DIR" "$UV_PROJECT_ENVIRONMENT"', call.args[1])
        self.assertIn(
            'curl -LsSf https://astral.sh/uv/install.sh | UV_UNMANAGED_INSTALL="$UV_INSTALL_DIR" sh',
            call.args[1],
        )
        self.assertIn('export PATH="$UV_INSTALL_DIR:$PATH"', call.args[1])
        self.assertIn('uv run --no-project "$SOURCE_EXEC_PATH"', call.args[1])

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True)
    @patch("api.services.sandbox_compute._select_proxy_for_session", return_value=None)
    @patch("api.agent.tools.custom_tools.SandboxComputeService")
    def test_sync_workspace_source_recovers_from_transient_error_session(
        self,
        mock_service_cls,
        _mock_select_proxy,
        _mock_service_enabled,
        _mock_tool_enabled,
    ):
        class _RecoveringBackend:
            def __init__(self) -> None:
                self.deploy_calls = []
                self.sync_calls = []
                self._attempt = 0

            def deploy_or_resume(self, agent, session):
                self._attempt += 1
                self.deploy_calls.append(
                    {
                        "agent_id": str(agent.id),
                        "state": session.state,
                        "attempt": self._attempt,
                    }
                )
                if self._attempt == 1:
                    return SandboxSessionUpdate(
                        state=AgentComputeSession.State.ERROR,
                        pod_name="sandbox-agent-recovering",
                        namespace="gobii-prod",
                    )
                return SandboxSessionUpdate(
                    state=AgentComputeSession.State.RUNNING,
                    pod_name="sandbox-agent-recovered",
                    namespace="gobii-prod",
                )

            def sync_filespace(self, agent, session, *, direction, payload=None):
                self.sync_calls.append(
                    {
                        "agent_id": str(agent.id),
                        "direction": direction,
                        "payload": payload,
                    }
                )
                if direction == "pull":
                    return {"status": "ok", "files": [], "sync_cursor": None}
                return {"status": "ok", "changes": []}

        service = SandboxComputeService(backend=_RecoveringBackend())
        mock_service_cls.return_value = service

        sync_error = _sync_workspace_source(self.agent, "/tools/sync_intercom_waiting.py")

        self.assertIsNone(sync_error)
        self.assertEqual(len(service._backend.deploy_calls), 2)
        self.assertEqual(
            [call["direction"] for call in service._backend.sync_calls],
            ["pull", "push"],
        )

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.agent.tools.custom_tools._resolve_bridge_base_url", return_value="https://example.com")
    @patch("api.agent.tools.custom_tools.SandboxComputeService")
    def test_execute_custom_tool_uses_temp_uv_dirs_for_local_backend(
        self,
        mock_service_cls,
        _mock_bridge_url,
        _mock_sandbox,
    ):
        source = self._build_runnable_tool_source(
            "def run(params, ctx):\n"
            "    return {'ok': True}\n"
        )
        write_result = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=source.encode("utf-8"),
            extension=".py",
            mime_type="text/x-python",
            path="/tools/local_temp_dirs.py",
            overwrite=True,
        )
        self.assertEqual(write_result.get("status"), "ok")

        tool = PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Local Temp Dirs",
            tool_name="custom_local_temp_dirs",
            description="Use local temp uv dirs.",
            source_path="/tools/local_temp_dirs.py",
            parameters_schema={"type": "object", "properties": {}},
            timeout_seconds=30,
        )

        mock_service = MagicMock()
        mock_service._backend = LocalSandboxBackend()
        mock_service.run_custom_tool_command.return_value = {
            "status": "ok",
            "stdout": f"{CUSTOM_TOOL_RESULT_MARKER}{{\"result\": {{\"ok\": true}}}}\n",
            "stderr": "",
        }
        mock_service_cls.return_value = mock_service

        result = execute_custom_tool(self.agent, tool, {})

        self.assertEqual(result["status"], "ok")
        env = mock_service.run_custom_tool_command.call_args.kwargs["env"]
        self.assertIn("SANDBOX_RUNTIME_CACHE_ROOT", env)
        self.assertIn("SANDBOX_CUSTOM_TOOL_UV_CACHE_DIR", env)
        self.assertIn("SANDBOX_CUSTOM_TOOL_UV_INSTALL_DIR", env)
        self.assertIn("HOME", env)
        self.assertIn("TMPDIR", env)
        self.assertNotIn("/workspace", env["SANDBOX_CUSTOM_TOOL_UV_CACHE_DIR"])
        self.assertNotIn("/workspace", env["SANDBOX_CUSTOM_TOOL_UV_INSTALL_DIR"])
        self.assertTrue(env["SANDBOX_CUSTOM_TOOL_UV_CACHE_DIR"].startswith(env["SANDBOX_RUNTIME_CACHE_ROOT"]))
        self.assertTrue(env["SANDBOX_CUSTOM_TOOL_UV_INSTALL_DIR"].startswith(env["SANDBOX_RUNTIME_CACHE_ROOT"]))
        self.assertTrue(env["HOME"].startswith(env["SANDBOX_RUNTIME_CACHE_ROOT"]))
        self.assertTrue(env["TMPDIR"].startswith(env["SANDBOX_RUNTIME_CACHE_ROOT"]))

    @patch("api.agent.tools.custom_tools._resolve_bridge_base_url", return_value="https://example.com")
    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True)
    @patch("api.services.sandbox_compute.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.services.sandbox_compute._select_proxy_for_session", return_value=None)
    @patch("api.services.sandbox_compute._resolve_backend", return_value=LocalSandboxBackend())
    def test_execute_custom_tool_installs_local_pep723_dependency_with_uv(
        self,
        _mock_resolve_backend,
        _mock_select_proxy,
        _mock_service_tool_enabled,
        _mock_service_enabled,
        _mock_tool_enabled,
        _mock_bridge_url,
    ):
        with tempfile.TemporaryDirectory() as tmp_dir:
            wheel_dir = os.path.join(tmp_dir, "wheels")
            os.makedirs(wheel_dir, exist_ok=True)
            wheel_path = os.path.join(wheel_dir, "helper_pkg-0.1.0-py3-none-any.whl")
            self._write_local_wheel(wheel_path)

            source = "\n".join(
                [
                    "# /// script",
                    "# dependencies = [",
                    f"#   \"helper-pkg @ {Path(wheel_path).as_uri()}\",",
                    "# ]",
                    "# ///",
                    "from helper_pkg import ping",
                    "from _gobii_ctx import main",
                    "",
                    "def run(params, ctx):",
                    "    return {'value': ping()}",
                    "",
                    "if __name__ == '__main__':",
                    "    main(run)",
                    "",
                ]
            )
            write_result = write_bytes_to_dir(
                agent=self.agent,
                content_bytes=source.encode("utf-8"),
                extension=".py",
                mime_type="text/x-python",
                path="/tools/local_dep_tool.py",
                overwrite=True,
            )
            self.assertEqual(write_result.get("status"), "ok")

            tool = PersistentAgentCustomTool.objects.create(
                agent=self.agent,
                name="Local Dep Tool",
                tool_name="custom_local_dep_tool",
                description="Loads a local wheel through PEP 723 metadata.",
                source_path="/tools/local_dep_tool.py",
                parameters_schema={"type": "object", "properties": {}},
                timeout_seconds=60,
            )

            result = execute_custom_tool(self.agent, tool, {})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], {"value": "pong"})

    @patch("api.agent.tools.custom_tools._resolve_bridge_base_url", return_value="https://example.com")
    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True)
    @patch("api.services.sandbox_compute.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.services.sandbox_compute._select_proxy_for_session", return_value=None)
    @patch("api.services.sandbox_compute._resolve_backend", return_value=LocalSandboxBackend())
    def test_execute_custom_tool_can_write_directly_to_agent_sqlite(
        self,
        _mock_resolve_backend,
        _mock_select_proxy,
        _mock_service_tool_enabled,
        _mock_service_enabled,
        _mock_tool_enabled,
        _mock_bridge_url,
    ):
        source = self._build_runnable_tool_source(
            "def run(params, ctx):\n"
            "    with ctx.sqlite() as conn:\n"
            "        conn.execute('CREATE TABLE IF NOT EXISTS custom_tool_rows (value TEXT NOT NULL)')\n"
            "        conn.execute('INSERT INTO custom_tool_rows(value) VALUES (?)', (params['value'],))\n"
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

        with agent_sqlite_db(str(self.agent.id)) as db_path:
            result = execute_custom_tool(self.agent, tool, {"value": "hello"})

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["result"], {"stored": "hello"})

            batch_result = execute_sqlite_batch(
                self.agent,
                {"sql": "SELECT value FROM custom_tool_rows ORDER BY rowid"},
            )
            self.assertEqual(batch_result.get("status"), "ok")
            self.assertEqual(batch_result["results"][0]["result"], [{"value": "hello"}])

            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute("SELECT value FROM custom_tool_rows ORDER BY rowid").fetchall()
            finally:
                conn.close()
            self.assertEqual(rows, [("hello",)])

    @patch("api.agent.tools.custom_tools.get_sqlite_db_path", return_value=None)
    @patch("api.agent.tools.custom_tools._resolve_bridge_base_url", return_value="https://example.com")
    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True)
    @patch("api.services.sandbox_compute.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.services.sandbox_compute._select_proxy_for_session", return_value=None)
    @patch("api.services.sandbox_compute._resolve_backend", return_value=LocalSandboxBackend())
    def test_execute_enabled_tool_threads_current_sqlite_db_path_into_custom_tools(
        self,
        _mock_resolve_backend,
        _mock_select_proxy,
        _mock_service_tool_enabled,
        _mock_service_enabled,
        _mock_tool_enabled,
        _mock_bridge_url,
        _mock_current_db_path,
    ):
        source = self._build_runnable_tool_source(
            "def run(params, ctx):\n"
            "    with ctx.sqlite() as conn:\n"
            "        conn.execute('CREATE TABLE IF NOT EXISTS custom_tool_rows (value TEXT NOT NULL)')\n"
            "        conn.execute('INSERT INTO custom_tool_rows(value) VALUES (?)', (params['value'],))\n"
            "    return {'stored': params['value']}\n",
            imports="import sqlite3",
        )
        write_result = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=source.encode("utf-8"),
            extension=".py",
            mime_type="text/x-python",
            path="/tools/store_value_via_manager.py",
            overwrite=True,
        )
        self.assertEqual(write_result.get("status"), "ok")

        PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Store Value Via Manager",
            tool_name="custom_store_value_via_manager",
            description="Store a value in SQLite through execute_enabled_tool.",
            source_path="/tools/store_value_via_manager.py",
            parameters_schema={"type": "object", "properties": {"value": {"type": "string"}}},
            timeout_seconds=30,
        )
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="custom_store_value_via_manager",
        )
        value = f"hello-{uuid.uuid4().hex}"

        with agent_sqlite_db(str(self.agent.id)) as db_path:
            result = execute_enabled_tool(
                self.agent,
                "custom_store_value_via_manager",
                {"value": value},
                current_sqlite_db_path=db_path,
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["result"], {"stored": value})

            batch_result = execute_sqlite_batch(
                self.agent,
                {"sql": f"SELECT value FROM custom_tool_rows WHERE value = '{value}' ORDER BY rowid"},
            )
            self.assertEqual(batch_result.get("status"), "ok")
            self.assertEqual(batch_result["results"][0]["result"], [{"value": value}])

    @patch("api.agent.tools.custom_tools._resolve_bridge_base_url", return_value="https://example.com")
    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True)
    @patch("api.services.sandbox_compute.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.services.sandbox_compute._select_proxy_for_session", return_value=None)
    @patch("api.services.sandbox_compute._resolve_backend", return_value=LocalSandboxBackend())
    def test_execute_custom_tool_proxy_helpers_prefer_all_proxy(
        self,
        _mock_resolve_backend,
        _mock_select_proxy,
        _mock_service_tool_enabled,
        _mock_service_enabled,
        _mock_tool_enabled,
        _mock_bridge_url,
    ):
        source = self._build_runnable_tool_source(
            "def run(params, ctx):\n"
            "    return {'proxy_url': ctx.proxy_url(), 'requests_proxies': ctx.requests_proxies()}\n"
        )
        write_result = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=source.encode("utf-8"),
            extension=".py",
            mime_type="text/x-python",
            path="/tools/read_proxy.py",
            overwrite=True,
        )
        self.assertEqual(write_result.get("status"), "ok")

        tool = PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Read Proxy",
            tool_name="custom_read_proxy",
            description="Read the canonical sandbox proxy helpers.",
            source_path="/tools/read_proxy.py",
            parameters_schema={"type": "object", "properties": {}},
            timeout_seconds=30,
        )

        with patch.dict(
            os.environ,
            {
                "ALL_PROXY": "socks5://all-proxy.internal:1080",
                "HTTPS_PROXY": "http://https-proxy.internal:3128",
                "HTTP_PROXY": "http://http-proxy.internal:3128",
            },
            clear=False,
        ):
            result = execute_custom_tool(self.agent, tool, {})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["proxy_url"], "socks5://all-proxy.internal:1080")
        self.assertEqual(
            result["result"]["requests_proxies"],
            {
                "http": "socks5://all-proxy.internal:1080",
                "https": "socks5://all-proxy.internal:1080",
            },
        )

    @patch("api.agent.tools.custom_tools._resolve_bridge_base_url", return_value="https://example.com")
    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True)
    @patch("api.services.sandbox_compute.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.services.sandbox_compute._select_proxy_for_session", return_value=None)
    @patch("api.services.sandbox_compute._resolve_backend", return_value=LocalSandboxBackend())
    def test_execute_custom_tool_can_read_env_var_secret_from_os_environ(
        self,
        _mock_resolve_backend,
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

    @patch("api.agent.tools.meta_ads.requests.get")
    @patch("api.agent.core.event_processing._ensure_credit_for_tool")
    def test_custom_tool_bridge_allows_hidden_meta_ads_builtin(
        self,
        mock_ensure_credit,
        mock_meta_get,
    ):
        self._create_meta_ads_profile()
        custom_tool = PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Wrapper",
            tool_name="custom_wrapper",
            description="Calls nested tools.",
            source_path="/tools/wrapper.py",
            parameters_schema={"type": "object", "properties": {}},
        )
        response_obj = MagicMock()
        response_obj.status_code = 200
        response_obj.headers = {"x-app-usage": '{"call_count":1}'}
        response_obj.json.return_value = {
            "data": [
                {
                    "id": "act_123",
                    "account_id": "123",
                    "name": "Main Account",
                    "account_status": 1,
                }
            ]
        }
        response_obj.text = ""
        mock_meta_get.return_value = response_obj
        mock_ensure_credit.return_value = {"cost": Decimal("0.000"), "credit": None}

        token = build_custom_tool_bridge_token(self.agent, custom_tool)

        with agent_sqlite_db(str(self.agent.id)) as db_path:
            response = self.client.post(
                reverse("api:custom-tool-bridge-execute"),
                data=json.dumps({"tool_name": "meta_ads", "params": {"operation": "accounts"}}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["operation"], "accounts")
            self.assertEqual(payload["destination_table"], "meta_ads_raw")
            self.assertEqual(payload["rows_synced"], 1)
            self.assertTrue(
                PersistentAgentEnabledTool.objects.filter(agent=self.agent, tool_full_name="meta_ads").exists()
            )

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT operation, profile_key, entity_level, entity_id, entity_name FROM meta_ads_raw"
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row, ("accounts", "default", "account", "act_123", "Main Account"))

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
        self.assertIn("Default mode for repetitive or bulk work", summary)
        self.assertIn("Those triggers are not exhaustive", summary)
        self.assertIn("err on the side of creating and using one", summary)
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
        self.assertIn("httpx[socks]", summary)
        self.assertIn('dependencies = ["requests[socks]"]', summary)
        self.assertIn("ctx.requests_proxies()", summary)
        self.assertIn("ctx.proxy_url()", summary)
        self.assertIn("curl", summary)
        self.assertIn("ctx.call_tool()", summary)
        self.assertIn("internal bridge transport", summary)
        self.assertIn("secret_type='env_var'", summary)
        self.assertIn("domain-scoped credential", summary)
        self.assertIn("not bare `requests`/`httpx`", summary)
        self.assertIn("direct HTTPS tunneling", summary)
        self.assertIn("sqlite3", summary)
        self.assertIn("write `/tools/my_tool.py`", summary)
        self.assertIn("Latest workspace edits are synced automatically", summary)
        self.assertIn("A short one-off tool is usually better than manual repetition", summary)
        self.assertIn("Immediate triggers:", summary)
        self.assertIn("bulk INSERT/UPDATE/UPSERT work", summary)
        self.assertIn("with ctx.sqlite() as db", summary)
        self.assertIn("sqlite_batch already reads that same durable DB directly", summary)
        self.assertIn("Do not ATTACH sandbox file paths in sqlite_batch", summary)
        self.assertIn("Prefer patching the same file", summary)
        self.assertIn("Start with a small sample/limit", summary)
        self.assertIn("ANTI-PATTERNS:", summary)
        self.assertIn("Bulk MCP fan-out:", summary)
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
