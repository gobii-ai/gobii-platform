import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox_server.asgi import application
from sandbox_server.server.sqlite_rsync import (
    _SQLITE_RSYNC_LOCKS,
    _authorized,
    _invalidate_replica,
    _quick_check,
    _receive_handshake,
    _sqlite_rsync_lock,
    _sqlite_path,
)


class SQLiteRsyncEndpointTests(unittest.TestCase):
    def test_asgi_adapter_retains_existing_http_health_handler(self):
        events = [{"type": "http.request", "body": b"", "more_body": False}]
        sent = []

        async def receive():
            return events.pop(0)

        async def send(message):
            sent.append(message)

        asyncio.run(
            application(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/healthz",
                    "root_path": "",
                    "query_string": b"",
                    "http_version": "1.1",
                    "headers": [],
                    "server": ("localhost", 8080),
                },
                receive,
                send,
            )
        )

        self.assertEqual(sent[0]["type"], "http.response.start")
        self.assertEqual(sent[0]["status"], 200)

    def test_authorization_requires_matching_internal_token(self):
        with patch.dict(os.environ, {"SANDBOX_COMPUTE_API_TOKEN": "expected"}, clear=False):
            self.assertTrue(
                _authorized(
                    {
                        "headers": [
                            (b"x-sandbox-compute-token", b"expected"),
                        ]
                    }
                )
            )
            self.assertFalse(
                _authorized(
                    {
                        "headers": [
                            (b"x-sandbox-compute-token", b"wrong"),
                        ]
                    }
                )
            )

    def test_replica_path_is_fixed_under_agent_workspace(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {
                "SANDBOX_WORKSPACE_ROOT": tmp_dir,
                "SANDBOX_AGENT_WORKSPACE_LAYOUT": "shared",
            },
            clear=False,
        ):
            sqlite_path = _sqlite_path("../../unexpected")

        self.assertEqual(sqlite_path.name, "custom_tool_agent_state.sqlite3")
        self.assertIn(".gobii/internal", sqlite_path.as_posix())
        self.assertTrue(sqlite_path.is_relative_to(Path(tmp_dir)))

    def test_handshake_must_match_configured_pod_agent(self):
        events = [
            {
                "type": "websocket.receive",
                "text": json.dumps({"agent_id": "other-agent", "mode": "origin"}),
            }
        ]

        async def receive():
            return events.pop(0)

        with patch.dict(os.environ, {"SANDBOX_AGENT_ID": "pod-agent"}, clear=False):
            self.assertEqual(asyncio.run(_receive_handshake(receive)), (None, None))

    def test_shared_backend_scopes_sync_locks_per_agent(self):
        _SQLITE_RSYNC_LOCKS.clear()
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {
                "SANDBOX_WORKSPACE_ROOT": tmp_dir,
                "SANDBOX_AGENT_WORKSPACE_LAYOUT": "shared",
                "SANDBOX_AGENT_ID": "",
            },
            clear=False,
        ):
            agent_one_lock = _sqlite_rsync_lock("agent-one")
            self.assertIs(agent_one_lock, _sqlite_rsync_lock("agent-one"))
            self.assertIsNot(agent_one_lock, _sqlite_rsync_lock("agent-two"))

    def test_invalidating_replica_removes_database_and_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_path = Path(tmp_dir) / "state.sqlite3"
            connection = sqlite3.connect(sqlite_path)
            connection.execute("CREATE TABLE values_table (value TEXT)")
            connection.commit()
            connection.close()
            Path(f"{sqlite_path}-wal").touch()

            self.assertEqual(_quick_check(sqlite_path), (True, "ok"))
            _invalidate_replica(sqlite_path)

            self.assertFalse(sqlite_path.exists())
            self.assertFalse(Path(f"{sqlite_path}-wal").exists())
