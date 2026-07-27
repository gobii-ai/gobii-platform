from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.pressure_resilience import (
    PRESSURE_RESILIENCE_SCENARIO_SLUGS,
    PRESSURE_RESILIENCE_SUITE_SLUG,
)
from api.evals.suites import SuiteRegistry


@tag("batch_eval_fingerprint")
class PressureResilienceEvalRegistrationTests(SimpleTestCase):
    def test_pressure_suite_and_scenario_are_registered(self):
        suite = SuiteRegistry.get(PRESSURE_RESILIENCE_SUITE_SLUG)

        self.assertEqual(tuple(suite.scenario_slugs), PRESSURE_RESILIENCE_SCENARIO_SLUGS)
        scenario = ScenarioRegistry.get(PRESSURE_RESILIENCE_SCENARIO_SLUGS[0])
        self.assertIn("context_limit", scenario.tags)
        self.assertIn("reply_channel", scenario.tags)
        self.assertEqual(
            [task.name for task in scenario.tasks],
            [
                "inject_pressure_fixture",
                "verify_context_pressure",
                "verify_bound_delivery",
                "verify_calm_prioritization",
                "verify_workload_emotion",
            ],
        )
