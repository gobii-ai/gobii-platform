import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox_server.app import application
from sandbox_server.config import _agent_workspace, _sandbox_env


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

    def test_agent_workspace_isolated_per_agent_by_default(self):
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

    def test_agent_workspace_uses_shared_root_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            "os.environ",
            {
                "SANDBOX_WORKSPACE_ROOT": tmp_dir,
                "SANDBOX_AGENT_WORKSPACE_LAYOUT": "isolated",
            },
            clear=False,
        ):
            first = _agent_workspace("agent-1")
            second = _agent_workspace("agent/2")

            self.assertEqual(first, Path(tmp_dir))
            self.assertEqual(second, Path(tmp_dir))
            self.assertTrue(first.is_dir())

    def test_sandbox_env_sets_internal_uv_defaults(self):
        with tempfile.TemporaryDirectory() as runtime_cache:
            with patch.dict(
                "os.environ",
                {"SANDBOX_RUNTIME_CACHE_ROOT": runtime_cache, "PATH": "/usr/bin"},
                clear=True,
            ):
                env = _sandbox_env(Path("/tmp/workspace/agent-1"))

        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env["HOME"], f"{runtime_cache}/agent-1/home")
        self.assertEqual(env["TMPDIR"], f"{runtime_cache}/agent-1/tmp")
        self.assertEqual(env["UV_PROJECT_ENVIRONMENT"], "/tmp/workspace/agent-1/.gobii/uv-project-env")
        self.assertEqual(env["UV_CACHE_DIR"], f"{runtime_cache}/agent-1/uv-cache")
        self.assertEqual(env["UV_TOOL_DIR"], f"{runtime_cache}/agent-1/uv-tools")
        self.assertEqual(env["XDG_CACHE_HOME"], f"{runtime_cache}/agent-1/xdg-cache")
        self.assertEqual(env["XDG_CONFIG_HOME"], f"{runtime_cache}/agent-1/xdg-config")
        self.assertEqual(env["XDG_DATA_HOME"], f"{runtime_cache}/agent-1/xdg-data")
        self.assertEqual(env["NPM_CONFIG_CACHE"], f"{runtime_cache}/agent-1/npm")
        self.assertEqual(env["npm_config_cache"], f"{runtime_cache}/agent-1/npm")
        self.assertEqual(env["PIP_CACHE_DIR"], f"{runtime_cache}/agent-1/pip")

    def test_sandbox_env_rehomes_generic_tmp_defaults(self):
        with tempfile.TemporaryDirectory() as runtime_cache:
            with patch.dict(
                "os.environ",
                {
                    "SANDBOX_RUNTIME_CACHE_ROOT": runtime_cache,
                    "PATH": "/usr/bin",
                    "HOME": "/tmp",
                    "TMPDIR": "/tmp",
                    "XDG_CACHE_HOME": "/tmp/.cache",
                    "NPM_CONFIG_CACHE": "/tmp/.npm",
                    "npm_config_cache": "/tmp/.npm",
                    "PIP_CACHE_DIR": "/tmp/.cache/pip",
                    "UV_PROJECT_ENVIRONMENT": "/app/.venv",
                },
                clear=True,
            ):
                env = _sandbox_env(Path("/tmp/workspace/agent-1"))

        self.assertEqual(env["HOME"], f"{runtime_cache}/agent-1/home")
        self.assertEqual(env["TMPDIR"], f"{runtime_cache}/agent-1/tmp")
        self.assertEqual(env["XDG_CACHE_HOME"], f"{runtime_cache}/agent-1/xdg-cache")
        self.assertEqual(env["NPM_CONFIG_CACHE"], f"{runtime_cache}/agent-1/npm")
        self.assertEqual(env["PIP_CACHE_DIR"], f"{runtime_cache}/agent-1/pip")
        self.assertEqual(env["UV_PROJECT_ENVIRONMENT"], "/tmp/workspace/agent-1/.gobii/uv-project-env")

    def test_standalone_package_import_does_not_require_api_package(self):
        repo_root = Path(__file__).resolve().parents[2]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(repo_root)
        script = """
import importlib.abc
import sys


class BlockApi(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "api" or fullname.startswith("api."):
            raise ModuleNotFoundError("blocked api import")
        return None


sys.meta_path.insert(0, BlockApi())
import sandbox_server
print("ok")
"""

        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        self.assertIn("ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()
