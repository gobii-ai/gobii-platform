import json
from types import SimpleNamespace

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.tools.add_discord_reaction import get_add_discord_reaction_tool
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.discord_native import (
    DISCORD_NATIVE_GATEWAY_WAKE,
    DISCORD_NATIVE_READABLE_COMPARISON,
    DISCORD_NATIVE_REACTION_SERIOUS_REQUEST_RESTRAINT,
    DISCORD_NATIVE_REACTION_REPLY_CONTEXT,
    DISCORD_NATIVE_REACTION_SHARED_WIN,
    DISCORD_NATIVE_RESEARCH_KICKOFF,
    DISCORD_NATIVE_SCENARIO_SLUGS,
    DISCORD_NATIVE_SUITE_SLUG,
    DISCORD_READABLE_COMPARISON_PROMPT,
    DiscordNativeGatewayWakeScenario,
    DiscordNativeReactionReplyContextScenario,
    DiscordNativeResearchKickoffScenario,
    contains_markdown_pipe_table,
)
from api.evals.suites import SuiteRegistry


@tag("batch_eval_fingerprint")
class DiscordNativeScenarioTests(SimpleTestCase):
    def test_suite_registers_real_harness_reaction_scenario(self):
        suite = SuiteRegistry.get(DISCORD_NATIVE_SUITE_SLUG)
        scenario = ScenarioRegistry.get(DISCORD_NATIVE_REACTION_REPLY_CONTEXT)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), DISCORD_NATIVE_SCENARIO_SLUGS)
        self.assertIsNotNone(scenario)
        self.assertIn("real_harness", scenario.get_metadata().tags)
        self.assertIsNotNone(ScenarioRegistry.get(DISCORD_NATIVE_REACTION_SHARED_WIN))
        self.assertIsNotNone(
            ScenarioRegistry.get(DISCORD_NATIVE_REACTION_SERIOUS_REQUEST_RESTRAINT)
        )
        self.assertIsNotNone(ScenarioRegistry.get(DISCORD_NATIVE_READABLE_COMPARISON))
        self.assertIsNotNone(ScenarioRegistry.get(DISCORD_NATIVE_RESEARCH_KICKOFF))
        self.assertIsNotNone(ScenarioRegistry.get(DISCORD_NATIVE_GATEWAY_WAKE))

    def test_readable_comparison_prompt_does_not_prescribe_formatting_contract(self):
        prompt = DISCORD_READABLE_COMPARISON_PROMPT.casefold()

        for implementation_term in ("bullet", "discord", "format", "markdown", "table"):
            with self.subTest(implementation_term=implementation_term):
                self.assertNotIn(implementation_term, prompt)

    def test_markdown_pipe_table_detector_distinguishes_discord_safe_structure(self):
        self.assertTrue(
            contains_markdown_pipe_table(
                "| Option | Tradeoff |\n"
                "| --- | --- |\n"
                "| Alpha | Fast, risky |\n"
                "| Beta | Balanced |"
            )
        )
        self.assertTrue(
            contains_markdown_pipe_table(
                "Option | Tradeoff\n"
                ":--- | ---:\n"
                "Alpha | Fast, risky"
            )
        )
        self.assertFalse(
            contains_markdown_pipe_table(
                "**Alpha**\n"
                "- Speed: fastest\n"
                "- Risk: highest\n\n"
                "**Beta**\n"
                "- Balanced"
            )
        )

    def test_research_kickoff_prompt_does_not_prescribe_responsiveness_contract(self):
        prompt = DiscordNativeResearchKickoffScenario.prompt.casefold()

        for implementation_term in (
            "acknowledge",
            "before",
            "kickoff",
            "progress",
            "working on",
        ):
            with self.subTest(implementation_term=implementation_term):
                self.assertNotIn(implementation_term, prompt)

    def test_gateway_wake_prompt_does_not_prescribe_dispatch_or_reply_behavior(self):
        prompt = DiscordNativeGatewayWakeScenario.prompt.casefold()

        for implementation_term in (
            "acknowledge",
            "discord",
            "reply",
            "respond",
            "wake",
        ):
            with self.subTest(implementation_term=implementation_term):
                self.assertNotIn(implementation_term, prompt)

    def test_reaction_tool_contract_requires_target_and_continuation(self):
        tool = get_add_discord_reaction_tool()["function"]

        self.assertEqual(tool["name"], "add_discord_reaction")
        self.assertEqual(
            set(tool["parameters"]["required"]),
            {"channel_id", "message_id", "emoji", "will_continue_work"},
        )

    def test_reaction_verifier_requires_exact_message_channel_and_emoji(self):
        call = SimpleNamespace(
            tool_name="add_discord_reaction",
            tool_params={
                "channel_id": "channel-1",
                "message_id": "message-1",
                "emoji": "👍",
                "will_continue_work": False,
            },
            result=json.dumps({"status": "success"}),
        )

        self.assertTrue(
            DiscordNativeReactionReplyContextScenario._reaction_matches(
                call,
                channel_id="channel-1",
                message_id="message-1",
            )
        )
        call.tool_params["message_id"] = "message-2"
        self.assertFalse(
            DiscordNativeReactionReplyContextScenario._reaction_matches(
                call,
                channel_id="channel-1",
                message_id="message-1",
            )
        )

    def test_reply_verifier_rejects_reaction_sized_acknowledgements(self):
        call = SimpleNamespace(
            tool_name="send_discord_message",
            tool_params={
                "channel_id": "channel-1",
                "message": "I see it.",
                "will_continue_work": False,
            },
            result=json.dumps({"status": "success"}),
        )

        self.assertFalse(
            DiscordNativeReactionReplyContextScenario._reply_matches(
                call,
                channel_id="channel-1",
            )
        )
        call.tool_params["message"] = "Check the auth service health and recent error logs first."
        self.assertTrue(
            DiscordNativeReactionReplyContextScenario._reply_matches(
                call,
                channel_id="channel-1",
            )
        )
