from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.prompt_context import _get_peer_communication_instruction
from api.agent.tools.peer_dm import get_send_agent_message_tool
from api.agent.tools.send_discord_message import get_send_discord_message_tool
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.responsibility_boundaries import (
    RESPONSIBILITY_BOUNDARY_CASES,
    RESPONSIBILITY_BOUNDARY_PEER_FYI_NO_ACK,
    RESPONSIBILITY_BOUNDARY_PEER_REQUEST_HANDOFF,
    RESPONSIBILITY_BOUNDARY_SCENARIO_SLUGS,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNER,
    RESPONSIBILITY_BOUNDARY_SUITE_SLUG,
)
from api.evals.suites import SuiteRegistry


@tag("batch_eval_fingerprint")
class ResponsibilityBoundaryScenarioTests(SimpleTestCase):
    def test_peer_contract_is_compact_and_ownership_first(self):
        instruction = _get_peer_communication_instruction()

        self.assertIn("handoff route, not shared ownership", instruction)
        self.assertIn("For an out-of-charter peer request, call no task tools", instruction)
        self.assertIn("route or decline it immediately", instruction)
        self.assertIn("Silence is required for status or FYI messages needing no action", instruction)
        self.assertIn("never send thanks, receipts, or 'noted' replies", instruction)
        self.assertIn("preserve named human or source attribution", instruction)
        self.assertNotIn("freely", instruction)
        self.assertLessEqual(len(instruction.split()), 80)

    def test_communication_tools_repeat_the_boundary_at_decision_time(self):
        peer_description = get_send_agent_message_tool()["function"]["description"]
        discord_description = get_send_discord_message_tool()["function"]["description"]

        self.assertIn("only for a necessary handoff within your charter", peer_description)
        self.assertIn("Never use it for thanks, receipts, 'noted' replies", peer_description)
        peer_message_description = get_send_agent_message_tool()["function"]["parameters"]["properties"]["message"][
            "description"
        ]
        self.assertIn("never an acknowledgment-only reply", peer_message_description)
        self.assertIn("only when this agent owns the response", discord_description)
        self.assertIn("questions addressed to someone else", discord_description)

    def test_suite_registers_all_boundary_scenarios(self):
        suite = SuiteRegistry.get(RESPONSIBILITY_BOUNDARY_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), RESPONSIBILITY_BOUNDARY_SCENARIO_SLUGS)
        self.assertEqual(
            set(suite.scenario_slugs),
            {
                RESPONSIBILITY_BOUNDARY_PEER_FYI_NO_ACK,
                RESPONSIBILITY_BOUNDARY_PEER_REQUEST_HANDOFF,
                RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNER,
            },
        )

    def test_scenarios_use_the_real_harness_and_low_cost_metadata(self):
        for slug in RESPONSIBILITY_BOUNDARY_SCENARIO_SLUGS:
            scenario = ScenarioRegistry.get(slug)
            metadata = scenario.get_metadata()

            self.assertEqual(metadata.category, "responsibility_boundaries")
            self.assertEqual(metadata.area, "agent_behavior")
            self.assertEqual(metadata.expected_runtime, "short")
            self.assertEqual(metadata.cost_class, "low")
            self.assertIn("real_harness", metadata.tags)

    def test_events_do_not_state_the_expected_behavior(self):
        prompts = " ".join(case.prompt for case in RESPONSIBILITY_BOUNDARY_CASES).lower()

        self.assertNotIn("stay in your lane", prompts)
        self.assertNotIn("do not acknowledge this", prompts)
        self.assertNotIn("do not answer this", prompts)
