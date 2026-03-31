import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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

        with patch("sandbox_compute_server.sync.requests.get", return_value=response) as get_mock:
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

        with patch("sandbox_compute_server.sync._agent_workspace", return_value=Path("/private/tmp/workspace")), patch(
            "sandbox_compute_server.sync._store_proxy_env",
            return_value=True,
        ), patch(
            "sandbox_compute_server.sync._proxy_env_from_manifest",
            return_value={"HTTP_PROXY": "socks5://proxy.internal:1080"},
        ), patch(
            "sandbox_compute_server.sync._load_manifest",
            return_value={"files": {}, "deleted": {}},
        ), patch(
            "sandbox_compute_server.sync._decode_content",
            return_value=None,
        ), patch(
            "sandbox_compute_server.sync._download_file",
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
            "sandbox_compute_server.sync._save_manifest"
        ):
            result = _handle_sync_filespace(payload)

        self.assertEqual(result["status"], "ok")
        download_mock.assert_called_once_with(
            "https://example.com/data.txt",
            5,
            {"HTTP_PROXY": "socks5://proxy.internal:1080"},
        )


if __name__ == "__main__":
    unittest.main()
