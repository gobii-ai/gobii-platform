"""
Tests that the agent loop executes all returned tool_calls in one completion.
"""
from unittest.mock import patch, MagicMock

from django.test import TestCase, tag
from django.contrib.auth import get_user_model

from api.models import PersistentAgent, BrowserUseAgent, UserQuota


@tag("batch_event_parallel")
class TestParallelToolCallsExecution(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(username='parallel@example.com', email='parallel@example.com', password='password')
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100
        quota.save()

    def setUp(self):
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="browser-agent-for-parallel-test")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Parallel Agent",
            charter="Test charter",
            browser_use_agent=browser_agent,
        )

    @patch('api.agent.core.event_processing._ensure_credit_for_tool', return_value=True)
    @patch('api.agent.core.event_processing.execute_send_sms', return_value={"status": "success"})
    @patch('api.agent.core.event_processing.execute_sqlite_query', return_value={"status": "ok", "rows": []})
    @patch('api.agent.core.event_processing._build_prompt_context')
    @patch('api.agent.core.event_processing._completion_with_failover')
    def test_executes_all_tool_calls_in_one_turn(self, mock_completion, mock_build_prompt, *_mocks):
        # Make prompt builder return minimal content and a small token count
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}], 1000)

        # Craft a response with two tool calls: sqlite_query then send_sms
        tc1 = MagicMock()
        tc1.function = MagicMock()
        tc1.function.name = 'sqlite_query'
        tc1.function.arguments = '{"sql": "select 1"}'

        tc2 = MagicMock()
        tc2.function = MagicMock()
        tc2.function.name = 'send_sms'
        tc2.function.arguments = '{"to": "+15555550100", "body": "hi"}'

        msg = MagicMock()
        msg.tool_calls = [tc1, tc2]
        msg.content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        resp.model_extra = {"usage": MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15, prompt_tokens_details=MagicMock(cached_tokens=0))}
        mock_completion.return_value = (resp, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "model": "m", "provider": "p"})

        # Run a single loop iteration by limiting MAX_AGENT_LOOP_ITERATIONS via patch
        from api.agent.core import event_processing as ep
        with patch.object(ep, 'MAX_AGENT_LOOP_ITERATIONS', 1):
            # Event window: no new messages or cron - minimal needed structure
            ew = MagicMock()
            ew.messages = []
            ew.cron_triggers = []
            result_usage = ep._run_agent_loop(self.agent, ew)

        # Both executors should have been called once
        # Access the patched functions from the decorator order above
        execute_sqlite_called = _mocks[1]
        execute_sms_called = _mocks[0]
        self.assertTrue(execute_sqlite_called.called, "sqlite_query was not executed")
        self.assertTrue(execute_sms_called.called, "send_sms was not executed")
        self.assertEqual(execute_sqlite_called.call_count, 1)
        self.assertEqual(execute_sms_called.call_count, 1)

