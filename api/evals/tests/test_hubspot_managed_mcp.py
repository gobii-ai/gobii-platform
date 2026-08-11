from types import SimpleNamespace

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_DEFINITIONS
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.hubspot_managed_mcp import (
    HUBSPOT_EVAL_TOOL_NAMES,
    HUBSPOT_MANAGED_MCP_CASES,
    HUBSPOT_MANAGED_MCP_SCENARIO_SLUGS,
    HUBSPOT_MANAGED_MCP_SUITE_SLUG,
    HUBSPOT_MCP_FORBIDS_LEGACY_PATHS,
    HUBSPOT_MCP_MISSING_CONNECTION,
    HUBSPOT_MCP_REAUTHORIZATION,
    HUBSPOT_USER_DETAILS_TOOL,
    hubspot_tools_appear_in_order,
)
from api.evals.suites import SuiteRegistry


@tag("eval_sim")
class HubSpotManagedMCPScenarioTests(SimpleTestCase):
    def test_suite_contains_managed_mcp_regressions(self):
        suite = SuiteRegistry.get(HUBSPOT_MANAGED_MCP_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), HUBSPOT_MANAGED_MCP_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 5)

    def test_synthetic_catalog_models_remote_hubspot_tools(self):
        for tool_name in HUBSPOT_EVAL_TOOL_NAMES:
            self.assertIn(tool_name, EVAL_SYNTHETIC_TOOL_DEFINITIONS)
            definition = EVAL_SYNTHETIC_TOOL_DEFINITIONS[tool_name]
            self.assertTrue(definition["description"])
            self.assertEqual(definition["parameters"]["type"], "object")

    def test_connected_cases_require_user_details_before_substantive_work(self):
        for case in HUBSPOT_MANAGED_MCP_CASES:
            if not case.connected:
                self.assertEqual(case.expected_hubspot_tools, ())
                continue
            self.assertEqual(case.expected_hubspot_tools[0], HUBSPOT_USER_DETAILS_TOOL)

    def test_reauthorization_case_stops_after_account_preflight(self):
        case = next(case for case in HUBSPOT_MANAGED_MCP_CASES if case.slug == HUBSPOT_MCP_REAUTHORIZATION)

        self.assertEqual(case.expected_hubspot_tools, (HUBSPOT_USER_DETAILS_TOOL,))
        self.assertIn("REQUIRES_REAUTHORIZATION", str(case.mock_config[HUBSPOT_USER_DETAILS_TOOL]))

    def test_legacy_guardrail_case_seeds_both_connection_methods(self):
        case = next(
            case
            for case in HUBSPOT_MANAGED_MCP_CASES
            if case.slug == HUBSPOT_MCP_FORBIDS_LEGACY_PATHS
        )

        self.assertTrue(case.seed_legacy_connection)
        self.assertIn("legacy_guardrail", case.tags)

    def test_missing_connection_case_has_no_remote_tool_fixture(self):
        case = next(case for case in HUBSPOT_MANAGED_MCP_CASES if case.slug == HUBSPOT_MCP_MISSING_CONNECTION)
        scenario = ScenarioRegistry.get(HUBSPOT_MCP_MISSING_CONNECTION)

        self.assertFalse(case.connected)
        self.assertEqual(case.mock_config, {})
        self.assertEqual(case.expected_hubspot_tools, ())
        self.assertTrue(scenario.requires_personal_agent)

    def test_order_helper_rejects_substantive_call_before_preflight(self):
        expected = (HUBSPOT_USER_DETAILS_TOOL, HUBSPOT_EVAL_TOOL_NAMES[1])
        correct = [SimpleNamespace(tool_name=name) for name in expected]
        reversed_calls = list(reversed(correct))

        self.assertTrue(hubspot_tools_appear_in_order(correct, expected))
        self.assertFalse(hubspot_tools_appear_in_order(reversed_calls, expected))

    def test_registered_scenarios_have_real_harness_metadata(self):
        for slug in HUBSPOT_MANAGED_MCP_SCENARIO_SLUGS:
            metadata = ScenarioRegistry.get(slug).get_metadata()
            self.assertEqual(metadata.category, "hubspot_managed_mcp")
            self.assertEqual(metadata.area, "system_skills")
            self.assertIn("managed_mcp", metadata.tags)
            self.assertIn("real_harness", metadata.tags)
