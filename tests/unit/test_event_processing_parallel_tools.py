"""
Tests for guarded parallel execution of safe tool batches.
"""
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.tools.agent_variables import clear_variables, get_agent_variable, set_agent_variable
from api.agent.tools.sqlite_state import reset_sqlite_db_path, set_sqlite_db_path
from api.agent.tools.tool_manager import enable_tools
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentCompletion, PersistentAgentStep, UserQuota


def _tool_call(name: str, arguments: str) -> dict:
    return {
        "id": f"{name}_call",
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


def _completion_response(tool_calls: list[dict]) -> tuple[SimpleNamespace, dict]:
    message = SimpleNamespace(tool_calls=tool_calls, content=None)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        model_extra={
            "usage": SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                prompt_tokens_details=SimpleNamespace(cached_tokens=0),
            )
        },
    )
    usage = {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "model": "m",
        "provider": "p",
    }
    return response, usage


@tag("batch_event_parallel")
class TestParallelToolCallsExecution(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(
            username="parallel@example.com",
            email="parallel@example.com",
            password="password",
        )
        quota, _ = UserQuota.objects.get_or_create(user=user)
        quota.agent_limit = 100
        quota.save()
        cls.user = user

    def setUp(self):
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="browser-agent-for-parallel-test")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Parallel Agent",
            charter="Test charter",
            browser_use_agent=browser_agent,
        )
        enable_tools(self.agent, ["sqlite_batch"])
        clear_variables()
        self.credit_patcher = patch(
            "api.models.TaskCreditService.check_and_consume_credit_for_owner",
            return_value={"success": True, "credit": None, "error_message": None},
        )
        self.credit_patcher.start()
        self.addCleanup(self.credit_patcher.stop)
        self.addCleanup(clear_variables)

    def _run_single_iteration(self, tool_calls: list[dict]):
        from api.agent.core import event_processing as ep

        with patch("api.agent.core.event_processing.build_prompt_context") as mock_build_prompt, patch(
            "api.agent.core.event_processing._completion_with_failover"
        ) as mock_completion:
            mock_build_prompt.return_value = (
                [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
                1000,
                None,
            )
            mock_completion.return_value = _completion_response(tool_calls)
            with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
                return ep._run_agent_loop(self.agent, is_first_run=False)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_sms", return_value={"status": "success", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.execute_enabled_tool", return_value={"status": "ok", "auto_sleep_ok": True})
    def test_executes_all_tool_calls_in_one_turn(
        self,
        mock_execute_enabled,
        mock_send_sms,
        _mock_credit,
    ):
        result_usage = self._run_single_iteration(
            [
                _tool_call("sqlite_batch", '{"sql": "select 1"}'),
                _tool_call("send_sms", '{"to": "+15555550100", "body": "hi"}'),
            ]
        )

        self.assertEqual(mock_execute_enabled.call_count, 1)
        self.assertEqual(mock_send_sms.call_count, 1)

        completions = list(PersistentAgentCompletion.objects.filter(agent=self.agent))
        self.assertEqual(len(completions), 1)
        completion = completions[0]
        self.assertEqual(completion.total_tokens, 15)
        self.assertEqual(completion.steps.count(), 2)

        tool_steps = list(PersistentAgentStep.objects.filter(description__startswith="Tool call:").order_by("created_at"))
        self.assertEqual(len(tool_steps), 2)
        for step in tool_steps:
            self.assertEqual(step.completion_id, completion.id)

        self.assertEqual(result_usage["total_tokens"], 15)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_enabled_tool")
    def test_parallel_safe_batch_executes_concurrently(self, mock_execute_enabled, _mock_credit):
        active = 0
        max_active = 0
        lock = threading.Lock()

        def side_effect(_agent, _tool_name, _params, isolated_mcp=False):
            nonlocal active, max_active
            self.assertTrue(isolated_mcp)
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return {"status": "ok", "auto_sleep_ok": True}

        mock_execute_enabled.side_effect = side_effect

        self._run_single_iteration(
            [
                _tool_call("read_file", '{"path": "/exports/a.txt"}'),
                _tool_call("mcp_brightdata_search_engine", '{"query": "openai"}'),
            ]
        )

        self.assertEqual(mock_execute_enabled.call_count, 2)
        self.assertGreaterEqual(max_active, 2)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_enabled_tool")
    def test_parallel_safe_batch_respects_configured_worker_limit(self, mock_execute_enabled, _mock_credit):
        active = 0
        max_active = 0
        lock = threading.Lock()

        def side_effect(_agent, _tool_name, _params, isolated_mcp=False):
            nonlocal active, max_active
            self.assertTrue(isolated_mcp)
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return {"status": "ok", "auto_sleep_ok": True}

        mock_execute_enabled.side_effect = side_effect

        with patch("api.agent.core.event_processing.get_max_parallel_tool_calls", return_value=4):
            self._run_single_iteration(
                [
                    _tool_call("read_file", '{"path": "/exports/a.txt"}'),
                    _tool_call("mcp_brightdata_search_engine", '{"query": "openai"}'),
                    _tool_call("http_request", '{"method": "GET", "url": "https://api.example.com/one.json"}'),
                    _tool_call("http_request", '{"method": "GET", "url": "https://api.example.com/two.json"}'),
                    _tool_call("http_request", '{"method": "GET", "url": "https://api.example.com/three.json"}'),
                ]
            )

        self.assertEqual(mock_execute_enabled.call_count, 5)
        self.assertEqual(max_active, 4)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_enabled_tool")
    @patch("api.agent.core.event_processing.execute_send_sms")
    def test_mixed_batch_falls_back_to_serial(
        self,
        mock_send_sms,
        mock_execute_enabled,
        _mock_credit,
    ):
        active = 0
        max_active = 0
        lock = threading.Lock()

        def tracked_result(*_args, **_kwargs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return {"status": "ok", "auto_sleep_ok": True}

        mock_execute_enabled.side_effect = tracked_result
        mock_send_sms.side_effect = lambda *_args, **_kwargs: tracked_result()

        self._run_single_iteration(
            [
                _tool_call("read_file", '{"path": "/exports/a.txt"}'),
                _tool_call("send_sms", '{"to": "+15555550100", "body": "hi"}'),
            ]
        )

        self.assertEqual(mock_execute_enabled.call_count, 1)
        self.assertFalse(mock_execute_enabled.call_args.kwargs.get("isolated_mcp", False))
        self.assertEqual(mock_send_sms.call_count, 1)
        self.assertEqual(max_active, 1)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_enabled_tool", return_value={"status": "ok", "auto_sleep_ok": True})
    def test_sqlite_batch_with_safe_tool_falls_back_to_serial(
        self,
        mock_execute_enabled,
        _mock_credit,
    ):
        self._run_single_iteration(
            [
                _tool_call("sqlite_batch", '{"sql": "select 1"}'),
                _tool_call("read_file", '{"path": "/exports/a.txt"}'),
            ]
        )

        self.assertEqual(mock_execute_enabled.call_count, 2)
        self.assertTrue(all(not call.kwargs.get("isolated_mcp", False) for call in mock_execute_enabled.call_args_list))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_enabled_tool", return_value={"status": "ok", "auto_sleep_ok": True})
    def test_http_get_batch_executes_in_parallel(
        self,
        mock_execute_enabled,
        _mock_credit,
    ):
        self._run_single_iteration(
            [
                _tool_call("http_request", '{"method": "GET", "url": "https://api.example.com/data.json"}'),
                _tool_call("read_file", '{"path": "/exports/a.txt"}'),
            ]
        )

        self.assertEqual(mock_execute_enabled.call_count, 2)
        self.assertTrue(all(call.kwargs.get("isolated_mcp", False) for call in mock_execute_enabled.call_args_list))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_enabled_tool", return_value={"status": "ok", "auto_sleep_ok": True})
    def test_http_post_batch_falls_back_to_serial(
        self,
        mock_execute_enabled,
        _mock_credit,
    ):
        self._run_single_iteration(
            [
                _tool_call("http_request", '{"method": "POST", "url": "https://api.example.com"}'),
                _tool_call("read_file", '{"path": "/exports/a.txt"}'),
            ]
        )

        self.assertEqual(mock_execute_enabled.call_count, 2)
        self.assertTrue(all(not call.kwargs.get("isolated_mcp", False) for call in mock_execute_enabled.call_args_list))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_enabled_tool", return_value={"status": "ok", "auto_sleep_ok": True})
    def test_http_get_download_batch_falls_back_to_serial(
        self,
        mock_execute_enabled,
        _mock_credit,
    ):
        self._run_single_iteration(
            [
                _tool_call("http_request", '{"method": "GET", "url": "https://api.example.com/file.txt", "download": true}'),
                _tool_call("read_file", '{"path": "/exports/a.txt"}'),
            ]
        )

        self.assertEqual(mock_execute_enabled.call_count, 2)
        self.assertTrue(all(not call.kwargs.get("isolated_mcp", False) for call in mock_execute_enabled.call_args_list))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_enabled_tool", return_value={"status": "ok", "auto_sleep_ok": True})
    def test_duplicate_export_paths_fall_back_to_serial(
        self,
        mock_execute_enabled,
        _mock_credit,
    ):
        self._run_single_iteration(
            [
                _tool_call("create_csv", '{"csv_text": "a\\n1\\n", "file_path": "/exports/report.csv"}'),
                _tool_call("create_csv", '{"csv_text": "a\\n2\\n", "file_path": "/exports/report.csv"}'),
            ]
        )

        self.assertEqual(mock_execute_enabled.call_count, 2)
        self.assertTrue(all(not call.kwargs.get("isolated_mcp", False) for call in mock_execute_enabled.call_args_list))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_enabled_tool", return_value={"status": "ok", "auto_sleep_ok": True})
    def test_same_batch_file_dependency_falls_back_to_serial(
        self,
        mock_execute_enabled,
        _mock_credit,
    ):
        self._run_single_iteration(
            [
                _tool_call("create_csv", '{"csv_text": "a\\n1\\n", "file_path": "/exports/report.csv"}'),
                _tool_call("read_file", '{"path": "$[/exports/report.csv]"}'),
            ]
        )

        self.assertEqual(mock_execute_enabled.call_count, 2)
        self.assertTrue(all(not call.kwargs.get("isolated_mcp", False) for call in mock_execute_enabled.call_args_list))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_enabled_tool", return_value={"status": "ok", "auto_sleep_ok": True})
    def test_same_batch_literal_file_dependency_falls_back_to_serial(
        self,
        mock_execute_enabled,
        _mock_credit,
    ):
        self._run_single_iteration(
            [
                _tool_call("create_csv", '{"csv_text": "a\\n1\\n", "file_path": "/exports/report.csv"}'),
                _tool_call("read_file", '{"path": "/exports/report.csv"}'),
            ]
        )

        self.assertEqual(mock_execute_enabled.call_count, 2)
        self.assertTrue(all(not call.kwargs.get("isolated_mcp", False) for call in mock_execute_enabled.call_args_list))

    @patch("api.agent.core.event_processing.apply_sqlite_agent_config_updates", return_value=SimpleNamespace(errors=[]))
    @patch(
        "api.agent.core.event_processing.apply_sqlite_kanban_updates",
        return_value=SimpleNamespace(errors=[], changes=False, snapshot=None),
    )
    @patch("api.agent.core.event_processing.apply_sqlite_skill_updates", return_value=SimpleNamespace(errors=[], changed=False))
    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_enabled_tool")
    def test_parallel_workers_receive_context_and_merge_variables_deterministically(
        self,
        mock_execute_enabled,
        _mock_credit,
        _mock_skill_updates,
        _mock_kanban_updates,
        _mock_config_updates,
    ):
        captured_paths = []

        def side_effect(_agent, tool_name, _params, isolated_mcp=False):
            from api.agent.tools.sqlite_state import get_sqlite_db_path

            self.assertTrue(isolated_mcp)
            captured_paths.append(get_sqlite_db_path())
            set_agent_variable("/shared", tool_name)
            return {"status": "ok", "auto_sleep_ok": True}

        mock_execute_enabled.side_effect = side_effect

        token = set_sqlite_db_path("/tmp/parallel-safe.sqlite")
        self.addCleanup(reset_sqlite_db_path, token)

        self._run_single_iteration(
            [
                _tool_call("read_file", '{"path": "/exports/a.txt"}'),
                _tool_call("mcp_brightdata_search_engine", '{"query": "openai"}'),
            ]
        )

        self.assertCountEqual(
            captured_paths,
            ["/tmp/parallel-safe.sqlite", "/tmp/parallel-safe.sqlite"],
        )
        self.assertEqual(get_agent_variable("/shared"), "mcp_brightdata_search_engine")
