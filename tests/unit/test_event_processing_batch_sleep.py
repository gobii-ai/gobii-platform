"""
Tests handling of sleep_until_next_trigger in multi-tool batches.
Ensures trailing sleep requests cause the agent loop to exit after the batch.
"""
from unittest.mock import patch, MagicMock

from django.test import TestCase, tag
from django.contrib.auth import get_user_model

from api.models import PersistentAgent, BrowserUseAgent, PersistentAgentToolCall, PersistentAgentStep, UserQuota


@tag("batch_event_parallel")
class TestBatchToolCallsWithSleep(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username='batchsleep@example.com', email='batchsleep@example.com', password='password'
        )
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100
        quota.save()

    def setUp(self):
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="browser-agent-for-batch-sleep-test")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Batch Sleep Agent",
            charter="Test charter",
            browser_use_agent=browser_agent,
        )

    @patch('api.agent.core.event_processing._ensure_credit_for_tool', return_value=True)
    @patch('api.agent.core.event_processing.execute_sqlite_batch', return_value={"status": "ok"})
    @patch('api.agent.core.event_processing.execute_update_charter', return_value={"status": "success"})
    @patch('api.agent.core.event_processing.execute_send_email', return_value={"status": "queued"})
    @patch('api.agent.core.event_processing._build_prompt_context')
    @patch('api.agent.core.event_processing._completion_with_failover')
    def test_batch_of_tools_ignores_sleep_record_but_exits(self, mock_completion, mock_build_prompt, *_mocks):
        # Minimal prompt context and token usage
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}], 1000)

        # Construct four tool calls: send_email, update_charter, sqlite_batch, sleep
        def mk_tc(name, args):
            tc = MagicMock()
            tc.function = MagicMock()
            tc.function.name = name
            tc.function.arguments = args
            return tc

        tc_email = mk_tc('send_email', '{"to": "a@example.com", "subject": "hi", "html_body": "<p>Hi</p>"}')
        tc_charter = mk_tc('update_charter', '{"charter": "do x"}')
        tc_sqlite = mk_tc('sqlite_batch', '{"ops": [{"sql": "create table if not exists x(id int)"}], "mode": "atomic"}')
        tc_sleep = mk_tc('sleep_until_next_trigger', '{}')

        msg = MagicMock()
        msg.tool_calls = [tc_email, tc_charter, tc_sqlite, tc_sleep]
        msg.content = None
        choice = MagicMock(); choice.message = msg
        resp = MagicMock(); resp.choices = [choice]
        # token usage dict present
        resp.model_extra = {"usage": MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15, prompt_tokens_details=MagicMock(cached_tokens=0))}
        mock_completion.return_value = (resp, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "model": "m", "provider": "p"})

        # Run a single loop iteration
        from api.agent.core import event_processing as ep
        with patch.object(ep, 'MAX_AGENT_LOOP_ITERATIONS', 2):
            result_usage = ep._run_agent_loop(self.agent, is_first_run=False)

        # Validate DB records: 3 tool calls persisted and NO sleep step recorded
        calls = list(PersistentAgentToolCall.objects.all().order_by('step__created_at'))
        self.assertEqual(len(calls), 3)
        self.assertEqual([c.tool_name for c in calls], ['send_email', 'update_charter', 'sqlite_batch'])

        # Ensure no sleep step exists because sleep was ignored in mixed batch
        sleep_steps = PersistentAgentStep.objects.filter(description__icontains='sleep until next trigger')
        self.assertFalse(sleep_steps.exists(), "Sleep step should be ignored when other tools are present")

        # Assert token usage aggregated
        self.assertIn('total_tokens', result_usage)
        self.assertGreaterEqual(result_usage['total_tokens'], 15)

        # A trailing sleep should end the loop, so only one LLM completion occurs
        self.assertEqual(mock_completion.call_count, 1)
