import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone
from waffle.models import Flag

from api.agent.core.agent_judge import (
    JUDGE_DAILY_RUN_LIMIT,
    NO_ACTION,
    REPORT_TOOL_NAME,
    build_judge_trigger,
    is_agent_judge_enabled_for_agent,
    maybe_run_agent_judge,
)
from api.agent.core.llm_config import get_agent_llm_tier
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentJudgeSuggestion,
    PersistentAgentStep,
    PersistentAgentSystemMessage,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
    UserQuota,
)
from console.agent_chat.pending_actions import list_pending_action_requests
from constants.feature_flags import PERSISTENT_AGENT_LLM_JUDGE


def _judge_response(payload: dict):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        {
                            "function": {
                                "name": REPORT_TOOL_NAME,
                                "arguments": json.dumps(payload),
                            }
                        }
                    ],
                )
            )
        ]
    )


@tag("batch_event_processing")
class AgentJudgeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="agent-judge@example.com",
            email="agent-judge@example.com",
            password="password",
        )
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 50
        quota.save()
        Flag.objects.update_or_create(
            name=PERSISTENT_AGENT_LLM_JUDGE,
            defaults={
                "everyone": True,
                "percent": 0,
                "superusers": False,
                "staff": False,
                "authenticated": False,
            },
        )

    def setUp(self):
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="judge-browser-agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Judge Agent",
            charter="Do useful work.",
            browser_use_agent=browser_agent,
        )

    def _add_steps(self, count: int) -> None:
        for index in range(count):
            PersistentAgentStep.objects.create(
                agent=self.agent,
                description=f"Step {index}",
            )

    def _add_error_tool_call(self, index: int) -> None:
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description=f"Tool error {index}",
        )
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="read_file",
            tool_params={"path": f"/tmp/{index}"},
            result='{"status":"error","message":"failed"}',
            status="error",
        )

    def _add_failed_tool_trigger(self) -> None:
        for index in range(3):
            self._add_error_tool_call(index)

    def test_steps_alone_do_not_trigger_judge(self):
        self._add_steps(40)

        trigger = build_judge_trigger(self.agent, tools=[])

        self.assertIsNone(trigger)

    def test_disabled_waffle_flag_blocks_judge(self):
        Flag.objects.update_or_create(
            name=PERSISTENT_AGENT_LLM_JUDGE,
            defaults={
                "everyone": False,
                "percent": 0,
                "superusers": False,
                "staff": False,
                "authenticated": False,
            },
        )
        self._add_steps(40)

        self.assertFalse(is_agent_judge_enabled_for_agent(self.agent))
        self.assertIsNone(build_judge_trigger(self.agent, tools=[]))

        with patch("api.agent.core.agent_judge.get_agent_judge_llm_config") as config_mock:
            maybe_run_agent_judge(self.agent, tools=[])

        config_mock.assert_not_called()

    def test_user_specific_waffle_flag_enables_judge(self):
        flag, _ = Flag.objects.update_or_create(
            name=PERSISTENT_AGENT_LLM_JUDGE,
            defaults={
                "everyone": None,
                "percent": 0,
                "superusers": False,
                "staff": False,
                "authenticated": False,
            },
        )
        flag.users.add(self.user)
        self._add_failed_tool_trigger()

        self.assertTrue(is_agent_judge_enabled_for_agent(self.agent))
        self.assertIsNotNone(build_judge_trigger(self.agent, tools=[]))

    def test_failed_tool_threshold_triggers_judge(self):
        self._add_failed_tool_trigger()

        trigger = build_judge_trigger(self.agent, tools=[])

        self.assertIsNotNone(trigger)
        self.assertIn("failed_tool_calls", trigger.reasons)

    def test_intelligence_upgrade_creates_step_directive_and_pending_action(self):
        self._add_failed_tool_trigger()
        response = _judge_response(
            {
                "suggestion_type": "intelligence_upgrade",
                "message": "This task appears to need deeper reasoning.",
                "agent_directive": "Re-evaluate the current approach and suggest a higher intelligence tier.",
                "recommended_tier": "max",
            }
        )

        with patch(
            "api.agent.core.agent_judge.get_agent_judge_llm_config",
            return_value=("test-provider", "test-model", {}),
        ) as config_mock, patch(
            "api.agent.core.agent_judge.run_completion",
            return_value=response,
        ):
            maybe_run_agent_judge(self.agent, tools=[])

        config_mock.assert_called_once()
        suggestion = PersistentAgentJudgeSuggestion.objects.get(agent=self.agent)
        self.assertEqual(suggestion.title, "Consider higher intelligence")
        self.assertEqual(suggestion.ui_message, "This task appears to need deeper reasoning.")
        self.assertEqual(
            suggestion.agent_directive,
            "Re-evaluate the current approach and suggest a higher intelligence tier.",
        )
        self.assertEqual(suggestion.confidence, 0)
        self.assertEqual(suggestion.evidence, {})
        self.assertEqual(suggestion.recommended_tier, "max")
        self.assertEqual(PersistentAgentSystemMessage.objects.filter(agent=self.agent).count(), 1)
        self.assertTrue(
            PersistentAgentSystemStep.objects.filter(
                step__agent=self.agent,
                code=PersistentAgentSystemStep.Code.LLM_JUDGE_SUGGESTION,
            ).exists()
        )
        self.assertTrue(
            PersistentAgentCompletion.objects.filter(
                agent=self.agent,
                completion_type=PersistentAgentCompletion.CompletionType.LLM_JUDGE,
            ).exists()
        )

        pending_actions = list_pending_action_requests(self.agent, self.user)
        judge_actions = [action for action in pending_actions if action.get("kind") == "judge_suggestion"]
        self.assertEqual(len(judge_actions), 1)
        self.assertEqual(judge_actions[0]["suggestionType"], "intelligence_upgrade")

    def test_no_action_only_logs_completion(self):
        self._add_failed_tool_trigger()
        response = _judge_response(
            {
                "suggestion_type": NO_ACTION,
                "message": "No action needed.",
            }
        )

        with patch(
            "api.agent.core.agent_judge.get_agent_judge_llm_config",
            return_value=("test-provider", "test-model", {}),
        ), patch(
            "api.agent.core.agent_judge.run_completion",
            return_value=response,
        ):
            maybe_run_agent_judge(self.agent, tools=[])

        self.assertFalse(PersistentAgentJudgeSuggestion.objects.filter(agent=self.agent).exists())
        self.assertFalse(PersistentAgentSystemMessage.objects.filter(agent=self.agent).exists())
        self.assertTrue(
            PersistentAgentCompletion.objects.filter(
                agent=self.agent,
                completion_type=PersistentAgentCompletion.CompletionType.LLM_JUDGE,
            ).exists()
        )

    def test_judge_completion_cooldown_prevents_repeated_recent_runs(self):
        self._add_failed_tool_trigger()
        response = _judge_response(
            {
                "suggestion_type": NO_ACTION,
                "message": "No action needed.",
            }
        )

        with patch(
            "api.agent.core.agent_judge.get_agent_judge_llm_config",
            return_value=("test-provider", "test-model", {}),
        ) as config_mock, patch(
            "api.agent.core.agent_judge.run_completion",
            return_value=response,
        ) as run_mock:
            maybe_run_agent_judge(self.agent, tools=[])
            self._add_steps(7)
            self._add_failed_tool_trigger()
            maybe_run_agent_judge(self.agent, tools=[])

        self.assertEqual(config_mock.call_count, 1)
        self.assertEqual(run_mock.call_count, 1)
        self.assertEqual(
            PersistentAgentCompletion.objects.filter(
                agent=self.agent,
                completion_type=PersistentAgentCompletion.CompletionType.LLM_JUDGE,
            ).count(),
            1,
        )

    def test_wall_clock_cooldown_blocks_judge_even_after_step_gap(self):
        PersistentAgentCompletion.objects.create(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.LLM_JUDGE,
        )
        self._add_steps(7)
        self._add_failed_tool_trigger()

        with patch("api.agent.core.agent_judge.get_agent_judge_llm_config") as config_mock:
            maybe_run_agent_judge(self.agent, tools=[])

        config_mock.assert_not_called()

    def test_daily_cap_blocks_judge(self):
        old_enough = timezone.now() - timedelta(minutes=1)
        completions = [
            PersistentAgentCompletion.objects.create(
                agent=self.agent,
                completion_type=PersistentAgentCompletion.CompletionType.LLM_JUDGE,
            )
            for _ in range(JUDGE_DAILY_RUN_LIMIT)
        ]
        PersistentAgentCompletion.objects.filter(id__in=[completion.id for completion in completions]).update(
            created_at=old_enough
        )
        self._add_steps(7)
        self._add_failed_tool_trigger()

        with patch("api.agent.core.agent_judge.JUDGE_RUN_COOLDOWN_SECONDS", 0), patch(
            "api.agent.core.agent_judge.get_agent_judge_llm_config"
        ) as config_mock:
            maybe_run_agent_judge(self.agent, tools=[])

        config_mock.assert_not_called()

    def test_dedicated_judge_routing_does_not_mutate_agent_tier(self):
        self._add_failed_tool_trigger()
        before = get_agent_llm_tier(self.agent)
        response = _judge_response(
            {
                "suggestion_type": NO_ACTION,
                "message": "No action needed.",
            }
        )

        with patch(
            "api.agent.core.agent_judge.get_agent_judge_llm_config",
            return_value=("test-provider", "test-model", {}),
        ) as config_mock, patch(
            "api.agent.core.agent_judge.run_completion",
            return_value=response,
        ):
            maybe_run_agent_judge(self.agent, tools=[])

        self.agent.refresh_from_db()
        self.assertEqual(get_agent_llm_tier(self.agent), before)
        config_mock.assert_called_once_with()
