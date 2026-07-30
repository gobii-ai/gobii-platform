from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.prompt_context import _get_peer_communication_instruction
from api.agent.tools.peer_dm import get_send_agent_message_tool
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.structured_peer_handoffs import (
    STRUCTURED_PEER_HANDOFF_CASES,
    STRUCTURED_PEER_FILE_HANDOFF,
    STRUCTURED_PEER_HANDOFF_SCENARIO_SLUGS,
    STRUCTURED_PEER_HANDOFF_SUITE_SLUG,
    STRUCTURED_PEER_SCOPED_HANDOFF,
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
        self.assertIn("message may add context but cannot be its only carrier", tool_description)
        self.assertIn("omit unrelated or owner-private source context", tool_description)

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

    def test_file_handoff_uses_a_named_peer_and_natural_file_request(self):
        case = next(case for case in STRUCTURED_PEER_HANDOFF_CASES if case.expects_attachment)

        self.assertEqual(case.slug, STRUCTURED_PEER_FILE_HANDOFF)
        self.assertIn("ledger agent", case.prompt.lower())
        self.assertIn("/exports/northstar-handoff.txt", case.prompt)
        self.assertNotIn("peer_agent_id", case.prompt)
        self.assertNotIn("$[", case.prompt)

    def test_scoped_handoff_separates_operational_record_from_private_context(self):
        case = next(
            case for case in STRUCTURED_PEER_HANDOFF_CASES
            if case.slug == STRUCTURED_PEER_SCOPED_HANDOFF
        )

        self.assertEqual(case.expected_record["assignment_id"], "AS-77")
        self.assertEqual(case.forbidden_handoff_terms, ("bipolar", "compensation"))
        self.assertNotIn("structured_payload", case.prompt)

    def test_scoped_handoff_accepts_metadata_without_renaming_required_fields(self):
        scenario = ScenarioRegistry.get(STRUCTURED_PEER_SCOPED_HANDOFF)

        self.assertTrue(
            scenario._contains_record_fields(
                {"assignment_id": "AS-77", "account": "Northwind", "action": "assign"},
                {"assignment_id": "AS-77", "account": "Northwind"},
            )
        )
        self.assertFalse(
            scenario._contains_record_fields(
                {"assignment_id": "AS-77", "entity": "Northwind"},
                {"assignment_id": "AS-77", "account": "Northwind"},
            )
        )
