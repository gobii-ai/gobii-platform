from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.compaction_exceptions import CompactionSummaryError
from api.evals.compaction_quality import (
    COMMS_COMPACTION_CASES,
    COMPACTION_QUALITY_CASES,
    COMPACTION_QUALITY_SCENARIO_SLUGS,
    COMPACTION_QUALITY_SUITE_SLUG,
    STEP_COMPACTION_CASES,
    check_compaction_summary,
)
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.compaction_quality import CompactionQualityScenario
from api.evals.suites import SuiteRegistry
from api.management.commands.run_evals import validate_compaction_quality_profiles
from api.models import EvalRunTask


def _profile(name, summary_endpoint_id, judge_endpoint_id):
    return SimpleNamespace(
        name=name,
        summarization_endpoint_id=summary_endpoint_id,
        eval_judge_endpoint_id=judge_endpoint_id,
        summarization_endpoint=SimpleNamespace(litellm_model=f"candidate/{name}"),
        eval_judge_endpoint=SimpleNamespace(litellm_model="judge/fixed"),
    )


def _selected_compaction_suite():
    return [
        (
            COMPACTION_QUALITY_SUITE_SLUG,
            list(COMPACTION_QUALITY_SCENARIO_SLUGS),
            "Compaction quality",
        )
    ]


@tag("batch_compaction")
class CompactionQualityFixtureTests(SimpleTestCase):
    def test_suite_registers_twelve_balanced_cases(self):
        suite = SuiteRegistry.get(COMPACTION_QUALITY_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(len(COMPACTION_QUALITY_CASES), 12)
        self.assertEqual(len(COMMS_COMPACTION_CASES), 6)
        self.assertEqual(len(STEP_COMPACTION_CASES), 6)
        self.assertEqual(tuple(suite.scenario_slugs), COMPACTION_QUALITY_SCENARIO_SLUGS)
        self.assertEqual(len(set(COMPACTION_QUALITY_SCENARIO_SLUGS)), 12)
        self.assertTrue(
            set(COMPACTION_QUALITY_SCENARIO_SLUGS).isdisjoint(
                SuiteRegistry.get("all").scenario_slugs
            )
        )
        self.assertTrue(
            set(COMPACTION_QUALITY_SCENARIO_SLUGS).isdisjoint(
                SuiteRegistry.get("core").scenario_slugs
            )
        )

    def test_registered_scenarios_have_compaction_metadata_and_one_scored_task(self):
        for slug in COMPACTION_QUALITY_SCENARIO_SLUGS:
            scenario = ScenarioRegistry.get(slug)
            metadata = scenario.get_metadata()

            self.assertEqual(metadata.category, "compaction_quality")
            self.assertEqual(metadata.area, "agent_memory")
            self.assertIn("compaction_quality", metadata.tags)
            self.assertFalse(scenario.include_in_default_suites)
            self.assertEqual(len(scenario.tasks), 1)
            self.assertEqual(scenario.tasks[0].assertion_type, "llm_judge")

    def test_valid_summary_passes_deterministic_checks_for_every_case(self):
        for case in COMPACTION_QUALITY_CASES:
            summary = "Current state: " + "; ".join(
                case.required_exact + case.required_normalized
            )
            result = check_compaction_summary(case, summary)
            self.assertTrue(result.passed, (case.slug, result.failures))

    def test_normalized_facts_allow_harmless_case_spacing_and_wording(self):
        multi_channel = COMMS_COMPACTION_CASES[0]
        corrections = COMMS_COMPACTION_CASES[1]
        pause_resume = COMMS_COMPACTION_CASES[2]
        handoff = COMMS_COMPACTION_CASES[3]
        campaign = COMMS_COMPACTION_CASES[4]
        multilingual = COMMS_COMPACTION_CASES[5]
        plan = STEP_COMPACTION_CASES[3]
        sqlite = STEP_COMPACTION_CASES[4]

        summaries = (
            (
                multi_channel,
                "; ".join(multi_channel.required_exact) + "; EMAIL; Discord",
            ),
            (
                corrections,
                "; ".join(corrections.required_exact)
                + "; Casey\u202fWu; Rowan\u202fBell",
            ),
            (
                pause_resume,
                "Portfolio‑import; billing‑reconciliation; research",
            ),
            (
                handoff,
                "; ".join(handoff.required_exact)
                + "; work‑email; personal‑email",
            ),
            (
                campaign,
                "; ".join(campaign.required_exact) + "; Conflict",
            ),
            (
                multilingual,
                "; ".join(multilingual.required_exact)
                + "; 00:00, 09:00, 16:00, 18:00\u202fUTC",
            ),
            (
                plan,
                "; ".join(plan.required_exact)
                + "; 2 done, 1 doing, 4 todo",
            ),
            (
                sqlite,
                "; ".join(sqlite.required_exact)
                + "; 4 stale records; owner column present",
            ),
        )

        for case, summary in summaries:
            with self.subTest(case=case.slug):
                self.assertTrue(check_compaction_summary(case, summary).passed)

    def test_opaque_values_remain_strictly_verbatim(self):
        case = STEP_COMPACTION_CASES[1]
        summary = "; ".join(case.required_exact).replace("MAP-9", "MAP‑9")

        result = check_compaction_summary(case, summary)

        self.assertIn("Missing required exact value: MAP-9", result.failures)

    def test_cron_fixture_uses_five_minute_cycles_without_prescribing_log_format(self):
        case = STEP_COMPACTION_CASES[0]
        cron_events = case.batches[0][:-1]

        self.assertEqual(
            tuple(event.scheduled_for for event in cron_events),
            tuple(
                f"2026-09-20T10:{index * 5:02d}:00+00:00"
                for index in range(12)
            ),
        )
        self.assertNotIn("@every 5m", case.required_exact)
        self.assertFalse(case.forbidden_exact)

    def test_checks_report_missing_forbidden_length_and_code_fence(self):
        case = COMMS_COMPACTION_CASES[1]
        summary = f"```\n{case.forbidden_exact[0]}\n```" + ("x" * case.max_chars)

        result = check_compaction_summary(case, summary)

        self.assertFalse(result.passed)
        self.assertTrue(any("Missing required" in failure for failure in result.failures))
        self.assertTrue(any("Retained forbidden" in failure for failure in result.failures))
        self.assertTrue(any("limit" in failure for failure in result.failures))
        self.assertIn("Summary contains a code fence.", result.failures)

    def test_credential_case_requires_the_exact_synthetic_value(self):
        case = next(case for case in STEP_COMPACTION_CASES if case.slug.endswith("credential_scope"))
        credential = "gobii_eval_key_DO_NOT_USE_7H3K9P2M"

        self.assertIn(credential, case.required_exact)
        result = check_compaction_summary(
            case,
            " ".join(value for value in case.required_exact if value != credential),
        )
        self.assertIn(f"Missing required exact value: {credential}", result.failures)

    def test_credential_case_requires_explicit_prohibition_facts(self):
        case = next(case for case in STEP_COMPACTION_CASES if case.slug.endswith("credential_scope"))
        summary = "; ".join(case.required_exact) + "; candidate-sandbox read-only use"

        result = check_compaction_summary(case, summary)

        self.assertIn(
            "Missing required normalized fact: external validation",
            result.failures,
        )
        self.assertIn("read-only wording alone is insufficient", case.semantic_requirements[-1])

    def test_credential_case_accepts_explicit_no_production_use_wording(self):
        case = next(case for case in STEP_COMPACTION_CASES if case.slug.endswith("credential_scope"))
        summary = (
            "; ".join(case.required_exact)
            + "; candidate-sandbox reads only; no external validation or prod use"
        )

        self.assertTrue(check_compaction_summary(case, summary).passed)


@tag("batch_compaction")
class CompactionQualityGenerationTests(SimpleTestCase):
    def test_incremental_comms_batches_forward_profile_and_eval_run(self):
        case = next(case for case in COMMS_COMPACTION_CASES if len(case.batches) == 2)
        scenario = CompactionQualityScenario()
        agent = SimpleNamespace(id="agent-eval")
        profile = SimpleNamespace(name="candidate")

        with patch(
            "api.evals.scenarios.compaction_quality.llm_summarise_comms",
            side_effect=("summary after batch one", "summary after batch two"),
        ) as summarise:
            result = scenario.generate_summary(
                case,
                agent=agent,
                routing_profile=profile,
                eval_run_id="run-eval",
            )

        self.assertEqual(result, "summary after batch two")
        self.assertEqual(summarise.call_count, 2)
        self.assertEqual(summarise.call_args_list[0].args[0], case.previous_summary)
        self.assertEqual(summarise.call_args_list[1].args[0], "summary after batch one")
        for summary_call in summarise.call_args_list:
            self.assertIs(summary_call.kwargs["agent"], agent)
            self.assertIs(summary_call.kwargs["routing_profile"], profile)
            self.assertEqual(summary_call.kwargs["eval_run_id"], "run-eval")

    def test_incremental_step_batches_forward_profile_and_eval_run(self):
        case = next(case for case in STEP_COMPACTION_CASES if len(case.batches) == 2)
        scenario = CompactionQualityScenario()
        agent = SimpleNamespace(id="agent-eval")
        profile = SimpleNamespace(name="candidate")

        with patch(
            "api.evals.scenarios.compaction_quality.llm_summarise_steps",
            side_effect=("step summary one", "step summary two"),
        ) as summarise:
            result = scenario.generate_summary(
                case,
                agent=agent,
                routing_profile=profile,
                eval_run_id="run-eval",
            )

        self.assertEqual(result, "step summary two")
        self.assertEqual(summarise.call_args_list[1].args[0], "step summary one")
        for summary_call in summarise.call_args_list:
            self.assertIs(summary_call.kwargs["routing_profile"], profile)
            self.assertEqual(summary_call.kwargs["eval_run_id"], "run-eval")

    def test_run_records_passing_summary_and_diagnostics(self):
        case = COMMS_COMPACTION_CASES[0]
        summary = "Current state: " + "; ".join(
            case.required_exact + case.required_normalized
        )
        scenario = CompactionQualityScenario()
        scenario.case = case
        profile = _profile("alpha", "summary-alpha", "judge-fixed")
        final_task = Mock()
        scenario.record_task_result = Mock(side_effect=(Mock(), final_task))
        scenario.generate_summary = Mock(return_value=summary)
        scenario.llm_judge = Mock(return_value=("Pass", "All semantic requirements are satisfied."))

        with patch(
            "api.evals.scenarios.compaction_quality.PersistentAgent.objects.get",
            return_value=SimpleNamespace(id="agent-eval"),
        ), patch(
            "api.evals.scenarios.compaction_quality.get_eval_routing_profile_for_current_run",
            return_value=profile,
        ):
            scenario.run("run-eval", "agent-eval")

        final_call = scenario.record_task_result.call_args_list[-1]
        self.assertEqual(final_call.args[2], EvalRunTask.Status.PASSED)
        self.assertEqual(final_call.kwargs["observed_summary"], summary)
        self.assertTrue(final_call.kwargs["artifacts"]["hard_checks_passed"])
        self.assertEqual(final_call.kwargs["artifacts"]["candidate_model"], "candidate/alpha")
        self.assertEqual(final_call.kwargs["artifacts"]["judge_model"], "judge/fixed")
        self.assertEqual(final_task.llm_model, "judge/fixed")
        final_task.save.assert_called_once()

    def test_run_fails_when_hard_checks_fail_even_if_judge_passes(self):
        scenario = CompactionQualityScenario()
        scenario.case = COMMS_COMPACTION_CASES[0]
        scenario.record_task_result = Mock(side_effect=(Mock(), Mock()))
        scenario.generate_summary = Mock(return_value="A concise but incomplete summary.")
        scenario.llm_judge = Mock(return_value=("Pass", "Semantically concise."))

        with patch(
            "api.evals.scenarios.compaction_quality.PersistentAgent.objects.get",
            return_value=SimpleNamespace(id="agent-eval"),
        ), patch(
            "api.evals.scenarios.compaction_quality.get_eval_routing_profile_for_current_run",
            return_value=_profile("alpha", "summary-alpha", "judge-fixed"),
        ):
            scenario.run("run-eval", "agent-eval")

        final_call = scenario.record_task_result.call_args_list[-1]
        self.assertEqual(final_call.args[2], EvalRunTask.Status.FAILED)
        self.assertFalse(final_call.kwargs["artifacts"]["hard_checks_passed"])

    def test_run_uses_judge_failure_and_error_statuses(self):
        case = COMMS_COMPACTION_CASES[0]
        summary = "Current state: " + "; ".join(
            case.required_exact + case.required_normalized
        )

        for judge_choice, expected_status in (
            ("Fail", EvalRunTask.Status.FAILED),
            ("Error", EvalRunTask.Status.ERRORED),
        ):
            with self.subTest(judge_choice=judge_choice):
                scenario = CompactionQualityScenario()
                scenario.case = case
                scenario.record_task_result = Mock(side_effect=(Mock(), Mock()))
                scenario.generate_summary = Mock(return_value=summary)
                scenario.llm_judge = Mock(
                    return_value=(judge_choice, "Synthetic judge result.")
                )

                with patch(
                    "api.evals.scenarios.compaction_quality.PersistentAgent.objects.get",
                    return_value=SimpleNamespace(id="agent-eval"),
                ), patch(
                    "api.evals.scenarios.compaction_quality.get_eval_routing_profile_for_current_run",
                    return_value=_profile("alpha", "summary-alpha", "judge-fixed"),
                ):
                    scenario.run("run-eval", "agent-eval")

                final_call = scenario.record_task_result.call_args_list[-1]
                self.assertEqual(final_call.args[2], expected_status)
                self.assertEqual(final_call.kwargs["artifacts"]["judge_choice"], judge_choice)

    def test_run_records_provider_failure_as_errored(self):
        scenario = CompactionQualityScenario()
        scenario.case = STEP_COMPACTION_CASES[0]
        scenario.record_task_result = Mock(side_effect=(Mock(), Mock()))
        scenario.generate_summary = Mock(
            side_effect=CompactionSummaryError("synthetic provider outage")
        )

        with patch(
            "api.evals.scenarios.compaction_quality.PersistentAgent.objects.get",
            return_value=SimpleNamespace(id="agent-eval"),
        ), patch(
            "api.evals.scenarios.compaction_quality.get_eval_routing_profile_for_current_run",
            return_value=_profile("alpha", "summary-alpha", "judge-fixed"),
        ):
            scenario.run("run-eval", "agent-eval")

        final_call = scenario.record_task_result.call_args_list[-1]
        self.assertEqual(final_call.args[2], EvalRunTask.Status.ERRORED)
        self.assertEqual(final_call.kwargs["artifacts"]["error_type"], "CompactionSummaryError")
        self.assertIn("provider outage", final_call.kwargs["artifacts"]["error"])


@tag("batch_compaction")
class CompactionQualityProfileValidationTests(SimpleTestCase):
    def test_accepts_candidate_profiles_with_one_independent_judge(self):
        validate_compaction_quality_profiles(
            _selected_compaction_suite(),
            (
                _profile("alpha", "summary-alpha", "judge-fixed"),
                _profile("beta", "summary-beta", "judge-fixed"),
            ),
        )

    def test_rejects_missing_explicit_profile(self):
        with self.assertRaisesRegex(CommandError, "explicit --routing-profile"):
            validate_compaction_quality_profiles(_selected_compaction_suite(), ())

    def test_rejects_missing_summary_or_judge_endpoint(self):
        with self.assertRaisesRegex(CommandError, "no summarization endpoint"):
            validate_compaction_quality_profiles(
                _selected_compaction_suite(),
                (_profile("alpha", None, "judge-fixed"),),
            )
        with self.assertRaisesRegex(CommandError, "no eval-judge endpoint"):
            validate_compaction_quality_profiles(
                _selected_compaction_suite(),
                (_profile("alpha", "summary-alpha", None),),
            )

    def test_rejects_candidate_equal_to_judge(self):
        with self.assertRaisesRegex(CommandError, "same endpoint"):
            validate_compaction_quality_profiles(
                _selected_compaction_suite(),
                (_profile("alpha", "shared", "shared"),),
            )

    def test_rejects_mismatched_judges(self):
        with self.assertRaisesRegex(CommandError, "same fixed eval-judge endpoint"):
            validate_compaction_quality_profiles(
                _selected_compaction_suite(),
                (
                    _profile("alpha", "summary-alpha", "judge-one"),
                    _profile("beta", "summary-beta", "judge-two"),
                ),
            )

    def test_non_compaction_suites_do_not_require_profiles(self):
        validate_compaction_quality_profiles(
            [("smoke", ["echo_response"], "Smoke")],
            (),
        )
