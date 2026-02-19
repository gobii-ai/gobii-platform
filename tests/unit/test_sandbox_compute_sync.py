from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import Max
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from api.agent.files.filespace_service import write_bytes_to_dir
from api.models import AgentComputeSession, AgentFsNode, BrowserUseAgent, MCPServerConfig, PersistentAgent
from api.services.sandbox_compute import (
    SandboxComputeService,
    SandboxSessionUpdate,
    _build_mcp_server_payload,
    _build_nonzero_exit_error_payload,
)
from api.services.sandbox_filespace_sync import build_filespace_pull_manifest


class _DummyBackend:
    def __init__(self) -> None:
        self.sync_calls: list[dict] = []

    def deploy_or_resume(self, agent, session):
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
        self.assertEqual(backend.sync_calls[0]["direction"], "pull")
        self.assertEqual(backend.sync_calls[1]["direction"], "pull")

        first_since = mock_manifest.call_args_list[0].kwargs.get("since")
        second_since = mock_manifest.call_args_list[1].kwargs.get("since")
        self.assertIsNone(first_since)
        self.assertEqual(second_since, cursor_one)

        session = AgentComputeSession.objects.get(agent=self.agent)
        self.assertEqual(session.last_filespace_pull_at, cursor_two)

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

    @override_settings(
        MCP_REMOTE_BRIDGE_ENABLED=True,
        PUBLIC_SITE_URL="https://app.example.com",
        MCP_REMOTE_BRIDGE_POLL_INTERVAL_SECONDS=3,
        MCP_REMOTE_BRIDGE_AUTH_TIMEOUT_SECONDS=222,
        MCP_REMOTE_BRIDGE_SHARED_SECRET="bridge-secret",
    )
    def test_build_mcp_server_payload_rewrites_mcp_remote_and_injects_bridge(self):
        with patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery"):
            server = MCPServerConfig.objects.create(
                scope=MCPServerConfig.Scope.USER,
                user=self.user,
                name="remote-test-server",
                display_name="Remote Test",
                command="npx",
                command_args=["@modelcontextprotocol/mcp-remote", "https://remote.example.com/mcp"],
                auth_method=MCPServerConfig.AuthMethod.NONE,
            )

        payload, runtime = _build_mcp_server_payload(str(server.id))

        self.assertIsNotNone(runtime)
        self.assertIsInstance(payload, dict)

        payload = payload or {}
        self.assertIn("@gobii-ai/remote-mcp-remote", payload["command_args"])
        self.assertIn("--auth-mode", payload["command_args"])
        self.assertIn("bridge", payload["command_args"])
        self.assertIn("--redirect-url", payload["command_args"])
        self.assertIn("--auth-bridge-poll-url", payload["command_args"])
        self.assertIn("--auth-bridge-notify-url", payload["command_args"])

        bridge = payload.get("mcp_remote_bridge") or {}
        self.assertTrue(
            str(bridge.get("redirect_url", "")).startswith("https://app.example.com/api/mcp/auth/callback/")
        )
        self.assertIn("https://app.example.com/api/mcp/auth/poll/", str(bridge.get("poll_url", "")))
        self.assertIn("session_id=", str(bridge.get("poll_url", "")))
        self.assertTrue(
            str(bridge.get("notify_url", "")).startswith("https://app.example.com/api/mcp/auth/notify/")
        )
        self.assertIn("bridge_token=bridge-secret", bridge.get("redirect_url", ""))
        self.assertIn("bridge_token=bridge-secret", bridge.get("poll_url", ""))
        self.assertIn("bridge_token=bridge-secret", bridge.get("notify_url", ""))
