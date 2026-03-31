import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox_server.app import application
from sandbox_server.config import _agent_workspace


class SandboxAppAndConfigTests(unittest.TestCase):
    def test_application_returns_500_when_handler_raises(self):
        body = io.BytesIO(json.dumps({"agent_id": "agent-1"}).encode("utf-8"))
        environ = {
            "PATH_INFO": "/sandbox/compute/run_command",
            "REQUEST_METHOD": "POST",
            "CONTENT_LENGTH": str(body.getbuffer().nbytes),
            "wsgi.input": body,
            "HTTP_AUTHORIZATION": "Bearer sandbox-token",
        }
        captured = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = headers

        def _raise(_payload):
            raise RuntimeError("boom")

        with patch.dict("os.environ", {"SANDBOX_COMPUTE_API_TOKEN": "sandbox-token"}, clear=False), patch.dict(
            "sandbox_server.app._ROUTES",
            {"/sandbox/compute/run_command": _raise},
            clear=False,
        ):
            response = application(environ, start_response)

        self.assertEqual(captured["status"], "500 Internal Server Error")
        self.assertEqual(
            json.loads(b"".join(response)),
            {"status": "error", "message": "Sandbox compute request failed."},
        )

    def test_agent_workspace_isolated_per_agent(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            "os.environ",
            {"SANDBOX_WORKSPACE_ROOT": tmp_dir},
            clear=False,
        ):
            first = _agent_workspace("agent-1")
            second = _agent_workspace("agent/2")

            self.assertEqual(first, Path(tmp_dir) / "agent-1")
            self.assertEqual(second, Path(tmp_dir) / "agent_2")
            self.assertNotEqual(first, second)
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())


if __name__ == "__main__":
    unittest.main()
