from django.test import TestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_DEFINITIONS
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.capability_routing import (
    CAPABILITY_ROUTING_CASES,
    CAPABILITY_ROUTING_SCENARIO_SLUGS,
    CAPABILITY_ROUTING_SUITE_SLUG,
    CONNECTED_TOOL,
    DIRECT_TOOL,
)
from api.evals.suites import SuiteRegistry


@tag("batch_eval_fingerprint")
class CapabilityRoutingEvalTests(TestCase):
    def test_suite_registers_both_real_harness_routes(self):
        suite = SuiteRegistry.get(CAPABILITY_ROUTING_SUITE_SLUG)

        self.assertEqual(tuple(suite.scenario_slugs), CAPABILITY_ROUTING_SCENARIO_SLUGS)
        self.assertEqual(len(CAPABILITY_ROUTING_CASES), 2)
        for slug in CAPABILITY_ROUTING_SCENARIO_SLUGS:
            scenario = ScenarioRegistry.get(slug)
            self.assertIn("real_harness", scenario.tags)
            self.assertEqual(
                [task.name for task in scenario.tasks],
                ["inject_prompt", "verify_route_state_used", "verify_first_route"],
            )

    def test_route_tools_are_neutral_and_require_distinct_inputs(self):
        direct = EVAL_SYNTHETIC_TOOL_DEFINITIONS[DIRECT_TOOL]
        connected = EVAL_SYNTHETIC_TOOL_DEFINITIONS[CONNECTED_TOOL]

        self.assertEqual(
            direct["parameters"]["required"],
            ["resource_id", "credential_file_path", "metric"],
        )
        self.assertEqual(connected["parameters"]["required"], ["metric"])
        self.assertNotIn("prefer", direct["description"].casefold())
        self.assertNotIn("prefer", connected["description"].casefold())

    def test_prompts_do_not_leak_route_table_or_tool_names(self):
        for case in CAPABILITY_ROUTING_CASES:
            prompt = case.prompt.casefold()
            self.assertNotIn("integration_routes", prompt)
            self.assertNotIn("sqlite", prompt)
            self.assertNotIn(DIRECT_TOOL, prompt)
            self.assertNotIn(CONNECTED_TOOL, prompt)
