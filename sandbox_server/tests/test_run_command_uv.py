import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox_server.run import _handle_run_command, _run_managed_process


class RunCommandUvTests(unittest.TestCase):
    def test_managed_process_terminates_background_children(self):
        result = _run_managed_process(
            "sleep 30 & echo $!",
            shell=True,
            cwd="/tmp",
            env=os.environ.copy(),
            timeout=5,
        )
        child_pid = int(result.stdout.strip())
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, signal.SIGCONT)

    def test_run_command_creates_uv_project_env_under_gobii_instead_of_dot_venv(self):
        with tempfile.TemporaryDirectory() as tmp_dir, tempfile.TemporaryDirectory() as runtime_cache:
            workspace = Path(tmp_dir)
            workspace.joinpath("pyproject.toml").write_text(
                '[project]\nname = "sandbox-test"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )

            payload = {
                "agent_id": "agent-1",
                "command": "uv sync --no-install-project",
            }

            with patch.dict(
                "os.environ",
                {
                    "PATH": os.environ["PATH"],
                    "HOME": os.environ.get("HOME", tmp_dir),
                    "SANDBOX_RUNTIME_CACHE_ROOT": runtime_cache,
                },
                clear=True,
            ), patch("sandbox_server.run._require_agent_id", return_value=("agent-1", None)), patch(
                "sandbox_server.run._agent_workspace",
                return_value=workspace,
            ), patch("sandbox_server.run._store_proxy_env"):
                result = _handle_run_command(payload)
                uv_project_env_exists = workspace.joinpath(".gobii", "uv-project-env").is_dir()
                dot_venv_exists = workspace.joinpath(".venv").exists()

        self.assertEqual(result["status"], "ok")
        self.assertTrue(uv_project_env_exists)
        self.assertFalse(dot_venv_exists)


if __name__ == "__main__":
    unittest.main()
