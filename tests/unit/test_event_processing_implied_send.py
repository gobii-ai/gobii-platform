"""Tests for implied send behavior in event processing."""
from decimal import Decimal
from datetime import timedelta
import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.core import event_processing as ep
from api.agent.core.internal_reasoning import INTERNAL_REASONING_PREFIX
from api.agent.core.prompt_context import _get_implied_send_context
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentKanbanCard,
    PersistentAgentMessage,
    PersistentAgentWebSession,
    PersistentAgentStep,
    PersistentAgentToolCall,
    UserQuota,
    build_web_agent_address,
    build_web_user_address,
)
from api.services.web_sessions import start_web_session


@tag("batch_event_processing_credits")
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
        self.task_credit_patcher = patch(
            "api.models.TaskCreditService.check_and_consume_credit_for_owner",
            return_value={"success": True, "credit": None},
        )
        self.task_credit_patcher.start()
        self.addCleanup(self.task_credit_patcher.stop)

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

    def _mock_completion(self, content, *, reasoning_content=None):
        msg = MagicMock()
        msg.tool_calls = None
        msg.function_call = None
        msg.content = content
        if reasoning_content is not None:
            msg.reasoning_content = reasoning_content
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_requires_active_web_session_for_last_chat(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        prior_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Tool call: send_chat_message",
        )
        PersistentAgentToolCall.objects.create(
            step=prior_step,
            tool_name="send_chat_message",
            tool_params={
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": "old",
                "will_continue_work": True,
            },
            result="{}",
        )

        resp = self._mock_completion("New implied web chat")
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

        self.assertFalse(mock_send_chat.called)
        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="Message delivery requires explicit send tools",
        ).first()
        self.assertIsNotNone(correction_step)
        self.assertIn(
            "most recently active non-web communication channel",
            correction_step.description,
        )

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_requires_active_web_session_for_inbound_web_message(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        user_address = build_web_user_address(self.user.id, self.agent.id)
        agent_address = build_web_agent_address(self.agent.id)

        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=agent_address,
            is_primary=True,
        )
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.WEB,
            address=user_address,
            is_primary=False,
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=user_address,
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=user_endpoint,
            conversation=conversation,
            body="Inbound web message",
        )

        resp = self._mock_completion("New implied web chat")
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

        self.assertFalse(mock_send_chat.called)
        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="Message delivery requires explicit send tools",
        ).first()
        self.assertIsNotNone(correction_step)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_requires_active_web_session_with_preferred_endpoint(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=build_web_user_address(self.user.id, self.agent.id),
            owner_agent=None,
        )
        self.agent.preferred_contact_endpoint = endpoint
        self.agent.save(update_fields=["preferred_contact_endpoint"])

        resp = self._mock_completion("Hello via implied web chat")
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

        self.assertFalse(mock_send_chat.called)
        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="Message delivery requires explicit send tools",
        ).first()
        self.assertIsNotNone(correction_step)

    def test_implied_send_prefers_deliverable_web_session(self):
        start_web_session(self.agent, self.user)

        context = _get_implied_send_context(self.agent)

        self.assertIsNotNone(context)
        self.assertEqual(context["channel"], "web")
        self.assertEqual(
            context["to_address"],
            build_web_user_address(self.user.id, self.agent.id),
        )

    def test_implied_send_ignores_hidden_session_after_visibility_grace(self):
        result = start_web_session(self.agent, self.user)
        PersistentAgentWebSession.objects.filter(pk=result.session.pk).update(
            is_visible=False,
            last_seen_at=timezone.now() - timedelta(seconds=30),
            last_visible_at=timezone.now() - timedelta(seconds=61),
        )

        context = _get_implied_send_context(self.agent)

        self.assertIsNone(context)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_allows_natural_progress_continuation_without_canonical_phrase(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        start_web_session(self.agent, self.user)

        resp = self._mock_completion("Let me analyze this and send a summary.")
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

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 2):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertTrue(mock_send_chat.called)
        self.assertGreaterEqual(len(mock_send_chat.call_args_list), 1)
        first_params = mock_send_chat.call_args_list[0][0][1]
        self.assertTrue(first_params.get("will_continue_work"))
        if len(mock_send_chat.call_args_list) > 1:
            second_params = mock_send_chat.call_args_list[1][0][1]
            self.assertIsNone(second_params.get("will_continue_work"))
        self.assertEqual(mock_completion.call_count, 2)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_uses_natural_continuation_when_open_kanban_work(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        start_web_session(self.agent, self.user)
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Continue researching portfolio companies",
            status=PersistentAgentKanbanCard.Status.TODO,
        )

        resp = self._mock_completion("I've scraped the sites. Let me extract key details next.")
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

        self.assertTrue(mock_send_chat.called)
        params = mock_send_chat.call_args[0][1]
        self.assertTrue(params.get("will_continue_work"))


@tag("batch_event_processing_credits")
class DailyLimitMessageOnlyModeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="daily-limit-mode@example.com",
            email="daily-limit-mode@example.com",
            password="password",
        )
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100
        quota.save()

    def setUp(self):
        self.task_credit_patcher = patch(
            "api.models.TaskCreditService.check_and_consume_credit_for_owner",
            return_value={"success": True, "credit": None},
        )
        self.task_credit_patcher.start()
        self.addCleanup(self.task_credit_patcher.stop)

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="browser-agent-for-daily-limit-mode",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Daily Limit Mode Agent",
            charter="Test charter",
            browser_use_agent=browser_agent,
        )

    def _daily_limit_state(self):
        return {
            "hard_limit": Decimal("2"),
            "hard_limit_remaining": Decimal("0"),
            "soft_target": Decimal("1"),
            "soft_target_remaining": Decimal("0"),
            "used": Decimal("2"),
            "next_reset": timezone.now(),
        }

    def _tool_definition(self, name: str) -> dict:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
        }

    def _completion(self, *, content=None, tool_calls=None):
        msg = MagicMock()
        msg.tool_calls = tool_calls
        msg.function_call = None
        msg.content = content
        msg.reasoning_content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    def _mock_completion(self, content, *, reasoning_content=None):
        msg = MagicMock()
        msg.tool_calls = None
        msg.function_call = None
        msg.content = content
        if reasoning_content is not None:
            msg.reasoning_content = reasoning_content
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    def _tool_call(self, name: str, arguments: dict) -> MagicMock:
        tool_call = MagicMock()
        tool_call.id = f"call_{name}"
        tool_call.function = MagicMock()
        tool_call.function.name = name
        tool_call.function.arguments = json.dumps(arguments)
        return tool_call

    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing.get_agent_daily_credit_state")
    @patch("api.agent.core.event_processing.get_agent_tools")
    def test_daily_limit_mode_filters_tool_list_to_message_tools(
        self,
        mock_get_tools,
        mock_get_daily_state,
        mock_build_prompt,
    ):
        start_web_session(self.agent, self.user)
        mock_get_tools.return_value = [
            self._tool_definition("send_email"),
            self._tool_definition("send_sms"),
            self._tool_definition("send_chat_message"),
            self._tool_definition("send_agent_message"),
            self._tool_definition("sqlite_query"),
        ]
        mock_get_daily_state.return_value = self._daily_limit_state()
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)
        observed_tool_names: list[str] = []

        def _capture_completion(*_args, **kwargs):
            observed_tool_names.extend(
                tool["function"]["name"]
                for tool in kwargs["tools"]
            )
            return (
                self._completion(content=None, tool_calls=None),
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "model": "m",
                    "provider": "p",
                },
            )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1), patch(
            "api.agent.core.event_processing._completion_with_failover",
            side_effect=_capture_completion,
        ):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertEqual(
            observed_tool_names,
            ["send_email", "send_sms", "send_chat_message", "send_agent_message"],
        )

    @patch("api.agent.core.event_processing.execute_enabled_tool")
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing.get_agent_daily_credit_state")
    @patch("api.agent.core.event_processing.get_agent_tools")
    def test_daily_limit_mode_rejects_non_message_tool_calls(
        self,
        mock_get_tools,
        mock_get_daily_state,
        mock_build_prompt,
        mock_execute_enabled_tool,
    ):
        mock_get_tools.return_value = [self._tool_definition("send_email")]
        mock_get_daily_state.return_value = self._daily_limit_state()
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)
        completion = self._completion(
            tool_calls=[self._tool_call("sqlite_query", {"query": "select 1"})]
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1), patch(
            "api.agent.core.event_processing._completion_with_failover",
            return_value=(
                completion,
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "model": "m",
                    "provider": "p",
                },
            ),
        ):
            ep._run_agent_loop(self.agent, is_first_run=False)

        mock_execute_enabled_tool.assert_not_called()
        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__contains="Only message tools are allowed right now",
        ).first()
        self.assertIsNotNone(correction_step)

    @patch("api.agent.core.event_processing.execute_send_email", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch(
        "api.agent.core.event_processing.TaskCreditService.check_and_consume_credit_for_owner",
        return_value={"success": True, "credit": None},
    )
    @patch("api.agent.core.event_processing.TaskCreditService.calculate_available_tasks_for_owner", return_value=Decimal("5"))
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing.get_agent_daily_credit_state")
    @patch("api.agent.core.event_processing.get_agent_tools")
    @patch("api.agent.core.event_processing.settings.GOBII_PROPRIETARY_MODE", True)
    def test_daily_limit_mode_executes_send_email_without_consuming_credit(
        self,
        mock_get_tools,
        mock_get_daily_state,
        mock_build_prompt,
        _mock_available,
        mock_consume,
        mock_send_email,
    ):
        mock_get_tools.return_value = [self._tool_definition("send_email")]
        mock_get_daily_state.return_value = self._daily_limit_state()
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)
        completion = self._completion(
            tool_calls=[
                self._tool_call(
                    "send_email",
                    {
                        "to_address": "owner@example.com",
                        "subject": "Daily limit reached",
                        "mobile_first_html": "<p>Please raise the limit.</p>",
                    },
                )
            ]
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1), patch(
            "api.agent.core.event_processing._completion_with_failover",
            return_value=(
                completion,
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "model": "m",
                    "provider": "p",
                },
            ),
        ):
            ep._run_agent_loop(self.agent, is_first_run=False)

        mock_send_email.assert_called_once()
        mock_consume.assert_not_called()
        tool_call = PersistentAgentToolCall.objects.filter(
            step__agent=self.agent,
            tool_name="send_email",
        ).order_by("-step_id").first()
        self.assertIsNotNone(tool_call)
        self.assertIsNone(tool_call.step.credits_cost)
        self.assertIsNone(tool_call.step.completion_id)

    @patch("api.agent.core.event_processing._should_imply_continue", return_value=False)
    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_rechecks_open_kanban_before_sleep(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
        _mock_should_imply_continue,
    ):
        """A conservative implied-stop decision should still continue on clear progress language."""
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        start_web_session(self.agent, self.user)
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Continue researching portfolio companies",
            status=PersistentAgentKanbanCard.Status.TODO,
        )

        first_resp = self._mock_completion("I've scraped the profiles. Let me extract key details next.")
        second_resp = self._mock_completion(None)

        mock_completion.side_effect = [
            (
                first_resp,
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "model": "m",
                    "provider": "p",
                },
            ),
            (
                second_resp,
                {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                    "model": "m",
                    "provider": "p",
                },
            ),
        ]

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 2):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertTrue(mock_send_chat.called)
        params = mock_send_chat.call_args[0][1]
        self.assertTrue(params.get("will_continue_work"))
        self.assertEqual(mock_completion.call_count, 2)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_strips_canonical_continuation_phrase(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        start_web_session(self.agent, self.user)

        resp = self._mock_completion(f"Here is the summary.\n{ep.CANONICAL_CONTINUATION_PHRASE}")
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

        self.assertTrue(mock_send_chat.called)
        params = mock_send_chat.call_args[0][1]
        self.assertEqual(params.get("body"), "Here is the summary.")
        self.assertTrue(params.get("will_continue_work"))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_enabled_tool", return_value={"status": "ok"})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_with_tool_followup_continues_without_canonical_phrase(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_enabled_tool,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        start_web_session(self.agent, self.user)

        tool_call = MagicMock()
        tool_call.id = "call_dummy"
        tool_call.function = MagicMock()
        tool_call.function.name = "dummy_tool"
        tool_call.function.arguments = "{}"

        msg = MagicMock()
        msg.tool_calls = [tool_call]
        msg.function_call = None
        msg.content = "Got it, I'll dig in and report back."
        msg.reasoning_content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]

        followup_msg = MagicMock()
        followup_msg.tool_calls = None
        followup_msg.function_call = None
        followup_msg.content = None
        followup_choice = MagicMock()
        followup_choice.message = followup_msg
        followup_resp = MagicMock()
        followup_resp.choices = [followup_choice]

        mock_completion.side_effect = [
            (
                resp,
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "model": "m",
                    "provider": "p",
                },
            ),
            (
                followup_resp,
                {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                    "model": "m",
                    "provider": "p",
                },
            ),
        ]

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 2):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertTrue(mock_send_chat.called)
        self.assertEqual(mock_completion.call_count, 2)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_explicit_send_strips_canonical_continuation_phrase(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        tool_call = MagicMock()
        tool_call.id = "call_send_123"
        tool_call.function = MagicMock()
        tool_call.function.name = "send_chat_message"
        tool_call.function.arguments = json.dumps(
            {
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": f"Here is the summary.\n{ep.CANONICAL_CONTINUATION_PHRASE}",
            }
        )

        msg = MagicMock()
        msg.tool_calls = [tool_call]
        msg.function_call = None
        msg.content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]

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

        self.assertTrue(mock_send_chat.called)
        params = mock_send_chat.call_args[0][1]
        self.assertEqual(params.get("body"), "Here is the summary.")
        self.assertTrue(params.get("will_continue_work"))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_explicit_send_infers_continue_for_progress_update_without_flag(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        """Progress-update explicit sends should continue even if the flag is omitted."""
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        tool_call = MagicMock()
        tool_call.id = "call_send_456"
        tool_call.function = MagicMock()
        tool_call.function.name = "send_chat_message"
        tool_call.function.arguments = json.dumps(
            {
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": "Great question! Let me dig into the most-discussed stories and find some standout comments.",
            }
        )

        msg = MagicMock()
        msg.tool_calls = [tool_call]
        msg.function_call = None
        msg.content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]

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

        params = mock_send_chat.call_args[0][1]
        self.assertTrue(params.get("will_continue_work"))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_explicit_send_keeps_stop_for_defer_language_without_flag(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        tool_call = MagicMock()
        tool_call.id = "call_send_defer_1"
        tool_call.function = MagicMock()
        tool_call.function.name = "send_chat_message"
        tool_call.function.arguments = json.dumps(
            {
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": "I'll wait here. Let me know if you need anything else.",
            }
        )

        msg = MagicMock()
        msg.tool_calls = [tool_call]
        msg.function_call = None
        msg.content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]

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

        params = mock_send_chat.call_args[0][1]
        self.assertIsNone(params.get("will_continue_work"))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_explicit_send_keeps_stop_for_completion_language_without_flag(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        tool_call = MagicMock()
        tool_call.id = "call_send_done_1"
        tool_call.function = MagicMock()
        tool_call.function.name = "send_chat_message"
        tool_call.function.arguments = json.dumps(
            {
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": "All done. Here's what I found.",
            }
        )

        msg = MagicMock()
        msg.tool_calls = [tool_call]
        msg.function_call = None
        msg.content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]

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

        params = mock_send_chat.call_args[0][1]
        self.assertIsNone(params.get("will_continue_work"))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_explicit_send_keeps_stop_when_message_asks_user_question_without_flag(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        tool_call = MagicMock()
        tool_call.id = "call_send_q_1"
        tool_call.function = MagicMock()
        tool_call.function.name = "send_chat_message"
        tool_call.function.arguments = json.dumps(
            {
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": "Can you share which thread you care about most?",
            }
        )

        msg = MagicMock()
        msg.tool_calls = [tool_call]
        msg.function_call = None
        msg.content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]

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

        params = mock_send_chat.call_args[0][1]
        self.assertIsNone(params.get("will_continue_work"))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_email", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_uses_last_chat_message_without_active_session(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        mock_send_email,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        prior_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Tool call: send_chat_message",
        )
        PersistentAgentToolCall.objects.create(
            step=prior_step,
            tool_name="send_chat_message",
            tool_params={
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": "old",
            },
            result="{}",
        )

        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="owner@example.com",
            owner_agent=None,
        )
        self.agent.preferred_contact_endpoint = endpoint
        self.agent.save(update_fields=["preferred_contact_endpoint"])

        resp = self._mock_completion("Hello fallback")
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

        self.assertFalse(mock_send_chat.called)
        self.assertFalse(mock_send_email.called)
        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="Message delivery requires explicit send tools",
        ).first()
        self.assertIsNotNone(correction_step)

    @patch("api.agent.core.event_processing.execute_send_email", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_failure_persists_reasoning_step(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_email,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="owner@example.com",
            owner_agent=None,
        )
        self.agent.preferred_contact_endpoint = endpoint
        self.agent.save(update_fields=["preferred_contact_endpoint"])

        resp = self._mock_completion(
            "Hello without destination",
            reasoning_content="Need explicit send destination.",
        )
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

        reasoning_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith=INTERNAL_REASONING_PREFIX,
        ).first()
        self.assertIsNotNone(reasoning_step)
        self.assertIn("Need explicit send destination.", reasoning_step.description)

        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="Message delivery requires explicit send tools",
        ).first()
        self.assertIsNotNone(correction_step)
        self.assertFalse(mock_send_email.called)

    @patch("api.agent.core.event_processing.get_llm_config_with_failover", return_value=[("mock", "mock-model", {})])
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_reasoning_only_content_list_continues_then_auto_sleeps(
        self,
        mock_completion,
        mock_build_prompt,
        _mock_llm_config,
    ):
        """Thinking-only responses continue up to MAX_NO_TOOL_STREAK before auto-sleeping."""
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        msg = MagicMock()
        msg.tool_calls = []
        msg.function_call = None
        msg.content = [{"type": "thinking", "text": "Plan the response."}]
        msg.reasoning_content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 5):
            ep._run_agent_loop(self.agent, is_first_run=False)

        # Should be called MAX_NO_TOOL_STREAK times before auto-sleeping
        # (thinking content doesn't cause immediate stop; streak limit does)
        self.assertEqual(mock_completion.call_count, ep.MAX_NO_TOOL_STREAK)

        reasoning_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith=INTERNAL_REASONING_PREFIX,
        ).first()
        self.assertIsNotNone(reasoning_step)
        self.assertIn("Plan the response.", reasoning_step.description)

        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="Message delivery requires explicit send tools",
        ).first()
        self.assertIsNone(correction_step)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_failure_still_executes_other_tool_calls(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        """When implied send fails due to no web session, other tool calls should still execute."""
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        # Set up a preferred endpoint that is NOT a web session (so implied send will fail)
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=build_web_user_address(self.user.id, self.agent.id),
            owner_agent=None,
        )
        self.agent.preferred_contact_endpoint = endpoint
        self.agent.save(update_fields=["preferred_contact_endpoint"])

        # Create a mock response with BOTH message content AND a tool call
        msg = MagicMock()
        msg.content = "Hello, this message should be dropped"
        msg.reasoning_content = None
        # Add a sleep tool call (simple tool that doesn't require mocking external services)
        sleep_tool_call = MagicMock()
        sleep_tool_call.id = "call_sleep_123"
        sleep_tool_call.function = MagicMock()
        sleep_tool_call.function.name = "sleep_until_next_trigger"
        sleep_tool_call.function.arguments = "{}"
        msg.tool_calls = [sleep_tool_call]

        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]

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

        # Implied send should NOT have been called (no active web session)
        self.assertFalse(mock_send_chat.called)

        # The correction step should exist (notifying agent that message was dropped)
        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="Message delivery requires explicit send tools",
        ).first()
        self.assertIsNotNone(correction_step)

        # The sleep tool call should still have been executed (creating a step)
        sleep_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description="Decided to sleep until next trigger.",
        ).first()
        self.assertIsNotNone(sleep_step, "Other tool calls should execute even when implied send fails")

    @patch("api.models.TaskCreditService.check_and_consume_credit_for_owner", return_value={"success": True, "credit": None})
    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    @patch("api.agent.core.event_processing.get_llm_config_with_failover")
    def test_run_loop_uses_prompt_resolved_failover_configs(
        self,
        mock_get_llm_config,
        mock_completion,
        mock_build_prompt,
        _mock_send_chat,
        _mock_credit,
        _mock_task_credit,
    ):
        prompt_failover_configs = [
            ("provider-a", "openai/gpt-4o-mini", {"allow_implied_send": True}),
            ("provider-b", "openai/gpt-4.1-mini", {"allow_implied_send": False}),
        ]
        mock_build_prompt.return_value = (
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
            1000,
            None,
            {
                "prompt_allows_implied_send": False,
                "prompt_failover_configs": prompt_failover_configs,
            },
        )
        mock_get_llm_config.side_effect = AssertionError("loop should use prompt-resolved failover configs")

        msg = MagicMock()
        msg.content = ""
        msg.reasoning_content = None
        sleep_tool_call = MagicMock()
        sleep_tool_call.function = MagicMock()
        sleep_tool_call.function.name = "sleep_until_next_trigger"
        sleep_tool_call.function.arguments = "{}"
        msg.tool_calls = [sleep_tool_call]

        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        resp.model_extra = {"usage": MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)}
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

        self.assertEqual(
            mock_completion.call_args.kwargs["failover_configs"],
            prompt_failover_configs,
        )

    @patch("api.models.TaskCreditService.check_and_consume_credit_for_owner", return_value={"success": True, "credit": None})
    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_respects_selected_model_opt_out(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
        _mock_task_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None, {"prompt_allows_implied_send": True})
        start_web_session(self.agent, self.user)

        resp = self._mock_completion("Model says hello")
        resp.model_extra = {"gobii_runtime_hints": {"allow_implied_send": False}}
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

        self.assertFalse(mock_send_chat.called)
        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="Message delivery requires explicit send tools",
        ).first()
        self.assertIsNone(correction_step)

    @patch("api.models.TaskCreditService.check_and_consume_credit_for_owner", return_value={"success": True, "credit": None})
    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_explicit_send_still_executes_when_implied_send_disabled(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
        _mock_task_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None, {"prompt_allows_implied_send": False})
        start_web_session(self.agent, self.user)

        msg = MagicMock()
        msg.content = ""
        msg.reasoning_content = None
        send_tool_call = MagicMock()
        send_tool_call.id = "call_send_123"
        send_tool_call.function = MagicMock()
        send_tool_call.function.name = "send_chat_message"
        send_tool_call.function.arguments = json.dumps(
            {
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": "Explicit send works",
            }
        )
        msg.tool_calls = [send_tool_call]

        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        resp.model_extra = {"gobii_runtime_hints": {"allow_implied_send": False}}
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

        self.assertTrue(mock_send_chat.called)

    @patch("api.models.TaskCreditService.check_and_consume_credit_for_owner", return_value={"success": True, "credit": None})
    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_prompt_side_opt_out_blocks_implied_send_even_if_selected_model_allows_it(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
        _mock_task_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None, {"prompt_allows_implied_send": False})
        start_web_session(self.agent, self.user)

        resp = self._mock_completion("Prompt-side opt-out should win")
        resp.model_extra = {"gobii_runtime_hints": {"allow_implied_send": True}}
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

        self.assertFalse(mock_send_chat.called)
        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="Message delivery requires explicit send tools",
        ).first()
        self.assertIsNone(correction_step)


@tag("batch_event_processing_credits")
class ContinuationSignalTests(TestCase):
    """Tests for the _has_continuation_signal helper function."""

    def test_has_continuation_signal_with_let_me(self):
        self.assertTrue(ep._has_continuation_signal("Let me check that for you."))

    def test_has_continuation_signal_with_ill(self):
        self.assertTrue(ep._has_continuation_signal("I'll compile a report now."))

    def test_has_continuation_signal_with_im_going_to(self):
        self.assertTrue(ep._has_continuation_signal("I'm going to fetch the data."))

    def test_has_continuation_signal_case_insensitive(self):
        self.assertTrue(ep._has_continuation_signal("LET ME DO THAT"))
        self.assertTrue(ep._has_continuation_signal("i'll work on it"))

    def test_has_continuation_signal_false_for_done(self):
        self.assertFalse(ep._has_continuation_signal("Work complete."))
        self.assertFalse(ep._has_continuation_signal("That's everything you asked for."))

    def test_has_continuation_signal_empty(self):
        self.assertFalse(ep._has_continuation_signal(""))
        self.assertFalse(ep._has_continuation_signal(None))

    def test_has_continuation_signal_with_working_on(self):
        self.assertTrue(ep._has_continuation_signal("I'm currently working on the analysis."))

    def test_has_continuation_signal_with_proceeding_to(self):
        self.assertTrue(ep._has_continuation_signal("Proceeding to extract the data."))


@tag("batch_event_processing_credits")
class CompletionSignalTests(TestCase):
    """Tests for the _has_completion_signal helper function."""

    def test_has_completion_signal_with_work_complete(self):
        self.assertTrue(ep._has_completion_signal("Work complete."))
        self.assertTrue(ep._has_completion_signal("Work complete"))

    def test_has_completion_signal_with_task_complete(self):
        self.assertTrue(ep._has_completion_signal("Task complete! Here's the report."))

    def test_has_completion_signal_with_all_done(self):
        self.assertTrue(ep._has_completion_signal("All done! Let me know if you need anything else."))

    def test_has_completion_signal_with_thats_everything(self):
        self.assertTrue(ep._has_completion_signal("That's everything you asked for."))

    def test_has_completion_signal_with_here_are_your_results(self):
        self.assertTrue(ep._has_completion_signal("Here are your results: ..."))

    def test_has_completion_signal_with_heres_what_i_found(self):
        self.assertTrue(ep._has_completion_signal("Here's what I found in the data."))

    def test_has_completion_signal_case_insensitive(self):
        self.assertTrue(ep._has_completion_signal("WORK COMPLETE"))
        self.assertTrue(ep._has_completion_signal("all done"))

    def test_has_completion_signal_false_for_continuation(self):
        self.assertFalse(ep._has_completion_signal("Let me check that."))
        self.assertFalse(ep._has_completion_signal("I'll get that for you."))
        self.assertFalse(ep._has_completion_signal(ep.CANONICAL_CONTINUATION_PHRASE))

    def test_has_completion_signal_empty(self):
        self.assertFalse(ep._has_completion_signal(""))
        self.assertFalse(ep._has_completion_signal(None))

    def test_has_completion_signal_with_that_completes(self):
        self.assertTrue(ep._has_completion_signal("That completes the analysis."))

    def test_has_completion_signal_with_this_completes(self):
        self.assertTrue(ep._has_completion_signal("This completes your request."))


@tag("batch_event_processing_credits")
class MessageContinuationInferenceTests(TestCase):
    """Unit tests for omitted will_continue_work inference on message tools."""

    def test_infer_continuation_true_for_progress_update(self):
        self.assertTrue(
            ep._should_infer_message_tool_continuation(
                "Great question! Let me dig into the most-discussed stories first."
            )
        )

    def test_infer_continuation_false_for_completion_signal(self):
        self.assertFalse(
            ep._should_infer_message_tool_continuation(
                "All done. Here's what I found."
            )
        )

    def test_infer_continuation_false_for_stop_hint(self):
        self.assertFalse(
            ep._should_infer_message_tool_continuation(
                "I'll wait here. Let me know if you need anything else."
            )
        )

    def test_infer_continuation_false_when_question_present(self):
        self.assertFalse(
            ep._should_infer_message_tool_continuation(
                "Can you share which story you want me to prioritize?"
            )
        )

    def test_infer_continuation_false_for_empty(self):
        self.assertFalse(ep._should_infer_message_tool_continuation(""))
        self.assertFalse(ep._should_infer_message_tool_continuation(None))
