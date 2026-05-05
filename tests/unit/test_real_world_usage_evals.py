from django.test import TestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.real_world_usage import (
    REAL_WORLD_USAGE_SCENARIO_SLUGS,
    REAL_WORLD_USAGE_SUITE_SLUG,
    RealWorldUsageScenario,
)
from api.evals.suites import SuiteRegistry


@tag("batch_real_world_evals")
class RealWorldUsageEvalRegistrationTests(TestCase):
    def test_all_real_world_usage_scenarios_are_registered(self):
        registered = ScenarioRegistry.list_all()

        self.assertEqual(len(REAL_WORLD_USAGE_SCENARIO_SLUGS), 30)
        self.assertEqual(len(REAL_WORLD_USAGE_SCENARIO_SLUGS), len(set(REAL_WORLD_USAGE_SCENARIO_SLUGS)))
        for slug in REAL_WORLD_USAGE_SCENARIO_SLUGS:
            self.assertIn(slug, registered)
            self.assertTrue(registered[slug].description.strip())

    def test_real_world_usage_suite_includes_expected_scenarios(self):
        suite = SuiteRegistry.get(REAL_WORLD_USAGE_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(suite.scenario_slugs, REAL_WORLD_USAGE_SCENARIO_SLUGS)

    def test_each_scenario_defines_regular_eval_prompt_and_required_tools(self):
        registered = ScenarioRegistry.list_all()

        for slug in REAL_WORLD_USAGE_SCENARIO_SLUGS:
            scenario = registered[slug]
            self.assertIsInstance(scenario, RealWorldUsageScenario)
            self.assertTrue(scenario.prompt.strip())
            self.assertTrue(scenario.usage_pattern.strip())
            self.assertTrue(scenario.source_signal.strip())
            self.assertGreaterEqual(len(scenario.required_tool_groups), 1)
            for group in scenario.required_tool_groups:
                self.assertTrue(group)
