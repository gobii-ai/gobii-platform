import json
from types import SimpleNamespace

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.tools.add_discord_reaction import get_add_discord_reaction_tool
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.discord_native import (
    DISCORD_NATIVE_REACTION_REPLY_CONTEXT,
    DISCORD_NATIVE_SCENARIO_SLUGS,
    DISCORD_NATIVE_SUITE_SLUG,
    DiscordNativeReactionReplyContextScenario,
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
