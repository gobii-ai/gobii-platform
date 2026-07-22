import json
from datetime import timedelta
from types import SimpleNamespace

from django.test import TestCase, tag
from django.utils import timezone

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.agent_scheduling import (
    AGENT_SCHEDULING_CASES,
    AGENT_SCHEDULING_SCENARIO_SLUGS,
    AGENT_SCHEDULING_SUITE_SLUG,
    RELATIVE_TIMER_PRESERVES_RECURRING,
    _cron_matches,
    _schedule_snapshot,
    _schedule_sql_strategy_failures,
)
from api.evals.stop_policy import (
    sqlite_batch_is_only_planning_state_mutation,
    sqlite_batch_is_only_planning_state_read,
    sqlite_batch_mutates_schedule_state,
    sqlite_statement_mutates_agent_schedules,
)
from api.evals.suites import SuiteRegistry
from api.models import PersistentAgentSchedule


def _schedule_row(
    key,
    *,
    name,
    instruction,
    kind="recurring",
    expression="0 7 * * *",
    run_at=None,
    enabled=True,
):
    return SimpleNamespace(
        schedule_key=key,
        name=name,
        instruction=instruction,
        kind=kind,
        expression=expression,
        timezone="America/New_York",
        run_at=run_at,
        enabled=enabled,
    )


def _sqlite_call(sql, *, status="complete", result_status="ok"):
    return SimpleNamespace(
        tool_name="sqlite_batch",
        tool_params={"sql": sql},
        status=status,
        result=json.dumps({"status": result_status}),
    )


@tag("batch_eval_fingerprint")
class AgentSchedulingEvalTests(TestCase):
    def test_suite_registers_bounded_messy_schedule_cases(self):
        suite = SuiteRegistry.get(AGENT_SCHEDULING_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(len(AGENT_SCHEDULING_SCENARIO_SLUGS), 8)
        self.assertEqual(suite.scenario_slugs, AGENT_SCHEDULING_SCENARIO_SLUGS)
        for slug in AGENT_SCHEDULING_SCENARIO_SLUGS:
            scenario = ScenarioRegistry.get(slug)
            self.assertIsNotNone(scenario)
            self.assertIn("schedule", scenario.tags)
            self.assertEqual(
                [task.name for task in scenario.tasks],
                ["inject_prompt", "verify_schedule_state", "verify_sqlite_strategy"],
            )

    def test_prompts_are_natural_and_do_not_leak_sqlite_solution(self):
        for case in AGENT_SCHEDULING_CASES:
            prompt = case.prompt.casefold()
            self.assertNotIn("__agent_schedules", prompt)
            self.assertNotIn("sqlite", prompt)
            self.assertNotIn("schedule_key", prompt)
            self.assertNotIn("insert into", prompt)

    def test_direct_common_schedule_cases_are_tagged(self):
        for slug in (
            "common_use_case_091_schedule_daily_digest",
            "common_use_case_092_schedule_hourly_monitor",
            "common_use_case_107_schedule_vc_digest",
        ):
            self.assertIn("schedule", ScenarioRegistry.get(slug).tags)

    def test_schedule_mutation_detection_requires_the_schedule_table_as_target(self):
        self.assertTrue(
            sqlite_statement_mutates_agent_schedules(
                "INSERT INTO __agent_schedules (schedule_key, kind) VALUES ('digest', 'recurring')"
            )
        )
        self.assertTrue(
            sqlite_statement_mutates_agent_schedules(
                "UPDATE [__agent_schedules] SET enabled = 0 WHERE schedule_key = 'digest'"
            )
        )
        self.assertTrue(
            sqlite_statement_mutates_agent_schedules(
                "DELETE FROM `__agent_schedules` WHERE schedule_key = 'digest'"
            )
        )
        self.assertFalse(
            sqlite_statement_mutates_agent_schedules(
                "CREATE TABLE audit AS SELECT * FROM __agent_schedules"
            )
        )
        self.assertFalse(
            sqlite_statement_mutates_agent_schedules(
                "INSERT INTO audit(note) VALUES ('UPDATE __agent_schedules later')"
            )
        )

    def test_schedule_state_accepts_new_table_and_legacy_scalar_mutations(self):
        new_table = _sqlite_call(
            "UPDATE __agent_schedules SET schedule = '0 9 * * *' WHERE schedule_key = 'digest'"
        )
        legacy = _sqlite_call(
            "UPDATE __agent_config SET schedule = '0 9 * * *' WHERE id = 1"
        )
        unrelated = _sqlite_call("SELECT * FROM __agent_schedules")

        self.assertTrue(sqlite_batch_mutates_schedule_state(new_table))
        self.assertTrue(sqlite_batch_mutates_schedule_state(legacy))
        self.assertFalse(sqlite_batch_mutates_schedule_state(unrelated))

    def test_schedule_table_reads_and_mutations_are_planning_state(self):
        schedule_read = _sqlite_call(
            "SELECT schedule_key, schedule FROM __agent_schedules ORDER BY schedule_key"
        )
        schedule_write = _sqlite_call(
            "DELETE FROM __agent_schedules WHERE schedule_key = 'digest'"
        )
        mixed_write = _sqlite_call(
            "DELETE FROM __agent_schedules WHERE schedule_key = 'digest'; "
            "CREATE TABLE leads (email TEXT)"
        )

        self.assertTrue(sqlite_batch_is_only_planning_state_read(schedule_read))
        self.assertTrue(sqlite_batch_is_only_planning_state_mutation(schedule_write))
        self.assertFalse(sqlite_batch_is_only_planning_state_mutation(mixed_write))

    def test_targeted_strategy_requires_read_and_where_clause(self):
        case = next(case for case in AGENT_SCHEDULING_CASES if case.expected_action == "update")
        good = _sqlite_call(
            "SELECT * FROM __agent_schedules WHERE name = 'Weekly pipeline review'; "
            "UPDATE __agent_schedules SET schedule = '15 10 * * 2' "
            "WHERE schedule_key = 'weekly-pipeline'"
        )
        untargeted = _sqlite_call(
            "SELECT * FROM __agent_schedules; UPDATE __agent_schedules SET schedule = '15 10 * * 2'"
        )

        self.assertEqual(_schedule_sql_strategy_failures(case, [good]), [])
        self.assertIn(
            "named schedule change was not a targeted mutation",
            _schedule_sql_strategy_failures(case, [untargeted]),
        )

    def test_timer_scorer_requires_default_checkin_preservation_and_own_purpose(self):
        now = timezone.now().replace(microsecond=0)
        onboarding = _schedule_row(
            "onboarding_checkin",
            name="Onboarding check-in",
            instruction="Check in after the first day.",
            kind=PersistentAgentSchedule.Kind.ONCE,
            expression=None,
            run_at=now + timedelta(hours=24),
        )
        morning = _schedule_row(
            "morning-digest",
            name="Morning digest",
            instruction="Prepare the daily operating digest.",
        )
        timer = _schedule_row(
            "maya-renewal",
            name="Maya renewal call",
            instruction="Remind me to call Maya about the renewal.",
            kind=PersistentAgentSchedule.Kind.ONCE,
            expression=None,
            run_at=now + timedelta(minutes=45),
        )
        before = _schedule_snapshot([onboarding, morning])
        scenario = ScenarioRegistry.get(RELATIVE_TIMER_PRESERVES_RECURRING)

        failures = scenario._state_failures(
            [onboarding, morning, timer],
            before=before,
            inbound=SimpleNamespace(timestamp=now),
            messages=[],
            exact_target=None,
        )
        missing_default = scenario._state_failures(
            [morning, timer],
            before=_schedule_snapshot([morning]),
            inbound=SimpleNamespace(timestamp=now),
            messages=[],
            exact_target=None,
        )

        self.assertEqual(failures, [])
        self.assertIn("new agent had no default onboarding check-in", missing_default)

    def test_cron_scorer_accepts_numeric_and_named_weekdays(self):
        self.assertTrue(_cron_matches("5 8 * * 1-5", minute=5, hour=8, weekdays="weekdays"))
        self.assertTrue(_cron_matches("30 16 * * fri", minute=30, hour=16, weekdays="friday"))
        self.assertFalse(_cron_matches("30 16 * * 1", minute=30, hour=16, weekdays="friday"))
