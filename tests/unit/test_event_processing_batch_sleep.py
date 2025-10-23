"""
Tests that when an LLM completion returns multiple tool calls including
sleep_until_next_trigger, the sleep call is ignored if other tools are present
so results can be processed in the next iteration.
"""
from unittest.mock import patch, MagicMock

from django.test import TestCase, tag
from django.contrib.auth import get_user_model

from api.models import PersistentAgent, BrowserUseAgent, PersistentAgentToolCall, PersistentAgentStep, UserQuota
from api.agent.tools.tool_manager import enable_tools


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
        enable_tools(self.agent, ["sqlite_batch"])

    @patch('api.agent.core.event_processing._ensure_credit_for_tool', return_value=True)
    @patch('api.agent.core.event_processing.execute_enabled_tool', return_value={"status": "ok"})
    @patch('api.agent.core.event_processing.execute_update_charter', return_value={"status": "success"})
    @patch('api.agent.core.event_processing.execute_send_email', return_value={"status": "queued"})
    @patch('api.agent.core.event_processing._build_prompt_context')
    @patch('api.agent.core.event_processing._completion_with_failover')
    def test_batch_of_tools_ignores_sleep_when_others_present(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_email,
        mock_update_charter,
        mock_execute_enabled,
        _mock_credit,
    ):
        # Minimal prompt context and token usage
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}], 1000, None)

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
        with patch.object(ep, 'MAX_AGENT_LOOP_ITERATIONS', 1):
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

    @patch('api.agent.core.event_processing._ensure_credit_for_tool', return_value=True)
    @patch('api.agent.core.event_processing.execute_update_charter', return_value={"status": "ok", "auto_sleep_ok": True})
    @patch('api.agent.core.event_processing.execute_send_email', return_value={"status": "ok", "auto_sleep_ok": True})
    @patch('api.agent.core.event_processing._build_prompt_context')
    @patch('api.agent.core.event_processing._completion_with_failover')
    def test_successful_actions_short_circuit_to_sleep(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_email,
        mock_update_charter,
        _mock_credit,
    ):
        """A tool batch that opts-in to auto-sleep should end the loop immediately."""

        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}], 1000, None)

        def mk_tc(name, args):
            tc = MagicMock()
            tc.function = MagicMock()
            tc.function.name = name
            tc.function.arguments = args
            return tc

        tc_email = mk_tc('send_email', '{"to": "a@example.com", "subject": "hi", "mobile_first_html": "<p>Hi</p>"}')
        tc_charter = mk_tc('update_charter', '{"new_charter": "Stay focused"}')
        tc_sleep = mk_tc('sleep_until_next_trigger', '{}')

        msg = MagicMock()
        msg.tool_calls = [tc_email, tc_charter, tc_sleep]
        msg.content = None
        choice = MagicMock(); choice.message = msg
        resp = MagicMock(); resp.choices = [choice]
        resp.model_extra = {"usage": MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15, prompt_tokens_details=MagicMock(cached_tokens=0))}

        mock_completion.return_value = (resp, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "model": "m", "provider": "p"})

        from api.agent.core import event_processing as ep
        with patch.object(ep, 'MAX_AGENT_LOOP_ITERATIONS', 2):
            result_usage = ep._run_agent_loop(self.agent, is_first_run=False)

        # Only the initial completion should occur because the loop auto-sleeps
        self.assertEqual(mock_completion.call_count, 1)

        calls = list(PersistentAgentToolCall.objects.all().order_by('step__created_at'))
        self.assertEqual(len(calls), 2)
        self.assertEqual([c.tool_name for c in calls], ['send_email', 'update_charter'])

        # No explicit sleep step should exist because the loop short-circuited
        sleep_steps = PersistentAgentStep.objects.filter(description__icontains='sleep until next trigger')
        self.assertFalse(sleep_steps.exists())

        self.assertIn('total_tokens', result_usage)
        self.assertGreaterEqual(result_usage['total_tokens'], 15)

    @patch('api.agent.core.event_processing._ensure_credit_for_tool', return_value=True)
    @patch('api.agent.core.event_processing.execute_spawn_web_task', return_value={"status": "pending", "auto_sleep_ok": True})
    @patch('api.agent.core.event_processing.execute_send_email', return_value={"status": "sent", "auto_sleep_ok": True})
    @patch('api.agent.core.event_processing._build_prompt_context')
    @patch('api.agent.core.event_processing._completion_with_failover')
    def test_auto_sleep_waits_for_all_tool_calls(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_email,
        mock_spawn_task,
        _mock_credit,
    ):
        """Ensure we execute every actionable tool call before honoring auto-sleep."""

        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}], 1000, None)

        def mk_tc(name, args):
            tc = MagicMock()
            tc.function = MagicMock()
            tc.function.name = name
            tc.function.arguments = args
            return tc

        tc_email = mk_tc('send_email', '{"to": "a@example.com", "subject": "hi", "mobile_first_html": "<p>Hi</p>"}')
        tc_spawn = mk_tc('spawn_web_task', '{"url": "https://example.com", "charter": "do something"}')

        msg = MagicMock()
        msg.tool_calls = [tc_email, tc_spawn]
        msg.content = None
        choice = MagicMock(); choice.message = msg
        resp = MagicMock(); resp.choices = [choice]
        resp.model_extra = {"usage": MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15, prompt_tokens_details=MagicMock(cached_tokens=0))}

        msg_followup = MagicMock()
        msg_followup.tool_calls = None
        msg_followup.content = "Done"
        followup_choice = MagicMock(); followup_choice.message = msg_followup
        followup_resp = MagicMock(); followup_resp.choices = [followup_choice]
        followup_resp.model_extra = {"usage": MagicMock(prompt_tokens=4, completion_tokens=2, total_tokens=6, prompt_tokens_details=MagicMock(cached_tokens=0))}

        mock_completion.side_effect = [
            (resp, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "model": "m", "provider": "p"}),
            (followup_resp, {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6, "model": "m", "provider": "p"}),
        ]

        from api.agent.core import event_processing as ep
        with patch.object(ep, 'MAX_AGENT_LOOP_ITERATIONS', 2):
            ep._run_agent_loop(self.agent, is_first_run=False)

        # The loop should have needed a second pass (no auto-sleep without explicit sleep tool)
        self.assertEqual(mock_completion.call_count, 2)

        mock_send_email.assert_called_once()
        mock_spawn_task.assert_called_once()

        calls = list(PersistentAgentToolCall.objects.all().order_by('step__created_at'))
        self.assertEqual(len(calls), 2)
        self.assertEqual([c.tool_name for c in calls], ['send_email', 'spawn_web_task'])

        sleep_steps = PersistentAgentStep.objects.filter(description__icontains='sleep until next trigger')
        self.assertFalse(sleep_steps.exists())

    @patch('api.agent.core.event_processing._ensure_credit_for_tool', return_value=True)
    @patch('api.agent.core.event_processing.execute_update_charter', return_value={"status": "ok", "auto_sleep_ok": True})
    @patch('api.agent.core.event_processing.execute_send_email', return_value={"status": "ok", "auto_sleep_ok": True})
    @patch('api.agent.core.event_processing._build_prompt_context')
    @patch('api.agent.core.event_processing._completion_with_failover')
    def test_auto_sleep_requires_sleep_tool_call(self, mock_completion, mock_build_prompt, *_mocks):
        """Without an explicit sleep tool call, the loop should continue processing."""

        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}], 1000, None)

        def mk_tc(name, args):
            tc = MagicMock()
            tc.function = MagicMock()
            tc.function.name = name
            tc.function.arguments = args
            return tc

        tc_email = mk_tc('send_email', '{"to": "a@example.com", "subject": "hi", "mobile_first_html": "<p>Hi</p>"}')
        tc_charter = mk_tc('update_charter', '{"new_charter": "Stay focused"}')

        msg = MagicMock()
        msg.tool_calls = [tc_email, tc_charter]
        msg.content = None

        choice = MagicMock(); choice.message = msg
        resp = MagicMock(); resp.choices = [choice]
        resp.model_extra = {"usage": MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15, prompt_tokens_details=MagicMock(cached_tokens=0))}

        msg_followup = MagicMock()
        msg_followup.tool_calls = None
        msg_followup.content = "Done"
        followup_choice = MagicMock(); followup_choice.message = msg_followup
        followup_resp = MagicMock(); followup_resp.choices = [followup_choice]
        followup_resp.model_extra = {"usage": MagicMock(prompt_tokens=4, completion_tokens=2, total_tokens=6, prompt_tokens_details=MagicMock(cached_tokens=0))}

        mock_completion.side_effect = [
            (resp, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "model": "m", "provider": "p"}),
            (followup_resp, {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6, "model": "m", "provider": "p"}),
        ]

        from api.agent.core import event_processing as ep
        with patch.object(ep, 'MAX_AGENT_LOOP_ITERATIONS', 2):
            ep._run_agent_loop(self.agent, is_first_run=False)

        # Ensure the loop ran a second completion rather than auto-sleeping early
        self.assertEqual(mock_completion.call_count, 2)

        # The actionable tool calls were still executed and recorded once each
        calls = list(PersistentAgentToolCall.objects.all().order_by('step__created_at'))
        self.assertEqual(len(calls), 2)
        self.assertEqual([c.tool_name for c in calls], ['send_email', 'update_charter'])

        sleep_steps = PersistentAgentStep.objects.filter(description__icontains='sleep until next trigger')
        self.assertFalse(sleep_steps.exists())
