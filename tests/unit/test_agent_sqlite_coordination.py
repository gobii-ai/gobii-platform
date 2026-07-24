import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.test import SimpleTestCase, tag

from api.agent.tools.sqlite_state import _should_compact_sqlite
from api.custom_tool_bridge import _nested_sqlite_tool_error
from api.services.agent_sqlite_coordination import (
    agent_sqlite_execution,
    agent_sqlite_execution_is_active,
)


@tag("batch_sandbox_sqlite_sync")
class AgentSQLiteCoordinationTests(SimpleTestCase):
    def test_lock_is_reentrant_for_same_execution_context(self):
        with (
            agent_sqlite_execution("agent-reentrant"),
            agent_sqlite_execution("agent-reentrant"),
        ):
            pass

    def test_competing_execution_waits_for_current_lease(self):
        with patch(
            "api.services.agent_sqlite_coordination._LOCK_ACQUIRE_TIMEOUT_SECONDS",
            2,
        ), ThreadPoolExecutor(max_workers=1) as executor:
            with agent_sqlite_execution("agent-contended"):
                future = executor.submit(
                    lambda: self._acquire_in_competing_context("agent-contended")
                )
                time.sleep(0.05)
                self.assertFalse(future.done())

            self.assertEqual(future.result(timeout=3), "acquired")

    def test_active_lease_is_observable_for_nested_call_rejection(self):
        self.assertFalse(agent_sqlite_execution_is_active("agent-observable"))
        with agent_sqlite_execution("agent-observable"):
            self.assertTrue(agent_sqlite_execution_is_active("agent-observable"))
        self.assertFalse(agent_sqlite_execution_is_active("agent-observable"))

    @patch(
        "api.custom_tool_bridge.agent_sqlite_execution_is_active",
        return_value=True,
    )
    def test_nested_sqlite_capable_child_tools_fail_without_waiting(self, _active):
        for tool_name in ("sqlite_batch", "python_exec", "run_command", "custom_other"):
            result = _nested_sqlite_tool_error("agent-nested", tool_name)
            self.assertEqual(result["error_code"], "nested_agent_sqlite_not_supported")
            self.assertFalse(result["retryable"])

        self.assertIsNone(_nested_sqlite_tool_error("agent-nested", "read_file"))

    def test_compaction_requires_near_limit_size_and_twenty_percent_free_pages(self):
        self.assertFalse(_should_compact_sqlite(89 * 1024 * 1024, 100, 90))
        self.assertFalse(_should_compact_sqlite(95 * 1024 * 1024, 100, 19))
        self.assertTrue(_should_compact_sqlite(95 * 1024 * 1024, 100, 20))

    @staticmethod
    def _acquire_in_competing_context(agent_id):
        with agent_sqlite_execution(agent_id):
            return "acquired"
