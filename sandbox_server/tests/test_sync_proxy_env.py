import unittest
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sandbox_server.server.internal_paths import CUSTOM_TOOL_SQLITE_FILESPACE_PATH
from sandbox_server.sync import _download_file, _handle_sync_filespace


class SyncProxyEnvTests(unittest.TestCase):
    def test_download_file_forwards_proxy_env_to_requests(self):
        response = type(
            "Response",
            (),
            {
                "__enter__": lambda self: self,
                "__exit__": lambda self, exc_type, exc, tb: False,
                "raise_for_status": lambda self: None,
                "iter_content": lambda self, chunk_size=0: iter([b"hello"]),
            },
        )()

        with patch("sandbox_server.sync.requests.get", return_value=response) as get_mock:
            content = _download_file(
                "https://example.com/file.txt",
                expected_size=5,
                proxy_env={
                    "HTTP_PROXY": "socks5://proxy.internal:1080",
                    "HTTPS_PROXY": "socks5://proxy.internal:1080",
                    "NO_PROXY": "localhost",
                },
            )

        self.assertEqual(content, b"hello")
        self.assertEqual(
            get_mock.call_args.kwargs,
            {
                "stream": True,
                "timeout": 30,
                "proxies": {
                    "http": "socks5://proxy.internal:1080",
                    "https": "socks5://proxy.internal:1080",
                    "no_proxy": "localhost",
                },
            },
        )

    def test_handle_sync_filespace_pull_uses_proxy_env_for_downloads(self):
        payload = {
            "agent_id": "agent-1",
            "direction": "pull",
            "files": [
                {
                    "path": "/data.txt",
                    "download_url": "https://example.com/data.txt",
                    "size_bytes": 5,
                    "updated_at": "2026-03-24T12:00:00+00:00",
                    "checksum_sha256": "",
                }
            ],
            "proxy_env": {"HTTP_PROXY": "socks5://proxy.internal:1080"},
        }

        with patch("sandbox_server.sync._agent_workspace", return_value=Path("/private/tmp/workspace")), patch(
            "sandbox_server.sync._store_proxy_env",
            return_value=True,
        ), patch(
            "sandbox_server.sync._proxy_env_from_manifest",
            return_value={"HTTP_PROXY": "socks5://proxy.internal:1080"},
        ), patch(
            "sandbox_server.sync._load_manifest",
            return_value={"files": {}, "deleted": {}},
        ), patch(
            "sandbox_server.sync._decode_content",
            return_value=None,
        ), patch(
            "sandbox_server.sync._download_file",
            return_value=b"hello",
        ) as download_mock, patch(
            "pathlib.Path.mkdir"
        ), patch(
            "pathlib.Path.stat",
            return_value=SimpleNamespace(st_mtime=123.0, st_size=5),
        ), patch(
            "builtins.open",
            create=True,
        ), patch(
            "sandbox_server.sync._save_manifest"
        ):
            result = _handle_sync_filespace(payload)

        self.assertEqual(result["status"], "ok")
        download_mock.assert_called_once_with(
            "https://example.com/data.txt",
            5,
            {"HTTP_PROXY": "socks5://proxy.internal:1080"},
        )

    def test_handle_sync_filespace_pull_scans_workspace_once_per_request(self):
        payload = {
            "agent_id": "agent-1",
            "direction": "pull",
            "files": [
                {"path": "/one.txt", "content": "hello", "updated_at": "2026-03-24T12:00:00+00:00"},
                {"path": "/two.txt", "content": "world", "updated_at": "2026-03-24T12:00:00+00:00"},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            agent_root = Path(tmp_dir).resolve()
            with patch(
                "sandbox_server.sync._agent_workspace",
                return_value=agent_root,
            ), patch(
                "sandbox_server.sync._store_proxy_env",
                return_value=False,
            ), patch(
                "sandbox_server.sync._proxy_env_from_manifest",
                return_value=None,
            ), patch(
                "sandbox_server.sync._load_manifest",
                return_value={"files": {}, "deleted": {}},
            ), patch(
                "sandbox_server.sync._save_manifest"
            ), patch(
                "sandbox_server.sync._workspace_size_bytes",
                return_value=0,
            ) as workspace_size_mock:
                result = _handle_sync_filespace(payload)

        self.assertEqual(result["status"], "ok")
        workspace_size_mock.assert_called_once()

    def test_handle_sync_filespace_push_includes_requested_internal_paths(self):
        payload = {
            "agent_id": "agent-1",
            "direction": "push",
            "internal_paths": [CUSTOM_TOOL_SQLITE_FILESPACE_PATH],
        }

        with TemporaryDirectory() as tmp_dir:
            agent_root = Path(tmp_dir).resolve()
            with patch(
                "sandbox_server.sync._agent_workspace",
                return_value=agent_root,
            ), patch(
                "sandbox_server.sync._store_proxy_env",
                return_value=False,
            ), patch(
                "sandbox_server.sync._proxy_env_from_manifest",
                return_value=None,
            ), patch(
                "sandbox_server.sync._load_manifest",
                return_value={"files": {}, "deleted": {}},
            ), patch(
                "sandbox_server.sync._save_manifest"
            ):
                sqlite_path = agent_root / ".gobii" / "internal" / "custom_tool_agent_state.sqlite3"
                sqlite_path.parent.mkdir(parents=True, exist_ok=True)
                sqlite_path.write_bytes(b"sqlite bytes")

                result = _handle_sync_filespace(payload)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["changes"]), 1)
        change = result["changes"][0]
        self.assertEqual(change["path"], CUSTOM_TOOL_SQLITE_FILESPACE_PATH)
        self.assertEqual(change["content_b64"], "c3FsaXRlIGJ5dGVz")
        self.assertEqual(change["mime_type"], "application/vnd.sqlite3")
        self.assertEqual(change["checksum_sha256"], sha256(b"sqlite bytes").hexdigest())

    def test_handle_sync_filespace_push_requested_internal_paths_bypass_since_filter(self):
        payload = {
            "agent_id": "agent-1",
            "direction": "push",
            "since": "3026-03-24T12:00:00+00:00",
            "internal_paths": [CUSTOM_TOOL_SQLITE_FILESPACE_PATH],
        }

        with TemporaryDirectory() as tmp_dir:
            agent_root = Path(tmp_dir).resolve()
            with patch(
                "sandbox_server.sync._agent_workspace",
                return_value=agent_root,
            ), patch(
                "sandbox_server.sync._store_proxy_env",
                return_value=False,
            ), patch(
                "sandbox_server.sync._proxy_env_from_manifest",
                return_value=None,
            ), patch(
                "sandbox_server.sync._load_manifest",
                return_value={"files": {}, "deleted": {}},
            ), patch(
                "sandbox_server.sync._save_manifest"
            ):
                sqlite_path = agent_root / ".gobii" / "internal" / "custom_tool_agent_state.sqlite3"
                sqlite_path.parent.mkdir(parents=True, exist_ok=True)
                sqlite_path.write_bytes(b"sqlite bytes")

                result = _handle_sync_filespace(payload)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["changes"]), 1)
        self.assertEqual(result["changes"][0]["path"], CUSTOM_TOOL_SQLITE_FILESPACE_PATH)

    def test_handle_sync_filespace_pull_clears_custom_tool_sqlite_sidecars_before_write(self):
        payload = {
            "agent_id": "agent-1",
            "direction": "pull",
            "files": [
                {
                    "path": CUSTOM_TOOL_SQLITE_FILESPACE_PATH,
                    "content_b64": "bmV3LXNxbGl0ZS1ieXRlcw==",
                    "checksum_sha256": sha256(b"new-sqlite-bytes").hexdigest(),
                    "updated_at": "3026-03-24T12:00:00+00:00",
                }
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            agent_root = Path(tmp_dir).resolve()
            with patch(
                "sandbox_server.sync._agent_workspace",
                return_value=agent_root,
            ), patch(
                "sandbox_server.sync._store_proxy_env",
                return_value=False,
            ), patch(
                "sandbox_server.sync._proxy_env_from_manifest",
                return_value=None,
            ), patch(
                "sandbox_server.sync._load_manifest",
                return_value={"files": {}, "deleted": {}},
            ), patch(
                "sandbox_server.sync._save_manifest"
            ):
                sqlite_path = agent_root / ".gobii" / "internal" / "custom_tool_agent_state.sqlite3"
                sqlite_path.parent.mkdir(parents=True, exist_ok=True)
                sqlite_path.write_bytes(b"old-bytes")
                sqlite_path.with_name(sqlite_path.name + "-wal").write_bytes(b"wal")
                sqlite_path.with_name(sqlite_path.name + "-shm").write_bytes(b"shm")

                result = _handle_sync_filespace(payload)

                self.assertEqual(result["status"], "ok")
                self.assertEqual(sqlite_path.read_bytes(), b"new-sqlite-bytes")
                self.assertFalse(sqlite_path.with_name(sqlite_path.name + "-wal").exists())
                self.assertFalse(sqlite_path.with_name(sqlite_path.name + "-shm").exists())

    def test_handle_sync_filespace_pull_clears_custom_tool_sqlite_sidecars_on_checksum_skip(self):
        existing_bytes = b"same-sqlite-bytes"
        payload = {
            "agent_id": "agent-1",
            "direction": "pull",
            "files": [
                {
                    "path": CUSTOM_TOOL_SQLITE_FILESPACE_PATH,
                    "content_b64": "c2FtZS1zcWxpdGUtYnl0ZXM=",
                    "checksum_sha256": sha256(existing_bytes).hexdigest(),
                    "updated_at": "3026-03-24T12:00:00+00:00",
                }
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            agent_root = Path(tmp_dir).resolve()
            with patch(
                "sandbox_server.sync._agent_workspace",
                return_value=agent_root,
            ), patch(
                "sandbox_server.sync._store_proxy_env",
                return_value=False,
            ), patch(
                "sandbox_server.sync._proxy_env_from_manifest",
                return_value=None,
            ), patch(
                "sandbox_server.sync._load_manifest",
                return_value={"files": {}, "deleted": {}},
            ), patch(
                "sandbox_server.sync._save_manifest"
            ):
                sqlite_path = agent_root / ".gobii" / "internal" / "custom_tool_agent_state.sqlite3"
                sqlite_path.parent.mkdir(parents=True, exist_ok=True)
                sqlite_path.write_bytes(existing_bytes)
                sqlite_path.with_name(sqlite_path.name + "-wal").write_bytes(b"wal")
                sqlite_path.with_name(sqlite_path.name + "-shm").write_bytes(b"shm")

                result = _handle_sync_filespace(payload)

                self.assertEqual(result["status"], "ok")
                self.assertEqual(sqlite_path.read_bytes(), existing_bytes)
                self.assertFalse(sqlite_path.with_name(sqlite_path.name + "-wal").exists())
                self.assertFalse(sqlite_path.with_name(sqlite_path.name + "-shm").exists())


if __name__ == "__main__":
    unittest.main()
