from types import SimpleNamespace

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.behavior_micro import _charter_patch_pairs
from api.evals.scenarios.lasting_feedback import (
    LASTING_FEEDBACK_ALREADY_SATISFIED_KEEPS_WORK,
    LASTING_FEEDBACK_META_INSTRUCTION_KEEPS_WORK,
    LASTING_FEEDBACK_SCENARIO_SLUGS,
    LASTING_FEEDBACK_SUITE_SLUG,
)
from api.evals.suites import SuiteRegistry


@tag("batch_eval_fingerprint")
class LastingFeedbackEvalTests(SimpleTestCase):
    def test_suite_registers_varied_forward_progress_cases(self):
        suite = SuiteRegistry.get(LASTING_FEEDBACK_SUITE_SLUG)

        self.assertEqual(suite.scenario_slugs, LASTING_FEEDBACK_SCENARIO_SLUGS)
        self.assertIn(LASTING_FEEDBACK_META_INSTRUCTION_KEEPS_WORK, suite.scenario_slugs)
        self.assertIn(LASTING_FEEDBACK_ALREADY_SATISFIED_KEEPS_WORK, suite.scenario_slugs)

    def test_scenarios_require_work_completion(self):
        suite = SuiteRegistry.get(LASTING_FEEDBACK_SUITE_SLUG)

        for slug in suite.scenario_slugs:
            task_names = {task.name for task in ScenarioRegistry.get(slug).tasks}
            self.assertIn("verify_active_work_completed", task_names)

    def test_existing_immediate_work_eval_parses_multiline_charter_patch(self):
        call = SimpleNamespace(tool_params={
            "sql": (
                "UPDATE __agent_config SET charter=patch_text(charter,\n"
                "'Analyze every signal.',\n"
                "'Record signals unless analysis is requested.') WHERE id=1;\n"
                "INSERT INTO signal_log(signal) VALUES ('A signal');"
            ),
        })

        self.assertEqual(
            _charter_patch_pairs(call),
            [("Analyze every signal.", "Record signals unless analysis is requested.")],
        )
