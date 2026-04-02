import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox_server.run import _handle_python_exec
from sandbox_server.tools import _handle_tool_request


class PythonExecEnvTests(unittest.TestCase):
    def test_sandbox_env_does_not_merge_proxy_vars_from_environment(self):
        with patch.dict(
            "os.environ",
            {
                "PATH": "/usr/bin",
                "SANDBOX_RUNTIME_CACHE_ROOT": "/tmp/runtime-cache",
                "HTTP_PROXY": "http://proxy.internal:3128",
                "HTTPS_PROXY": "http://proxy.internal:3128",
                "NO_PROXY": "localhost,127.0.0.1",
            },
            clear=True,
        ):
            from sandbox_server.config import _sandbox_env

            env = _sandbox_env(Path("/tmp/workspace"))

        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env["HOME"], "/tmp/runtime-cache/workspace/home")
        self.assertEqual(env["TMPDIR"], "/tmp/runtime-cache/workspace/tmp")
        self.assertEqual(env["UV_PROJECT_ENVIRONMENT"], "/tmp/workspace/.gobii/uv-project-env")
        self.assertEqual(env["UV_CACHE_DIR"], "/tmp/runtime-cache/workspace/uv-cache")
        self.assertEqual(env["UV_TOOL_DIR"], "/tmp/runtime-cache/workspace/uv-tools")
        self.assertEqual(env["XDG_CACHE_HOME"], "/tmp/runtime-cache/workspace/xdg-cache")
        self.assertEqual(env["NPM_CONFIG_CACHE"], "/tmp/runtime-cache/workspace/npm")
        self.assertEqual(env["PIP_CACHE_DIR"], "/tmp/runtime-cache/workspace/pip")
        self.assertNotIn("HTTP_PROXY", env)
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertNotIn("NO_PROXY", env)

    def test_handle_python_exec_passes_env_to_sandbox_env(self):
        payload = {
            "agent_id": "agent-1",
            "code": "print('hello')",
            "env": {"OPENAI_API_KEY": "sk-test"},
            "trusted_env_keys": ["OPENAI_API_KEY"],
        }
        completed = type(
            "CompletedProcess",
            (),
            {"returncode": 0, "stdout": "hello\n", "stderr": ""},
        )()

        with patch("sandbox_server.run._require_agent_id", return_value=("agent-1", None)), patch(
            "sandbox_server.run._agent_workspace",
            return_value=Path("/tmp/workspace"),
        ), patch("sandbox_server.run._store_proxy_env"), patch(
            "sandbox_server.run._normalize_timeout",
            return_value=30,
        ), patch(
            "sandbox_server.run._sandbox_env",
            return_value={"PATH": "/usr/bin", "OPENAI_API_KEY": "sk-test"},
        ) as sandbox_env_mock, patch(
            "sandbox_server.run.subprocess.run",
            return_value=completed,
        ) as run_mock:
            result = _handle_python_exec(payload)

        self.assertEqual(result["status"], "ok")
        sandbox_env_mock.assert_called_once_with(
            Path("/tmp/workspace"),
            {"OPENAI_API_KEY": "sk-test"},
            trusted_env_keys=["OPENAI_API_KEY"],
        )
        self.assertEqual(run_mock.call_args.kwargs["env"]["OPENAI_API_KEY"], "sk-test")

    def test_handle_python_exec_merges_proxy_env_from_manifest(self):
        payload = {
            "agent_id": "agent-1",
            "code": "print('hello')",
        }
        completed = type(
            "CompletedProcess",
            (),
            {"returncode": 0, "stdout": "hello\n", "stderr": ""},
        )()

        with patch("sandbox_server.run._require_agent_id", return_value=("agent-1", None)), patch(
            "sandbox_server.run._agent_workspace",
            return_value=Path("/tmp/workspace"),
        ), patch("sandbox_server.run._store_proxy_env"), patch(
            "sandbox_server.run._proxy_env_from_manifest",
            return_value={"HTTP_PROXY": "http://proxy.internal:3128", "http_proxy": "http://proxy.internal:3128"},
        ), patch(
            "sandbox_server.run._normalize_timeout",
            return_value=30,
        ), patch(
            "sandbox_server.run._sandbox_env",
            return_value={"PATH": "/usr/bin"},
        ), patch(
            "sandbox_server.run.subprocess.run",
            return_value=completed,
        ) as run_mock:
            result = _handle_python_exec(payload)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(run_mock.call_args.kwargs["env"]["HTTP_PROXY"], "http://proxy.internal:3128")
        self.assertEqual(run_mock.call_args.kwargs["env"]["http_proxy"], "http://proxy.internal:3128")

    def test_handle_python_exec_defaults_cwd_to_agent_workspace(self):
        payload = {
            "agent_id": "agent-1",
            "code": "print('hello')",
        }
        completed = type(
            "CompletedProcess",
            (),
            {"returncode": 0, "stdout": "hello\n", "stderr": ""},
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
            result = _handle_python_exec(payload)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(run_mock.call_args.kwargs["cwd"], "/tmp/workspace")

    def test_handle_python_exec_ignores_non_dict_env(self):
        payload = {
            "agent_id": "agent-1",
            "code": "print('hello')",
            "env": "OPENAI_API_KEY=sk-test",
        }
        completed = type(
            "CompletedProcess",
            (),
            {"returncode": 0, "stdout": "hello\n", "stderr": ""},
        )()

        with patch("sandbox_server.run._require_agent_id", return_value=("agent-1", None)), patch(
            "sandbox_server.run._agent_workspace",
            return_value=Path("/tmp/workspace"),
        ), patch("sandbox_server.run._store_proxy_env"), patch(
            "sandbox_server.run._normalize_timeout",
            return_value=30,
        ), patch(
            "sandbox_server.run._sandbox_env",
            return_value={"PATH": "/usr/bin"},
        ) as sandbox_env_mock, patch(
            "sandbox_server.run.subprocess.run",
            return_value=completed,
        ):
            result = _handle_python_exec(payload)

        self.assertEqual(result["status"], "ok")
        sandbox_env_mock.assert_called_once_with(
            Path("/tmp/workspace"),
            None,
            trusted_env_keys=[],
        )

    def test_tool_request_python_exec_preserves_env_payload(self):
        payload = {
            "agent_id": "agent-1",
            "tool_name": "python_exec",
            "params": {
                "code": "print('hello')",
                "env": {"OPENAI_API_KEY": "sk-test"},
                "trusted_env_keys": ["OPENAI_API_KEY"],
            },
        }

        with patch("sandbox_server.tools._require_agent_id", return_value=("agent-1", None)), patch(
            "sandbox_server.tools._agent_workspace",
            return_value=Path("/tmp/workspace"),
        ), patch("sandbox_server.tools._store_proxy_env"), patch(
            "sandbox_server.tools._trace_context",
            return_value=(None, None),
        ), patch(
            "sandbox_server.tools._handle_python_exec",
            return_value={"status": "ok"},
        ) as python_exec_mock:
            result = _handle_tool_request(payload)

        self.assertEqual(result["status"], "ok")
        python_exec_mock.assert_called_once_with(
            {
                "agent_id": "agent-1",
                "code": "print('hello')",
                "env": {"OPENAI_API_KEY": "sk-test"},
                "trusted_env_keys": ["OPENAI_API_KEY"],
            }
        )


if __name__ == "__main__":
    unittest.main()
