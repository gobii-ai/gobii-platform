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
    JudgePromptLimits,
    NO_ACTION,
    REPORT_TOOL_NAME,
    _build_judge_messages,
    _judge_tool_definition,
    _judge_prompt_limits,
    approve_judge_suggestion,
    build_manual_judge_trigger,
    build_judge_trigger,
    is_agent_judge_enabled_for_agent,
    maybe_run_agent_judge,
    run_manual_agent_judge,
)
from api.agent.core.llm_config import get_agent_llm_tier
from api.services.prompt_settings import invalidate_prompt_settings_cache
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentCommsEndpoint,
    PersistentAgentJudgeSuggestion,
    PersistentAgentMessage,
    PersistentAgentSkill,
    PersistentAgentStep,
    PersistentAgentSystemSkillState,
    PersistentAgentSystemMessage,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
    PromptConfig,
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
        self.agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=f"agent-{self.agent.id}@example.com",
            is_primary=True,
        )
        self.user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address=f"user-{self.agent.id}@example.com",
        )

    def tearDown(self):
        invalidate_prompt_settings_cache()
        super().tearDown()

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

    def _add_message(self, index: int, *, outbound: bool = False, body: str | None = None) -> PersistentAgentMessage:
        return PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.agent_endpoint if outbound else self.user_endpoint,
            to_endpoint=self.user_endpoint if outbound else self.agent_endpoint,
            is_outbound=outbound,
            body=body or f"Message {index}",
        )

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

    def test_negative_language_trigger_only_checks_latest_user_message(self):
        self._add_steps(1)
        older_message = self._add_message(0, body="This is still broken.")
        latest_message = self._add_message(1, body="Can you try that again?")
        PersistentAgentMessage.objects.filter(id=older_message.id).update(
            timestamp=timezone.now() - timedelta(minutes=2)
        )
        PersistentAgentMessage.objects.filter(id=latest_message.id).update(
            timestamp=timezone.now() - timedelta(minutes=1)
        )

        trigger = build_judge_trigger(self.agent, tools=[])

        self.assertIsNone(trigger)

    def test_negative_language_trigger_uses_stronger_latest_user_signal(self):
        self._add_steps(1)
        self._add_message(0, body="This is still broken.")

        trigger = build_judge_trigger(self.agent, tools=[])

        self.assertIsNotNone(trigger)
        self.assertIn("negative_user_language", trigger.reasons)

    def test_negative_language_trigger_includes_latest_user_profanity(self):
        self._add_steps(1)
        self._add_message(0, body="This is fucking broken.")

        trigger = build_judge_trigger(self.agent, tools=[])

        self.assertIsNotNone(trigger)
        self.assertIn("negative_user_language", trigger.reasons)

    def test_extra_trigger_reason_runs_judge(self):
        self._add_steps(1)

        trigger = build_judge_trigger(
            self.agent,
            tools=[],
            extra_trigger_reasons=["burn_rate_throttled"],
        )

        self.assertIsNotNone(trigger)
        self.assertEqual(trigger.reasons, ["burn_rate_throttled"])

    def test_judge_tool_does_not_offer_request_human_input_suggestion(self):
        tool = _judge_tool_definition()

        suggestion_types = tool["function"]["parameters"]["properties"]["suggestion_type"]["enum"]
        self.assertNotIn("request_human_input", suggestion_types)

    def test_stonewall_loop_ignores_generic_blocker_words(self):
        self._add_steps(1)
        self._add_message(0, body="Please continue")
        self._add_message(1, outbound=True, body="I need to check the page before continuing.")
        self._add_message(2, body="Please continue")
        self._add_message(3, outbound=True, body="This is blocked by a slow page load.")
        self._add_message(4, body="Please continue")

        trigger = build_judge_trigger(self.agent, tools=[])

        self.assertIsNone(trigger)

    def test_stonewall_loop_requires_explicit_blocker_phrase(self):
        self._add_steps(1)
        self._add_message(0, body="Please continue")
        self._add_message(1, outbound=True, body="I need more information before I can proceed.")
        self._add_message(2, body="Please continue")
        self._add_message(3, outbound=True, body="I need more information before I can proceed.")
        self._add_message(4, body="Please continue")

        trigger = build_judge_trigger(self.agent, tools=[])

        self.assertIsNotNone(trigger)
        self.assertIn("stonewall_loop", trigger.reasons)

    def test_trajectory_packet_includes_generic_context_and_provenance(self):
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="Daily report",
            description="Prepare a recurring report.",
            version=1,
            tools=["sqlite_batch"],
            instructions="Query source data before writing the report.",
        )
        PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key="documents",
            is_enabled=True,
        )
        PersistentAgentSystemMessage.objects.create(
            agent=self.agent,
            body="Use the latest user directive first.",
        )
        self._add_failed_tool_trigger()

        trigger = build_judge_trigger(self.agent, tools=[])

        self.assertIsNotNone(trigger)
        trajectory = trigger.trajectory
        self.assertIn("current_context", trajectory)
        self.assertEqual(
            trajectory["current_context"]["skills"]["saved_skills"][0]["name"],
            "Daily report",
        )
        self.assertEqual(
            trajectory["current_context"]["skills"]["enabled_system_skills"][0]["skill_key"],
            "documents",
        )
        self.assertIn("sqlite", trajectory["current_context"])
        self.assertEqual(
            trajectory["recent_trajectory"]["tool_calls"][-1]["source_type"],
            "tool_call",
        )
        self.assertEqual(
            trajectory["recent_trajectory"]["steps"][-1]["trajectory_scope"],
            "recent",
        )
        self.assertEqual(
            trajectory["recent_trajectory"]["system_directives"][-1]["source_type"],
            "system_directive",
        )

    def test_judge_trajectory_uses_prompt_config_ultra_max_limits(self):
        config, _ = PromptConfig.objects.get_or_create(singleton_id=1)
        config.ultra_max_message_history_limit = 2
        config.ultra_max_tool_call_history_limit = 3
        config.ultra_max_skill_prompt_limit = 1
        config.ultra_max_enabled_tool_limit = 2
        config.save()
        invalidate_prompt_settings_cache()

        for index in range(4):
            self._add_message(index, outbound=bool(index % 2))
            self._add_error_tool_call(index)
            PersistentAgentSkill.objects.create(
                agent=self.agent,
                name=f"Skill {index}",
                description=f"Skill description {index}",
                version=1,
                instructions=f"Skill instructions {index}",
            )

        trigger = build_manual_judge_trigger(
            self.agent,
            tools=[
                {"function": {"name": "first_tool", "description": "First"}},
                {"function": {"name": "second_tool", "description": "Second"}},
                {"function": {"name": "third_tool", "description": "Third"}},
            ],
        )

        trajectory = trigger.trajectory
        self.assertEqual(len(trajectory["recent_trajectory"]["messages"]), 2)
        self.assertEqual(len(trajectory["recent_trajectory"]["tool_calls"]), 3)
        self.assertEqual(len(trajectory["current_context"]["skills"]["saved_skills"]), 1)
        self.assertEqual(len(trajectory["current_context"]["skills"]["enabled_system_skills"]), 0)
        self.assertEqual(len(trajectory["capability_manifest"]), 2)

    def test_judge_prompt_uses_promptree_rendered_user_content(self):
        trigger = build_manual_judge_trigger(self.agent, tools=[])
        limits = JudgePromptLimits(
            prompt_token_budget=500,
            message_history_limit=2,
            tool_call_history_limit=2,
            skill_prompt_limit=1,
            enabled_tool_limit=1,
        )

        with patch("api.agent.core.agent_judge._create_token_estimator", return_value=lambda text: len(text.split())):
            messages = _build_judge_messages(trigger.trajectory, model="test-model", prompt_limits=limits)

        user_content = messages[1]["content"]
        self.assertIn("<judge_contract>", user_content)
        self.assertIn("<high_priority>", user_content)
        self.assertFalse(user_content.strip().startswith("{"))

    def test_judge_prompt_shrinks_large_tool_result_under_budget(self):
        trajectory = {
            "agent": {
                "id": str(self.agent.id),
                "name": "Judge Agent",
                "current_tier": "standard",
                "charter": "Do useful work.",
            },
            "packet_notes": [],
            "trigger_reasons": ["manual_audit"],
            "non_judge_step_count": 1,
            "policy_excerpts": ["Use evidence."],
            "capability_manifest": [],
            "current_context": {
                "skills": {},
                "sqlite": {},
            },
            "recent_trajectory": {
                "plan_snapshot": {},
                "messages": [],
                "system_directives": [],
                "steps": [],
                "tool_calls": [
                    {
                        "tool_name": "large_tool",
                        "status": "complete",
                        "params": {"query": "large"},
                        "result": "large_result " * 1000,
                    }
                ],
            },
        }
        limits = JudgePromptLimits(
            prompt_token_budget=140,
            message_history_limit=2,
            tool_call_history_limit=2,
            skill_prompt_limit=1,
            enabled_tool_limit=1,
        )

        with patch("api.agent.core.agent_judge._create_token_estimator", return_value=lambda text: len(text.split())):
            messages = _build_judge_messages(trajectory, model="test-model", prompt_limits=limits)

        user_content = messages[1]["content"]
        self.assertIn("BYTES TRUNCATED", user_content)
        self.assertLess(len(user_content.split()), 400)

    def test_judge_prompt_budget_uses_ultra_max_and_endpoint_cap(self):
        config, _ = PromptConfig.objects.get_or_create(singleton_id=1)
        config.ultra_max_prompt_token_budget = 1000
        config.ultra_max_message_history_limit = 4
        config.ultra_max_tool_call_history_limit = 5
        config.ultra_max_skill_prompt_limit = 2
        config.ultra_max_enabled_tool_limit = 3
        config.save()
        invalidate_prompt_settings_cache()

        with patch("api.agent.core.agent_judge._agent_judge_endpoint_max_input_tokens", return_value=None):
            limits = _judge_prompt_limits()
        self.assertEqual(limits.prompt_token_budget, 1000)
        self.assertEqual(limits.message_history_limit, 4)
        self.assertEqual(limits.tool_call_history_limit, 5)
        self.assertEqual(limits.skill_prompt_limit, 2)
        self.assertEqual(limits.enabled_tool_limit, 3)

        with patch("api.agent.core.agent_judge._agent_judge_endpoint_max_input_tokens", return_value=2500):
            capped_limits = _judge_prompt_limits()
        self.assertEqual(capped_limits.prompt_token_budget, 500)

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

    def test_manual_judge_suggestion_requires_staff_review(self):
        self._add_failed_tool_trigger()
        response = _judge_response(
            {
                "suggestion_type": "strategy_shift",
                "message": "Try a simpler plan before using more tools.",
                "agent_directive": "Pause and propose a simpler next step.",
            }
        )

        with patch(
            "api.agent.core.agent_judge.get_agent_judge_llm_config",
            return_value=("test-provider", "test-model", {}),
        ), patch(
            "api.agent.core.agent_judge.run_completion",
            return_value=response,
        ):
            result = run_manual_agent_judge(self.agent, tools=[])

        self.assertTrue(result["ran"])
        self.assertEqual(result["suggestion"]["status"], PersistentAgentJudgeSuggestion.Status.PENDING_REVIEW)
        self.assertEqual(result["suggestion"]["agentDirective"], "Pause and propose a simpler next step.")
        suggestion = PersistentAgentJudgeSuggestion.objects.get(agent=self.agent)
        self.assertEqual(suggestion.status, PersistentAgentJudgeSuggestion.Status.PENDING_REVIEW)
        self.assertIsNone(suggestion.system_message)
        self.assertFalse(PersistentAgentSystemMessage.objects.filter(agent=self.agent).exists())

        pending_actions = list_pending_action_requests(self.agent, self.user)
        self.assertFalse([action for action in pending_actions if action.get("kind") == "judge_suggestion"])

        approve_judge_suggestion(suggestion)
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, PersistentAgentJudgeSuggestion.Status.ACTIVE)
        self.assertIsNotNone(suggestion.system_message)
        self.assertTrue(suggestion.system_message.is_active)

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
