import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox_server.config import _sandbox_env
from sandbox_server.run import _handle_run_command


class EnvAllowlistTrustedKeysTests(unittest.TestCase):
    def test_sandbox_env_blocks_untrusted_extra_env_keys(self):
        with patch.dict(
            "os.environ",
            {"SANDBOX_COMPUTE_ALLOWED_ENV_KEYS": "PATH,HOME", "PATH": "/usr/bin", "HOME": "/tmp/home"},
            clear=False,
        ):
            env = _sandbox_env(
                extra_env={"POSTGRES_CONNECTION_STRING": "postgres://example"},
            )

        self.assertNotIn("POSTGRES_CONNECTION_STRING", env)

    def test_sandbox_env_allows_trusted_extra_env_keys(self):
        with patch.dict(
            "os.environ",
            {"SANDBOX_COMPUTE_ALLOWED_ENV_KEYS": "PATH,HOME", "PATH": "/usr/bin", "HOME": "/tmp/home"},
            clear=False,
        ):
            env = _sandbox_env(
                extra_env={"POSTGRES_CONNECTION_STRING": "postgres://example"},
                trusted_env_keys=["POSTGRES_CONNECTION_STRING"],
            )

        self.assertEqual(env.get("POSTGRES_CONNECTION_STRING"), "postgres://example")
        self.assertEqual(env.get("UV_PROJECT_ENVIRONMENT"), ".gobii/uv-project-env")

    def test_run_command_forwards_trusted_env_keys(self):
        payload = {
            "agent_id": "agent-1",
            "command": "echo ok",
            "env": {"POSTGRES_CONNECTION_STRING": "postgres://example"},
            "trusted_env_keys": ["POSTGRES_CONNECTION_STRING"],
        }
        completed = type(
            "CompletedProcess",
            (),
            {"returncode": 0, "stdout": "ok\n", "stderr": ""},
        )()

        with patch("sandbox_server.run._require_agent_id", return_value=("agent-1", None)), patch(
            "sandbox_server.run._agent_workspace",
            return_value=Path("/tmp/workspace"),
        ), patch("sandbox_server.run._store_proxy_env"), patch(
            "sandbox_server.run._normalize_timeout",
            return_value=30,
        ), patch(
            "sandbox_server.run._sandbox_env",
            return_value={"PATH": "/usr/bin", "POSTGRES_CONNECTION_STRING": "postgres://example"},
        ) as sandbox_env_mock, patch(
            "sandbox_server.run.subprocess.run",
            return_value=completed,
        ):
            result = _handle_run_command(payload)

        self.assertEqual(result["status"], "ok")
        sandbox_env_mock.assert_called_once_with(
            Path("/tmp/workspace"),
            {"POSTGRES_CONNECTION_STRING": "postgres://example"},
            trusted_env_keys=["POSTGRES_CONNECTION_STRING"],
        )

    def test_run_command_defaults_cwd_to_agent_workspace(self):
        payload = {
            "agent_id": "agent-1",
            "command": "pwd",
        }
        completed = type(
            "CompletedProcess",
            (),
            {"returncode": 0, "stdout": "/tmp/workspace\n", "stderr": ""},
        )()

        with patch("sandbox_server.run._require_agent_id", return_value=("agent-1", None)), patch(
            "sandbox_server.run._agent_workspace",
            return_value=Path("/tmp/workspace"),
        ), patch("sandbox_server.run._store_proxy_env"), patch(
            "sandbox_server.run._normalize_timeout",
            return_value=30,
        ), patch(
            "sandbox_server.run._sandbox_env",
            return_value={"PATH": "/usr/bin", "UV_PROJECT_ENVIRONMENT": ".gobii/uv-project-env"},
        ), patch(
            "sandbox_server.run.subprocess.run",
            return_value=completed,
        ) as run_mock:
            result = _handle_run_command(payload)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(run_mock.call_args.kwargs["cwd"], "/tmp/workspace")


if __name__ == "__main__":
    unittest.main()
