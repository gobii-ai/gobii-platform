"""Tests for implied send behavior in event processing."""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.core import event_processing as ep
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentStep,
    PersistentAgentToolCall,
    UserQuota,
)


@tag("batch_event_processing")
class ImpliedSendTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="implied@example.com",
            email="implied@example.com",
            password="password",
        )
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100
        quota.save()

    def setUp(self):
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="browser-agent-for-implied-send",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Implied Send Agent",
            charter="Test charter",
            browser_use_agent=browser_agent,
        )

    def _mock_completion(self, content):
        msg = MagicMock()
        msg.tool_calls = None
        msg.content = content
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_sms", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_reuses_last_tool_params(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_sms,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        prior_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Tool call: send_sms",
        )
        PersistentAgentToolCall.objects.create(
            step=prior_step,
            tool_name="send_sms",
            tool_params={
                "to_number": "+15550123456",
                "body": "old",
                "will_continue_work": True,
            },
            result="{}",
        )

        resp = self._mock_completion("New implied SMS")
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertTrue(mock_send_sms.called)
        params = mock_send_sms.call_args[0][1]
        self.assertEqual(params.get("to_number"), "+15550123456")
        self.assertEqual(params.get("body"), "New implied SMS")
        self.assertNotEqual(params.get("will_continue_work"), True)

        latest_call = (
            PersistentAgentToolCall.objects.filter(step__agent=self.agent)
            .order_by("-step__created_at")
            .first()
        )
        self.assertIsNotNone(latest_call)
        self.assertEqual(latest_call.tool_name, "send_sms")
        self.assertEqual(latest_call.tool_params.get("body"), "New implied SMS")

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_email", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_uses_preferred_contact_endpoint(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_email,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="owner@example.com",
            owner_agent=None,
        )
        self.agent.preferred_contact_endpoint = endpoint
        self.agent.save(update_fields=["preferred_contact_endpoint"])

        resp = self._mock_completion("Hello via implied email")
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertTrue(mock_send_email.called)
        params = mock_send_email.call_args[0][1]
        self.assertEqual(params.get("to_address"), "owner@example.com")
        self.assertEqual(params.get("mobile_first_html"), "Hello via implied email")
        self.assertEqual(params.get("subject"), "Update from Implied Send Agent")
