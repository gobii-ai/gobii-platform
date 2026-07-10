from types import SimpleNamespace
from unittest.mock import patch
import importlib

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, tag

import api.evals.loader  # noqa: F401
from api.evals.base import ScenarioTask
from api.evals.loader import CORE_SCENARIO_SLUGS, CORE_SQLITE_TOOL_RESULT_SCENARIO_SLUGS
from api.evals.meta_gobii import META_GOBII_EVAL_CASES
from api.evals.registry import ScenarioRegistry
from api.evals.runner import EvalRunner
from api.evals.scenarios.behavior_micro import (
    CHARTER_MEMORY_MICRO_SCENARIO_SLUGS,
    COMMON_USE_CASE_EVAL_CASES,
    SCHEDULE_INTENT_MICRO_SCENARIO_SLUGS,
)
from api.evals.scoring import evaluate_scenario_tasks, summarize_runs
from api.evals.suites import SuiteRegistry
from api.management.commands.run_evals import Command, enforce_required_pass
from api.models import BrowserUseAgent, EvalRun, EvalRunTask, EvalSuiteRun, PersistentAgent
from console.evals.api_views import _serialize_suite_run, _task_counts


def _task(status, *, is_scored=True, is_setup=False):
    return SimpleNamespace(
        status=status,
        is_scored=is_scored,
        is_setup=is_setup,
    )


def _run(status, tasks):
    return SimpleNamespace(
        status=status,
        tasks=SimpleNamespace(all=lambda: tasks),
    )


@tag("batch_eval_fingerprint")
class EvalSuiteValidityTests(SimpleTestCase):
    def test_core_is_curated_and_keeps_charter_memory(self):
        core = SuiteRegistry.get("core")
        all_suite = SuiteRegistry.get("all")

        self.assertEqual(core.scenario_slugs, list(CORE_SCENARIO_SLUGS))
        self.assertLessEqual(len(core.scenario_slugs), len(all_suite.scenario_slugs) * 0.4)
        self.assertTrue(set(CHARTER_MEMORY_MICRO_SCENARIO_SLUGS).issubset(core.scenario_slugs))
        self.assertTrue(set(SCHEDULE_INTENT_MICRO_SCENARIO_SLUGS).issubset(core.scenario_slugs))

    def test_contract_suites_are_not_implicitly_behavioral_core(self):
        core_slugs = set(CORE_SCENARIO_SLUGS)

        for suite_slug in (
            "custom_tool_result_contract",
            "daily_credit_prompt",
        ):
            suite = SuiteRegistry.get(suite_slug)
            self.assertTrue(set(suite.scenario_slugs).isdisjoint(core_slugs))
            for scenario_slug in suite.scenario_slugs:
                self.assertEqual(ScenarioRegistry.get(scenario_slug).get_metadata().tier, "contract")

        sqlite_suite_slugs = set(SuiteRegistry.get("sqlite_tool_results").scenario_slugs)
        self.assertTrue(set(CORE_SQLITE_TOOL_RESULT_SCENARIO_SLUGS).issubset(core_slugs))
        self.assertTrue(set(CORE_SQLITE_TOOL_RESULT_SCENARIO_SLUGS).issubset(sqlite_suite_slugs))
        self.assertEqual(set(CORE_SQLITE_TOOL_RESULT_SCENARIO_SLUGS), sqlite_suite_slugs)

        for case in META_GOBII_EVAL_CASES:
            self.assertNotIn(case.scenario_slug, core_slugs)
            self.assertEqual(ScenarioRegistry.get(case.scenario_slug).get_metadata().tier, "contract")

    def test_common_cases_do_not_reward_legacy_native_integration_tools(self):
        self.assertFalse(any(case.category == "sheets" for case in COMMON_USE_CASE_EVAL_CASES))
        for case in COMMON_USE_CASE_EVAL_CASES:
            accepted_alternatives = {
                alternative
                for alternatives in case.accepted_tool_alternatives.values()
                for alternative in alternatives
            }
            self.assertFalse(any(name.startswith("google_sheets-") for name in accepted_alternatives))
            self.assertFalse(any(name.startswith("apollo_io-") for name in accepted_alternatives))
            self.assertFalse(any(name.startswith("google_sheets-") for name in case.expected_tools))
            self.assertFalse(any(name.startswith("apollo_io-") for name in case.expected_tools))

    def test_setup_and_diagnostic_task_constructors_are_unscored(self):
        setup = ScenarioTask.setup(name="inject_prompt")
        diagnostic = ScenarioTask.diagnostic(name="record_optional_plan")

        self.assertTrue(setup.is_setup)
        self.assertFalse(setup.is_scored)
        self.assertFalse(diagnostic.is_setup)
        self.assertFalse(diagnostic.is_scored)
        with self.assertRaises(ValueError):
            ScenarioTask(name="bad_setup", is_setup=True)

    def test_registered_setup_tasks_are_not_scored(self):
        setup_names = {"send_message", "instruct_agent", "trigger_scheduled_run", "dismiss_plain_request"}
        for scenario in ScenarioRegistry.list_all().values():
            for task in scenario.tasks:
                if task.name.startswith(("inject_", "seed_")) or task.name in setup_names:
                    with self.subTest(scenario=scenario.slug, task=task.name):
                        self.assertTrue(task.is_setup)
                        self.assertFalse(task.is_scored)

    def test_scenario_outcome_ignores_setup_but_requires_every_scored_assertion(self):
        tasks = [
            _task(EvalRunTask.Status.PASSED, is_scored=False, is_setup=True),
            _task(EvalRunTask.Status.PASSED),
            _task(EvalRunTask.Status.FAILED),
            _task(EvalRunTask.Status.PASSED, is_scored=False),
        ]

        outcome = evaluate_scenario_tasks(tasks, run_status=EvalRun.Status.COMPLETED)
        counts = _task_counts(tasks)

        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.as_dict()["scoring_schema_version"], 2)
        self.assertEqual(outcome.scored_requirements, 2)
        self.assertEqual(counts["scored_total"], 2)
        self.assertEqual(counts["setup_total"], 1)
        self.assertEqual(counts["diagnostic_total"], 1)
        self.assertEqual(counts["pass_rate"], 0.5)

    def test_macro_pass_rate_does_not_let_large_scenario_outvote_failed_scenario(self):
        runs = [
            _run(
                EvalRun.Status.COMPLETED,
                [_task(EvalRunTask.Status.PASSED) for _ in range(8)],
            ),
            _run(
                EvalRun.Status.COMPLETED,
                [_task(EvalRunTask.Status.FAILED)],
            ),
        ]

        totals = summarize_runs(runs)

        self.assertEqual(totals["passed"], 1)
        self.assertEqual(totals["failed"], 1)
        self.assertEqual(totals["pass_rate"], 0.5)

    def test_require_pass_is_opt_in_enforcement(self):
        enforce_required_pass({"total": 1, "failed": 0, "pending": 0, "unscored": 0})
        with self.assertRaises(CommandError):
            enforce_required_pass({"total": 1, "failed": 1, "pending": 0, "unscored": 0})
        with self.assertRaises(CommandError):
            enforce_required_pass({"total": 0, "failed": 0, "pending": 0, "unscored": 0})

        parser = Command().create_parser("manage.py", "run_evals")
        options = vars(parser.parse_args(["--list", "--require-pass"]))
        self.assertTrue(options["require_pass"])


@tag("batch_eval_fingerprint")
class EvalRunnerScoringRoleTests(TestCase):
    def test_runner_persists_setup_diagnostic_and_scored_roles(self):
        class ScoringRoleScenario:
            slug = "test_scoring_roles"
            version = "1"
            tasks = [
                ScenarioTask.setup(name="inject_prompt"),
                ScenarioTask.diagnostic(name="optional_plan"),
                ScenarioTask(name="verify_outcome"),
            ]

            def run(self, run_id, agent_id):
                EvalRunTask.objects.filter(run_id=run_id).update(
                    status=EvalRunTask.Status.PASSED,
                )

        previous = ScenarioRegistry.get(ScoringRoleScenario.slug)
        ScenarioRegistry.register(ScoringRoleScenario())
        self.addCleanup(
            lambda: (
                ScenarioRegistry._scenarios.__setitem__(ScoringRoleScenario.slug, previous)
                if previous is not None
                else ScenarioRegistry._scenarios.pop(ScoringRoleScenario.slug, None)
            )
        )
        user = get_user_model().objects.create_user(
            username="eval-scoring-roles@example.test",
            email="eval-scoring-roles@example.test",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Eval scoring browser")
        agent = PersistentAgent.objects.create(
            user=user,
            browser_use_agent=browser_agent,
            name="Eval scoring agent",
            charter="Test scoring roles.",
        )
        run = EvalRun.objects.create(
            scenario_slug=ScoringRoleScenario.slug,
            scenario_version="1",
            agent=agent,
            initiated_by=user,
        )

        with (
            patch("api.evals.runner.broadcast_run_update"),
            patch("api.evals.runner.broadcast_task_update"),
            patch("api.evals.runner.aggregate_run_metrics"),
        ):
            EvalRunner(str(run.id)).execute()

        task_by_name = {task.name: task for task in run.tasks.all()}
        self.assertTrue(task_by_name["inject_prompt"].is_setup)
        self.assertFalse(task_by_name["inject_prompt"].is_scored)
        self.assertFalse(task_by_name["optional_plan"].is_setup)
        self.assertFalse(task_by_name["optional_plan"].is_scored)
        self.assertTrue(task_by_name["verify_outcome"].is_scored)

    def test_suite_serialization_prefetches_tasks_once_for_all_runs(self):
        user = get_user_model().objects.create_user(
            username="eval-serialization@example.test",
            email="eval-serialization@example.test",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Eval serialization browser")
        agent = PersistentAgent.objects.create(
            user=user,
            browser_use_agent=browser_agent,
            name="Eval serialization agent",
            charter="Test serialization.",
        )
        suite = EvalSuiteRun.objects.create(suite_slug="serialization", initiated_by=user)
        for index in range(4):
            run = EvalRun.objects.create(
                suite_run=suite,
                scenario_slug=f"serialization_{index}",
                agent=agent,
                initiated_by=user,
                status=EvalRun.Status.COMPLETED,
            )
            EvalRunTask.objects.create(
                run=run,
                sequence=1,
                name="verify",
                assertion_type="manual",
                status=EvalRunTask.Status.PASSED,
            )

        with self.assertNumQueries(2):
            payload = _serialize_suite_run(suite, include_runs=True, include_tasks=True)

        self.assertEqual(payload["scenario_totals"]["passed"], 4)

    def test_scoring_migration_backfills_historical_setup_and_diagnostic_names(self):
        user = get_user_model().objects.create_user(username="eval-scoring-backfill@example.test")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Eval backfill browser")
        agent = PersistentAgent.objects.create(
            user=user,
            browser_use_agent=browser_agent,
            name="Eval backfill agent",
            charter="Test backfill.",
        )
        run = EvalRun.objects.create(scenario_slug="historical", agent=agent, initiated_by=user)
        for sequence, name in enumerate(("inject_prompt", "verify_plan_policy", "verify_outcome"), start=1):
            EvalRunTask.objects.create(
                run=run,
                sequence=sequence,
                name=name,
                assertion_type="manual",
            )
        migration = importlib.import_module("api.migrations.0419_evalruntask_scoring_roles")
        historical_apps = SimpleNamespace(get_model=lambda app_label, model_name: EvalRunTask)

        migration.backfill_task_scoring_roles(historical_apps, None)

        task_by_name = {task.name: task for task in run.tasks.all()}
        self.assertTrue(task_by_name["inject_prompt"].is_setup)
        self.assertFalse(task_by_name["inject_prompt"].is_scored)
        self.assertFalse(task_by_name["verify_plan_policy"].is_setup)
        self.assertFalse(task_by_name["verify_plan_policy"].is_scored)
        self.assertTrue(task_by_name["verify_outcome"].is_scored)
