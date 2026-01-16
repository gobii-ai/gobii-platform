"""Tests for tool error handling in event processing."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.core import event_processing as ep
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentToolCall, UserQuota


@tag("batch_event_processing")
class ToolErrorHandlingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="tool-errors@example.com",
            email="tool-errors@example.com",
            password="password",
        )
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 50
        quota.save()

    def setUp(self):
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="tool-error-browser-agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Tool Error Agent",
            charter="Test charter",
            browser_use_agent=browser_agent,
        )

    def _mock_tool_call_response(self, tool_name, tool_args):
        msg = MagicMock()
        msg.tool_calls = [
            {
                "id": "call_tool_1",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(tool_args),
                },
            }
        ]
        msg.function_call = None
        msg.content = None
        msg.reasoning_content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    def _run_loop_with_tool(self, tool_name, tool_args, *, tool_result=None, tool_error=None):
        response = self._mock_tool_call_response(tool_name, tool_args)
        token_usage = {
            "prompt_tokens": 5,
            "completion_tokens": 5,
            "total_tokens": 10,
            "model": "mock-model",
            "provider": "mock-provider",
        }

        execute_patch = patch("api.agent.core.event_processing.execute_enabled_tool", return_value=tool_result)
        if tool_error is not None:
            execute_patch = patch(
                "api.agent.core.event_processing.execute_enabled_tool",
                side_effect=tool_error,
            )

        with patch("api.agent.core.event_processing.build_prompt_context", return_value=([{"role": "system", "content": "sys"}], 1000, None)), \
             patch("api.agent.core.event_processing.get_llm_config_with_failover", return_value=[("mock", "mock-model", {})]), \
             patch("api.agent.core.event_processing._completion_with_failover", return_value=(response, token_usage)), \
             patch("api.agent.core.event_processing.get_agent_tools", return_value=[{"type": "function", "function": {"name": tool_name, "parameters": {"type": "object", "properties": {}}}}]), \
             patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None}), \
             patch("api.agent.core.event_processing._enforce_tool_rate_limit", return_value=True), \
             patch("api.agent.core.event_processing.seed_sqlite_agent_config", return_value=None), \
             patch("api.agent.core.event_processing.seed_sqlite_kanban", return_value=None), \
             patch("api.agent.core.event_processing.apply_sqlite_agent_config_updates", return_value=SimpleNamespace(errors=[])), \
             patch("api.agent.core.event_processing.apply_sqlite_kanban_updates", return_value=SimpleNamespace(changes=[], snapshot=None, errors=[])), \
             execute_patch:
            with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
                ep._run_agent_loop(self.agent, is_first_run=False)

    def test_tool_exception_is_captured_and_bounded(self):
        error_message = (
            "HTTPSConnectionPool(host='hn.algolia.com', port=443): Read timed out."
        )
        self._run_loop_with_tool(
            "read_file",
            {},
            tool_error=TimeoutError(error_message),
        )

        tool_call = PersistentAgentToolCall.objects.filter(step__agent=self.agent).first()
        self.assertIsNotNone(tool_call)
        result = json.loads(tool_call.result)
        self.assertEqual(result.get("status"), "error")
        self.assertEqual(result.get("error_type"), "TimeoutError")
        self.assertTrue(result.get("retryable"))
        message = result.get("message", "")
        self.assertIn("Tool execution failed", message)
        self.assertLessEqual(len(message.encode("utf-8")), ep.TOOL_ERROR_MESSAGE_MAX_BYTES)
        self.assertNotIn("detail", result)

    def test_tool_error_payload_is_normalized_and_truncated(self):
        oversized_message = "M" * (ep.TOOL_ERROR_MESSAGE_MAX_BYTES + 2000)
        oversized_stack = "S" * (ep.TOOL_ERROR_DETAIL_MAX_BYTES + 3000)
        tool_result = {
            "status": "error",
            "message": oversized_message,
            "error_type": "RequestException",
            "exception": {"stacktrace": oversized_stack},
        }
        self._run_loop_with_tool("read_file", {}, tool_result=tool_result)

        tool_call = PersistentAgentToolCall.objects.filter(step__agent=self.agent).first()
        self.assertIsNotNone(tool_call)
        result = json.loads(tool_call.result)
        self.assertEqual(result.get("status"), "error")
        self.assertEqual(result.get("error_type"), "RequestException")
        self.assertFalse(result.get("retryable"))
        message = result.get("message", "")
        detail = result.get("detail", "")
        self.assertLessEqual(len(message.encode("utf-8")), ep.TOOL_ERROR_MESSAGE_MAX_BYTES)
        self.assertLessEqual(len(detail.encode("utf-8")), ep.TOOL_ERROR_DETAIL_MAX_BYTES)
        self.assertNotIn("exception", result)
