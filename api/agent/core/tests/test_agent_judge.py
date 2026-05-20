"""Tests for persistent-agent judge trajectory packets."""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.core.agent_judge import JudgePromptLimits, _build_trajectory_packet
from api.models import BrowserUseAgent, PersistentAgent


@tag("batch_event_processing")
class AgentJudgeTrajectoryPacketTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="agent-judge@example.com",
            email="agent-judge@example.com",
            password="secret",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="JudgePacketBA")
        self.agent = PersistentAgent.objects.create(
            user=user,
            name="Judge Packet Agent",
            charter="Route all contact through Tyler Kenobi. " + ("Keep this directive. " * 100),
            browser_use_agent=browser_agent,
        )

    def test_trajectory_packet_includes_full_charter_and_recipient_neutral_blocker_guidance(self):
        prompt_limits = JudgePromptLimits(
            prompt_token_budget=10_000,
            message_history_limit=0,
            tool_call_history_limit=0,
            skill_prompt_limit=0,
            enabled_tool_limit=0,
        )

        with patch(
            "api.agent.core.agent_judge.get_agent_llm_tier",
            return_value=SimpleNamespace(value="max"),
        ), patch(
            "api.agent.core.agent_judge._build_current_context_snapshot",
            return_value={"skills": {}, "sqlite": {}},
        ):
            packet = _build_trajectory_packet(
                self.agent,
                tools=[],
                recent_messages=[],
                recent_tool_calls=[],
                trigger_reasons=[],
                non_judge_step_count=0,
                prompt_limits=prompt_limits,
            )

        self.assertGreater(len(self.agent.charter), 1200)
        self.assertEqual(packet["agent"]["charter"], self.agent.charter)
        policy_text = "\n".join(packet["policy_excerpts"])
        self.assertIn("appropriate responsible participant, manager, peer agent, or user", policy_text)
        self.assertIn("do not assume the account owner or user is always the right recipient", policy_text)
