import base64
import os
import sqlite3
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import Max
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.files.filespace_service import write_bytes_to_dir
from api.models import (
    AgentComputeSession,
    AgentFsNode,
    BrowserUseAgent,
    MCPServerConfig,
    PersistentAgent,
    PersistentAgentSecret,
)
from api.services.sandbox_compute import (
    SandboxComputeService,
    SandboxComputeUnavailable,
    SandboxSessionUpdate,
    _build_nonzero_exit_error_payload,
    _post_sync_queue_key,
    custom_tool_workspace_root_for_backend,
)
from api.services.sandbox_internal_paths import (
    CUSTOM_TOOL_SQLITE_FILESPACE_PATH,
    custom_tool_sqlite_workspace_path,
    is_sandbox_internal_path,
)
from api.services.sandbox_filespace_sync import apply_filespace_push, build_filespace_pull_manifest
from api.tasks.sandbox_compute import sync_filespace_after_call
from sandbox_server.server.internal_paths import CUSTOM_TOOL_SQLITE_FILESPACE_PATH as SANDBOX_CUSTOM_TOOL_SQLITE_FILESPACE_PATH


class _DummyBackend:
    def __init__(self) -> None:
        self.deploy_calls: list[dict] = []
        self.sync_calls: list[dict] = []
        self.run_command_calls: list[dict] = []
        self.mcp_calls: list[dict] = []
        self.tool_calls: list[dict] = []

    def deploy_or_resume(self, agent, session):
        self.deploy_calls.append(
            {
                "agent_id": str(agent.id),
                "state": session.state,
            }
        )
        return SandboxSessionUpdate(state=AgentComputeSession.State.RUNNING)

    def sync_filespace(self, agent, session, *, direction, payload=None):
        self.sync_calls.append(
            {
                "agent_id": str(agent.id),
                "direction": direction,
                "payload": payload or {},
            }
        )
        return {"status": "ok", "applied": 0, "skipped": 0, "conflicts": 0}

    def run_command(
        self,
        agent,
        session,
        command,
        *,
        cwd=None,
        env=None,
        trusted_env_keys=None,
        timeout=None,
        interactive=False,
    ):
        self.run_command_calls.append(
            {
                "agent_id": str(agent.id),
                "command": command,
                "cwd": cwd,
                "env": env or {},
                "trusted_env_keys": trusted_env_keys or [],
                "timeout": timeout,
                "interactive": interactive,
            }
        )
        return {"status": "ok", "exit_code": 0, "stdout": f"ran: {command}", "stderr": ""}

    def mcp_request(
        self,
        agent,
        session,
        server_config_id,
        tool_name,
        params,
        *,
        full_tool_name=None,
        server_payload=None,
    ):
        self.mcp_calls.append(
            {
                "agent_id": str(agent.id),
                "server_config_id": str(server_config_id),
                "tool_name": tool_name,
                "params": params,
                "full_tool_name": full_tool_name,
                "server_payload": server_payload or {},
            }
        )
        return {"status": "ok", "result": {"tool_name": tool_name, "params": params}}

    def tool_request(self, agent, session, tool_name, params):
        self.tool_calls.append(
            {
                "agent_id": str(agent.id),
                "tool_name": tool_name,
                "params": params,
            }
        )
        return {"status": "ok", "result": {"tool_name": tool_name, "params": params}}


@tag("batch_agent_lifecycle")
class SandboxComputeSyncTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="sandbox-sync-user",
            email="sandbox-sync-user@example.com",
            password="pw",
        )
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Sandbox Sync Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Sandbox Sync Agent",
            charter="sandbox sync charter",
            browser_use_agent=browser_agent,
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

    def _create_sqlite_db_file(self, path, rows=None):
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS crypto_prices (coin_id TEXT PRIMARY KEY, name TEXT)")
            for row in rows or []:
                conn.execute(
                    "INSERT OR REPLACE INTO crypto_prices (coin_id, name) VALUES (?, ?)",
                    row,
                )
            conn.commit()
        finally:
            conn.close()

    def _sqlite_bytes(self, rows=None):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = f"{tmp_dir}/state.db"
            self._create_sqlite_db_file(db_path, rows=rows)
            with open(db_path, "rb") as handle:
                return handle.read()

    def test_custom_tool_sqlite_internal_path_matches_sandbox_server_constant(self):
        self.assertEqual(CUSTOM_TOOL_SQLITE_FILESPACE_PATH, SANDBOX_CUSTOM_TOOL_SQLITE_FILESPACE_PATH)

    def test_custom_tool_workspace_root_defaults_to_agent_scoped_path(self):
        self.assertEqual(
            custom_tool_workspace_root_for_backend(_DummyBackend(), "agent-1"),
            "/workspace/agent-1",
        )

    def test_pull_manifest_includes_checksum_and_cursor(self):
        write_result = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=b"hello world",
            extension="",
            mime_type="text/plain",
            path="/hello.txt",
            overwrite=True,
        )
        self.assertEqual(write_result.get("status"), "ok")
        node = AgentFsNode.objects.get(id=write_result["node_id"])

        manifest = build_filespace_pull_manifest(self.agent)
        self.assertEqual(manifest.get("status"), "ok")
        entries = manifest.get("files") or []
        hello_entry = next(entry for entry in entries if entry.get("path") == "/hello.txt")
        self.assertEqual(hello_entry["checksum_sha256"], node.checksum_sha256)

        expected_cursor = (
            AgentFsNode.objects.filter(filespace=node.filespace, node_type=AgentFsNode.NodeType.FILE).aggregate(
                max_updated_at=Max("updated_at")
            )["max_updated_at"]
        )
        self.assertEqual(manifest.get("sync_cursor"), expected_cursor.isoformat() if expected_cursor else None)

    def test_running_session_refreshes_pull_using_cursor(self):
        backend = _DummyBackend()
        now = timezone.now()
        cursor_one = now - timedelta(seconds=5)
        cursor_two = now

        with patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True), patch(
            "api.services.sandbox_compute._select_proxy_for_session", return_value=None
        ), patch(
            "api.services.sandbox_compute.build_filespace_pull_manifest",
            side_effect=[
                {
                    "status": "ok",
                    "files": [],
                    "sync_cursor": cursor_one.isoformat(),
                },
                {
                    "status": "ok",
                    "files": [],
                    "sync_cursor": cursor_two.isoformat(),
                },
            ],
        ) as mock_manifest:
            service = SandboxComputeService(backend=backend)
            AgentComputeSession.objects.create(agent=self.agent, state=AgentComputeSession.State.RUNNING)

            service._ensure_session(self.agent, source="tool_request")
            service._ensure_session(self.agent, source="tool_request")

        self.assertEqual(len(backend.sync_calls), 2)
        self.assertEqual(len(backend.deploy_calls), 2)
        self.assertEqual(backend.sync_calls[0]["direction"], "pull")
        self.assertEqual(backend.sync_calls[1]["direction"], "pull")

        first_since = mock_manifest.call_args_list[0].kwargs.get("since")
        second_since = mock_manifest.call_args_list[1].kwargs.get("since")
        self.assertIsNone(first_since)
        self.assertEqual(second_since, cursor_one)

        session = AgentComputeSession.objects.get(agent=self.agent)
        self.assertEqual(session.last_filespace_pull_at, cursor_two)

    def test_ensure_session_fails_before_pull_when_backend_returns_error_state(self):
        class _ErrorBackend(_DummyBackend):
            def deploy_or_resume(self, agent, session):
                self.deploy_calls.append(
                    {
                        "agent_id": str(agent.id),
                        "state": session.state,
                    }
                )
                return SandboxSessionUpdate(
                    state=AgentComputeSession.State.ERROR,
                    pod_name="sandbox-agent-bad",
                    namespace="gobii-pr-815",
                )

        backend = _ErrorBackend()
        service = SandboxComputeService(backend=backend)

        with patch("api.services.sandbox_compute.build_filespace_pull_manifest") as manifest_mock:
            with self.assertRaises(SandboxComputeUnavailable) as exc_info:
                service._ensure_session(self.agent, source="custom_tool_source_sync")

        self.assertIn("state=error", str(exc_info.exception))
        self.assertIn("sandbox-agent-bad", str(exc_info.exception))
        manifest_mock.assert_not_called()
        self.assertEqual(len(backend.deploy_calls), 2)
        session = AgentComputeSession.objects.get(agent=self.agent)
        self.assertEqual(session.state, AgentComputeSession.State.ERROR)
        self.assertEqual(session.pod_name, "sandbox-agent-bad")
        self.assertEqual(session.namespace, "gobii-pr-815")

    def test_ensure_session_retries_once_before_pull_when_backend_recovers(self):
        class _RecoveringBackend(_DummyBackend):
            def __init__(self) -> None:
                super().__init__()
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
                        namespace="gobii-pr-815",
                    )
                return SandboxSessionUpdate(
                    state=AgentComputeSession.State.RUNNING,
                    pod_name="sandbox-agent-recovered",
                    namespace="gobii-pr-815",
                )

        backend = _RecoveringBackend()
        service = SandboxComputeService(backend=backend)

        with patch(
            "api.services.sandbox_compute.build_filespace_pull_manifest",
            return_value={"status": "ok", "files": [], "sync_cursor": None},
        ) as manifest_mock:
            session = service._ensure_session(self.agent, source="custom_tool_source_sync")

        self.assertEqual(len(backend.deploy_calls), 2)
        self.assertEqual(len(backend.sync_calls), 1)
        manifest_mock.assert_called_once()
        self.assertEqual(session.state, AgentComputeSession.State.RUNNING)
        self.assertEqual(session.pod_name, "sandbox-agent-recovered")
        self.assertEqual(session.namespace, "gobii-pr-815")

    def test_pull_manifest_excludes_internal_custom_tool_sqlite_path(self):
        internal = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=b"sqlite-state",
            extension="",
            mime_type="application/vnd.sqlite3",
            path=CUSTOM_TOOL_SQLITE_FILESPACE_PATH,
            overwrite=True,
        )
        gobii_internal = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=b"hidden-trace",
            extension="",
            mime_type="text/plain",
            path="/.gobii/internal/tool.log",
            overwrite=True,
        )
        uv_cache = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=b"wheel-cache",
            extension="",
            mime_type="application/octet-stream",
            path="/.uv-cache/wheels/pkg.whl",
            overwrite=True,
        )
        uv_project_env = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=b"bin/python",
            extension="",
            mime_type="application/octet-stream",
            path="/.gobii/uv-project-env/bin/python",
            overwrite=True,
        )
        visible = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=b"user-file",
            extension="",
            mime_type="text/plain",
            path="/visible.txt",
            overwrite=True,
        )

        self.assertEqual(internal.get("status"), "ok")
        self.assertEqual(gobii_internal.get("status"), "ok")
        self.assertEqual(uv_cache.get("status"), "ok")
        self.assertEqual(uv_project_env.get("status"), "ok")
        self.assertEqual(visible.get("status"), "ok")

        manifest = build_filespace_pull_manifest(self.agent)

        self.assertEqual(manifest.get("status"), "ok")
        paths = [entry.get("path") for entry in manifest.get("files") or []]
        self.assertIn("/visible.txt", paths)
        self.assertNotIn(CUSTOM_TOOL_SQLITE_FILESPACE_PATH, paths)
        self.assertNotIn("/.gobii/internal/tool.log", paths)
        self.assertNotIn("/.gobii/uv-project-env/bin/python", paths)
        self.assertNotIn("/.uv-cache/wheels/pkg.whl", paths)

    def test_apply_filespace_push_ignores_internal_custom_tool_sqlite_path(self):
        result = apply_filespace_push(
            self.agent,
            [
                {
                    "path": CUSTOM_TOOL_SQLITE_FILESPACE_PATH,
                    "content_b64": "c3FsaXRlLXN0YXRl",
                    "mime_type": "application/vnd.sqlite3",
                },
                {
                    "path": "/.gobii/internal/tool.log",
                    "content_b64": "aGlkZGVuLXRyYWNl",
                    "mime_type": "text/plain",
                },
                {
                    "path": "/.gobii/uv-project-env/bin/python",
                    "content_b64": "YmluL3B5dGhvbg==",
                    "mime_type": "application/octet-stream",
                },
                {
                    "path": "/.uv-cache/wheels/pkg.whl",
                    "content_b64": "d2hlZWwtY2FjaGU=",
                    "mime_type": "application/octet-stream",
                },
                {
                    "path": "/visible.txt",
                    "content_b64": "dmlzaWJsZQ==",
                    "mime_type": "text/plain",
                },
            ],
        )

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("skipped"), 4)
        self.assertFalse(AgentFsNode.objects.filter(path=CUSTOM_TOOL_SQLITE_FILESPACE_PATH).exists())
        self.assertFalse(AgentFsNode.objects.filter(path="/.gobii/internal/tool.log").exists())
        self.assertFalse(AgentFsNode.objects.filter(path="/.gobii/uv-project-env/bin/python").exists())
        self.assertFalse(AgentFsNode.objects.filter(path="/.uv-cache/wheels/pkg.whl").exists())
        self.assertTrue(AgentFsNode.objects.filter(path="/visible.txt").exists())

    def test_is_sandbox_internal_path_matches_reserved_subtrees(self):
        self.assertTrue(is_sandbox_internal_path(CUSTOM_TOOL_SQLITE_FILESPACE_PATH))
        self.assertTrue(is_sandbox_internal_path("/.gobii/internal/tool.log"))
        self.assertTrue(is_sandbox_internal_path("/.gobii/uv-project-env/bin/python"))
        self.assertTrue(is_sandbox_internal_path("/.uv-cache/wheels/pkg.whl"))
        self.assertFalse(is_sandbox_internal_path("/reports/.uv-cache-not-really.txt"))

    def test_nonzero_exit_error_uses_last_stderr_line_as_message(self):
        stderr = (
            '  File "/workspace/exports/hello_country.py", line 30\n'
            '    print(f"\n'
            "          ^\n"
            "SyntaxError: unterminated f-string literal (detected at line 30)\n"
        )
        payload = _build_nonzero_exit_error_payload(
            process_name="Python",
            exit_code=1,
            stdout="",
            stderr=stderr,
        )

        self.assertEqual(payload.get("status"), "error")
        self.assertEqual(payload.get("exit_code"), 1)
        self.assertEqual(payload.get("message"), "SyntaxError: unterminated f-string literal (detected at line 30)")
        self.assertEqual(payload.get("detail"), stderr)

    def test_nonzero_exit_error_falls_back_when_stderr_missing(self):
        payload = _build_nonzero_exit_error_payload(
            process_name="Command",
            exit_code=7,
            stdout="",
            stderr="",
        )

        self.assertEqual(payload.get("status"), "error")
        self.assertEqual(payload.get("message"), "Command exited with status 7.")
        self.assertEqual(payload.get("stderr"), "")
        self.assertNotIn("detail", payload)

    def test_nonzero_exit_error_preserves_streams(self):
        payload = _build_nonzero_exit_error_payload(
            process_name="Python",
            exit_code=3,
            stdout="partial output",
            stderr="ValueError: boom\n",
        )

        self.assertEqual(payload.get("stdout"), "partial output")
        self.assertEqual(payload.get("stderr"), "ValueError: boom\n")
        self.assertEqual(payload.get("message"), "ValueError: boom")

    def test_mcp_request_enqueues_async_post_sync(self):
        backend = _DummyBackend()
        with patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True), patch(
            "api.services.sandbox_compute._select_proxy_for_session",
            return_value=None,
        ), patch(
            "api.services.sandbox_compute.build_filespace_pull_manifest",
            return_value={"status": "ok", "files": [], "sync_cursor": None},
        ), patch(
            "api.services.sandbox_compute._build_mcp_server_payload",
            return_value=({"config_id": "cfg-1", "name": "postgres"}, object()),
        ), patch.object(
            SandboxComputeService,
            "_enqueue_post_sync_after_call",
        ) as mock_enqueue, patch.object(
            SandboxComputeService,
            "_sync_workspace_push",
        ) as mock_sync:
            service = SandboxComputeService(backend=backend)
            result = service.mcp_request(self.agent, "cfg-1", "pg_execute_query", {"sql": "select 1"})

        self.assertEqual(result.get("status"), "ok")
        mock_enqueue.assert_called_once_with(self.agent, source="mcp_request")
        mock_sync.assert_not_called()

    def test_tool_request_enqueues_async_post_sync(self):
        backend = _DummyBackend()
        with patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True), patch(
            "api.services.sandbox_compute._select_proxy_for_session",
            return_value=None,
        ), patch(
            "api.services.sandbox_compute.build_filespace_pull_manifest",
            return_value={"status": "ok", "files": [], "sync_cursor": None},
        ), patch.object(
            SandboxComputeService,
            "_enqueue_post_sync_after_call",
        ) as mock_enqueue, patch.object(
            SandboxComputeService,
            "_sync_workspace_push",
        ) as mock_sync:
            service = SandboxComputeService(backend=backend)
            result = service.tool_request(self.agent, "create_file", {"path": "/tmp/a.txt", "content": "ok"})

        self.assertEqual(result.get("status"), "ok")
        mock_enqueue.assert_called_once_with(self.agent, source="tool_request")
        mock_sync.assert_not_called()

    def test_run_command_enqueues_async_post_sync(self):
        backend = _DummyBackend()
        with patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True), patch(
            "api.services.sandbox_compute._select_proxy_for_session",
            return_value=None,
        ), patch(
            "api.services.sandbox_compute.build_filespace_pull_manifest",
            return_value={"status": "ok", "files": [], "sync_cursor": None},
        ), patch("api.services.sandbox_compute._sync_on_run_command", return_value=True), patch.object(
            SandboxComputeService,
            "_enqueue_post_sync_after_call",
        ) as mock_enqueue, patch.object(
            SandboxComputeService,
            "_sync_workspace_push",
        ) as mock_sync:
            service = SandboxComputeService(backend=backend)
            result = service.run_command(self.agent, "echo hello")

        self.assertEqual(result.get("status"), "ok")
        mock_enqueue.assert_called_once_with(self.agent, source="run_command")
        mock_sync.assert_not_called()

    def test_run_custom_tool_command_syncs_sqlite_for_remote_backend(self):
        backend = _DummyBackend()
        synced_bytes = self._sqlite_bytes(rows=[("bitcoin", "Bitcoin")])
        export_bytes = b"mermaid export"

        def _sync_filespace(agent, session, *, direction, payload=None):
            backend.sync_calls.append(
                {
                    "agent_id": str(agent.id),
                    "direction": direction,
                    "payload": payload or {},
                }
            )
            if direction == "push":
                return {
                    "status": "ok",
                    "changes": [
                        {
                            "path": CUSTOM_TOOL_SQLITE_FILESPACE_PATH,
                            "content_b64": base64.b64encode(synced_bytes).decode("ascii"),
                            "mime_type": "application/vnd.sqlite3",
                        },
                        {
                            "path": "/.gobii/internal/tool.log",
                            "content_b64": "aWdub3JlZCBieXRlcw==",
                            "mime_type": "text/plain",
                        },
                        {
                            "path": "/exports/mermaid_98928a38.png",
                            "content_b64": base64.b64encode(export_bytes).decode("ascii"),
                            "mime_type": "image/png",
                        },
                    ],
                }
            return {"status": "ok", "applied": 0, "skipped": 0, "conflicts": 0}

        backend.sync_filespace = _sync_filespace

        with tempfile.TemporaryDirectory() as tmp_dir, patch(
            "api.services.sandbox_compute.sandbox_compute_enabled",
            return_value=True,
        ), patch(
            "api.services.sandbox_compute._select_proxy_for_session",
            return_value=None,
        ), patch(
            "api.services.sandbox_compute.build_filespace_pull_manifest",
            return_value={"status": "ok", "files": [], "sync_cursor": None},
        ):
            db_path = f"{tmp_dir}/state.db"
            self._create_sqlite_db_file(db_path, rows=[("test", "Test")])

            service = SandboxComputeService(backend=backend)
            result = service.run_custom_tool_command(
                self.agent,
                "echo hello",
                env={"EXTRA": "1"},
                timeout=15,
                local_sqlite_db_path=db_path,
                sqlite_env_key="SANDBOX_CUSTOM_TOOL_SQLITE_DB_PATH",
            )

            self.assertEqual(result.get("status"), "ok")
            self.assertEqual(
                result.get("shared_sqlite_db"),
                {
                    "available": True,
                    "same_db_as_sqlite_batch": True,
                    "transport": "sandbox_sync",
                    "sync_back": "ok",
                    "deleted": False,
                    "size_bytes": len(synced_bytes),
                },
            )
            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute("SELECT coin_id, name FROM crypto_prices ORDER BY coin_id").fetchall()
            finally:
                conn.close()
            self.assertEqual(rows, [("bitcoin", "Bitcoin")])
            export_node = AgentFsNode.objects.get(path="/exports/mermaid_98928a38.png")
            self.assertEqual(export_node.mime_type, "image/png")
            self.assertEqual(export_node.size_bytes, len(export_bytes))

        internal_pull = next(
            call
            for call in backend.sync_calls
            if call["direction"] == "pull"
            and any(
                entry.get("path") == CUSTOM_TOOL_SQLITE_FILESPACE_PATH
                for entry in call["payload"].get("files", [])
            )
        )
        internal_entry = internal_pull["payload"]["files"][0]
        self.assertEqual(internal_entry["path"], CUSTOM_TOOL_SQLITE_FILESPACE_PATH)
        self.assertIn("content_b64", internal_entry)
        self.assertEqual(
            backend.run_command_calls[0]["env"]["SANDBOX_CUSTOM_TOOL_SQLITE_DB_PATH"],
            custom_tool_sqlite_workspace_path(self.agent.id),
        )
        push_call = next(call for call in backend.sync_calls if call["direction"] == "push")
        self.assertEqual(push_call["payload"]["internal_paths"], [CUSTOM_TOOL_SQLITE_FILESPACE_PATH])
        self.assertNotIn("since", push_call["payload"])
        session = AgentComputeSession.objects.get(agent=self.agent)
        self.assertIsNotNone(session.last_filespace_sync_at)

    def test_sync_custom_tool_sqlite_pull_snapshots_live_wal_database(self):
        backend = _DummyBackend()
        service = SandboxComputeService(backend=backend)
        session = AgentComputeSession.objects.create(agent=self.agent, state=AgentComputeSession.State.RUNNING)

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = f"{tmp_dir}/state.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("CREATE TABLE crypto_prices (coin_id TEXT PRIMARY KEY, name TEXT)")
                conn.execute("INSERT INTO crypto_prices VALUES ('test', 'Test')")
                conn.commit()

                result = service._sync_custom_tool_sqlite_pull(self.agent, session, db_path)
                self.assertEqual(result.get("status"), "ok")

                pull_call = next(call for call in backend.sync_calls if call["direction"] == "pull")
                sqlite_entry = next(
                    entry
                    for entry in pull_call["payload"]["files"]
                    if entry.get("path") == CUSTOM_TOOL_SQLITE_FILESPACE_PATH
                )
                snapshot_bytes = base64.b64decode(sqlite_entry["content_b64"])

                snapshot_path = f"{tmp_dir}/snapshot.db"
                with open(snapshot_path, "wb") as handle:
                    handle.write(snapshot_bytes)

                snapshot_conn = sqlite3.connect(snapshot_path)
                try:
                    rows = snapshot_conn.execute(
                        "SELECT coin_id, name FROM crypto_prices ORDER BY coin_id"
                    ).fetchall()
                finally:
                    snapshot_conn.close()
            finally:
                conn.close()

        self.assertEqual(rows, [("test", "Test")])

    def test_sync_custom_tool_workspace_push_replaces_host_db_without_stale_wal_sidecars(self):
        backend = _DummyBackend()
        service = SandboxComputeService(backend=backend)
        cursor = timezone.now() - timedelta(minutes=5)
        session = AgentComputeSession.objects.create(
            agent=self.agent,
            state=AgentComputeSession.State.RUNNING,
            last_filespace_sync_at=cursor,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = f"{tmp_dir}/state.db"
            live_conn = sqlite3.connect(db_path)
            try:
                live_conn.execute("PRAGMA journal_mode=WAL;")
                live_conn.execute("CREATE TABLE crypto_prices (coin_id TEXT PRIMARY KEY, name TEXT)")
                live_conn.execute("INSERT INTO crypto_prices VALUES ('test', 'Test')")
                live_conn.commit()
                self.assertTrue(os.path.exists(f"{db_path}-wal"))

                synced_db_path = f"{tmp_dir}/synced.db"
                synced_conn = sqlite3.connect(synced_db_path)
                try:
                    synced_conn.execute("CREATE TABLE crypto_prices (coin_id TEXT PRIMARY KEY, name TEXT)")
                    synced_conn.execute("INSERT INTO crypto_prices VALUES ('bitcoin', 'Bitcoin')")
                    synced_conn.execute("INSERT INTO crypto_prices VALUES ('ethereum', 'Ethereum')")
                    synced_conn.commit()
                finally:
                    synced_conn.close()

                with open(synced_db_path, "rb") as handle:
                    synced_bytes = handle.read()

                push_payload = {}

                def _sync_filespace(agent, session, *, direction, payload=None):
                    if direction == "push":
                        push_payload.update(payload or {})
                        return {
                            "status": "ok",
                            "changes": [
                                {
                                    "path": CUSTOM_TOOL_SQLITE_FILESPACE_PATH,
                                    "content_b64": base64.b64encode(synced_bytes).decode("ascii"),
                                    "mime_type": "application/vnd.sqlite3",
                                }
                            ],
                        }
                    return {"status": "ok", "applied": 0, "skipped": 0, "conflicts": 0}

                backend.sync_filespace = _sync_filespace

                result = service._sync_custom_tool_workspace_push(self.agent, session, db_path)
                self.assertEqual(result.get("status"), "ok")
                self.assertTrue(result.get("sqlite_synced"))
                self.assertFalse(os.path.exists(f"{db_path}-wal"))
                self.assertFalse(os.path.exists(f"{db_path}-shm"))

                restored_conn = sqlite3.connect(db_path)
                try:
                    rows = restored_conn.execute(
                        "SELECT coin_id, name FROM crypto_prices ORDER BY coin_id"
                    ).fetchall()
                finally:
                    restored_conn.close()
            finally:
                live_conn.close()

        self.assertEqual(rows, [("bitcoin", "Bitcoin"), ("ethereum", "Ethereum")])
        self.assertEqual(push_payload["since"], cursor.isoformat())
        self.assertEqual(push_payload["internal_paths"], [CUSTOM_TOOL_SQLITE_FILESPACE_PATH])
        session.refresh_from_db()
        self.assertGreater(session.last_filespace_sync_at, cursor)

    def test_sync_custom_tool_workspace_push_applies_exports_when_sqlite_is_deleted(self):
        backend = _DummyBackend()
        service = SandboxComputeService(backend=backend)
        cursor = timezone.now() - timedelta(minutes=5)
        session = AgentComputeSession.objects.create(
            agent=self.agent,
            state=AgentComputeSession.State.RUNNING,
            last_filespace_sync_at=cursor,
        )

        with tempfile.NamedTemporaryFile(delete=False) as handle:
            db_path = handle.name
        self.addCleanup(lambda: os.path.exists(db_path) and os.remove(db_path))
        self._create_sqlite_db_file(db_path, rows=[("test", "Test")])
        export_bytes = b"mermaid export"

        def _sync_filespace(agent, session, *, direction, payload=None):
            if direction == "push":
                return {
                    "status": "ok",
                    "changes": [
                        {
                            "path": CUSTOM_TOOL_SQLITE_FILESPACE_PATH,
                            "is_deleted": True,
                        },
                        {
                            "path": "/exports/mermaid_98928a38.png",
                            "content_b64": base64.b64encode(export_bytes).decode("ascii"),
                            "mime_type": "image/png",
                        },
                    ],
                }
            return {"status": "ok", "applied": 0, "skipped": 0, "conflicts": 0}

        backend.sync_filespace = _sync_filespace

        result = service._sync_custom_tool_workspace_push(self.agent, session, db_path)

        self.assertEqual(result, {"status": "ok", "sqlite_synced": True, "deleted": True})
        self.assertFalse(os.path.exists(db_path))
        export_node = AgentFsNode.objects.get(path="/exports/mermaid_98928a38.png")
        self.assertEqual(export_node.mime_type, "image/png")
        self.assertEqual(export_node.size_bytes, len(export_bytes))
        session.refresh_from_db()
        self.assertGreater(session.last_filespace_sync_at, cursor)

    def test_run_custom_tool_command_requires_shared_sqlite_to_sync_back(self):
        backend = _DummyBackend()

        def _sync_filespace(agent, session, *, direction, payload=None):
            backend.sync_calls.append(
                {
                    "agent_id": str(agent.id),
                    "direction": direction,
                    "payload": payload or {},
                }
            )
            if direction == "push":
                return {"status": "ok", "changes": []}
            return {"status": "ok", "applied": 0, "skipped": 0, "conflicts": 0}

        backend.sync_filespace = _sync_filespace

        with tempfile.TemporaryDirectory() as tmp_dir, patch(
            "api.services.sandbox_compute.sandbox_compute_enabled",
            return_value=True,
        ), patch(
            "api.services.sandbox_compute._select_proxy_for_session",
            return_value=None,
        ), patch(
            "api.services.sandbox_compute.build_filespace_pull_manifest",
            return_value={"status": "ok", "files": [], "sync_cursor": None},
        ):
            db_path = f"{tmp_dir}/state.db"
            self._create_sqlite_db_file(db_path, rows=[("test", "Test")])

            service = SandboxComputeService(backend=backend)
            result = service.run_custom_tool_command(
                self.agent,
                "echo hello",
                local_sqlite_db_path=db_path,
                sqlite_env_key="SANDBOX_CUSTOM_TOOL_SQLITE_DB_PATH",
            )

        self.assertEqual(result, {"status": "error", "message": "Custom tool SQLite sync did not return the shared agent DB."})

    def test_run_command_merges_env_var_secrets_with_precedence(self):
        backend = _DummyBackend()
        self._create_env_var_secret("SANDBOX_TOKEN", "from-secret")

        with patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True), patch(
            "api.services.sandbox_compute.sandbox_compute_enabled_for_agent",
            return_value=True,
        ), patch(
            "api.services.sandbox_compute._select_proxy_for_session",
            return_value=None,
        ), patch(
            "api.services.sandbox_compute.build_filespace_pull_manifest",
            return_value={"status": "ok", "files": [], "sync_cursor": None},
        ), patch(
            "api.services.sandbox_compute._sync_on_run_command",
            return_value=False,
        ):
            service = SandboxComputeService(backend=backend)
            result = service.run_command(
                self.agent,
                "echo hello",
                env={"SANDBOX_TOKEN": "from-caller", "EXTRA": "caller-value"},
            )

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(len(backend.run_command_calls), 1)
        merged_env = backend.run_command_calls[0]["env"]
        self.assertEqual(merged_env["SANDBOX_TOKEN"], "from-secret")
        self.assertEqual(merged_env["EXTRA"], "caller-value")
        self.assertEqual(backend.run_command_calls[0]["trusted_env_keys"], ["SANDBOX_TOKEN"])

    def test_run_custom_tool_command_merges_env_var_secrets_with_precedence(self):
        backend = _DummyBackend()
        self._create_env_var_secret("OPENAI_API_KEY", "from-secret")
        synced_bytes = self._sqlite_bytes(rows=[("test", "Test")])

        def _sync_filespace(agent, session, *, direction, payload=None):
            backend.sync_calls.append(
                {
                    "agent_id": str(agent.id),
                    "direction": direction,
                    "payload": payload or {},
                }
            )
            if direction == "push":
                return {
                    "status": "ok",
                    "changes": [
                        {
                            "path": CUSTOM_TOOL_SQLITE_FILESPACE_PATH,
                            "content_b64": base64.b64encode(synced_bytes).decode("ascii"),
                            "mime_type": "application/vnd.sqlite3",
                        }
                    ],
                }
            return {"status": "ok", "applied": 0, "skipped": 0, "conflicts": 0}

        backend.sync_filespace = _sync_filespace

        with tempfile.NamedTemporaryFile(delete=False) as handle:
            sqlite_path = handle.name
        self.addCleanup(lambda: os.path.exists(sqlite_path) and os.remove(sqlite_path))
        self._create_sqlite_db_file(sqlite_path, rows=[("test", "Test")])

        with patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True), patch(
            "api.services.sandbox_compute.sandbox_compute_enabled_for_agent",
            return_value=True,
        ), patch(
            "api.services.sandbox_compute._select_proxy_for_session",
            return_value=None,
        ), patch(
            "api.services.sandbox_compute.build_filespace_pull_manifest",
            return_value={"status": "ok", "files": [], "sync_cursor": None},
        ):
            service = SandboxComputeService(backend=backend)
            result = service.run_custom_tool_command(
                self.agent,
                "python -c 'print(1)'",
                env={"OPENAI_API_KEY": "from-caller", "KEEP_ME": "yes"},
                local_sqlite_db_path=sqlite_path,
                sqlite_env_key="SANDBOX_CUSTOM_TOOL_SQLITE_DB_PATH",
            )

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(
            result.get("shared_sqlite_db"),
            {
                "available": True,
                "same_db_as_sqlite_batch": True,
                "transport": "sandbox_sync",
                "sync_back": "ok",
                "deleted": False,
                "size_bytes": len(synced_bytes),
            },
        )
        self.assertEqual(len(backend.run_command_calls), 1)
        merged_env = backend.run_command_calls[0]["env"]
        self.assertEqual(merged_env["OPENAI_API_KEY"], "from-secret")
        self.assertEqual(merged_env["KEEP_ME"], "yes")
        self.assertEqual(backend.run_command_calls[0]["trusted_env_keys"], ["OPENAI_API_KEY"])

    def test_python_exec_merges_env_var_secrets_with_precedence(self):
        backend = _DummyBackend()
        self._create_env_var_secret("OPENAI_API_KEY", "from-secret")

        with patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True), patch(
            "api.services.sandbox_compute.sandbox_compute_enabled_for_agent",
            return_value=True,
        ), patch(
            "api.services.sandbox_compute._select_proxy_for_session",
            return_value=None,
        ), patch(
            "api.services.sandbox_compute.build_filespace_pull_manifest",
            return_value={"status": "ok", "files": [], "sync_cursor": None},
        ), patch(
            "api.services.sandbox_compute._sync_on_tool_call",
            return_value=False,
        ):
            service = SandboxComputeService(backend=backend)
            result = service.tool_request(
                self.agent,
                "python_exec",
                {
                    "code": "print('ok')",
                    "env": {"OPENAI_API_KEY": "from-caller", "KEEP_ME": "yes"},
                },
            )

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(len(backend.tool_calls), 1)
        merged_env = backend.tool_calls[0]["params"]["env"]
        self.assertEqual(merged_env["OPENAI_API_KEY"], "from-secret")
        self.assertEqual(merged_env["KEEP_ME"], "yes")
        self.assertEqual(
            backend.tool_calls[0]["params"]["trusted_env_keys"],
            ["OPENAI_API_KEY"],
        )

    def test_mcp_request_merges_env_var_secrets_with_precedence(self):
        backend = _DummyBackend()
        self._create_env_var_secret("SANDBOX_TOKEN", "from-secret")
        config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="example-mcp",
            display_name="Example MCP",
            command="mcp-server",
            command_args=["--stdio"],
            auth_method=MCPServerConfig.AuthMethod.NONE,
            environment={"SANDBOX_TOKEN": "from-runtime", "RUNTIME_ONLY": "1"},
            is_active=True,
        )

        runtime = SimpleNamespace(
            config_id=str(config.id),
            name=config.name,
            command=config.command,
            args=config.command_args,
            url=config.url,
            env=dict(config.environment or {}),
            headers={},
            auth_method=config.auth_method,
            scope=config.scope,
        )

        manager_mock = type("ManagerMock", (), {})()
        manager_mock._build_runtime_from_config = lambda cfg: runtime
        manager_mock._build_auth_headers = lambda _runtime: {}

        with patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True), patch(
            "api.services.sandbox_compute.sandbox_compute_enabled_for_agent",
            return_value=True,
        ), patch(
            "api.services.sandbox_compute._select_proxy_for_session",
            return_value=None,
        ), patch(
            "api.services.sandbox_compute.build_filespace_pull_manifest",
            return_value={"status": "ok", "files": [], "sync_cursor": None},
        ), patch(
            "api.services.sandbox_compute._sync_on_mcp_call",
            return_value=False,
        ), patch(
            "api.agent.tools.mcp_manager.get_mcp_manager",
            return_value=manager_mock,
        ):
            service = SandboxComputeService(backend=backend)
            result = service.mcp_request(self.agent, str(config.id), "ping", {"hello": "world"})

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(len(backend.mcp_calls), 1)
        payload_env = backend.mcp_calls[0]["server_payload"]["env"]
        self.assertEqual(payload_env["SANDBOX_TOKEN"], "from-secret")
        self.assertEqual(payload_env["RUNTIME_ONLY"], "1")

    def test_enqueue_post_sync_coalesces_per_agent(self):
        backend = _DummyBackend()
        redis_mock = type("RedisMock", (), {})()
        redis_mock.set_calls = []

        def _set(name, value, nx=None, ex=None):
            redis_mock.set_calls.append((name, value, nx, ex))
            return len(redis_mock.set_calls) == 1

        redis_mock.set = _set
        redis_mock.delete = lambda key: 1

        with patch("api.services.sandbox_compute.sandbox_compute_enabled", return_value=True), patch(
            "api.services.sandbox_compute.get_redis_client",
            return_value=redis_mock,
        ), patch(
            "api.tasks.sandbox_compute.sync_filespace_after_call.delay",
        ) as mock_delay:
            service = SandboxComputeService(backend=backend)
            service._enqueue_post_sync_after_call(self.agent, source="mcp_request")
            service._enqueue_post_sync_after_call(self.agent, source="tool_request")

        self.assertEqual(len(redis_mock.set_calls), 2)
        self.assertEqual(mock_delay.call_count, 1)

    def test_async_post_sync_task_clears_coalesce_key_on_success(self):
        AgentComputeSession.objects.create(agent=self.agent, state=AgentComputeSession.State.RUNNING)
        redis_mock = type("RedisMock", (), {})()
        redis_mock.deleted_keys = []
        redis_mock.delete = lambda key: redis_mock.deleted_keys.append(key) or 1

        with patch("api.tasks.sandbox_compute.sandbox_compute_enabled", return_value=True), patch(
            "api.tasks.sandbox_compute.get_redis_client",
            return_value=redis_mock,
        ), patch("api.tasks.sandbox_compute.SandboxComputeService") as mock_service_cls:
            mock_service_cls.return_value._sync_workspace_push.return_value = {"status": "ok"}
            result = sync_filespace_after_call(str(self.agent.id), source="mcp_request")

        self.assertEqual(result.get("status"), "ok")
        mock_service_cls.return_value._sync_workspace_push.assert_called_once()
        self.assertEqual(
            redis_mock.deleted_keys,
            [_post_sync_queue_key(str(self.agent.id))],
        )

    def test_async_post_sync_task_clears_coalesce_key_on_failure(self):
        AgentComputeSession.objects.create(agent=self.agent, state=AgentComputeSession.State.RUNNING)
        redis_mock = type("RedisMock", (), {})()
        redis_mock.deleted_keys = []
        redis_mock.delete = lambda key: redis_mock.deleted_keys.append(key) or 1

        with patch("api.tasks.sandbox_compute.sandbox_compute_enabled", return_value=True), patch(
            "api.tasks.sandbox_compute.get_redis_client",
            return_value=redis_mock,
        ), patch("api.tasks.sandbox_compute.SandboxComputeService") as mock_service_cls:
            mock_service_cls.return_value._sync_workspace_push.return_value = {
                "status": "error",
                "message": "push failed",
            }
            result = sync_filespace_after_call(str(self.agent.id), source="tool_request")

        self.assertEqual(result.get("status"), "error")
        self.assertEqual(
            redis_mock.deleted_keys,
            [_post_sync_queue_key(str(self.agent.id))],
        )
