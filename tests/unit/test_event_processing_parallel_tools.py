"""
Tests that the agent loop executes all returned tool_calls in one completion.
"""
from unittest.mock import patch, MagicMock

from django.test import TestCase, tag
from django.contrib.auth import get_user_model

from api.models import PersistentAgent, BrowserUseAgent, PersistentAgentCompletion, PersistentAgentStep, UserQuota
from api.agent.tools.tool_manager import enable_tools


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
        enable_tools(self.agent, ["sqlite_batch"])

    @patch('api.agent.core.event_processing._ensure_credit_for_tool', return_value=True)
    @patch('api.agent.core.event_processing.execute_send_sms', return_value={"status": "success"})
    @patch('api.agent.core.event_processing.execute_enabled_tool', return_value={"status": "ok"})
    @patch('api.agent.core.event_processing.build_prompt_context')
    @patch('api.agent.core.event_processing._completion_with_failover')
    def test_executes_all_tool_calls_in_one_turn(
        self,
        mock_completion,
        mock_build_prompt,
        mock_execute_enabled,
        mock_send_sms,
        _mock_credit,
    ):
        # Make prompt builder return minimal content and a small token count
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}], 1000, None)

        # Craft a response with two tool calls: sqlite_batch then send_sms
        tc1 = MagicMock()
        tc1.function = MagicMock()
        tc1.function.name = 'sqlite_batch'
        tc1.function.arguments = '{"ops": [{"sql": "select 1"}]}'

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
            result_usage = ep._run_agent_loop(self.agent, is_first_run=False)

        # Both executors should have been called once
        # Access the patched functions from the decorator order above
        self.assertTrue(mock_execute_enabled.called, "sqlite_batch was not executed")
        self.assertTrue(mock_send_sms.called, "send_sms was not executed")
        self.assertEqual(mock_execute_enabled.call_count, 1)
        self.assertEqual(mock_send_sms.call_count, 1)

        completions = list(PersistentAgentCompletion.objects.filter(agent=self.agent))
        self.assertEqual(len(completions), 1, "Exactly one completion should be recorded")
        completion = completions[0]
        self.assertEqual(completion.total_tokens, 15)
        self.assertEqual(completion.steps.count(), 3)

        tool_steps = list(PersistentAgentStep.objects.filter(description__startswith="Tool call:").order_by('created_at'))
        self.assertEqual(len(tool_steps), 2)
        for step in tool_steps:
            self.assertEqual(step.completion_id, completion.id)

        # _run_agent_loop should aggregate token usage and return it
        self.assertIn('total_tokens', result_usage)
        self.assertEqual(result_usage['total_tokens'], 15)
