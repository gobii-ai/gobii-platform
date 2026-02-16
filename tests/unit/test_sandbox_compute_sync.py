from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import Max
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.files.filespace_service import write_bytes_to_dir
from api.models import AgentComputeSession, AgentFsNode, BrowserUseAgent, PersistentAgent
from api.services.sandbox_compute import SandboxComputeService
from api.services.sandbox_filespace_sync import build_filespace_pull_manifest


class _DummyBackend:
    def __init__(self) -> None:
        self.sync_calls: list[dict] = []

    def deploy_or_resume(self, agent, session):
        return None

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
