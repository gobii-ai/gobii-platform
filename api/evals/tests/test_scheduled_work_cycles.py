from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.prompt_context import _get_continuation_mode_prompt_block, _get_sqlite_guidance
from api.agent.tools.sqlite_batch import get_sqlite_batch_tool
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.scheduled_work_cycles import (
    SCHEDULED_EMPTY_QUEUE_SILENCE,
    SCHEDULED_OWNED_WORK_DISPATCH,
    SCHEDULED_WORK_CYCLE_CASES,
    SCHEDULED_WORK_CYCLE_SCENARIO_SLUGS,
    SCHEDULED_WORK_CYCLE_SUITE_SLUG,
)
from api.evals.suites import SuiteRegistry


@tag("batch_eval_fingerprint")
class ScheduledWorkCycleScenarioTests(SimpleTestCase):
    def test_suite_registers_both_scheduled_cycle_regressions(self):
        suite = SuiteRegistry.get(SCHEDULED_WORK_CYCLE_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), SCHEDULED_WORK_CYCLE_SCENARIO_SLUGS)
        self.assertEqual(
            set(suite.scenario_slugs),
            {SCHEDULED_EMPTY_QUEUE_SILENCE, SCHEDULED_OWNED_WORK_DISPATCH},
        )

    def test_cases_do_not_leak_the_expected_sqlite_behavior(self):
        case_text = " ".join(
            (case.description for case in SCHEDULED_WORK_CYCLE_CASES)
        ).casefold()

        self.assertNotIn("__messages", case_text)
        self.assertNotIn("never poll", case_text)

    def test_scenarios_use_the_real_harness(self):
        for slug in SCHEDULED_WORK_CYCLE_SCENARIO_SLUGS:
            metadata = ScenarioRegistry.get(slug).get_metadata()

            self.assertEqual(metadata.category, "scheduled_work_cycles")
            self.assertEqual(metadata.expected_runtime, "short")
            self.assertEqual(metadata.cost_class, "low")
            self.assertIn("real_harness", metadata.tags)

    def test_scheduled_cycle_inputs_choose_owned_state_and_preserve_follow_through(self):
        continuation = _get_continuation_mode_prompt_block()
        sqlite_guidance = _get_sqlite_guidance()
        sqlite_description = get_sqlite_batch_tool()["function"]["parameters"]["properties"][
            "will_continue_work"
        ]["description"]

        self.assertIn("query owned state", continuation)
        self.assertIn("with `will_continue_work=true`", continuation)
        self.assertIn("not `__messages`", continuation)
        self.assertIn("act from rows or sleep silently", continuation)
        self.assertIn("for any read that may trigger another tool", sqlite_guidance)
        self.assertIn("queue reads are true", sqlite_guidance)
        self.assertIn("for any read that may trigger another tool", sqlite_description)
