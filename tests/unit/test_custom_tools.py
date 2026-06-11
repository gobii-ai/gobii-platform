import base64
import contextlib
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import types
import uuid
import zipfile
from decimal import Decimal
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from api.agent.files.filespace_service import write_bytes_to_dir
from api.agent.system_skills.defaults import CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL
from api.agent.tools.custom_tools import (
    CUSTOM_TOOL_RESULT_MARKER,
    _GOBII_CTX_MODULE,
    _sync_workspace_source,
    build_custom_tool_bridge_token,
    execute_create_custom_tool,
    execute_custom_tool,
    format_custom_tools_state_for_prompt,
    get_create_custom_tool_tool,
    normalize_custom_tool_name,
    normalize_custom_tool_parameters_schema,
    validate_custom_tool_source_code,
)
from api.agent.tools.custom_tool_names import CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY
from api.agent.tools.apply_patch import execute_apply_patch
from api.agent.tools.create_file import get_create_file_tool
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
from api.services.sandbox_kubernetes import KubernetesSandboxBackend
from api.services.sandbox_compute import SandboxComputeService, SandboxSessionUpdate, LocalSandboxBackend
from api.utils.json_schema import sanitize_tool_parameters_schema_for_llm
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
    PersistentAgentSystemStep,
    PersistentAgentSystemSkillState,
    PersistentAgentToolCall,
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
    def _run_bootstrap_snippet(source: str) -> str:
        module = types.ModuleType("_gobii_ctx")
        exec(_GOBII_CTX_MODULE, module.__dict__)
        prior_module = sys.modules.get("_gobii_ctx")
        sys.modules["_gobii_ctx"] = module
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                exec(source, {})
        finally:
            if prior_module is None:
                sys.modules.pop("_gobii_ctx", None)
            else:
                sys.modules["_gobii_ctx"] = prior_module
        return stdout.getvalue()

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

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_custom_tool_guidance_requires_helpful_side_effect_results(self, _mock_sandbox):
        create_tool_description = get_create_custom_tool_tool()["function"]["description"]
        skill_instructions = CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL.prompt_instructions

        for text in (
            "source_path='/tools/my_tool.py'",
            "source_code",
            "do not pass only `source_path` unless you already wrote that file",
            "if rejected, fix every listed issue and retry create_custom_tool, not create_file",
            "Before the first call, verify",
            "Exact import `from _gobii_ctx import main`",
            "`parameters_schema.required` requires real source inputs",
            "imports cover referenced modules, e.g. `import sqlite3` before `sqlite3.Row`",
            "SQLite: `with ctx.sqlite() as db:`, never `db = ctx.sqlite()`",
            "batch/limit tools return `remaining_work`/`next_cursor`",
            "exact final line `if __name__ == '__main__': main(run)`",
            "file_path='/tools/my_tool.py'",
            "db.row_factory = sqlite3.Row",
            "after the block exits the DB is closed",
            "before SELECT/fetchall",
            "datetime.now(timezone.utc)",
            "not datetime.timezone",
            "Every success or error return dict should include `next_action`",
            "do_not_repeat_manually=true",
            "source-code next_action text exactly",
            "what changed or which outputs are ready",
            "remaining work",
            "verification guidance",
            "direct_post_urls",
            "scrape_ready_urls",
            "accepted ready-to-use values",
            "require source params like `urls`, `domains`, `candidates`, `source_table`, or `input_table`",
            "Do not repeat manually; verify read-only",
        ):
            self.assertIn(text, create_tool_description)

        for text in (
            "exact import `from _gobii_ctx import main`",
            "exact final line `if __name__ == '__main__': main(run)`",
            "imports cover referenced modules, e.g. `import sqlite3` before `sqlite3.Row`",
            "`parameters_schema.required` requires real source inputs",
            "SQLite: `with ctx.sqlite() as db:`, never `db = ctx.sqlite()`",
            "batch/limit tools return `remaining_work`/`next_cursor`",
            "Do not pass only `source_path` unless that file already exists",
            "If rejected, fix every listed issue and retry create_custom_tool, not create_file",
            "db.row_factory = sqlite3.Row",
            "later changes do not convert tuples",
            "not `row.get(...)`",
            "after the block exits the DB is closed",
            "target resource ids/names",
            "source filters/date ranges",
            "Never invoke a custom tool with empty params",
            "validation/dedupe",
            "Every success or error return dict should include `next_action`",
            "do_not_repeat_manually=true",
            "source-code next_action text exactly",
            "what changed or which outputs are ready",
            "remaining work or cursor",
            "verification guidance",
            "direct_post_urls",
            "scrape_ready_urls",
            "accepted ready-to-use values",
            "Do not repeat manually; verify read-only",
        ):
            self.assertIn(text, skill_instructions)

    def test_side_effect_source_requires_manual_replay_prevention(self):
        source = self._build_runnable_tool_source(
            """
def run(params, ctx):
    return {
        "status": "ok",
        "summary": "Appended 10 rows to Sheets.",
        "side_effects": [{"target": "Sheet1", "rows_written": 10}],
        "next_action": "Verify the sheet.",
    }
"""
        )

        error = validate_custom_tool_source_code(source, "/tools/sheets_sync.py")

        self.assertIn("manual replay prevention", error or "")

        valid_source = source.replace(
            '"next_action": "Verify the sheet.",',
            '"do_not_repeat_manually": True,\n'
            '        "next_action": "Do not repeat manually; verify read-only; do not append/add/update again.",',
        )
        self.assertIsNone(validate_custom_tool_source_code(valid_source, "/tools/sheets_sync.py"))

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_create_custom_tool_saves_invalid_source_for_patch_loop(self, _mock_sandbox):
        source = self._build_runnable_tool_source(
            """
def run(params, ctx):
    return {
        "status": "ok",
        "summary": "Appended 10 rows to Sheets.",
        "side_effects": [{"target": "Sheet1", "rows_written": 10}],
        "next_action": "Verify the sheet.",
    }
"""
        )

        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "Patchable Sheets Sync",
                "description": "Append rows to Sheets.",
                "source_path": "/tools/patchable_sheets_sync.py",
                "source_code": source,
                "parameters_schema": {"type": "object", "properties": {}},
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["source_path"], "/tools/patchable_sheets_sync.py")
        self.assertIn("manual replay prevention", result["message"])
        self.assertFalse(PersistentAgentCustomTool.objects.filter(agent=self.agent, tool_name="custom_patchable_sheets_sync").exists())
        node = AgentFsNode.objects.get(path="/tools/patchable_sheets_sync.py")
        with node.content.open("rb") as handle:
            self.assertIn(b"Appended 10 rows", handle.read())

    def test_normalize_custom_tool_parameters_schema_synthesizes_missing_required_fields(self):
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
                    "result_id flocks": {
                        "type": "string",
                        "description": (
                            "Required parameter inferred from schema.required because no explicit property definition was provided."
                        ),
                    },
                },
                "required": ["result_id flocks", "file_path"],
            },
        )

    def test_normalize_custom_tool_parameters_schema_repairs_quoted_properties_key(self):
        schema = normalize_custom_tool_parameters_schema(
            {
                "type": "object",
                "required": ["spreadsheet_id", "worksheet_name"],
                "properties": {},
                '"properties"': {
                    '"spreadsheet_id"': {
                        "type": "string",
                        '"description': "The ID of the Google Sheet.",
                    },
                    '"worksheet_name"': {
                        "type": "string",
                        '"description': "The worksheet name.",
                    },
                },
            }
        )

        self.assertEqual(
            schema,
            {
                "type": "object",
                "required": ["spreadsheet_id", "worksheet_name"],
                "properties": {
                    "spreadsheet_id": {
                        "type": "string",
                        "description": "The ID of the Google Sheet.",
                    },
                    "worksheet_name": {
                        "type": "string",
                        "description": "The worksheet name.",
                    },
                },
            },
        )

    def test_normalize_custom_tool_parameters_schema_adds_missing_array_items(self):
        schema = normalize_custom_tool_parameters_schema(
            {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "object",
                        "properties": {
                            "params": {"type": "array"},
                        },
                    },
                },
            }
        )

        self.assertEqual(
            schema["properties"]["sql"]["properties"]["params"]["items"],
            {"type": "string"},
        )

    def test_normalize_custom_tool_parameters_schema_synthesizes_properties_when_missing(self):
        schema = normalize_custom_tool_parameters_schema(
            {
                "type": "object",
                "required": ["spreadsheet_id"],
            }
        )

        self.assertEqual(
            schema["properties"]["spreadsheet_id"],
            {
                "type": "string",
                "description": (
                    "Required parameter inferred from schema.required because no explicit property definition was provided."
                ),
            },
        )

    def test_normalize_custom_tool_parameters_schema_prefers_dict_on_key_collision(self):
        schema = normalize_custom_tool_parameters_schema(
            {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string"},
                },
                '"properties"': "malformed duplicate",
            }
        )

        self.assertEqual(
            schema["properties"],
            {
                "spreadsheet_id": {"type": "string"},
            },
        )

    def test_normalize_custom_tool_parameters_schema_preserves_boolean_property_schema(self):
        schema = normalize_custom_tool_parameters_schema(
            {
                "type": "object",
                "properties": {
                    "disabled": False,
                },
            }
        )

        self.assertIs(schema["properties"]["disabled"], False)

    def test_normalize_custom_tool_parameters_schema_canonicalizes_nested_schema_types(self):
        schema = normalize_custom_tool_parameters_schema(
            {
                "type": "OBJECT",
                "properties": {
                    "keywords": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                    "metadata": {
                        "type": "OBJECT",
                        "additionalProperties": {"type": "INTEGER"},
                        "default": {"type": "ARRAY"},
                    },
                    "selector": {
                        "oneOf": [{"type": "NUMBER"}, {"type": "NULL"}],
                        "anyOf": [{"type": "BOOLEAN"}],
                        "allOf": [
                            {
                                "type": "OBJECT",
                                "properties": {
                                    "count": {"type": "INTEGER"},
                                },
                            }
                        ],
                    },
                },
            }
        )

        self.assertEqual(
            schema,
            {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "metadata": {
                        "type": "object",
                        "additionalProperties": {"type": "integer"},
                        "default": {"type": "ARRAY"},
                    },
                    "selector": {
                        "oneOf": [{"type": "number"}, {"type": "null"}],
                        "anyOf": [{"type": "boolean"}],
                        "allOf": [
                            {
                                "type": "object",
                                "properties": {
                                    "count": {"type": "integer"},
                                },
                            }
                        ],
                    },
                },
                "required": [],
            },
        )

    def test_llm_schema_sanitizer_removes_provider_rejected_top_level_keywords(self):
        schema = sanitize_tool_parameters_schema_for_llm(
            get_create_file_tool()["function"]["parameters"]
        )

        self.assertEqual(schema["type"], "object")
        self.assertNotIn("oneOf", schema)

        nested_schema = {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "inactive"],
                    "oneOf": [{"type": "string"}],
                },
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "anyOf": [{"type": "string"}],
                            },
                        },
                    },
                },
            },
        }
        sanitized = sanitize_tool_parameters_schema_for_llm(nested_schema)
        self.assertNotIn("enum", sanitized["properties"]["status"])
        self.assertNotIn("oneOf", sanitized["properties"]["status"])
        self.assertNotIn("anyOf", sanitized["properties"]["items"]["items"]["properties"]["kind"])

    def test_persistent_agent_custom_tool_model_clean_canonicalizes_nested_schema_types(self):
        tool = PersistentAgentCustomTool(
            agent=self.agent,
            name="Pohl Searcher",
            tool_name="pohl_searcher",
            description="Search for keywords.",
            source_path=" tools/pohl_searcher.py ",
            parameters_schema={
                "type": "OBJECT",
                "required": ["keywords"],
                "properties": {
                    "keywords": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                },
            },
            timeout_seconds=300,
        )

        tool.full_clean()

        self.assertEqual(tool.tool_name, "custom_pohl_searcher")
        self.assertEqual(tool.source_path, "/tools/pohl_searcher.py")
        self.assertEqual(tool.entrypoint, "run")
        self.assertEqual(
            tool.parameters_schema,
            {
                "type": "object",
                "required": ["keywords"],
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        )

    def test_persistent_agent_custom_tool_model_clean_rejects_invalid_entrypoint(self):
        tool = PersistentAgentCustomTool(
            agent=self.agent,
            name="Pohl Searcher",
            tool_name="pohl_searcher",
            description="Search for keywords.",
            source_path="/tools/pohl_searcher.py",
            parameters_schema={"type": "object", "properties": {}},
            entrypoint="other",
            timeout_seconds=300,
        )

        with self.assertRaisesMessage(ValidationError, "entrypoint must be `run`."):
            tool.full_clean()

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
        skill_state = PersistentAgentSystemSkillState.objects.get(
            agent=self.agent,
            skill_key=CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY,
        )
        self.assertTrue(skill_state.is_enabled)
        self.assertIsNotNone(skill_state.last_used_at)
        self.assertEqual(skill_state.usage_count, 1)

        tool = PersistentAgentCustomTool.objects.get(agent=self.agent, tool_name="custom_greeter")
        self.assertEqual(tool.source_path, "/tools/greeter.py")
        self.assertEqual(tool.entrypoint, "run")
        self.assertEqual(tool.timeout_seconds, 300)

        node = AgentFsNode.objects.get(path="/tools/greeter.py")
        with node.content.open("rb") as handle:
            self.assertIn(b"def run", handle.read())

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_create_custom_tool_rejects_likely_undefined_f_string_name(self, _mock_sandbox):
        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "Runlog Sync",
                "description": "Summarize synced rows.",
                "source_path": "/tools/runlog_sync.py",
                "source_code": self._build_runnable_tool_source(
                    "def run(params, ctx):\n"
                    "    total_runlog_rows = 3\n"
                    "    return {'summary': f'Synced {total_runlog_row} run_log rows'}\n"
                ),
                "parameters_schema": {"type": "object", "properties": {}},
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("undefined f-string name", result["message"])
        self.assertIn("total_runlog_row", result["message"])
        self.assertFalse(PersistentAgentCustomTool.objects.filter(agent=self.agent, tool_name="custom_runlog_sync").exists())

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_create_custom_tool_rejects_undefined_name_inside_f_string_expression(self, _mock_sandbox):
        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "Sheet Sync",
                "description": "Summarize synced rows.",
                "source_path": "/tools/sheet_sync.py",
                "source_code": self._build_runnable_tool_source(
                    "def run(params, ctx):\n"
                    "    total_rows = 3\n"
                    "    return {'summary': f\"Synced {total_rows}. {'Seeded' if seeded else 'Existing'}\"}\n"
                ),
                "parameters_schema": {"type": "object", "properties": {}},
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("undefined f-string name", result["message"])
        self.assertIn("seeded", result["message"])
        self.assertFalse(PersistentAgentCustomTool.objects.filter(agent=self.agent, tool_name="custom_sheet_sync").exists())

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_create_custom_tool_allows_defined_f_string_names(self, _mock_sandbox):
        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "Runlog Summary",
                "description": "Summarize synced rows.",
                "source_path": "/tools/runlog_summary.py",
                "source_code": self._build_runnable_tool_source(
                    "def run(params, ctx):\n"
                    "    total_runlog_rows = 3\n"
                    "    return {\n"
                    "        'summary': f'Synced {total_runlog_rows} run_log rows',\n"
                    "        'next_action': 'Verify read-only.',\n"
                    "    }\n"
                ),
                "parameters_schema": {"type": "object", "properties": {}},
                "enable": False,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(PersistentAgentCustomTool.objects.filter(agent=self.agent, tool_name="custom_runlog_summary").exists())

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_create_custom_tool_rejects_datetime_timezone_after_datetime_class_import(self, _mock_sandbox):
        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "Datetime Sync",
                "description": "Use a UTC timestamp.",
                "source_path": "/tools/datetime_sync.py",
                "source_code": self._build_runnable_tool_source(
                    "def run(params, ctx):\n"
                    "    now = datetime.now(datetime.timezone.utc).isoformat()\n"
                    "    return {'status': 'ok', 'timestamp': now}\n",
                    imports="from datetime import datetime, timedelta",
                ),
                "parameters_schema": {"type": "object", "properties": {}},
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("invalid datetime reference", result["message"])
        self.assertIn("datetime.timezone", result["message"])
        self.assertFalse(PersistentAgentCustomTool.objects.filter(agent=self.agent, tool_name="custom_datetime_sync").exists())

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_create_custom_tool_allows_timezone_import_with_datetime_class_import(self, _mock_sandbox):
        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "Datetime Valid",
                "description": "Use a UTC timestamp.",
                "source_path": "/tools/datetime_valid.py",
                "source_code": self._build_runnable_tool_source(
                    "def run(params, ctx):\n"
                    "    now = datetime.now(timezone.utc).isoformat()\n"
                    "    return {'status': 'ok', 'timestamp': now}\n",
                    imports="from datetime import datetime, timezone",
                ),
                "parameters_schema": {"type": "object", "properties": {}},
                "enable": False,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(PersistentAgentCustomTool.objects.filter(agent=self.agent, tool_name="custom_datetime_valid").exists())

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_create_custom_tool_rejects_greedy_url_regex_suffix_check(self, _mock_sandbox):
        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "Post URL Classifier",
                "description": "Classify direct post URLs.",
                "source_path": "/tools/post_url_classifier.py",
                "source_code": self._build_runnable_tool_source(
                    "def run(params, ctx):\n"
                    "    import re\n"
                    "    pattern = re.compile(r'^https?://example\\.com/posts/[a-zA-Z0-9_-]+')\n"
                    "    url = 'https://example.com/posts/product-launch-activity-1234'\n"
                    "    match = pattern.match(url)\n"
                    "    suffix = url[match.end():] if match else ''\n"
                    "    return {'status': 'ok', 'next_action': suffix}\n"
                ),
                "parameters_schema": {"type": "object", "properties": {}},
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("regex path-segment match", result["message"])
        self.assertIn("url[match.end():]", result["message"])
        self.assertFalse(
            PersistentAgentCustomTool.objects.filter(agent=self.agent, tool_name="custom_post_url_classifier").exists()
        )

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_create_custom_tool_rejects_url_validator_without_required_runtime_inputs(self, _mock_sandbox):
        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "URL Classifier",
                "description": "Classify and validate candidate URLs.",
                "source_path": "/tools/url_classifier.py",
                "source_code": self._build_runnable_tool_source(
                    "def run(params, ctx):\n"
                    "    urls = params.get('urls') or ['https://example.com/posts/sample']\n"
                    "    accepted = [url for url in urls if '/posts/' in url]\n"
                    "    rejected = [url for url in urls if url not in accepted]\n"
                    "    return {\n"
                    "        'status': 'ok',\n"
                    "        'summary': f'{len(accepted)} accepted, {len(rejected)} rejected',\n"
                    "        'direct_post_urls': accepted,\n"
                    "        'rejected_urls': rejected,\n"
                    "        'next_action': 'Use direct_post_urls only.',\n"
                    "    }\n"
                ),
                "parameters_schema": {
                    "type": "object",
                    "properties": {
                        "urls": {"type": "array", "items": {"type": "string"}},
                        "min_posts": {"type": "integer"},
                    },
                },
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("require explicit runtime inputs", result["message"])
        self.assertFalse(PersistentAgentCustomTool.objects.filter(agent=self.agent, tool_name="custom_url_classifier").exists())

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_create_custom_tool_allows_url_validator_with_required_runtime_inputs(self, _mock_sandbox):
        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "URL Classifier Valid",
                "description": "Classify and validate candidate URLs.",
                "source_path": "/tools/url_classifier_valid.py",
                "source_code": self._build_runnable_tool_source(
                    "def run(params, ctx):\n"
                    "    urls = params['candidates']\n"
                    "    limit = params['limit']\n"
                    "    accepted = [url for url in urls if '/posts/' in url]\n"
                    "    accepted = accepted[:limit]\n"
                    "    rejected = [url for url in urls if url not in accepted]\n"
                    "    return {\n"
                    "        'status': 'ok',\n"
                    "        'summary': f'{len(accepted)} accepted, {len(rejected)} rejected',\n"
                    "        'direct_post_urls': accepted,\n"
                    "        'rejected_urls': rejected,\n"
                    "        'next_action': 'Use direct_post_urls only.',\n"
                    "    }\n"
                ),
                "parameters_schema": {
                    "type": "object",
                    "properties": {
                        "candidates": {"type": "array", "items": {"type": "string"}},
                        "limit": {"type": "integer"},
                    },
                    "required": ["candidates", "limit"],
                },
                "enable": False,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(
            PersistentAgentCustomTool.objects.filter(agent=self.agent, tool_name="custom_url_classifier_valid").exists()
        )

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_create_custom_tool_rejects_batch_tool_without_actionable_result_signal(self, _mock_sandbox):
        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "Batch Sync",
                "description": "Sync a bounded batch.",
                "source_path": "/tools/batch_sync.py",
                "source_code": self._build_runnable_tool_source(
                    "def run(params, ctx):\n"
                    "    batch_size = params.get('batch_size')\n"
                    "    return {'status': 'ok', 'summary': 'Wrote rows.', 'batch_size': batch_size}\n"
                ),
                "parameters_schema": {"type": "object", "properties": {"batch_size": {"type": "integer"}}},
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("actionable result guidance", result["message"])
        self.assertFalse(PersistentAgentCustomTool.objects.filter(agent=self.agent, tool_name="custom_batch_sync").exists())

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_create_custom_tool_rejects_batch_tool_without_remaining_work_signal(self, _mock_sandbox):
        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "Batch No Cursor",
                "description": "Sync a bounded batch.",
                "source_path": "/tools/batch_no_cursor.py",
                "source_code": self._build_runnable_tool_source(
                    "def run(params, ctx):\n"
                    "    batch_size = params.get('batch_size')\n"
                    "    return {\n"
                    "        'status': 'ok',\n"
                    "        'summary': 'Wrote rows.',\n"
                    "        'batch_size': batch_size,\n"
                    "        'next_action': 'Verify read-only.',\n"
                    "    }\n"
                ),
                "parameters_schema": {"type": "object", "properties": {"batch_size": {"type": "integer"}}},
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("remaining-work or cursor", result["message"])
        self.assertFalse(
            PersistentAgentCustomTool.objects.filter(agent=self.agent, tool_name="custom_batch_no_cursor").exists()
        )

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_create_custom_tool_allows_batch_tool_with_actionable_result_signal(self, _mock_sandbox):
        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "Batch Sync Valid",
                "description": "Sync a bounded batch.",
                "source_path": "/tools/batch_sync_valid.py",
                "source_code": self._build_runnable_tool_source(
                    "def run(params, ctx):\n"
                    "    batch_size = params.get('batch_size')\n"
                    "    return {\n"
                    "        'status': 'ok',\n"
                    "        'summary': 'Wrote rows.',\n"
                    "        'batch_size': batch_size,\n"
                    "        'remaining_work': 0,\n"
                    "        'next_action': 'Verify read-only; continue only if remaining_work is nonzero.',\n"
                    "    }\n"
                ),
                "parameters_schema": {"type": "object", "properties": {"batch_size": {"type": "integer"}}},
                "enable": False,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(
            PersistentAgentCustomTool.objects.filter(agent=self.agent, tool_name="custom_batch_sync_valid").exists()
        )

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.agent.tools.tool_manager.enable_tools")
    def test_create_custom_tool_stores_canonicalized_nested_schema_types(self, mock_enable_tools, _mock_sandbox):
        mock_enable_tools.return_value = {
            "status": "success",
            "enabled": ["custom_pohl_searcher"],
            "already_enabled": [],
            "evicted": [],
            "invalid": [],
        }

        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "Pohl Searcher",
                "description": "Search for keywords.",
                "source_path": "/tools/pohl_searcher.py",
                "source_code": self._build_runnable_tool_source(
                    "def run(params, ctx):\n"
                    "    return {'keywords': params['keywords']}\n"
                ),
                "parameters_schema": {
                    "type": "OBJECT",
                    "required": ["keywords"],
                    "properties": {
                        "keywords": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"},
                        },
                    },
                },
            },
        )

        self.assertEqual(result["status"], "ok")

        tool = PersistentAgentCustomTool.objects.get(agent=self.agent, tool_name="custom_pohl_searcher")
        self.assertEqual(
            tool.parameters_schema,
            {
                "type": "object",
                "required": ["keywords"],
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        )

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

    def test_apply_patch_updates_source_and_touches_custom_tool(self):
        write_result = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=b"def run(params, ctx):\n    return {'message': 'hi'}\n",
            extension=".py",
            mime_type="text/x-python",
            path="/tools/patch_greeter.py",
            overwrite=True,
        )
        self.assertEqual(write_result.get("status"), "ok")

        tool = PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Patch Greeter",
            tool_name="custom_patch_greeter",
            description="Return a greeting.",
            source_path="/tools/patch_greeter.py",
            parameters_schema={"type": "object", "properties": {}},
        )
        later = tool.updated_at + timedelta(minutes=5)

        patch_text = "\n".join([
            "*** Begin Patch",
            "*** Update File: /tools/patch_greeter.py",
            "@@",
            " def run(params, ctx):",
            "-    return {'message': 'hi'}",
            "+    return {'message': 'hello'}",
            "*** End Patch",
        ])
        with patch("api.agent.tools.apply_patch.timezone.now", return_value=later):
            result = execute_apply_patch(self.agent, {"patch": patch_text})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["updated"], ["/tools/patch_greeter.py"])

        node = AgentFsNode.objects.get(path="/tools/patch_greeter.py")
        with node.content.open("rb") as handle:
            self.assertIn(b"hello", handle.read())

        tool.refresh_from_db()
        self.assertEqual(tool.updated_at, later)

    def test_apply_patch_rejects_invalid_filespace_paths(self):
        cases = [
            (
                "relative",
                "\n".join([
                    "*** Begin Patch",
                    "*** Add File: tools/relative.py",
                    "+print('hi')",
                    "*** End Patch",
                ]),
                "absolute filespace path",
            ),
            (
                "traversal",
                "\n".join([
                    "*** Begin Patch",
                    "*** Add File: /tools/../escape.py",
                    "+print('hi')",
                    "*** End Patch",
                ]),
                "must not contain '.' or '..'",
            ),
            (
                "unsafe",
                "\n".join([
                    "*** Begin Patch",
                    "*** Add File: /tools/bad name.py",
                    "+print('hi')",
                    "*** End Patch",
                ]),
                "unsafe characters",
            ),
        ]

        for _name, patch_text, expected_message in cases:
            with self.subTest(_name):
                result = execute_apply_patch(self.agent, {"patch": patch_text})
                self.assertEqual(result["status"], "error")
                self.assertIn(expected_message, result["message"])

    def test_apply_patch_reports_missing_file(self):
        patch_text = "\n".join([
            "*** Begin Patch",
            "*** Update File: /tools/missing.py",
            "@@",
            "-print('old')",
            "+print('new')",
            "*** End Patch",
        ])

        result = execute_apply_patch(self.agent, {"patch": patch_text})

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "File not found: /tools/missing.py")

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
    def test_tool_manager_preserves_required_fields_and_synthesizes_missing_properties(
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
            ["result_id flocks", "file_path"],
        )
        self.assertEqual(
            tool_def["function"]["parameters"]["properties"]["result_id flocks"],
            {
                "type": "string",
                "description": (
                    "Required parameter inferred from schema.required because no explicit property definition was provided."
                ),
            },
        )

    @patch("api.agent.tools.tool_manager.is_custom_tools_available_for_agent", return_value=True)
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_tool_manager_normalizes_legacy_nested_schema_types_for_llm(
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
            name="Pohl Searcher",
            tool_name="custom_pohl_searcher",
            description="Search keywords.",
            source_path="/tools/pohl_searcher.py",
            parameters_schema={
                "type": "object",
                "required": ["keywords"],
                "properties": {
                    "keywords": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                },
            },
        )
        PersistentAgentEnabledTool.objects.create(agent=self.agent, tool_full_name="custom_pohl_searcher")

        definitions = get_enabled_tool_definitions(self.agent)
        tool_def = next(
            definition
            for definition in definitions
            if definition["function"]["name"] == "custom_pohl_searcher"
        )

        self.assertEqual(
            tool_def["function"]["parameters"],
            {
                "type": "object",
                "required": ["keywords"],
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        )

    def test_create_custom_tool_definition_mentions_direct_tool_and_sqlite_access(self):
        definition = get_create_custom_tool_tool()
        description = definition["function"]["description"]
        properties = definition["function"]["parameters"]["properties"]

        self.assertIn("PEP 723", description)
        self.assertIn("from _gobii_ctx import main", description)
        self.assertIn("ctx.call_tool", description)
        self.assertIn("custom_*", description)
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
        self.assertIn("tool-to-tool calls", description)
        self.assertIn("secret_type='env_var'", description)
        self.assertIn("domain-scoped credential", description)
        self.assertIn("not bare `requests`/`httpx`", description)
        self.assertIn("direct HTTPS tunneling", description)
        self.assertIn("source_path='/tools/my_tool.py'", description)
        self.assertIn("do not pass only `source_path` unless you already wrote that file", description)
        self.assertIn("if rejected, fix every listed issue and retry create_custom_tool, not create_file", description)
        self.assertIn("file_path='/tools/my_tool.py'", description)
        self.assertIn("content=<python source>", description)
        self.assertIn("`/exports/report.txt` are Gobii tool args", description)
        self.assertIn("Path('/workspace/exports/report.txt')", description)
        self.assertIn("open('/exports/report.txt', ...)", description)
        self.assertIn("Latest workspace edits are synced automatically", description)
        self.assertIn("Use for 3+ repeated steps", description)
        self.assertIn("err early", description)
        self.assertIn("Avoid manual MCP/tool/API loops", description)
        self.assertIn("Slow batches should be chunkable", description)
        self.assertIn("include `limit`/`batch_size`, filters, progress", description)
        self.assertIn("batch/limit tools return `remaining_work`/`next_cursor`", description)
        self.assertIn("patch for smaller resumable batches", description)
        self.assertIn("Prefer patching the same file", description)
        self.assertIn("with ctx.sqlite() as db", description)
        self.assertIn("after the block exits the DB is closed", description)
        self.assertIn("agent SQLite DB", description)
        self.assertIn("do not ATTACH sandbox file paths", description)
        self.assertIn("Required filespace path", properties["source_path"]["description"])
        self.assertIn("Still required when source_code is provided", properties["source_path"]["description"])
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
        self.assertIn("Patch all validation issues before retrying", result["message"])
        self.assertIn("exact import", result["message"])

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_create_custom_tool_rejects_source_without_main_import_with_retry_checklist(self, _mock_sandbox):
        result = execute_create_custom_tool(
            self.agent,
            {
                "name": "Greeter",
                "description": "Return a greeting.",
                "source_path": "/tools/greeter.py",
                "source_code": "def run(params, ctx):\n    return {'message': 'hi'}\n\nif __name__ == '__main__': main(run)\n",
                "parameters_schema": {"type": "object", "properties": {}},
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("from _gobii_ctx import main", result["message"])
        self.assertIn("Patch all validation issues before retrying", result["message"])
        self.assertIn("exact final line", result["message"])
        self.assertIn("referenced imports", result["message"])

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
            ["pull", "pull", "push"],
        )

    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True)
    @patch("api.services.sandbox_compute._select_proxy_for_session", return_value=None)
    @patch("api.agent.tools.custom_tools.SandboxComputeService")
    def test_sync_workspace_source_recovers_from_transient_proxy_connection_error(
        self,
        mock_service_cls,
        _mock_select_proxy,
        _mock_service_enabled,
        _mock_tool_enabled,
    ):
        class _ReadyKubernetesBackend(KubernetesSandboxBackend):
            def deploy_or_resume(self, agent, session):
                return SandboxSessionUpdate(
                    state=AgentComputeSession.State.RUNNING,
                    pod_name=f"sandbox-agent-{agent.id}",
                    namespace="gobii-prod",
                )

        AgentComputeSession.objects.filter(agent=self.agent).delete()
        backend = object.__new__(_ReadyKubernetesBackend)
        backend._namespace = "gobii-prod"
        backend._compute_api_token = "supervisor-token"
        backend._proxy_timeout = 30
        service = SandboxComputeService(backend=backend)
        mock_service_cls.return_value = service

        pull_response = MagicMock()
        pull_response.raise_for_status.return_value = None
        pull_response.text = '{"status": "ok"}'
        pull_response.json.return_value = {"status": "ok", "applied": 0, "skipped": 0, "conflicts": 0}
        targeted_pull_response = MagicMock()
        targeted_pull_response.raise_for_status.return_value = None
        targeted_pull_response.text = '{"status": "ok"}'
        targeted_pull_response.json.return_value = {"status": "ok", "applied": 0, "skipped": 0, "conflicts": 0}
        push_response = MagicMock()
        push_response.raise_for_status.return_value = None
        push_response.text = '{"status": "ok"}'
        push_response.json.return_value = {"status": "ok", "changes": []}
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        session.post.side_effect = [
            pull_response,
            requests.ConnectionError("connection refused"),
            targeted_pull_response,
            push_response,
        ]

        with patch("api.services.sandbox_kubernetes.requests.Session", return_value=session), patch(
            "api.services.sandbox_kubernetes.time.sleep",
            return_value=None,
        ), patch("api.services.sandbox_kubernetes.random.uniform", return_value=0):
            sync_error = _sync_workspace_source(self.agent, "/tools/sync_intercom_waiting.py")

        self.assertIsNone(sync_error)
        self.assertEqual(session.post.call_count, 4)
        self.assertIn("/sandbox/compute/sync_filespace", session.post.call_args_list[1].args[0])
        self.assertIn("/sandbox/compute/sync_filespace", session.post.call_args_list[2].args[0])
        self.assertIn("/sandbox/compute/sync_filespace", session.post.call_args_list[3].args[0])

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
            "    return {\n"
            "        'stored': params['value'],\n"
            "        'do_not_repeat_manually': True,\n"
            "        'next_action': 'Do not repeat manually; verify read-only.',\n"
            "    }\n"
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
            self.assertEqual(
                result["result"],
                {
                    "stored": "hello",
                    "do_not_repeat_manually": True,
                    "next_action": "Do not repeat manually; verify read-only.",
                },
            )

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
            "    return {\n"
            "        'stored': params['value'],\n"
            "        'do_not_repeat_manually': True,\n"
            "        'next_action': 'Do not repeat manually; verify read-only.',\n"
            "    }\n",
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
            self.assertEqual(
                result["result"],
                {
                    "stored": value,
                    "do_not_repeat_manually": True,
                    "next_action": "Do not repeat manually; verify read-only.",
                },
            )

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
        parent_tool_call = PersistentAgentToolCall.objects.create(
            step=parent_step,
            tool_name="custom_wrapper",
            tool_params={},
            result=json.dumps({"status": "running"}),
            status="complete",
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
            .select_related("tool_call", "tool_call__parent_tool_call", "completion", "task_credit")
        )
        self.assertEqual(nested_steps.count(), 1)
        nested_step = nested_steps.get()
        self.assertEqual(nested_step.completion_id, completion.id)
        self.assertEqual(nested_step.credits_cost, Decimal("0.040"))
        self.assertEqual(nested_step.task_credit_id, credit.id)
        self.assertEqual(nested_step.tool_call.tool_name, "update_charter")
        self.assertEqual(nested_step.tool_call.parent_tool_call_id, parent_tool_call.pk)
        self.assertEqual(nested_step.tool_call.parent_tool_call.tool_name, "custom_wrapper")
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

    def _create_bridge_custom_tool(
        self,
        *,
        tool_name: str = "custom_wrapper",
    ) -> PersistentAgentCustomTool:
        return PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Wrapper",
            tool_name=tool_name,
            description="Calls nested tools.",
            source_path="/tools/wrapper.py",
            parameters_schema={"type": "object", "properties": {}},
        )

    def _post_bridge_tool_call(
        self,
        token: str,
        *,
        tool_name: str = "send_email",
        params: dict | None = None,
    ):
        return self.client.post(
            reverse("api:custom-tool-bridge-execute"),
            data=json.dumps({"tool_name": tool_name, "params": params or {"value": 1}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    @override_settings(CUSTOM_TOOL_CHILD_FAILURE_LIMIT=3)
    @patch("api.custom_tool_bridge.maybe_run_agent_judge")
    @patch("api.custom_tool_bridge.execute_tracked_runtime_tool_call")
    def test_custom_tool_bridge_aborts_after_three_child_failures(
        self,
        mock_execute_tracked_runtime,
        mock_maybe_run_agent_judge,
    ):
        cache.clear()
        custom_tool = self._create_bridge_custom_tool()
        source_code = self._build_runnable_tool_source(
            "def run(params, ctx):\n"
            "    return ctx.call_tool('send_email', params)\n"
        )
        write_result = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=source_code.encode("utf-8"),
            extension=".py",
            mime_type="text/x-python",
            path=custom_tool.source_path,
            overwrite=True,
        )
        self.assertEqual(write_result.get("status"), "ok")
        parent_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Outer custom tool step",
        )
        token = build_custom_tool_bridge_token(
            self.agent,
            custom_tool,
            parent_step_id=str(parent_step.id),
        )
        child_error = {"status": "error", "message": "Child failed.", "retryable": True}
        mock_execute_tracked_runtime.return_value = (child_error, None)

        first = self._post_bridge_tool_call(token)
        second = self._post_bridge_tool_call(token)
        third = self._post_bridge_tool_call(token)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(third.status_code, 200)
        self.assertEqual(first.json(), child_error)
        self.assertEqual(second.json(), child_error)
        third_payload = third.json()
        self.assertEqual(third_payload["status"], "error")
        self.assertTrue(third_payload["custom_tool_abort"])
        self.assertEqual(third_payload["failure_count"], 3)
        self.assertEqual(third_payload["threshold"], 3)
        self.assertEqual(
            third_payload["message"],
            "Custom tool stopped after 3 failed child tool calls.",
        )

        system_step = PersistentAgentSystemStep.objects.get(
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            notes="custom_tool_child_failure_budget_exceeded",
        )
        self.assertEqual(system_step.step.agent_id, self.agent.id)
        mock_maybe_run_agent_judge.assert_called_once_with(
            self.agent,
            extra_trigger_reasons=["custom_tool_child_failure_budget_exceeded"],
            trigger_context={
                "custom_tool_sources": [
                    {
                        "source_type": "custom_tool_source",
                        "tool_name": custom_tool.tool_name,
                        "name": custom_tool.name,
                        "source_path": custom_tool.source_path,
                        "source_code": source_code,
                    }
                ],
            },
        )
        self.assertEqual(mock_execute_tracked_runtime.call_count, 3)

        fourth = self._post_bridge_tool_call(token)
        self.assertEqual(fourth.status_code, 200)
        self.assertTrue(fourth.json()["custom_tool_abort"])
        self.assertEqual(mock_execute_tracked_runtime.call_count, 3)

    @override_settings(CUSTOM_TOOL_CHILD_FAILURE_LIMIT=3)
    @patch("api.custom_tool_bridge.maybe_run_agent_judge")
    @patch("api.custom_tool_bridge.execute_tracked_runtime_tool_call")
    def test_custom_tool_bridge_successes_do_not_increment_failure_budget(
        self,
        mock_execute_tracked_runtime,
        mock_maybe_run_agent_judge,
    ):
        cache.clear()
        custom_tool = self._create_bridge_custom_tool()
        token = build_custom_tool_bridge_token(self.agent, custom_tool)
        mock_execute_tracked_runtime.side_effect = [
            ({"status": "error", "message": "first"}, None),
            ({"status": "ok", "result": {"sent": 1}}, None),
            ({"status": "error", "message": "second"}, None),
        ]

        first = self._post_bridge_tool_call(token)
        second = self._post_bridge_tool_call(token)
        third = self._post_bridge_tool_call(token)

        self.assertFalse(first.json().get("custom_tool_abort", False))
        self.assertEqual(second.json()["status"], "ok")
        self.assertFalse(third.json().get("custom_tool_abort", False))
        mock_maybe_run_agent_judge.assert_not_called()
        self.assertFalse(
            PersistentAgentSystemStep.objects.filter(
                notes="custom_tool_child_failure_budget_exceeded",
            ).exists()
        )

    @override_settings(CUSTOM_TOOL_CHILD_FAILURE_LIMIT=3)
    @patch("api.custom_tool_bridge.maybe_run_agent_judge")
    @patch("api.custom_tool_bridge.execute_tracked_runtime_tool_call")
    def test_custom_tool_bridge_failure_budget_is_scoped_to_parent_step(
        self,
        mock_execute_tracked_runtime,
        mock_maybe_run_agent_judge,
    ):
        cache.clear()
        custom_tool = self._create_bridge_custom_tool()
        parent_one = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Parent one",
        )
        parent_two = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Parent two",
        )
        token_one = build_custom_tool_bridge_token(
            self.agent,
            custom_tool,
            parent_step_id=str(parent_one.id),
        )
        token_two = build_custom_tool_bridge_token(
            self.agent,
            custom_tool,
            parent_step_id=str(parent_two.id),
        )
        mock_execute_tracked_runtime.return_value = ({"status": "error", "message": "failed"}, None)

        self._post_bridge_tool_call(token_one)
        self._post_bridge_tool_call(token_one)
        response = self._post_bridge_tool_call(token_two)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json().get("custom_tool_abort", False))
        mock_maybe_run_agent_judge.assert_not_called()
        self.assertEqual(mock_execute_tracked_runtime.call_count, 3)

    @override_settings(CUSTOM_TOOL_CHILD_FAILURE_LIMIT=3)
    @patch("api.custom_tool_bridge.maybe_run_agent_judge")
    def test_custom_tool_bridge_counts_recursive_child_denials(
        self,
        mock_maybe_run_agent_judge,
    ):
        cache.clear()
        custom_tool = self._create_bridge_custom_tool()
        token = build_custom_tool_bridge_token(self.agent, custom_tool)

        first = self._post_bridge_tool_call(token, tool_name=custom_tool.tool_name)
        second = self._post_bridge_tool_call(token, tool_name=custom_tool.tool_name)
        third = self._post_bridge_tool_call(token, tool_name=custom_tool.tool_name)

        self.assertEqual(
            first.json()["message"],
            "Custom tools cannot call themselves recursively.",
        )
        self.assertEqual(
            second.json()["message"],
            "Custom tools cannot call themselves recursively.",
        )
        self.assertTrue(third.json()["custom_tool_abort"])
        mock_maybe_run_agent_judge.assert_called_once()

    def test_custom_tool_context_raises_on_bridge_abort_response(self):
        source = (
            "import _gobii_ctx\n"
            "ctx = _gobii_ctx.ToolContext()\n"
            "ctx._call_tool_via_curl = lambda body: "
            "'{\"status\":\"error\",\"custom_tool_abort\":true,\"message\":\"stopped\"}'\n"
            "try:\n"
            "    ctx.call_tool('send_email', {})\n"
            "except RuntimeError as exc:\n"
            "    print(str(exc))\n"
        )

        result = self._run_bootstrap_snippet(source)
        self.assertIn("stopped", result)

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

        summary = format_custom_tools_state_for_prompt(self.agent, recent_limit=2)

        self.assertIn("Custom tools: 2 saved, 1 enabled.", summary)
        self.assertIn("Recent custom tools:", summary)
        self.assertIn("custom_alpha", summary)
        self.assertIn("custom_beta", summary)
        self.assertNotIn("Default mode for repetitive or bulk work", summary)
        self.assertNotIn("DEV LOOP:", summary)
        self.assertNotIn("ANTI-PATTERNS:", summary)

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
