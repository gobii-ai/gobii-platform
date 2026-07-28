from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.prompt_context import _get_peer_communication_instruction
from api.agent.tools.peer_dm import get_send_agent_message_tool
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.structured_peer_handoffs import (
    STRUCTURED_PEER_HANDOFF_CASES,
    STRUCTURED_PEER_HANDOFF_SCENARIO_SLUGS,
    STRUCTURED_PEER_HANDOFF_SUITE_SLUG,
)
from api.evals.suites import SuiteRegistry


@tag("batch_eval_fingerprint")
class StructuredPeerHandoffEvalTests(SimpleTestCase):
    def test_tool_contract_makes_message_optional_and_payload_schema_free(self):
        parameters = get_send_agent_message_tool()["function"]["parameters"]
        payload_schema = parameters["properties"]["structured_payload"]

        self.assertNotIn("message", parameters["required"])
        self.assertEqual(parameters["required"], ["peer_agent_id", "will_continue_work"])
        self.assertEqual(
            payload_schema["anyOf"],
            [
                {"type": "object", "additionalProperties": True},
                {"type": "array"},
            ],
        )

    def test_peer_guidance_distinguishes_prose_from_exact_data(self):
        instruction = _get_peer_communication_instruction()
        tool_description = get_send_agent_message_tool()["function"]["description"]

        self.assertIn("Fielded records/lists use structured payloads", instruction)
        self.assertIn("questions use prose", instruction)
        self.assertIn("message may add prose context but must not be its only carrier", tool_description)

    def test_suite_registers_real_harness_scenarios(self):
        suite = SuiteRegistry.get(STRUCTURED_PEER_HANDOFF_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), STRUCTURED_PEER_HANDOFF_SCENARIO_SLUGS)
        for slug in STRUCTURED_PEER_HANDOFF_SCENARIO_SLUGS:
            metadata = ScenarioRegistry.get(slug).get_metadata()
            self.assertEqual(metadata.expected_runtime, "short")
            self.assertEqual(metadata.cost_class, "low")
            self.assertIn("real_harness", metadata.tags)

    def test_eval_prompts_do_not_name_the_expected_transport(self):
        prompts = " ".join(case.prompt for case in STRUCTURED_PEER_HANDOFF_CASES).lower()

        self.assertNotIn("structured_payload", prompts)
        self.assertNotIn("structured payload", prompts)
        self.assertNotIn("json", prompts)
