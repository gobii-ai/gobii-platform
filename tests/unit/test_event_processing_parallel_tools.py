"""
Tests for guarded parallel execution of safe tool batches.
"""
import json
import os
import threading
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TestCase, tag

from api.agent.tools.agent_variables import clear_variables, get_agent_variable, set_agent_variable
from api.agent.tools.sqlite_state import reset_sqlite_db_path, set_sqlite_db_path
from api.agent.tools.tool_manager import enable_tools
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
    UserQuota,
)


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

    def _run_single_iteration_with_sqlite(self, tool_calls: list[dict]):
        with tempfile.TemporaryDirectory() as tmp_dir:
            token = set_sqlite_db_path(os.path.join(tmp_dir, "state.db"))
            try:
                return self._run_single_iteration(tool_calls)
            finally:
                reset_sqlite_db_path(token)

    def test_noop_agent_config_update_is_persisted_in_tool_result(self):
        self._run_single_iteration_with_sqlite([
            _tool_call(
                "sqlite_batch",
                '{"sql": "UPDATE __agent_config SET charter = charter WHERE id = 1"}',
            ),
        ])

        result = json.loads(PersistentAgentToolCall.objects.get(step__agent=self.agent).result)
        self.assertEqual(
            result["agent_config_update"],
            {
                "updated_fields": [],
                "unchanged_fields": ["charter"],
                "errors": {},
            },
        )

    def test_emotion_update_is_persisted_and_annotated_in_tool_result(self):
        self._run_single_iteration_with_sqlite([
            _tool_call(
                "sqlite_batch",
                '{"sql": "UPDATE __agent_config SET emotion = \'🚀\', '
                'emotion_timeout_seconds = 3600 WHERE id = 1"}',
            ),
        ])

        result = json.loads(PersistentAgentToolCall.objects.get(step__agent=self.agent).result)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.emotion, "🚀")
        self.assertIsNotNone(self.agent.emotion_expires_at)
        self.assertEqual(result["results"][0]["message"], "Query 0 affected 1 rows.")
        self.assertEqual(
            result["agent_config_update"],
            {
                "updated_fields": ["emotion"],
                "unchanged_fields": [],
                "errors": {},
            },
        )

    def test_cte_emotion_update_reports_direct_row_count_not_trigger_work(self):
        self._run_single_iteration_with_sqlite([
            _tool_call(
                "sqlite_batch",
                json.dumps({
                    "sql": (
                        "WITH mood(value, ttl) AS (VALUES ('🚀', 3600)) "
                        "UPDATE __agent_config "
                        "SET emotion = (SELECT value FROM mood), "
                        "emotion_timeout_seconds = (SELECT ttl FROM mood) WHERE id = 1"
                    ),
                }),
            ),
        ])

        result = json.loads(PersistentAgentToolCall.objects.get(step__agent=self.agent).result)
        self.assertEqual(result["results"][0]["message"], "Query 0 affected 1 rows.")
        self.assertEqual(result["agent_config_update"]["updated_fields"], ["emotion"])

    def test_config_reconciliation_is_aggregated_with_field_errors(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.schedule = "0 9 * * *"
        self.agent.save(update_fields=["planning_state", "schedule", "updated_at"])

        self._run_single_iteration_with_sqlite([
            _tool_call(
                "sqlite_batch",
                '{"sql": "UPDATE __agent_config SET charter = \'Updated charter\' WHERE id = 1"}',
            ),
            _tool_call(
                "sqlite_batch",
                '{"sql": "UPDATE __agent_config SET schedule = \'0 10 * * *\' WHERE id = 1"}',
            ),
        ])

        tool_calls = list(
            PersistentAgentToolCall.objects.filter(step__agent=self.agent).order_by("step__created_at")
        )
        first_result, second_result = (json.loads(call.result) for call in tool_calls)
        self.assertNotIn("agent_config_update", first_result)
        reconciliation = second_result["agent_config_update"]
        self.assertEqual(reconciliation["updated_fields"], ["charter"])
        self.assertEqual(reconciliation["unchanged_fields"], ["schedule"])
        self.assertEqual(set(reconciliation["errors"]), {"schedule"})
        self.assertIn("planning mode", reconciliation["errors"]["schedule"].lower())

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

        def side_effect(
            _agent,
            _tool_name,
            _params,
            isolated_mcp=False,
            current_sqlite_db_path=None,
            resolved_entry=None,
        ):
            nonlocal active, max_active
            self.assertTrue(isolated_mcp)
            self.assertIsNone(current_sqlite_db_path)
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
                _tool_call("http_request", '{"method": "GET", "url": "https://api.example.com/data.json"}'),
            ]
        )

        self.assertEqual(mock_execute_enabled.call_count, 2)
        self.assertGreaterEqual(max_active, 2)

    @patch("api.agent.core.event_processing.execute_enabled_tool", return_value={"status": "ok", "auto_sleep_ok": True})
    def test_parallel_preparation_batches_rate_limits_and_persists_pending_rows(
        self,
        mock_execute_enabled,
    ):
        from api.agent.core import event_processing as ep

        rate_limit_batch = ep._ToolRateLimitBatch(
            limits={},
            recent_counts={},
            checked_names={
                "mcp_brightdata_search_engine",
                "mcp_brightdata_scrape_as_markdown",
                "read_file",
            },
        )
        with patch(
            "api.agent.core.event_processing._should_abort_processing",
            return_value=False,
        ) as mock_abort, patch(
            "api.agent.core.event_processing._build_tool_rate_limit_batch",
            return_value=rate_limit_batch,
        ) as mock_rate_limits, patch(
            "api.agent.core.event_processing._ensure_credit_for_tool",
            return_value={"cost": None, "credit": None},
        ) as mock_credit:
            self._run_single_iteration(
                [
                    _tool_call("mcp_brightdata_search_engine", '{"query": "openai"}'),
                    _tool_call("mcp_brightdata_scrape_as_markdown", '{"url": "https://example.com"}'),
                    _tool_call("read_file", '{"path": "/exports/a.txt"}'),
                ]
            )

        preparation_contexts = [
            call.kwargs.get("check_context")
            for call in mock_abort.call_args_list
        ]
        self.assertEqual(preparation_contexts.count("tool_batch"), 3)
        mock_rate_limits.assert_called_once()
        self.assertEqual(mock_credit.call_count, 3)
        self.assertEqual(mock_execute_enabled.call_count, 3)
        self.assertEqual(PersistentAgentToolCall.objects.filter(step__agent=self.agent).count(), 3)

    def test_batch_rate_limit_counts_calls_admitted_in_same_batch(self):
        from api.agent.core import event_processing as ep

        tool_settings = SimpleNamespace(
            hourly_limit_for_tool=lambda tool_name: 1 if tool_name == "read_file" else None
        )
        with patch(
            "api.agent.core.event_processing.get_tool_settings_for_owner",
            return_value=tool_settings,
        ) as mock_settings:
            rate_limit_batch = ep._build_tool_rate_limit_batch(
                self.agent,
                ["read_file", "read_file"],
            )

        mock_settings.assert_called_once_with(self.user)
        self.assertTrue(
            ep._enforce_tool_rate_limit(
                self.agent,
                "read_file",
                rate_limit_batch=rate_limit_batch,
            )
        )
        self.assertFalse(
            ep._enforce_tool_rate_limit(
                self.agent,
                "read_file",
                rate_limit_batch=rate_limit_batch,
            )
        )
        self.assertTrue(
            PersistentAgentSystemStep.objects.filter(
                step__agent=self.agent,
                code=PersistentAgentSystemStep.Code.RATE_LIMIT,
            ).exists()
        )

    def test_batch_rate_limit_settings_failure_does_not_retry_per_call(self):
        from api.agent.core import event_processing as ep

        with patch(
            "api.agent.core.event_processing.get_tool_settings_for_owner",
            side_effect=DatabaseError("offline"),
        ), patch.object(ep, "_resolve_tool_hourly_limit") as fallback:
            batch = ep._build_tool_rate_limit_batch(self.agent, ["read_file"])
            self.assertTrue(
                ep._enforce_tool_rate_limit(
                    self.agent,
                    "read_file",
                    rate_limit_batch=batch,
                )
            )

        self.assertEqual(batch.checked_names, {"read_file"})
        fallback.assert_not_called()

    def test_batch_rate_limit_count_failure_does_not_retry_per_call(self):
        from api.agent.core import event_processing as ep

        tool_settings = SimpleNamespace(hourly_limit_for_tool=lambda _name: 1)
        with patch(
            "api.agent.core.event_processing.get_tool_settings_for_owner",
            return_value=tool_settings,
        ), patch(
            "api.agent.core.event_processing.PersistentAgentToolCall.objects.filter",
            side_effect=DatabaseError("offline"),
        ), patch.object(ep, "_resolve_tool_hourly_limit") as fallback:
            batch = ep._build_tool_rate_limit_batch(self.agent, ["read_file"])
            self.assertTrue(
                ep._enforce_tool_rate_limit(
                    self.agent,
                    "read_file",
                    rate_limit_batch=batch,
                )
            )

        self.assertEqual(batch.checked_names, {"read_file"})
        fallback.assert_not_called()

    def test_prepare_tool_batch_holds_sibling_single_result_reads(self):
        from api.agent.core import event_processing as ep

        calls = [
            _tool_call(
                "sqlite_batch",
                json.dumps(
                    {
                        "sql": (
                            "SELECT substr(result_text, 1, 3000) FROM __tool_results "
                            f"WHERE result_id='result-{index}'"
                        )
                    }
                ),
            )
            for index in range(4)
        ]
        with patch.object(ep, "_enforce_tool_rate_limit", return_value=True) as mock_rate_limit, patch.object(
            ep,
            "_ensure_credit_for_tool",
            return_value={"cost": None, "credit": None},
        ) as mock_credit:
            prepared = ep._prepare_tool_batch(
                self.agent,
                tool_calls=calls,
                budget_ctx=None,
                eval_run_id=None,
                heartbeat=None,
                lock_extender=None,
                credit_snapshot={},
                allow_inferred_message_continue=True,
                has_non_sleep_calls=True,
                has_user_facing_message=False,
                attach_completion=lambda step_kwargs: None,
                attach_prompt_archive=lambda step: None,
            )

        self.assertEqual(prepared.prepared_calls, [])
        self.assertTrue(prepared.followup_required)
        self.assertEqual(prepared.parallel_ineligible_reason, "sqlite_result_fanout_gate")
        mock_rate_limit.assert_not_called()
        mock_credit.assert_not_called()
        self.assertFalse(PersistentAgentToolCall.objects.filter(step__agent=self.agent).exists())
        correction = PersistentAgentStep.objects.get(agent=self.agent, description__startswith="Tool policy: held 4")
        self.assertIn("one shaped query", correction.description)

    def test_sqlite_single_result_read_call_count_boundaries(self):
        from api.agent.core import event_processing as ep

        def calls(*statements):
            return [
                _tool_call("sqlite_batch", json.dumps({"sql": statement}))
                for statement in statements
            ]

        cases = (
            (
                calls(
                    "SELECT result_text FROM __tool_results WHERE result_id='one'",
                    "SELECT count(*) FROM domain_entities",
                ),
                1,
            ),
            (
                calls(
                    "SELECT result_text FROM __tool_results WHERE result_id IN ('one', 'two')",
                    "SELECT count(*) FROM __tool_results WHERE tool_name='http_request'",
                ),
                0,
            ),
            (
                calls(
                    "SELECT result_id FROM local_results WHERE result_id='one'",
                    "SELECT 1 /* __tool_results WHERE result_id='two' */",
                ),
                0,
            ),
            (
                calls(
                    "SELECT result_json FROM __tool_results WHERE result_id IN ('one')",
                    "SELECT result_json FROM __tool_results WHERE result_id IN ('two')",
                ),
                2,
            ),
        )

        for tool_calls, expected in cases:
            with self.subTest(tool_calls=tool_calls):
                self.assertEqual(ep._sqlite_single_result_read_call_count(tool_calls), expected)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_enabled_tool")
    def test_native_brightdata_tool_batch_executes_in_parallel(self, mock_execute_enabled, _mock_credit):
        active = 0
        max_active = 0
        lock = threading.Lock()

        def side_effect(
            _agent,
            _tool_name,
            _params,
            isolated_mcp=False,
            current_sqlite_db_path=None,
            resolved_entry=None,
        ):
            nonlocal active, max_active
            self.assertTrue(isolated_mcp)
            self.assertIsNone(current_sqlite_db_path)
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return {"status": "ok", "auto_sleep_ok": True}

        mock_execute_enabled.side_effect = side_effect

        self._run_single_iteration(
            [
                _tool_call("mcp_brightdata_search_engine", '{"query": "openai"}'),
                _tool_call("mcp_brightdata_scrape_as_markdown", '{"url": "https://example.com"}'),
                _tool_call("read_file", '{"path": "/exports/a.txt"}'),
            ]
        )

        self.assertEqual(mock_execute_enabled.call_count, 3)
        self.assertGreaterEqual(max_active, 2)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_enabled_tool")
    def test_parallel_safe_batch_respects_configured_worker_limit(self, mock_execute_enabled, _mock_credit):
        active = 0
        max_active = 0
        lock = threading.Lock()

        def side_effect(
            _agent,
            _tool_name,
            _params,
            isolated_mcp=False,
            current_sqlite_db_path=None,
            resolved_entry=None,
        ):
            nonlocal active, max_active
            self.assertTrue(isolated_mcp)
            self.assertIsNone(current_sqlite_db_path)
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
                    _tool_call("http_request", '{"method": "GET", "url": "https://api.example.com/zero.json"}'),
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

    @patch("api.agent.core.event_processing.apply_sqlite_agent_config_updates", return_value=SimpleNamespace(errors={}))
    @patch("api.agent.core.event_processing.apply_sqlite_skill_updates", return_value=SimpleNamespace(errors=[], changed=False))
    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_enabled_tool")
    def test_parallel_workers_receive_context_and_merge_variables_deterministically(
        self,
        mock_execute_enabled,
        _mock_credit,
        _mock_skill_updates,
        _mock_config_updates,
    ):
        captured_paths = []

        def side_effect(
            _agent,
            tool_name,
            _params,
            isolated_mcp=False,
            current_sqlite_db_path=None,
            resolved_entry=None,
        ):
            from api.agent.tools.sqlite_state import get_sqlite_db_path

            self.assertTrue(isolated_mcp)
            self.assertEqual(current_sqlite_db_path, "/tmp/parallel-safe.sqlite")
            captured_paths.append(get_sqlite_db_path())
            set_agent_variable("/shared", tool_name)
            return {"status": "ok", "auto_sleep_ok": True}

        mock_execute_enabled.side_effect = side_effect

        token = set_sqlite_db_path("/tmp/parallel-safe.sqlite")
        self.addCleanup(reset_sqlite_db_path, token)

        self._run_single_iteration(
            [
                _tool_call("read_file", '{"path": "/exports/a.txt"}'),
                _tool_call("http_request", '{"method": "GET", "url": "https://api.example.com/data.json"}'),
            ]
        )

        self.assertCountEqual(
            captured_paths,
            ["/tmp/parallel-safe.sqlite", "/tmp/parallel-safe.sqlite"],
        )
        self.assertEqual(get_agent_variable("/shared"), "http_request")
