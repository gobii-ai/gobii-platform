import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox_server.mcp import _prepare_runtime_proxy_env
from sandbox_server.sync import _download_file


class TransparentProxyRoutingTests(unittest.TestCase):
    def test_prepare_runtime_proxy_env_injects_stored_proxy_vars_into_stdio_runtime(self):
        runtime = {"command": "npx", "env": {"KEEP": "1"}}
        payload = {
            "agent_id": "agent-1",
            "proxy_env": {"HTTP_PROXY": "http://proxy.internal:3128"},
        }

        with patch.dict(
            "sandbox_server.mcp.os.environ",
            {
                "HTTP_PROXY": "http://sandbox-egress:3128",
                "http_proxy": "http://sandbox-egress:3128",
                "ALL_PROXY": "socks5://sandbox-egress:1080",
            },
            clear=False,
        ), patch("sandbox_server.mcp._require_agent_id", return_value=("agent-1", None)), patch(
            "sandbox_server.mcp._agent_workspace",
            return_value=Path("/tmp/workspace"),
        ), patch("sandbox_server.mcp._store_proxy_env") as store_proxy_env_mock, patch(
            "sandbox_server.mcp._proxy_env_from_manifest",
            return_value={"HTTP_PROXY": "http://proxy.internal:3128", "http_proxy": "http://proxy.internal:3128"},
        ):
            agent_id, error = _prepare_runtime_proxy_env(payload, runtime)

        self.assertEqual(agent_id, "agent-1")
        self.assertIsNone(error)
        self.assertEqual(runtime["env"]["KEEP"], "1")
        self.assertEqual(runtime["env"]["HTTP_PROXY"], "http://proxy.internal:3128")
        self.assertEqual(runtime["env"]["http_proxy"], "http://proxy.internal:3128")
        self.assertEqual(runtime["env"]["ALL_PROXY"], "socks5://sandbox-egress:1080")
        store_proxy_env_mock.assert_called_once()

    def test_prepare_runtime_proxy_env_uses_process_proxy_vars_when_manifest_empty(self):
        runtime = {"command": "npm", "env": {"KEEP": "1"}}
        payload = {"agent_id": "agent-1"}

        with patch.dict(
            "sandbox_server.mcp.os.environ",
            {
                "HTTP_PROXY": "http://sandbox-egress:3128",
                "HTTPS_PROXY": "http://sandbox-egress:3128",
                "ALL_PROXY": "socks5://sandbox-egress:1080",
                "NO_PROXY": "localhost,127.0.0.1",
            },
            clear=False,
        ), patch("sandbox_server.mcp._require_agent_id", return_value=("agent-1", None)), patch(
            "sandbox_server.mcp._agent_workspace",
            return_value=Path("/tmp/workspace"),
        ), patch("sandbox_server.mcp._store_proxy_env") as store_proxy_env_mock, patch(
            "sandbox_server.mcp._proxy_env_from_manifest",
            return_value={},
        ):
            agent_id, error = _prepare_runtime_proxy_env(payload, runtime)

        self.assertEqual(agent_id, "agent-1")
        self.assertIsNone(error)
        self.assertEqual(runtime["env"]["KEEP"], "1")
        self.assertEqual(runtime["env"]["HTTP_PROXY"], "http://sandbox-egress:3128")
        self.assertEqual(runtime["env"]["HTTPS_PROXY"], "http://sandbox-egress:3128")
        self.assertEqual(runtime["env"]["ALL_PROXY"], "socks5://sandbox-egress:1080")
        self.assertEqual(runtime["env"]["NO_PROXY"], "localhost,127.0.0.1")
        store_proxy_env_mock.assert_called_once()

    def test_download_file_uses_default_requests_transport(self):
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
            content = _download_file("https://example.com/file.txt", expected_size=5)

        self.assertEqual(content, b"hello")
        self.assertEqual(get_mock.call_args.kwargs, {"stream": True, "timeout": 30})


if __name__ == "__main__":
    unittest.main()
