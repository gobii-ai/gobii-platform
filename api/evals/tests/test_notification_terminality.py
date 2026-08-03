from types import SimpleNamespace

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401
from api.evals.scenarios.notification_terminality import (
    CUSTOM_TOOL_IDLE_RESULT_SLEEPS,
    EXTERNAL_ACTION_EVIDENCE_INTEGRITY,
    INTERRUPTED_COMPLETED_OUTCOME,
    NOTIFICATION_TERMINALITY_COMPLETED,
    NOTIFICATION_TERMINALITY_REMAINING,
    NON_RETRYABLE_SOURCE_TERMINALITY,
    NOTIFICATION_TERMINALITY_SCENARIO_SLUGS,
    NOTIFICATION_TERMINALITY_SUITE_SLUG,
    NotificationTerminalityScenario,
    _CASES,
    _tool_call_was_executed,
)
from api.evals.suites import SuiteRegistry


@tag("batch_event_processing")
class NotificationTerminalityEvalTests(SimpleTestCase):
    @staticmethod
    def _call(name, completion_id, created_at=2):
        return SimpleNamespace(
            tool_name=name,
            step=SimpleNamespace(completion_id=completion_id, created_at=created_at),
        )

    def test_suite_registers_all_regressions(self):
        suite = SuiteRegistry.get(NOTIFICATION_TERMINALITY_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), NOTIFICATION_TERMINALITY_SCENARIO_SLUGS)
        self.assertIn(NON_RETRYABLE_SOURCE_TERMINALITY, suite.scenario_slugs)
        self.assertIn(EXTERNAL_ACTION_EVIDENCE_INTEGRITY, suite.scenario_slugs)
        self.assertIn(INTERRUPTED_COMPLETED_OUTCOME, suite.scenario_slugs)
        self.assertIn(CUSTOM_TOOL_IDLE_RESULT_SLEEPS, suite.scenario_slugs)

    def test_preexecution_skip_is_not_counted_as_a_source_attempt(self):
        skipped = SimpleNamespace(result='{"status":"error","executed":false}')
        attempted = SimpleNamespace(result='{"status":"error","retryable":false}')

        self.assertFalse(_tool_call_was_executed(skipped))
        self.assertTrue(_tool_call_was_executed(attempted))

    def test_completed_side_effects_reject_direct_notification_repeats(self):
        case = next(case for case in _CASES if case.slug == NOTIFICATION_TERMINALITY_COMPLETED)
        workflow = [self._call(case.custom_tool_name, "completion-1")]
        notifications = {
            "send_email": [self._call("send_email", "completion-1")],
        }

        passed, _summary = NotificationTerminalityScenario._notification_score(
            case, workflow, notifications, []
        )

        self.assertFalse(passed)

    def test_completed_side_effects_accept_no_direct_repeats(self):
        case = next(case for case in _CASES if case.slug == NOTIFICATION_TERMINALITY_COMPLETED)

        passed, _summary = NotificationTerminalityScenario._notification_score(
            case,
            [self._call(case.custom_tool_name, "completion-1")],
            {"send_email": []},
            [],
        )

        self.assertTrue(passed)

    def test_completed_side_effects_allow_kickoff_before_workflow(self):
        case = next(case for case in _CASES if case.slug == NOTIFICATION_TERMINALITY_COMPLETED)

        passed, _summary = NotificationTerminalityScenario._notification_score(
            case,
            [self._call(case.custom_tool_name, "completion-2", created_at=2)],
            {
                "send_email": [
                    self._call("send_email", "completion-1", created_at=1)
                ],
            },
            [],
        )

        self.assertTrue(passed)

    def test_remaining_side_effects_require_later_completion(self):
        case = next(case for case in _CASES if case.slug == NOTIFICATION_TERMINALITY_REMAINING)
        workflow = [self._call(case.custom_tool_name, "completion-1")]
        same_completion = {
            "send_email": [self._call("send_email", "completion-1")],
        }
        later_completion = {
            "send_email": [self._call("send_email", "completion-2")],
        }

        same_passed, _summary = NotificationTerminalityScenario._notification_score(
            case, workflow, same_completion, []
        )
        later_passed, _summary = NotificationTerminalityScenario._notification_score(
            case, workflow, later_completion, []
        )

        self.assertFalse(same_passed)
        self.assertTrue(later_passed)

    def test_dependency_hold_is_a_first_shot_failure(self):
        case = next(case for case in _CASES if case.slug == NOTIFICATION_TERMINALITY_REMAINING)

        passed, _summary = NotificationTerminalityScenario._notification_score(
            case,
            [self._call(case.custom_tool_name, "completion-1")],
            {
                "send_email": [self._call("send_email", "completion-2")],
            },
            [SimpleNamespace(description="Tool dependency: held outbound send")],
        )

        self.assertFalse(passed)

    def test_idle_custom_result_is_terminal_without_a_followup_completion(self):
        case = next(case for case in _CASES if case.slug == CUSTOM_TOOL_IDLE_RESULT_SLEEPS)

        self.assertTrue(case.result_is_terminal)
        self.assertEqual(case.custom_result["result"]["next_action"], "sleep")
        self.assertFalse(case.notifications_remain)
