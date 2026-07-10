from types import SimpleNamespace
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.event_processing import _get_completed_process_run_count
from api.agent.core.prompt_context import _get_sqlite_examples, _get_system_instruction, build_prompt_context_preview
from api.agent.core.tool_results import _wrap_as_sqlite_result
from api.agent.tools.create_chart import get_create_chart_tool
from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_DEFINITIONS
from api.agent.tools.plan import get_update_plan_tool
from api.agent.tools.request_contact_permission import get_request_contact_permission_tool
from api.agent.tools.request_human_input import execute_request_human_input, get_request_human_input_tool
from api.agent.tools.web_chat_sender import execute_send_chat_message
from api.evals.scenarios.effort_calibration import (
    ARTIFACT_TOOL_NAMES,
    EFFORT_CALIBRATION_SCENARIO_SLUGS,
    EFFORT_EXPLICIT_DEEP_RESEARCH_REMAINS_CAPABLE,
    EFFORT_OVERWORK_TOOL_NAMES,
    EFFORT_PARTIAL_SOURCE_BLOCK_REPORTS_AND_RESUMES,
    RESEARCH_TOOL_NAMES,
    EFFORT_SIMPLE_CURRENT_COMPANY_REPORT,
    EFFORT_SIMPLE_CURRENT_YC_BATCH_REPORT,
    EFFORT_TOOL_WAIT_NEXT_SCHEDULE_REQUIRES_SCHEDULE,
    EFFORT_UNSCHEDULED_REMAINING_WORK_SETS_RESUME,
    PARTIAL_SOURCE_FORBIDDEN_TOOL_NAMES,
    EffortCalibrationScenario,
    EffortDefaultableResearchNoQuestionBatteryScenario,
    EffortExplicitDeepResearchRemainsCapableScenario,
    EffortTrivialAnswerStopsScenario,
    _find_near_duplicate_texts,
    _hierarchical_report_shape,
    _question_count,
    _sqlite_call_persists_resume_state,
    _sqlite_result_text_reads,
    _web_query_value,
)
from api.evals.scenarios.monitor_pollution import BACKGROUND_DRAIN_TIMEOUT_SECONDS
from api.evals.scenarios.sqlite_tool_results import (
    INVENTORY_URLS,
    LISTING_URLS,
    SOURCE_URLS,
    SQLITE_ITEM_LINK_REPORT,
    SQLITE_TOOL_RESULT_SCENARIO_SLUGS,
    SQLITE_TOOL_RESULT_SUITE_SLUG,
    SqliteDedupeRequeryScenario,
    SqliteIntermediateWorkingTableScenario,
    SqliteItemLinkReportScenario,
    SqliteMultiResultWebSynthesisScenario,
    SqliteToolResultScenario,
)
from api.evals.stop_policy import should_stop_for_eval_policy
from api.evals.suites import SuiteRegistry
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    EvalRun,
    EvalRunTask,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentToolCall,
)


@tag("eval_sim")
class EffortCalibrationSuiteTests(SimpleTestCase):
    def test_effort_calibration_suite_contains_expected_scenarios(self):
        suite = SuiteRegistry.get("effort_calibration")

        self.assertIsNotNone(suite)
        self.assertEqual(suite.scenario_slugs, EFFORT_CALIBRATION_SCENARIO_SLUGS)
        self.assertIn(EFFORT_SIMPLE_CURRENT_YC_BATCH_REPORT, suite.scenario_slugs)
        self.assertIn(EFFORT_SIMPLE_CURRENT_COMPANY_REPORT, suite.scenario_slugs)
        self.assertIn(EFFORT_EXPLICIT_DEEP_RESEARCH_REMAINS_CAPABLE, suite.scenario_slugs)
        self.assertIn(EFFORT_UNSCHEDULED_REMAINING_WORK_SETS_RESUME, suite.scenario_slugs)
        self.assertIn(EFFORT_PARTIAL_SOURCE_BLOCK_REPORTS_AND_RESUMES, suite.scenario_slugs)
        self.assertIn(EFFORT_TOOL_WAIT_NEXT_SCHEDULE_REQUIRES_SCHEDULE, suite.scenario_slugs)

    def test_sqlite_tool_results_suite_contains_item_link_report(self):
        suite = SuiteRegistry.get(SQLITE_TOOL_RESULT_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(suite.scenario_slugs, SQLITE_TOOL_RESULT_SCENARIO_SLUGS)
        self.assertIn(SQLITE_ITEM_LINK_REPORT, suite.scenario_slugs)

    def test_resume_state_requires_one_real_mutation_to_persist_count_and_cursor(self):
        valid = SimpleNamespace(
            tool_name="sqlite_batch",
            status="complete",
            tool_params={
                "sql": (
                    "CREATE TABLE resume_state (remaining_count INTEGER, next_cursor TEXT); "
                    "INSERT INTO resume_state (remaining_count, next_cursor) VALUES (12, 'offset-3');"
                )
            },
        )
        unrelated_mutation = SimpleNamespace(
            tool_name="sqlite_batch",
            status="complete",
            tool_params={
                "sql": (
                    "CREATE TABLE scratch (value TEXT); INSERT INTO scratch(value) VALUES ('done'); "
                    "SELECT remaining_work, next_cursor FROM some_state;"
                )
            },
        )
        comment_only_markers = SimpleNamespace(
            tool_name="sqlite_batch",
            status="complete",
            tool_params={
                "sql": (
                    "INSERT INTO scratch(value) VALUES ('done') "
                    "/* remaining_work and next_cursor are handled elsewhere */;"
                )
            },
        )
        create_table_as_select = SimpleNamespace(
            tool_name="sqlite_batch",
            status="complete",
            tool_params={
                "sql": (
                    "CREATE TABLE resume_state AS SELECT 12 AS remaining_count, "
                    "'offset-3' AS next_cursor;"
                )
            },
        )
        schema_only_create = SimpleNamespace(
            tool_name="sqlite_batch",
            status="complete",
            tool_params={
                "sql": "CREATE TABLE resume_state(remaining_count INTEGER, next_cursor TEXT);"
            },
        )

        self.assertTrue(_sqlite_call_persists_resume_state(valid))
        self.assertTrue(_sqlite_call_persists_resume_state(create_table_as_select))
        self.assertFalse(_sqlite_call_persists_resume_state(unrelated_mutation))
        self.assertFalse(_sqlite_call_persists_resume_state(comment_only_markers))
        self.assertFalse(_sqlite_call_persists_resume_state(schema_only_create))

    def test_overwork_check_allows_only_one_intermediate_plan_update(self):
        scenario, recorded = EffortCalibrationScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        plan_call = SimpleNamespace(tool_name="update_plan", step=SimpleNamespace(id="plan-1"))

        with patch(
            "api.evals.scenarios.effort_calibration._relevant_tool_calls_for_run",
            return_value=[plan_call],
        ):
            self.assertTrue(
                scenario._record_no_overwork_tools(
                    "run",
                    after=None,
                    task_name="verify_no_overwork_tools",
                    forbidden_tool_names=PARTIAL_SOURCE_FORBIDDEN_TOOL_NAMES,
                    max_plan_updates=1,
                )
            )

        second_plan = SimpleNamespace(tool_name="update_plan", step=SimpleNamespace(id="plan-2"))
        with patch(
            "api.evals.scenarios.effort_calibration._relevant_tool_calls_for_run",
            return_value=[plan_call, second_plan],
        ):
            self.assertFalse(
                scenario._record_no_overwork_tools(
                    "run",
                    after=None,
                    task_name="verify_no_overwork_tools",
                    forbidden_tool_names=PARTIAL_SOURCE_FORBIDDEN_TOOL_NAMES,
                    max_plan_updates=1,
                )
            )
        self.assertIn("plan updates 2/1", recorded[-1][1]["observed_summary"])

    def test_effort_scorer_helpers_and_constants_are_fingerprinted(self):
        self.assertIn(
            _sqlite_call_persists_resume_state,
            EffortCalibrationScenario.fingerprint_dependencies,
        )
        self.assertIn(
            "partial_source_forbidden_tool_names",
            EffortCalibrationScenario.fingerprint_data,
        )
        sqlite_dependency_names = {
            dependency.__name__
            for dependency in SqliteToolResultScenario.fingerprint_dependencies
        }
        self.assertIn("_dedupe_claim_units", sqlite_dependency_names)
        self.assertIn("_tool_call_completed_successfully", sqlite_dependency_names)
        self.assertIn("_outbound_messages_after", sqlite_dependency_names)
        self.assertIn("mock_builders", SqliteToolResultScenario.fingerprint_data)

    def test_deep_research_prompt_scores_outcomes_without_prescribing_algorithm(self):
        prompt = EffortExplicitDeepResearchRemainsCapableScenario.prompt

        self.assertIn("decision-useful", prompt)
        self.assertIn("avoid redundant research", prompt)
        self.assertNotIn("one broad discovery search first", prompt)
        self.assertNotIn("then scrape", prompt)
        self.assertNotIn("add another search only if", prompt)

    def test_dedupe_requery_answer_assertion_does_not_force_specific_claim_category(self):
        self.assertEqual(SqliteDedupeRequeryScenario.required_terms, ())

    def test_dedupe_requery_prompt_scores_efficient_outcome_without_sql_recipe(self):
        prompt = SqliteDedupeRequeryScenario.prompt

        self.assertIn("genuinely distinct claims", prompt)
        self.assertNotIn("SQLite", prompt)
        self.assertNotIn("efficiently", prompt)
        self.assertNotIn("CTE", prompt)
        self.assertNotIn("group/ranking", prompt)
        self.assertNotIn("__tool_results", prompt)

    def test_sqlite_eval_prompts_score_behavior_without_prescribing_sql(self):
        for scenario in (
            SqliteMultiResultWebSynthesisScenario,
            SqliteIntermediateWorkingTableScenario,
        ):
            self.assertNotIn("SQLite", scenario.prompt)
            self.assertNotIn("__tool_results", scenario.prompt)

    def test_near_duplicate_query_detector_flags_repetitive_searches(self):
        duplicates = _find_near_duplicate_texts(
            [
                "latest YC Winter 2026 batch companies sector breakdown",
                "YC W26 Moon hotels cattle drones startups",
                "latest yc winter 2026 batch companies sector breakdown statistics",
            ]
        )

        self.assertEqual(
            duplicates,
            [
                (
                    "latest YC Winter 2026 batch companies sector breakdown",
                    "latest yc winter 2026 batch companies sector breakdown statistics",
                )
            ],
        )

    def test_web_query_value_collapses_aliases_per_tool_call(self):
        self.assertEqual(
            _web_query_value(
                {
                    "query": "latest Y Combinator batch companies 2025 2026",
                    "keyword": "latest Y Combinator batch 2025 2026 companies",
                    "prompt": "Tell me about the latest Y Combinator batch of companies",
                }
            ),
            "latest Y Combinator batch companies 2025 2026",
        )

    def test_eval_synthetic_search_tool_matches_production_query_shape(self):
        parameters = EVAL_SYNTHETIC_TOOL_DEFINITIONS["mcp_brightdata_search_engine"]["parameters"]
        description = EVAL_SYNTHETIC_TOOL_DEFINITIONS["mcp_brightdata_search_engine"]["description"]

        self.assertEqual(set(parameters["properties"]), {"query"})
        self.assertEqual(parameters["required"], ["query"])
        self.assertFalse(parameters["additionalProperties"])
        self.assertIn(".example.test URLs are valid source URLs", description)

    def test_sqlite_result_text_read_detector_finds_retrieval_loops(self):
        reads = _sqlite_result_text_reads(
            "SELECT result_text FROM __tool_results WHERE result_id='abc123'; "
            "SELECT grep_context_all(result_text, 'AI', 500, 3) FROM __tool_results WHERE result_id='def456';"
        )

        self.assertEqual(reads, ["abc123", "def456"])

    def test_result_text_loop_check_ignores_safely_rejected_projection(self):
        scenario, recorded = EffortCalibrationScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        calls = [
            SimpleNamespace(
                step="rejected",
                result='{"status":"error","error_type":"unshaped_multi_result_payload"}',
                tool_name="sqlite_batch",
                tool_params={"sql": "SELECT result_text FROM __tool_results WHERE result_id='raw'"},
            ),
            SimpleNamespace(
                step="shaped",
                result='{"status":"ok"}',
                tool_name="sqlite_batch",
                tool_params={"sql": "SELECT json_extract(result_json, '$.vendor') FROM __tool_results"},
            ),
        ]

        with patch("api.evals.scenarios.effort_calibration._tool_calls_for_run", return_value=calls):
            passed = scenario._record_no_sqlite_result_text_reread_loop(
                "run", after=None, task_name="verify_no_query_or_sqlite_loops"
            )

        self.assertTrue(passed)
        self.assertIn("Observed 0 __tool_results.result_text read(s)", recorded[-1][1]["observed_summary"])

    def test_result_text_loop_check_counts_runtime_error_after_read(self):
        scenario, recorded = EffortCalibrationScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        calls = [
            SimpleNamespace(
                step="runtime-error",
                result='{"status":"error","message":"no such table: missing_table","results":[{"rows":[]}]}',
                tool_name="sqlite_batch",
                tool_params={
                    "sql": (
                        "SELECT result_text FROM __tool_results WHERE result_id='r1'; "
                        "SELECT missing_column FROM missing_table;"
                    )
                },
            )
        ]

        with patch("api.evals.scenarios.effort_calibration._tool_calls_for_run", return_value=calls):
            passed = scenario._record_no_sqlite_result_text_reread_loop(
                "run", after=None, task_name="verify_no_query_or_sqlite_loops"
            )

        self.assertFalse(passed)
        self.assertIn("saw reads=['r1']", recorded[-1][1]["observed_summary"])

    def test_hierarchical_report_shape_requires_sources_and_structure(self):
        ok, summary = _hierarchical_report_shape(
            (
                "## Northstar Robotics\n\n"
                "- Atlas launched for mixed-fleet warehouse routing.\n"
                "- Series B funding supports deployments.\n\n"
                "| Area | Takeaway |\n"
                "| --- | --- |\n"
                "| Product | Atlas reduces aisle congestion. |\n\n"
                "Sources: https://northstar.example.test/blog/atlas-launch and "
                "https://news.example.test/northstar-series-b"
            ),
            source_urls=[
                "https://northstar.example.test/blog/atlas-launch",
                "https://news.example.test/northstar-series-b",
            ],
            min_source_count=2,
            min_chars=150,
            max_chars=1000,
            required_any_groups=(("Northstar Robotics",),),
        )

        self.assertTrue(ok, summary)

    def test_sqlite_tool_result_sourced_answer_rejects_progress_before_final(self):
        scenario, recorded = SqliteToolResultScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        messages = [SimpleNamespace(body="I have the results. Now I will query SQLite."), SimpleNamespace(body="Final: https://api.example.test/products/caremesh.json HIPAA")]
        with patch("api.evals.scenarios.sqlite_tool_results._outbound_messages_after", return_value=messages):
            passed = scenario._record_sourced_answer("run", agent_id="agent", after=None, task_name="verify_sourced_answer", source_urls=["https://api.example.test/products/caremesh.json"], required_terms=["HIPAA"], min_sources=1)
        self.assertFalse(passed)
        self.assertIn("progress_messages=1", recorded[-1][1]["observed_summary"])

    def test_sqlite_dedupe_answer_accepts_two_distinct_claims_with_mapped_sources(self):
        scenario, recorded = SqliteDedupeRequeryScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        messages = [
            SimpleNamespace(
                body=(
                    f"1. **AxonFlow:** Best for enterprise teams because its SOC 2 controls and 99.95% SLA "
                    f"support strict governance. [Source]({SOURCE_URLS[0]})\n\n"
                    f"2. **CareMesh:** Best for HIPAA healthcare support because its BAA and PHI redaction "
                    f"protect regulated workflows. [Source]({SOURCE_URLS[2]})"
                )
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._outbound_messages_after", return_value=messages):
            passed = scenario._record_sourced_answer(
                "run",
                agent_id="agent",
                after=None,
                task_name="verify_sourced_answer",
                source_urls=SOURCE_URLS,
                required_terms=(),
                min_sources=2,
            )

        self.assertTrue(passed, recorded[-1][1]["observed_summary"])

    def test_sqlite_dedupe_answer_maps_rich_sections_with_separate_source_lines(self):
        scenario, recorded = SqliteDedupeRequeryScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        messages = [
            SimpleNamespace(
                body=(
                    "### 1️⃣ AxonFlow — enterprise governance\n\n"
                    "> Best for enterprise support because SOC 2 controls, analytics, Salesforce, "
                    "and a 99.95% SLA support strict governance.\n\n"
                    f"**Source:** [AxonFlow evidence]({SOURCE_URLS[0]})\n\n"
                    "---\n\n"
                    "### 2️⃣ CareMesh — regulated healthcare\n\n"
                    "> Best for HIPAA healthcare because its BAA, PHI redaction, escalation routing, "
                    "and audit exports protect regulated workflows.\n\n"
                    f"**Source:** [CareMesh evidence]({SOURCE_URLS[2]})"
                )
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._outbound_messages_after", return_value=messages):
            passed = scenario._record_sourced_answer(
                "run",
                agent_id="agent",
                after=None,
                task_name="verify_sourced_answer",
                source_urls=SOURCE_URLS,
                required_terms=(),
                min_sources=2,
            )

        self.assertTrue(passed, recorded[-1][1]["observed_summary"])

    def test_sqlite_dedupe_answer_collapses_duplicate_brightsupport_claims(self):
        scenario, recorded = SqliteDedupeRequeryScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        messages = [
            SimpleNamespace(
                body=(
                    f"1. BrightSupport is best for SMB teams due to low administration and transparent pricing. "
                    f"[Source]({SOURCE_URLS[1]})\n\n"
                    f"2. BrightSupport is best for SMB teams due to quick setup and monthly pricing. "
                    f"[Source]({SOURCE_URLS[3]})"
                )
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._outbound_messages_after", return_value=messages):
            passed = scenario._record_sourced_answer(
                "run",
                agent_id="agent",
                after=None,
                task_name="verify_sourced_answer",
                source_urls=SOURCE_URLS,
                required_terms=(),
                min_sources=2,
            )

        self.assertFalse(passed)
        self.assertIn("fewer than two distinct claims", recorded[-1][1]["observed_summary"])
        self.assertIn("duplicate claim families", recorded[-1][1]["observed_summary"])

    def test_sqlite_dedupe_answer_rejects_claim_cited_to_wrong_source(self):
        scenario, recorded = SqliteDedupeRequeryScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        messages = [
            SimpleNamespace(
                body=(
                    f"1. AxonFlow is strongest for enterprise teams because it has SOC 2 controls. "
                    f"[Source]({SOURCE_URLS[1]})\n\n"
                    f"2. CareMesh is strongest for HIPAA healthcare because it has a BAA. "
                    f"[Source]({SOURCE_URLS[2]})"
                )
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._outbound_messages_after", return_value=messages):
            passed = scenario._record_sourced_answer(
                "run",
                agent_id="agent",
                after=None,
                task_name="verify_sourced_answer",
                source_urls=SOURCE_URLS,
                required_terms=(),
                min_sources=2,
            )

        self.assertFalse(passed)
        self.assertIn("['CareMesh']", recorded[-1][1]["observed_summary"])

    def test_sqlite_plan_answer_rejects_pending_soc2_as_qualified(self):
        scenario, recorded = SqliteIntermediateWorkingTableScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        messages = [
            SimpleNamespace(
                body=(
                    "## Qualifying plans\n\n"
                    "| Vendor | Plan | Compliance |\n| --- | --- | --- |\n"
                    "| BrightSupport | Business | SOC 2 pending |\n\n"
                    "## Recommendation\n\nCareMesh Clinic is the HIPAA choice at $720.\n\n"
                    "https://api.example.test/products/caremesh.json"
                )
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._outbound_messages_after", return_value=messages):
            passed = scenario._record_sourced_answer(
                "run",
                agent_id="agent",
                after=None,
                task_name="verify_sourced_answer",
                source_urls=["https://api.example.test/products/caremesh.json"],
                required_terms=["CareMesh", "HIPAA", "$720"],
                min_sources=1,
            )

        self.assertFalse(passed)
        self.assertIn("SOC 2 pending was presented", recorded[-1][1]["observed_summary"])

    def test_sqlite_plan_answer_rejects_or_filter_error_from_live_eval(self):
        issues = SqliteIntermediateWorkingTableScenario()._answer_quality_issues(
            """
            ## Recommendation: CareMesh Clinic

            This is the only plan that meets all criteria.

            | Vendor | Plan | Price | Seats | Compliance | Meets criteria? |
            | --- | --- | --- | --- | --- | --- |
            | Dockwise | Commerce Plus | $890 | 65 | SOC 2 | ⚠️ Partial |

            Dockwise Commerce Plus is a poor fit because it has no HIPAA.
            BrightSupport Business has SOC 2 pending, so it is not qualified.
            """
        )

        self.assertIn(
            "Dockwise Commerce Plus was not accurately identified as qualifying at $890 for 65 seats with SOC 2",
            issues,
        )
        self.assertIn("CareMesh was incorrectly described as the only qualifying plan", issues)

    def test_sqlite_plan_quality_accepts_qualifying_table_section(self):
        issues = SqliteIntermediateWorkingTableScenario()._answer_quality_issues(
            """
            ## Plans Meeting All Criteria

            | Vendor | Plan | Price | Seats | Compliance |
            | --- | --- | --- | --- | --- |
            | CareMesh | Clinic | $720 | 50 | HIPAA, SOC 2 |
            | Dockwise | Commerce Plus | $890 | 65 | SOC 2, PCI |

            BrightSupport Business has SOC 2 pending, so it does not qualify.
            """
        )

        self.assertEqual(issues, [])

    def test_sqlite_plan_quality_allows_only_plan_with_both_certifications(self):
        issues = SqliteIntermediateWorkingTableScenario()._answer_quality_issues(
            """
            ## Qualifying Plans

            | Vendor | Plan | Price | Seats | Compliance |
            | --- | --- | --- | --- | --- |
            | CareMesh | Clinic | $720 | 50 | HIPAA, SOC 2 |
            | Dockwise | Commerce Plus | $890 | 65 | PCI, SOC 2 |

            BrightSupport Business has SOC 2 pending, so it does not qualify.
            CareMesh Clinic is the only plan that has both HIPAA and SOC 2.
            """
        )

        self.assertEqual(issues, [])

    def test_sqlite_plan_answer_allows_pending_soc2_when_explicitly_disqualified(self):
        scenario, recorded = SqliteIntermediateWorkingTableScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        messages = [
            SimpleNamespace(
                body=(
                    "## Disqualified\n\n"
                    "BrightSupport Business has SOC 2 pending, so it does not qualify yet.\n\n"
                    "Dockwise Commerce Plus also qualifies at $890 for 65 seats with SOC 2.\n\n"
                    "## Recommendation\n\nCareMesh Clinic is the HIPAA choice at $720.\n\n"
                    "https://api.example.test/products/caremesh.json"
                )
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._outbound_messages_after", return_value=messages):
            passed = scenario._record_sourced_answer(
                "run",
                agent_id="agent",
                after=None,
                task_name="verify_sourced_answer",
                source_urls=["https://api.example.test/products/caremesh.json"],
                required_terms=["CareMesh", "HIPAA", "$720"],
                min_sources=1,
            )

        self.assertTrue(passed, recorded[-1][1]["observed_summary"])

    def test_sqlite_plan_answer_allows_pending_soc2_marked_not_certified(self):
        scenario, recorded = SqliteIntermediateWorkingTableScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        messages = [
            SimpleNamespace(
                body=(
                    "## Candidate breakdown\n\n"
                    "| Plan | Compliance | Passes? |\n| --- | --- | --- |\n"
                    "| BrightSupport Business | SOC 2 pending | ❌ Not certified |\n\n"
                    "Dockwise Commerce Plus also qualifies at $890 for 65 seats with SOC 2.\n\n"
                    "## Recommendation\n\nCareMesh Clinic is the HIPAA choice at $720.\n\n"
                    "https://api.example.test/products/caremesh.json"
                )
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._outbound_messages_after", return_value=messages):
            passed = scenario._record_sourced_answer(
                "run",
                agent_id="agent",
                after=None,
                task_name="verify_sourced_answer",
                source_urls=["https://api.example.test/products/caremesh.json"],
                required_terms=["CareMesh", "HIPAA", "$720"],
                min_sources=1,
            )

        self.assertTrue(passed, recorded[-1][1]["observed_summary"])

    def test_sqlite_plan_answer_allows_pending_soc2_marked_not_yet_certified(self):
        issues = SqliteIntermediateWorkingTableScenario()._answer_quality_issues(
            """
            ## Plans Meeting All Criteria

            - CareMesh Clinic qualifies at $720 with HIPAA and SOC 2.
            - Dockwise Commerce Plus qualifies at $890 for 65 seats with SOC 2.

            ## Disqualified

            BrightSupport Business has SOC 2 pending and is not yet certified.
            """
        )

        self.assertEqual(issues, [])

    def test_sqlite_plan_answer_allows_pending_soc2_that_does_not_count(self):
        issues = SqliteIntermediateWorkingTableScenario()._answer_quality_issues(
            """
            CareMesh Clinic qualifies at $720 with HIPAA and SOC 2.
            Dockwise Commerce Plus qualifies at $890 for 65 seats with SOC 2.
            BrightSupport Business ($760, 45 seats) — “SOC 2 pending” doesn't count as SOC 2.
            """
        )

        self.assertEqual(issues, [])

    def test_sqlite_plan_quality_accepts_exact_live_two_qualifier_wording(self):
        issues = SqliteIntermediateWorkingTableScenario()._answer_quality_issues(
            """
            Criteria: HIPAA or SOC 2 (certified, not pending). Only two plans qualify:
            CareMesh Clinic qualifies at $720 with HIPAA and SOC 2.
            Dockwise Commerce Plus qualifies at $890 for 65 seats with SOC 2.
            BrightSupport Business: ❌ SOC 2 pending — not certified yet.
            Dockwise Commerce is excluded: PCI only, no SOC 2 or HIPAA.
            Recommendation: CareMesh is the lowest cost among qualifiers; Dockwise Commerce Plus is a solid backup.
            """
        )

        self.assertEqual(issues, [])

    def test_not_pending_criteria_does_not_mask_later_pending_plan(self):
        issues = SqliteIntermediateWorkingTableScenario()._answer_quality_issues(
            """
            Criteria: SOC 2 certified, not pending.
            CareMesh Clinic qualifies at $720 with HIPAA and SOC 2.
            Dockwise Commerce Plus qualifies at $890 for 65 seats with SOC 2.
            BrightSupport Business has SOC 2 pending.
            """
        )

        self.assertIn("SOC 2 pending was presented without explicitly disqualifying it", issues)

    def test_only_qualifier_detection_uses_explicit_singular_claims(self):
        scenario = SqliteIntermediateWorkingTableScenario()
        grounding = (
            " CareMesh Clinic qualifies at $720 with HIPAA and SOC 2. "
            "Dockwise Commerce Plus qualifies at $890 for 65 seats with SOC 2. "
            "BrightSupport Business has SOC 2 pending and is not certified."
        )
        for correct in (
            "CareMesh and Dockwise are the only two qualifying plans.",
            "CareMesh is one of only two qualifying plans.",
            "Recommendation: CareMesh. It is the only plan under $800; Dockwise also qualifies.",
            "CareMesh is the sole plan with HIPAA; Dockwise also qualifies with SOC 2.",
            "Only CareMesh meets the HIPAA requirement; Dockwise qualifies via SOC 2.",
            "CareMesh alone meets HIPAA; Dockwise also qualifies with SOC 2.",
            "The only plan that meets HIPAA is CareMesh; Dockwise also qualifies with SOC 2.",
            "CareMesh is the only qualifying plan with both HIPAA and SOC 2; Dockwise qualifies via SOC 2.",
        ):
            with self.subTest(correct=correct):
                self.assertNotIn(
                    "CareMesh was incorrectly described as the only qualifying plan",
                    scenario._answer_quality_issues(correct + grounding),
                )
        for incorrect in (
            "CareMesh is the only qualifying plan.",
            "The only plan that qualifies is CareMesh.",
            "Only CareMesh qualifies.",
            "CareMesh alone meets all criteria.",
            "CareMesh is the sole plan that meets every criterion.",
        ):
            with self.subTest(incorrect=incorrect):
                self.assertIn(
                    "CareMesh was incorrectly described as the only qualifying plan",
                    scenario._answer_quality_issues(incorrect + grounding),
                )

    def test_pending_soc2_section_headings_switch_polarity(self):
        scenario = SqliteIntermediateWorkingTableScenario()
        disqualified = """
        CareMesh Clinic qualifies at $720 with HIPAA and SOC 2.
        Dockwise Commerce Plus qualifies at $890 for 65 seats with SOC 2.
        ## Plans that didn't qualify
        BrightSupport Business | SOC 2 pending
        """
        qualified = """
        ## Plans that did not qualify
        AxonFlow is over budget.
        ## Only two plans qualify
        CareMesh Clinic qualifies at $720 with HIPAA and SOC 2.
        Dockwise Commerce Plus qualifies at $890 for 65 seats with SOC 2.
        BrightSupport Business | SOC 2 pending
        """
        reset_to_neutral = """
        CareMesh Clinic qualifies at $720 with HIPAA and SOC 2.
        Dockwise Commerce Plus qualifies at $890 for 65 seats with SOC 2.
        ## Plans that did not qualify
        AxonFlow is over budget.
        ## Other candidates
        BrightSupport Business | SOC 2 pending
        """
        sibling_line = """
        CareMesh Clinic qualifies at $720 with HIPAA and SOC 2.
        Dockwise Commerce Plus qualifies at $890 for 65 seats with SOC 2.
        BrightSupport Starter is disqualified because SOC 2 is pending.
        BrightSupport Business has SOC 2 pending.
        """

        self.assertNotIn(
            "SOC 2 pending was presented without explicitly disqualifying it",
            scenario._answer_quality_issues(disqualified),
        )
        self.assertIn(
            "SOC 2 pending was presented without explicitly disqualifying it",
            scenario._answer_quality_issues(qualified),
        )
        for body in (reset_to_neutral, sibling_line):
            self.assertIn(
                "SOC 2 pending was presented without explicitly disqualifying it",
                scenario._answer_quality_issues(body),
            )
        for heading in (
            "**Plans that did not qualify**",
            "Disqualified plans",
            "## Non-qualifying plans",
            "<h2>Plans that did not qualify</h2>",
            "<strong>Disqualified plans</strong>",
        ):
            with self.subTest(heading=heading):
                body = f"""
                CareMesh Clinic qualifies at $720 with HIPAA and SOC 2.
                Dockwise Commerce Plus qualifies at $890 for 65 seats with SOC 2.
                {heading}
                BrightSupport Business | SOC 2 pending
                """
                self.assertNotIn(
                    "SOC 2 pending was presented without explicitly disqualifying it",
                    scenario._answer_quality_issues(body),
                )

    def test_sqlite_plan_answer_rejects_negated_dockwise_qualification(self):
        for negative_term in ("unqualified", "disqualified"):
            with self.subTest(negative_term=negative_term):
                issues = SqliteIntermediateWorkingTableScenario()._answer_quality_issues(
                    f"""
                    CareMesh Clinic qualifies at $720 with HIPAA and SOC 2.
                    BrightSupport Business has SOC 2 pending, so it is not qualified.
                    Dockwise Commerce Plus is {negative_term} at $890 for 65 seats with SOC 2.
                    """
                )

                self.assertIn(
                    "Dockwise Commerce Plus was not accurately identified as qualifying at $890 for 65 seats with SOC 2",
                    issues,
                )

    def test_later_disqualification_does_not_rescue_pending_plan_in_qualifying_section(self):
        issues = SqliteIntermediateWorkingTableScenario()._answer_quality_issues(
            """
            ## Qualifying plans

            BrightSupport Business has SOC 2 pending.
            Dockwise Commerce Plus qualifies at $890 for 65 seats with SOC 2.

            ## Disqualified

            BrightSupport Business is not yet certified.
            """
        )

        self.assertIn("SOC 2 pending was presented without explicitly disqualifying it", issues)

    def test_sqlite_plan_answer_reads_pending_disqualification_across_full_answer(self):
        scenario, recorded = SqliteIntermediateWorkingTableScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        messages = [
            SimpleNamespace(
                body=(
                    "## Candidate breakdown\n\n"
                    "| Plan | Compliance | Price | Qualified | Note |\n"
                    "| --- | --- | --- | --- | --- |\n"
                    "| BrightSupport Business | SOC 2 pending | $760 | ❌ | Awaiting audit |\n\n"
                    "## Recommendation\n\n"
                    "CareMesh Clinic is the HIPAA choice at $720. BrightSupport's SOC 2 is still pending, "
                    "so it is not a certified solution yet. Dockwise Commerce Plus also qualifies at $890 "
                    "for 65 seats with SOC 2.\n\n"
                    "https://api.example.test/products/caremesh.json"
                )
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._outbound_messages_after", return_value=messages):
            passed = scenario._record_sourced_answer(
                "run",
                agent_id="agent",
                after=None,
                task_name="verify_sourced_answer",
                source_urls=["https://api.example.test/products/caremesh.json"],
                required_terms=["CareMesh", "HIPAA", "$720"],
                min_sources=1,
            )

        self.assertTrue(passed, recorded[-1][1]["observed_summary"])

    def test_sqlite_item_link_report_rejects_missing_listing_urls(self):
        scenario, recorded = SqliteToolResultScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        messages = [
            SimpleNamespace(
                body=(
                    "## Initial Model Y Report\n\n"
                    "| Vehicle | Price | Dealer |\n"
                    "| --- | --- | --- |\n"
                    "| 2023 Model Y Long Range | $27,455 | Harrisburg Mitsubishi |\n"
                    "| 2025 Model Y | $39,129 | Renn Kirby Frederick |"
                )
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._outbound_messages_after", return_value=messages):
            passed = scenario._record_sourced_answer(
                "run",
                agent_id="agent",
                after=None,
                task_name="verify_listing_links_in_report",
                source_urls=LISTING_URLS,
                required_terms=["Model Y", "Harrisburg", "$27,455"],
                min_sources=2,
            )

        self.assertFalse(passed)
        self.assertIn("linked_sources=0", recorded[-1][1]["observed_summary"])

    def test_sqlite_item_link_report_rejects_feed_urls_as_listing_substitutes(self):
        scenario, recorded = SqliteToolResultScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        messages = [
            SimpleNamespace(
                body=(
                    "## Tesla Model Y Inventory Report\n\n"
                    "| Vehicle | Price | Dealer | Source |\n"
                    "| --- | --- | --- | --- |\n"
                    "| 2023 Model Y Long Range | $27,455 | Harrisburg Mitsubishi | "
                    f"[local.json]({INVENTORY_URLS[0]}) |\n"
                    "| 2025 Model Y | $39,129 | Renn Kirby Frederick | "
                    f"[dealer.json]({INVENTORY_URLS[1]}) |"
                )
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._outbound_messages_after", return_value=messages):
            passed = scenario._record_sourced_answer(
                "run",
                agent_id="agent",
                after=None,
                task_name="verify_listing_links_in_report",
                source_urls=LISTING_URLS,
                required_terms=["Model Y", "Harrisburg", "$27,455"],
                min_sources=2,
            )

        self.assertFalse(passed)
        self.assertIn("linked_sources=0", recorded[-1][1]["observed_summary"])

    def test_sqlite_item_link_report_allows_row_links_repeated_in_sources(self):
        scenario, recorded = SqliteItemLinkReportScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        first_url, second_url = LISTING_URLS[:2]
        messages = [
            SimpleNamespace(
                body=(
                    "## Model Y report\n\n"
                    "| VIN | Vehicle | Price | Dealer | Listing |\n| --- | --- | --- | --- | --- |\n"
                    f"| 7SAY-001 | 2023 Model Y | $32,985 | Harrisburg Mitsubishi | [Open]({first_url}) |\n"
                    f"| 7SAY-002 | 2023 Model Y | $27,455 | Harrisburg Mitsubishi | [Open]({second_url}) |\n\n"
                    f"Sources: {first_url} and {second_url}"
                )
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._outbound_messages_after", return_value=messages):
            passed = scenario._record_sourced_answer(
                "run",
                agent_id="agent",
                after=None,
                task_name="verify_listing_links_in_report",
                source_urls=LISTING_URLS,
                required_terms=["Model Y", "Harrisburg", "$27,455"],
                min_sources=2,
            )

        self.assertTrue(passed, recorded[-1][1]["observed_summary"])

    def test_sqlite_item_link_report_rejects_unmapped_listing_urls(self):
        scenario, recorded = SqliteItemLinkReportScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        messages = [
            SimpleNamespace(
                body=(
                    "## Model Y report\n\n2023 Model Y — Harrisburg — $27,455.\n\n"
                    f"Sources: {LISTING_URLS[0]} and {LISTING_URLS[1]}"
                )
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._outbound_messages_after", return_value=messages):
            passed = scenario._record_sourced_answer(
                "run",
                agent_id="agent",
                after=None,
                task_name="verify_listing_links_in_report",
                source_urls=LISTING_URLS,
                required_terms=["Model Y", "Harrisburg", "$27,455"],
                min_sources=2,
            )

        self.assertFalse(passed)
        self.assertIn("mapped to item details", recorded[-1][1]["observed_summary"])

    def test_sqlite_item_link_report_rejects_shared_details_for_multiple_urls(self):
        scenario, recorded = SqliteItemLinkReportScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        messages = [
            SimpleNamespace(
                body=(
                    "2023 Model Y report for Harrisburg: best price $27,455. "
                    f"Sources: {LISTING_URLS[0]} and {LISTING_URLS[1]}"
                )
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._outbound_messages_after", return_value=messages):
            passed = scenario._record_sourced_answer(
                "run",
                agent_id="agent",
                after=None,
                task_name="verify_listing_links_in_report",
                source_urls=LISTING_URLS,
                required_terms=["Model Y", "Harrisburg", "$27,455"],
                min_sources=2,
            )

        self.assertFalse(passed)
        self.assertIn("mapped to item details", recorded[-1][1]["observed_summary"])

    def test_sqlite_item_link_report_uses_declared_verifier_task(self):
        scenario = SqliteItemLinkReportScenario()
        task_names = [task.name for task in scenario.tasks]

        self.assertEqual(scenario.sourced_answer_task_name, "verify_listing_links_in_report")
        self.assertIn(scenario.sourced_answer_task_name, task_names)
        self.assertNotIn("verify_sourced_answer", task_names)

    def test_monitor_pollution_allows_slow_background_browser_drain(self):
        self.assertGreaterEqual(BACKGROUND_DRAIN_TIMEOUT_SECONDS, 600)

    def test_sqlite_tool_result_usage_rejects_manual_values_working_table(self):
        scenario, recorded = SqliteToolResultScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        calls = [SimpleNamespace(step="step", tool_name="sqlite_batch", tool_params={"sql": "CREATE TABLE plan_candidates(vendor TEXT); INSERT INTO plan_candidates VALUES ('CareMesh'); SELECT * FROM plan_candidates;"})]
        with patch("api.evals.scenarios.sqlite_tool_results._tool_calls_for_run", return_value=calls):
            passed = scenario._record_sqlite_usage("run", after=None, task_name="verify_working_table_sqlite_usage", require_working_table=True)
        self.assertFalse(passed)
        self.assertIn("no aggregate __tool_results query", recorded[-1][1]["observed_summary"])

    def test_sqlite_usage_rejects_budget_exhausted_manual_copy_attempt(self):
        scenario, recorded = SqliteToolResultScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        calls = [
            SimpleNamespace(
                step="step-1",
                status="complete",
                result='{"status":"ok"}',
                tool_name="sqlite_batch",
                tool_params={
                    "sql": """
                    CREATE TABLE scraped_pages AS
                    SELECT result_id, result_text FROM __tool_results
                    WHERE result_id IN ('r1', 'r2', 'r3', 'r4');
                    SELECT result_id, substr(result_text, 1, 1200) AS preview
                    FROM scraped_pages ORDER BY result_id;
                    """
                },
            ),
            SimpleNamespace(
                step="step-2",
                status="complete",
                result='{"status":"ok"}',
                tool_name="sqlite_batch",
                tool_params={"sql": "SELECT result_id, substr(result_text, 1, 800) FROM scraped_pages;"},
            ),
            SimpleNamespace(
                step="step-3",
                status="complete",
                result='{"status":"ok"}',
                tool_name="sqlite_batch",
                tool_params={"sql": "SELECT count(*) FROM scraped_pages;"},
            ),
            SimpleNamespace(
                step="step-4",
                status="complete",
                result='{"status":"error","error_type":"sqlite_efficiency_budget_exhausted"}',
                tool_name="sqlite_batch",
                tool_params={"sql": "CREATE TABLE copied AS SELECT * FROM (VALUES ('manual'));"},
            ),
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._tool_calls_for_run", return_value=calls):
            passed = scenario._record_sqlite_usage(
                "run",
                after=None,
                task_name="verify_smart_sqlite_synthesis",
            )

        self.assertFalse(passed)
        self.assertIn("manual-copy attempt", recorded[-1][1]["observed_summary"])
        self.assertIn("efficiency budget was exhausted", recorded[-1][1]["observed_summary"])

    def test_sqlite_usage_rejects_failed_manual_copy_even_after_good_synthesis(self):
        scenario, recorded = SqliteToolResultScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        calls = [
            SimpleNamespace(
                step="step-1",
                status="complete",
                result='{"status":"ok"}',
                tool_name="sqlite_batch",
                tool_params={
                    "sql": (
                        "WITH shaped AS (SELECT result_id, json_extract(result_json, '$.content') AS content "
                        "FROM __tool_results) SELECT * FROM shaped;"
                    )
                },
            ),
            SimpleNamespace(
                step="step-2",
                status="complete",
                result='{"status":"error","error_type":"manual_tool_result_copy"}',
                tool_name="sqlite_batch",
                tool_params={"sql": "CREATE TABLE copied AS SELECT * FROM (VALUES ('manual'));"},
            ),
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._tool_calls_for_run", return_value=calls):
            passed = scenario._record_sqlite_usage(
                "run",
                after=None,
                task_name="verify_smart_sqlite_synthesis",
            )

        self.assertFalse(passed)
        self.assertIn("manual-copy attempt", recorded[-1][1]["observed_summary"])

    def test_sqlite_usage_allows_one_guarded_manual_copy_when_later_repaired(self):
        scenario, recorded = SqliteToolResultScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        calls = [
            SimpleNamespace(
                step="step-1",
                status="complete",
                result='{"status":"error","error_type":"manual_tool_result_copy"}',
                tool_name="sqlite_batch",
                tool_params={
                    "sql": (
                        "CREATE TABLE copied AS "
                        "SELECT 'A long copied claim from the first tool result with enough detail' "
                        "UNION ALL SELECT 'A long copied claim from the second tool result with enough detail' "
                        "UNION ALL SELECT 'A long copied claim from the third tool result with enough detail';"
                    )
                },
            ),
            SimpleNamespace(
                step="step-2",
                status="complete",
                result='{"status":"ok"}',
                tool_name="sqlite_batch",
                tool_params={
                    "sql": (
                        "WITH shaped AS (SELECT json_extract(result_json, '$.text') AS claim "
                        "FROM __tool_results) SELECT claim FROM shaped ORDER BY claim;"
                    )
                },
            ),
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._tool_calls_for_run", return_value=calls):
            passed = scenario._record_sqlite_usage(
                "run",
                after=None,
                task_name="verify_smart_sqlite_synthesis",
            )

        self.assertTrue(passed, recorded[-1][1]["observed_summary"])
        self.assertTrue(recorded[-1][1]["artifacts"]["usage"]["allowed_manual_repair"])

    def test_sqlite_usage_allows_one_genuine_shape_error_when_later_repaired(self):
        scenario, recorded = SqliteToolResultScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        calls = [
            SimpleNamespace(
                step="step-1",
                status="complete",
                result='{"status":"error","error_type":"sqlite_error","message":"no such column: content"}',
                tool_name="sqlite_batch",
                tool_params={"sql": "SELECT content FROM __tool_results ORDER BY created_at;"},
            ),
            SimpleNamespace(
                step="step-2",
                status="complete",
                result='{"status":"ok"}',
                tool_name="sqlite_batch",
                tool_params={
                    "sql": (
                        "WITH shaped AS (SELECT result_id, json_extract(result_json, '$.content') AS content "
                        "FROM __tool_results) SELECT result_id, content FROM shaped ORDER BY result_id;"
                    )
                },
            ),
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._tool_calls_for_run", return_value=calls):
            passed = scenario._record_sqlite_usage(
                "run",
                after=None,
                task_name="verify_smart_sqlite_synthesis",
            )

        self.assertTrue(passed, recorded[-1][1]["observed_summary"])
        self.assertTrue(recorded[-1][1]["artifacts"]["usage"]["allowed_shape_repair"])

    def test_sqlite_usage_allows_two_distinct_shape_errors_when_repaired(self):
        scenario, recorded = SqliteToolResultScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        calls = [
            SimpleNamespace(
                step="step-1",
                status="complete",
                result='{"status":"error","message":"malformed JSON"}',
                tool_name="sqlite_batch",
                tool_params={"sql": "SELECT json_extract(j.value, '$.id') FROM __tool_results, json_each(result_json) j"},
            ),
            SimpleNamespace(
                step="step-2",
                status="complete",
                result='{"status":"error","message":"DISTINCT aggregates must have exactly one argument"}',
                tool_name="sqlite_batch",
                tool_params={"sql": "SELECT group_concat(DISTINCT source_url, ',') FROM __tool_results"},
            ),
            SimpleNamespace(
                step="step-3",
                status="complete",
                result='{"status":"ok"}',
                tool_name="sqlite_batch",
                tool_params={
                    "sql": (
                        "WITH shaped AS (SELECT json_extract(item.value, '$.id') AS item_id "
                        "FROM __tool_results, json_each(result_json, '$.items') AS item) "
                        "SELECT item_id, count(*) FROM shaped GROUP BY item_id"
                    )
                },
            ),
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._tool_calls_for_run", return_value=calls):
            passed = scenario._record_sqlite_usage(
                "run",
                after=None,
                task_name="verify_smart_sqlite_synthesis",
            )

        self.assertTrue(passed, recorded[-1][1]["observed_summary"])
        self.assertTrue(recorded[-1][1]["artifacts"]["usage"]["allowed_shape_repair"])

    def test_sqlite_usage_rejects_repeated_shape_error_loop(self):
        scenario, recorded = SqliteToolResultScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        calls = [
            SimpleNamespace(
                step="step-1",
                status="complete",
                result='{"status":"error","error_type":"sqlite_error","message":"no such column: content"}',
                tool_name="sqlite_batch",
                tool_params={"sql": "SELECT content FROM __tool_results;"},
            ),
            SimpleNamespace(
                step="step-2",
                status="complete",
                result='{"status":"error","error_type":"sqlite_error","message":"no such column: payload"}',
                tool_name="sqlite_batch",
                tool_params={"sql": "SELECT payload FROM __tool_results;"},
            ),
            SimpleNamespace(
                step="step-3",
                status="complete",
                result='{"status":"ok"}',
                tool_name="sqlite_batch",
                tool_params={
                    "sql": (
                        "WITH shaped AS (SELECT result_id, json_extract(result_json, '$.content') AS content "
                        "FROM __tool_results) SELECT * FROM shaped;"
                    )
                },
            ),
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._tool_calls_for_run", return_value=calls):
            passed = scenario._record_sqlite_usage(
                "run",
                after=None,
                task_name="verify_smart_sqlite_synthesis",
            )

        self.assertFalse(passed)
        self.assertIn("repeated failed sqlite attempts=2", recorded[-1][1]["observed_summary"])

    def test_sqlite_usage_rejects_order_by_as_only_smart_signal(self):
        scenario, recorded = SqliteToolResultScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        calls = [
            SimpleNamespace(
                step="step",
                status="complete",
                result='{"status":"ok"}',
                tool_name="sqlite_batch",
                tool_params={
                    "sql": "SELECT result_id, tool_name FROM __tool_results ORDER BY created_at DESC;"
                },
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._tool_calls_for_run", return_value=calls):
            passed = scenario._record_sqlite_usage(
                "run",
                after=None,
                task_name="verify_smart_sqlite_synthesis",
            )

        self.assertFalse(passed)
        self.assertIn("no substantive SQLite transform", recorded[-1][1]["observed_summary"])

    def test_web_sqlite_usage_rejects_raw_preview_as_only_transform(self):
        scenario, recorded = SqliteMultiResultWebSynthesisScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        calls = [
            SimpleNamespace(
                step="step",
                status="complete",
                result='{"status":"ok"}',
                tool_name="sqlite_batch",
                tool_params={
                    "sql": (
                        "SELECT result_id, source_url, substr(result_text, 1, 5000) AS head "
                        "FROM __tool_results ORDER BY result_id"
                    )
                },
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._tool_calls_for_run", return_value=calls):
            passed = scenario._record_sqlite_usage(
                "run",
                after=None,
                task_name="verify_smart_sqlite_synthesis",
            )

        self.assertFalse(passed)
        self.assertIn(
            "no smart aggregate query or reusable table",
            recorded[-1][1]["observed_summary"],
        )

    def test_web_sqlite_usage_rejects_repeated_unshaped_preview(self):
        scenario, recorded = SqliteMultiResultWebSynthesisScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        calls = [
            SimpleNamespace(
                step="step",
                status="complete",
                result='{"status":"warning"}',
                tool_name="sqlite_batch",
                tool_params={
                    "sql": (
                        "SELECT result_id, substr(result_text, 1, 30000) AS content FROM __tool_results; "
                        "SELECT result_id, substr(result_text, 1, 2000) AS head FROM __tool_results;"
                    )
                },
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._tool_calls_for_run", return_value=calls):
            passed = scenario._record_sqlite_usage(
                "run",
                after=None,
                task_name="verify_smart_sqlite_synthesis",
            )

        self.assertFalse(passed)
        observed = recorded[-1][1]["observed_summary"]
        self.assertIn("repeated unshaped multi-result payload projections=2", observed)

    def test_sqlite_dedupe_usage_allows_bounded_schema_probe(self):
        scenario, recorded = SqliteDedupeRequeryScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        calls = [
            SimpleNamespace(
                step="step",
                tool_name="sqlite_batch",
                tool_params={
                    "sql": """
                    SELECT substr(result_json, 1, 200) FROM __tool_results WHERE result_id='r1';
                    SELECT json_extract(result_json, '$.content.text') FROM __tool_results WHERE result_id='r1';
                    WITH claims AS (
                        SELECT result_id, json_extract(result_json, '$.content.text') AS claim
                        FROM __tool_results
                        WHERE result_id IN ('r1', 'r2', 'r3', 'r4')
                    )
                    SELECT claim, count(*) FROM claims GROUP BY claim ORDER BY count(*) DESC;
                    """
                },
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._tool_calls_for_run", return_value=calls):
            passed = scenario._record_sqlite_usage(
                "run",
                after=None,
                task_name="verify_dedupe_sqlite_usage",
                max_single_result_filters=scenario.max_single_result_filters,
            )
        self.assertTrue(passed, recorded[-1][1]["observed_summary"])

    def test_sqlite_working_table_usage_allows_bounded_shape_repair(self):
        scenario, recorded = SqliteIntermediateWorkingTableScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        calls = [
            SimpleNamespace(
                step="step",
                tool_name="sqlite_batch",
                tool_params={
                    "sql": """
                    SELECT result_id, substr(result_json, 1, 500) FROM __tool_results WHERE result_id='r1';
                    SELECT json_extract(result_json, '$.vendor') FROM __tool_results WHERE result_id='r1';
                    DROP TABLE IF EXISTS plan_candidates;
                    CREATE TABLE plan_candidates AS
                    SELECT json_extract(result_json, '$.vendor') AS vendor,
                           json_extract(p.value, '$.plan') AS plan
                    FROM __tool_results, json_each(result_json, '$.plans') AS p;
                    SELECT vendor, plan FROM plan_candidates ORDER BY vendor;
                    SELECT result_json FROM __tool_results WHERE result_id='r1';
                    """
                },
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._tool_calls_for_run", return_value=calls):
            passed = scenario._record_sqlite_usage(
                "run",
                after=None,
                task_name="verify_working_table_sqlite_usage",
                require_working_table=True,
                max_single_result_filters=scenario.max_single_result_filters,
            )
        self.assertTrue(passed, recorded[-1][1]["observed_summary"])

    def test_sqlite_working_set_usage_allows_one_smart_aggregate_query(self):
        scenario, recorded = SqliteIntermediateWorkingTableScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        calls = [
            SimpleNamespace(
                step="step",
                tool_name="sqlite_batch",
                tool_params={
                    "sql": """
                    WITH plan_candidates AS (
                        SELECT json_extract(result_json, '$.vendor') AS vendor,
                               json_extract(plan.value, '$.plan') AS plan,
                               json_extract(plan.value, '$.monthly_price_usd') AS monthly_price_usd
                        FROM __tool_results
                        JOIN json_each(result_json, '$.plans') AS plan
                        WHERE tool_name = 'http_request'
                    )
                    SELECT vendor, plan, monthly_price_usd
                    FROM plan_candidates
                    WHERE monthly_price_usd < 900
                    ORDER BY monthly_price_usd;
                    """
                },
            )
        ]
        with patch("api.evals.scenarios.sqlite_tool_results._tool_calls_for_run", return_value=calls):
            passed = scenario._record_sqlite_usage(
                "run",
                after=None,
                task_name="verify_working_table_sqlite_usage",
                require_working_table=True,
                max_single_result_filters=scenario.max_single_result_filters,
            )

        self.assertTrue(passed, recorded[-1][1]["observed_summary"])

    def test_effort_no_question_check_rejects_progress_only_message(self):
        scenario, recorded = EffortCalibrationScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        messages = [SimpleNamespace(body="Good, I have the search results identifying YC Winter 2026 as the latest batch.")]
        with (
            patch("api.evals.scenarios.effort_calibration._human_input_requests_for_run", return_value=[]),
            patch("api.evals.scenarios.effort_calibration._outbound_messages_after", return_value=messages),
        ):
            passed = scenario._record_no_question_battery(
                "run",
                agent_id="agent",
                after=None,
                task_name="verify_no_question_or_progress_message",
                max_message_questions=0,
            )
        self.assertFalse(passed)
        self.assertIn("progress_messages=1", recorded[-1][1]["observed_summary"])

    def test_question_count_ignores_source_url_query_strings(self):
        self.assertEqual(
            _question_count("Sources: https://www.ycombinator.com/companies?batch=Winter%202026"),
            0,
        )
        self.assertEqual(
            _question_count("Source: www.ycombinator.com/companies?batch=Winter%202026"),
            0,
        )
        self.assertEqual(
            _question_count(
                "Source: [ycombinator.com/companies?batch=Winter%202026]"
                "(https://www.ycombinator.com/companies?batch=Winter%202026)"
            ),
            0,
        )

    def test_chart_tool_description_requires_request_or_material_need(self):
        description = get_create_chart_tool()["function"]["description"]

        self.assertIn("requested or materially useful chart", description)
        self.assertIn("paste returned inline/inline_html", description)

    def test_plan_tool_description_excludes_simple_one_shot_work(self):
        description = get_update_plan_tool()["function"]["description"]

        self.assertIn("substantial multi-step work", description)
        self.assertIn("Do not use for quick answers", description)
        self.assertIn("progress narration", description)

    def test_defaultable_research_treats_update_plan_as_overwork(self):
        scenario = EffortDefaultableResearchNoQuestionBatteryScenario()
        policy = scenario._eval_stop_policy()
        update_plan_call = SimpleNamespace(tool_name="update_plan")

        self.assertIn("update_plan", policy["stop_on_tool_names"])
        self.assertNotIn("update_plan", policy["ignored_tool_names"])
        with patch(
            "api.evals.scenarios.effort_calibration._relevant_tool_calls_for_run",
            return_value=[update_plan_call],
        ):
            self.assertEqual(
                scenario._research_calls_for_scoring("run", after=None),
                [update_plan_call],
            )

    def test_plan_discipline_excludes_bounded_current_research(self):
        class NoPeerLinks:
            def filter(self, *_args, **_kwargs):
                return self

            def exists(self):
                return False

        agent = SimpleNamespace(
            id="effort-plan-discipline-agent",
            planning_state=PersistentAgent.PlanningState.SKIPPED,
            organization_id=None,
        )
        with patch("api.agent.core.prompt_context.AgentPeerLink.objects.filter", return_value=NoPeerLinks()), patch(
            "api.agent.core.prompt_context.CommsAllowlistEntry.objects.filter",
            return_value=NoPeerLinks(),
        ):
            instructions = _get_system_instruction(agent)

        self.assertIn("Use `update_plan` only for substantial multi-step work", instructions)
        self.assertIn("Keep it current", instructions)
        self.assertIn("deliver the result before marking it complete", instructions)

    def test_contact_permission_description_defers_setup_only_future_sends(self):
        description = get_request_contact_permission_tool()["function"]["description"]

        self.assertIn("For future recurring delivery", description)
        self.assertIn("wait until a send is due", description)

    def test_human_input_description_excludes_category_choice_surveys(self):
        description = get_request_human_input_tool()["function"]["description"]

        self.assertIn("material decision or blocker", description)
        self.assertIn("Default reversible preferences instead of surveying", description)
        self.assertIn("at most three questions", description)

    def test_linkedin_jobs_synthetic_tool_accepts_category_queries(self):
        description = EVAL_SYNTHETIC_TOOL_DEFINITIONS["mcp_brightdata_web_data_linkedin_job_listings"][
            "description"
        ]

        self.assertIn("category query", description)
        self.assertIn("representative category such as a fintech company", description)
        self.assertIn("instead of asking which company", description)

    def test_batch_work_synthetic_tools_surface_remaining_work_guidance(self):
        outreach_description = EVAL_SYNTHETIC_TOOL_DEFINITIONS["eval_send_outreach_batch"]["description"]
        schedule_description = EVAL_SYNTHETIC_TOOL_DEFINITIONS["eval_prepare_next_batch"]["description"]
        batch_parameters = EVAL_SYNTHETIC_TOOL_DEFINITIONS["eval_send_outreach_batch"]["parameters"]

        self.assertIn("remaining_work", outreach_description)
        self.assertIn("set a resume schedule", outreach_description)
        self.assertIn("only makes sense when a schedule exists", schedule_description)
        self.assertEqual(set(batch_parameters["properties"]), {"batch_size", "limit"})
        self.assertFalse(batch_parameters["additionalProperties"])

    def test_fresh_full_tool_result_wrapper_discourages_redundant_sqlite_rereads(self):
        wrapped = _wrap_as_sqlite_result('{"answer": "ready"}', 19)

        self.assertIn("Use it now", wrapped)
        self.assertIn("do not re-fetch or verify it unless ambiguous", wrapped)
        self.assertIn("Transform or persist it only when the task requires", wrapped)

    def test_sqlite_prompt_rejects_fake_external_actions_and_discovery_reads(self):
        prompt = _get_sqlite_examples()

        self.assertIn("Trust current context", prompt)
        self.assertIn("before an idempotent `CREATE`/`UPDATE`", prompt)
        self.assertIn("a `SELECT`, comment, or row never performs or proves an external action", prompt)
        self.assertIn("do not query `__messages` to verify a send you have not made", prompt)


@tag("eval_sim")
class EvalStopPolicyBudgetTests(TestCase):
    def test_relevant_tool_call_budget_ignores_config_bookkeeping_reads(self):
        User = get_user_model()
        user = User.objects.create_user(username="eval_budget_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Eval Budget Browser")
        agent = PersistentAgent.objects.create(
            name="Eval Budget Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )
        run = EvalRun.objects.create(
            scenario_slug="effort_test",
            scenario_version="1.0.0",
            agent=agent,
            initiated_by=user,
        )

        bookkeeping_step = PersistentAgentStep.objects.create(agent=agent, eval_run=run)
        PersistentAgentToolCall.objects.create(
            step=bookkeeping_step,
            tool_name="sqlite_batch",
            tool_params={"sql": "SELECT charter, schedule FROM __agent_config WHERE id = 1;"},
            result='{"status":"ok"}',
        )
        relevant_step = PersistentAgentStep.objects.create(agent=agent, eval_run=run)
        PersistentAgentToolCall.objects.create(
            step=relevant_step,
            tool_name="http_request",
            tool_params={"url": "https://example.test/data.json"},
            result='{"status":"ok"}',
        )

        should_stop, reason = should_stop_for_eval_policy(
            str(run.id),
            {"max_relevant_tool_calls": 1},
        )

        self.assertTrue(should_stop)
        self.assertIn("relevant tool call budget reached: 1/1", reason)

    def test_relevant_tool_call_budget_does_not_stop_pending_call_before_execution(self):
        User = get_user_model()
        user = User.objects.create_user(username="eval_pending_budget_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Pending Budget Browser")
        agent = PersistentAgent.objects.create(
            name="Pending Budget Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )
        run = EvalRun.objects.create(
            scenario_slug="pending_budget_test",
            scenario_version="1.0.0",
            agent=agent,
            initiated_by=user,
        )
        step = PersistentAgentStep.objects.create(agent=agent, eval_run=run)
        call = PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="request_human_input",
            tool_params={"question": "Which client?", "will_continue_work": False},
            result="",
            status="pending",
        )

        should_stop, reason = should_stop_for_eval_policy(
            str(run.id),
            {"max_relevant_tool_calls": 1},
        )

        self.assertFalse(should_stop)
        self.assertEqual(reason, "")

        call.status = "complete"
        call.result = '{"status":"ok"}'
        call.save(update_fields=["status", "result"])
        should_stop, reason = should_stop_for_eval_policy(
            str(run.id),
            {"max_relevant_tool_calls": 1},
        )

        self.assertTrue(should_stop)
        self.assertIn("relevant tool call budget reached: 1/1", reason)

    def test_partial_source_policy_and_scorer_allow_plan_before_candidate_batch(self):
        User = get_user_model()
        user = User.objects.create_user(username="partial_source_policy_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Partial Source Policy Browser")
        agent = PersistentAgent.objects.create(
            name="Partial Source Policy Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )
        run = EvalRun.objects.create(
            scenario_slug=EFFORT_PARTIAL_SOURCE_BLOCK_REPORTS_AND_RESUMES,
            scenario_version="1.0.0",
            agent=agent,
            initiated_by=user,
        )
        step = PersistentAgentStep.objects.create(agent=agent, eval_run=run)
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="update_plan",
            tool_params={"plan": [{"step": "Verify batch", "status": "doing"}], "will_continue_work": True},
            result="",
            status="pending",
        )

        should_stop, reason = should_stop_for_eval_policy(
            str(run.id),
            {
                "stop_on_tool_names": list(PARTIAL_SOURCE_FORBIDDEN_TOOL_NAMES),
                "stop_on_unexpected_relevant_tool": True,
                "allowed_tool_names": ["eval_verify_candidate_batch", "sqlite_batch", "update_plan"],
                "max_relevant_tool_calls": 6,
            },
        )

        self.assertFalse(should_stop)
        EvalRunTask.objects.create(run=run, name="verify_no_overwork_tools", sequence=1)
        self.assertTrue(
            EffortCalibrationScenario()._record_no_overwork_tools(
                str(run.id),
                after=agent.created_at,
                task_name="verify_no_overwork_tools",
                forbidden_tool_names=PARTIAL_SOURCE_FORBIDDEN_TOOL_NAMES,
            )
        )
        self.assertEqual(reason, "")


@tag("eval_sim")
class EffortCalibrationHarnessTests(TestCase):
    def test_ready_agent_seeds_completed_process_run(self):
        User = get_user_model()
        user = User.objects.create_user(username="effort_ready_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Effort Ready Browser")
        agent = PersistentAgent.objects.create(
            name="Effort Ready Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        EffortTrivialAnswerStopsScenario()._ready_agent(str(agent.id))

        self.assertEqual(_get_completed_process_run_count(agent), 1)

    def test_send_chat_rejects_schema_placeholder_body(self):
        User = get_user_model()
        user = User.objects.create_user(username="placeholder_chat_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Placeholder Chat Browser")
        agent = PersistentAgent.objects.create(
            name="Placeholder Chat Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        result = execute_send_chat_message(agent, {"body": "body", "will_continue_work": False})

        self.assertEqual(result["status"], "error")
        self.assertIn("schema placeholder", result["message"])
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=True).exists())

    def test_send_chat_rejects_raw_tool_call_markup_body(self):
        User = get_user_model()
        user = User.objects.create_user(username="tool_markup_chat_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Tool Markup Chat Browser")
        agent = PersistentAgent.objects.create(
            name="Tool Markup Chat Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        result = execute_send_chat_message(
            agent,
            {
                "body": (
                    '<function><invoke name="http_request"><parameter name="url">'
                    "https://api.example.test/data.json</parameter></invoke></function>"
                ),
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("raw tool-call markup", result["message"])
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=True).exists())

    def test_future_work_preserved_accepts_resume_schedule(self):
        User = get_user_model()
        user = User.objects.create_user(username="future_work_schedule_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Future Work Browser")
        agent = PersistentAgent.objects.create(
            name="Future Work Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
            schedule="0 9 * * *",
        )
        run = EvalRun.objects.create(
            scenario_slug="future_work_test",
            scenario_version="1.0.0",
            agent=agent,
            initiated_by=user,
        )
        EvalRunTask.objects.create(run=run, name="verify_future_work_preserved", sequence=1)

        passed = EffortCalibrationScenario()._record_future_work_preserved(
            str(run.id),
            agent_id=str(agent.id),
            after=agent.created_at,
            task_name="verify_future_work_preserved",
            work_tool_names={"eval_send_outreach_batch"},
        )

        self.assertTrue(passed)
        self.assertEqual(
            run.tasks.get(name="verify_future_work_preserved").status,
            EvalRunTask.Status.PASSED,
        )

    def test_future_work_preserved_accepts_semantic_sqlite_resume_state(self):
        User = get_user_model()
        user = User.objects.create_user(username="future_work_sqlite_resume_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Future Work SQLite Resume Browser")
        agent = PersistentAgent.objects.create(
            name="Future Work SQLite Resume Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
            schedule="",
        )
        run = EvalRun.objects.create(
            scenario_slug="future_work_sqlite_resume_test",
            scenario_version="1.0.0",
            agent=agent,
            initiated_by=user,
        )
        EvalRunTask.objects.create(run=run, name="verify_future_work_preserved", sequence=1)
        work_step = PersistentAgentStep.objects.create(agent=agent, eval_run=run)
        PersistentAgentToolCall.objects.create(
            step=work_step,
            tool_name="eval_verify_candidate_batch",
            tool_params={},
            result='{"status":"partial","remaining_work":12,"next_cursor":"candidate-offset-3"}',
        )
        sqlite_step = PersistentAgentStep.objects.create(agent=agent, eval_run=run)
        PersistentAgentToolCall.objects.create(
            step=sqlite_step,
            tool_name="sqlite_batch",
            tool_params={
                "sql": (
                    "CREATE TABLE IF NOT EXISTS candidate_verification_state "
                    "(next_cursor TEXT, remaining INTEGER); "
                    "INSERT INTO candidate_verification_state (next_cursor, remaining) "
                    "VALUES ('candidate-offset-3', 12);"
                )
            },
            result='{"status":"ok"}',
        )

        passed = EffortCalibrationScenario()._record_future_work_preserved(
            str(run.id),
            agent_id=str(agent.id),
            after=agent.created_at,
            task_name="verify_future_work_preserved",
            work_tool_names={"eval_verify_candidate_batch"},
        )

        self.assertTrue(passed)
        self.assertEqual(
            run.tasks.get(name="verify_future_work_preserved").status,
            EvalRunTask.Status.PASSED,
        )

    def test_future_work_preserved_rejects_resume_state_in_charter(self):
        User = get_user_model()
        user = User.objects.create_user(username="future_work_charter_resume_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Future Work Charter Resume Browser")
        agent = PersistentAgent.objects.create(
            name="Future Work Charter Resume Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
            schedule="",
        )
        run = EvalRun.objects.create(
            scenario_slug="future_work_charter_resume_test",
            scenario_version="1.0.0",
            agent=agent,
            initiated_by=user,
        )
        EvalRunTask.objects.create(run=run, name="verify_future_work_preserved", sequence=1)
        work_step = PersistentAgentStep.objects.create(agent=agent, eval_run=run)
        PersistentAgentToolCall.objects.create(
            step=work_step,
            tool_name="eval_verify_candidate_batch",
            tool_params={},
            result='{"status":"partial","remaining_work":12,"next_cursor":"candidate-offset-3"}',
        )
        sqlite_step = PersistentAgentStep.objects.create(agent=agent, eval_run=run)
        PersistentAgentToolCall.objects.create(
            step=sqlite_step,
            tool_name="sqlite_batch",
            tool_params={
                "sql": (
                    "UPDATE __agent_config SET charter = "
                    "'remaining_work=12; next_cursor=candidate-offset-3' WHERE id=1;"
                )
            },
            result='{"status":"ok"}',
        )

        passed = EffortCalibrationScenario()._record_future_work_preserved(
            str(run.id),
            agent_id=str(agent.id),
            after=agent.created_at,
            task_name="verify_future_work_preserved",
            work_tool_names={"eval_verify_candidate_batch"},
        )

        self.assertFalse(passed)
        task = run.tasks.get(name="verify_future_work_preserved")
        self.assertEqual(task.status, EvalRunTask.Status.FAILED)
        self.assertIn("__agent_config/charter does not count", task.observed_summary)

    def test_future_work_preserved_rejects_single_unscheduled_batch(self):
        User = get_user_model()
        user = User.objects.create_user(username="future_work_missing_schedule_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Future Work Missing Browser")
        agent = PersistentAgent.objects.create(
            name="Future Work Missing Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
            schedule="",
        )
        run = EvalRun.objects.create(
            scenario_slug="future_work_missing_test",
            scenario_version="1.0.0",
            agent=agent,
            initiated_by=user,
        )
        EvalRunTask.objects.create(run=run, name="verify_future_work_preserved", sequence=1)
        step = PersistentAgentStep.objects.create(agent=agent, eval_run=run)
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="eval_send_outreach_batch",
            tool_params={"batch_size": 4, "will_continue_work": True},
            result='{"status":"ok","remaining_work":999}',
        )

        passed = EffortCalibrationScenario()._record_future_work_preserved(
            str(run.id),
            agent_id=str(agent.id),
            after=agent.created_at,
            task_name="verify_future_work_preserved",
            work_tool_names={"eval_send_outreach_batch"},
        )

        self.assertFalse(passed)
        task = run.tasks.get(name="verify_future_work_preserved")
        self.assertEqual(task.status, EvalRunTask.Status.FAILED)
        self.assertIn("dedicated SQLite resume table", task.observed_summary)

    def test_send_chat_rejects_leaked_thinking_tag(self):
        User = get_user_model()
        user = User.objects.create_user(username="thinking_tag_chat_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Thinking Tag Chat Browser")
        agent = PersistentAgent.objects.create(
            name="Thinking Tag Chat Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        result = execute_send_chat_message(agent, {"body": "<endor_thinking>", "will_continue_work": True})

        self.assertEqual(result["status"], "error")
        self.assertIn("raw tool-call markup", result["message"])
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=True).exists())

    def test_eval_send_chat_skips_in_progress_message_structurally(self):
        User = get_user_model()
        user = User.objects.create_user(username="eval_in_progress_chat_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Eval In Progress Chat Browser")
        agent = PersistentAgent.objects.create(
            name="Eval In Progress Chat Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        result = execute_send_chat_message(
            agent,
            {
                "body": (
                    "I have all 5 matching vehicles from both feeds. "
                    "Let me compute batch-level comparisons and send the report."
                ),
                "will_continue_work": True,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["skipped"])
        self.assertIn("eval in-progress", result["message"])
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=True).exists())

    def test_send_chat_skips_progress_only_message_before_any_reply(self):
        User = get_user_model()
        user = User.objects.create_user(username="progress_only_chat_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Progress Only Chat Browser")
        agent = PersistentAgent.objects.create(
            name="Progress Only Chat Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        result = execute_send_chat_message(
            agent,
            {
                "body": "Got what I need from the search - let me also grab the full profile for any extra detail.",
                "will_continue_work": True,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["skipped"])
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=True).exists())

    def test_send_chat_skips_optional_progress_question(self):
        User = get_user_model()
        user = User.objects.create_user(username="optional_progress_chat_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Optional Progress Chat Browser")
        agent = PersistentAgent.objects.create(
            name="Optional Progress Chat Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        result = execute_send_chat_message(
            agent,
            {
                "body": (
                    "I'll get the RSS feed parsed and the schedule wired up now. "
                    "Any tweaks before I lock this in? Otherwise I'm off and running!"
                ),
                "will_continue_work": True,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["skipped"])
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=True).exists())

    def test_send_chat_skips_deep_research_progress_before_final(self):
        User = get_user_model()
        user = User.objects.create_user(username="deep_progress_chat_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Deep Progress Chat Browser")
        agent = PersistentAgent.objects.create(
            name="Deep Progress Chat Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        result = execute_send_chat_message(
            agent,
            {
                "body": (
                    "I now have detailed data from all 8 source pages. "
                    "Let me mark the research steps done and deliver the synthesized memo."
                ),
                "will_continue_work": True,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["skipped"])
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=True).exists())

    def test_send_chat_skips_tool_recovery_progress_before_final(self):
        User = get_user_model()
        user = User.objects.create_user(username="recovery_progress_chat_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Recovery Progress Chat Browser")
        agent = PersistentAgent.objects.create(
            name="Recovery Progress Chat Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        result = execute_send_chat_message(
            agent,
            {
                "body": (
                    "The earlier queries with CTE + json_extract kept hitting a spurious error. "
                    "Let me extract the markdown content directly and build the comparison data."
                ),
                "will_continue_work": True,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["skipped"])
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=True).exists())

    def test_send_chat_skips_extra_deep_research_progress_before_final(self):
        User = get_user_model()
        user = User.objects.create_user(username="extra_deep_progress_chat_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Extra Deep Progress Chat Browser")
        agent = PersistentAgent.objects.create(
            name="Extra Deep Progress Chat Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        result = execute_send_chat_message(
            agent,
            {
                "body": (
                    "Good data gathered on Northstar and four competitors plus market context. "
                    "Let me do a couple more targeted searches to strengthen the competitive analysis, "
                    "then synthesize the full memo."
                ),
                "will_continue_work": True,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["skipped"])
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=True).exists())

    def test_send_chat_strips_trailing_optional_followup_from_final_answer(self):
        User = get_user_model()
        user = User.objects.create_user(username="optional_followup_final_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Optional Followup Final Browser")
        agent = PersistentAgent.objects.create(
            name="Optional Followup Final Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        result = execute_send_chat_message(
            agent,
            {
                "body": (
                    "## Bitcoin Price\n\n"
                    "**$68,500.50 USD**\n\n"
                    "> Markets move fast though—want me to keep an eye on it for you? 😊"
                ),
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "ok")
        message = PersistentAgentMessage.objects.get(owner_agent=agent, is_outbound=True)
        self.assertEqual(message.body, "## Bitcoin Price\n\n**$68,500.50 USD**\n\n> Markets move fast though")

    def test_request_human_input_rejects_large_preference_survey_outside_planning(self):
        agent = SimpleNamespace(planning_state=PersistentAgent.PlanningState.SKIPPED)

        result = execute_request_human_input(
            agent,
            {
                "question": "Which fintech company should I use?",
                "options": [
                    {"title": "Stripe", "description": "Payments infrastructure"},
                    {"title": "Plaid", "description": "Financial data APIs"},
                    {"title": "Chime", "description": "Consumer digital banking"},
                    {"title": "Affirm", "description": "Buy now, pay later"},
                ],
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("not preference surveys", result["message"])
        self.assertIn("choose a reasonable default", result["message"])


@tag("eval_sim")
class FirstRunPromptCalibrationTests(TestCase):
    def test_first_run_prompt_does_not_force_progress_greeting_or_default_schedule(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="first-run-effort@example.com",
            email="first-run-effort@example.com",
        )
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        browser_agent = BrowserUseAgent.objects.create(user=user, name="First Run Effort Browser")
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address="first-run-effort-web",
        )
        agent = PersistentAgent.objects.create(
            name="First Run Effort Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Answer directly.",
            preferred_contact_endpoint=endpoint,
        )

        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ), patch(
            "api.agent.core.prompt_context.get_llm_config_with_failover",
            return_value=[("endpoint", "openai/gpt-4o-mini", {})],
        ):
            context, _, _ = build_prompt_context_preview(agent, is_first_run=True)

        system_prompt = next(message["content"] for message in context if message["role"] == "system")
        self.assertIn("## Durable role, preferences, and schedule", system_prompt)
        self.assertIn("## Plans and completion", system_prompt)
        self.assertIn("leave one-offs unscheduled", system_prompt.lower())
        self.assertIn("Report preserved partial work and stop", system_prompt)
        self.assertNotIn("Before ANY tool calls", system_prompt)
        self.assertNotIn("Greeting comes first, always", system_prompt)
        self.assertNotIn("Schedule: When in doubt, set one", system_prompt)
        self.assertNotIn("Without a schedule, you die", system_prompt)
        self.assertNotIn("agent.Use", system_prompt)
        self.assertNotIn("output.Note", system_prompt)

    def test_planning_mode_prompt_ends_clear_feed_setup_before_execution(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="planning-feed-effort@example.com",
            email="planning-feed-effort@example.com",
        )
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Planning Feed Effort Browser")
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address="planning-feed-effort-web",
        )
        agent = PersistentAgent.objects.create(
            name="Planning Feed Effort Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Plan before executing.",
            planning_state=PersistentAgent.PlanningState.PLANNING,
            preferred_contact_endpoint=endpoint,
        )

        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ), patch(
            "api.agent.core.prompt_context.get_llm_config_with_failover",
            return_value=[("endpoint", "openai/gpt-4o-mini", {})],
        ):
            context, _, _ = build_prompt_context_preview(agent, is_first_run=True)

        system_prompt = next(message["content"] for message in context if message["role"] == "system")
        self.assertIn("If the request is already clear, call `end_planning", system_prompt)
        self.assertIn("Do not execute the task", system_prompt)
        self.assertIn("Do not inspect feeds, files, APIs, or task data", system_prompt)
        self.assertNotIn("## Effort and tool choice", system_prompt)
        self.assertNotIn("<sqlite_contract>", system_prompt)

    def test_system_prompt_has_delivery_and_config_guardrails(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="delivery-guardrails@example.com",
            email="delivery-guardrails@example.com",
        )
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Delivery Guardrails Browser")
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address="delivery-guardrails-web",
        )
        agent = PersistentAgent.objects.create(
            name="Delivery Guardrails Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Do one-off work carefully.",
            planning_state=PersistentAgent.PlanningState.SKIPPED,
            preferred_contact_endpoint=endpoint,
        )

        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ), patch(
            "api.agent.core.prompt_context.get_llm_config_with_failover",
            return_value=[("endpoint", "openai/gpt-4o-mini", {})],
        ):
            context, _, _ = build_prompt_context_preview(agent, is_first_run=False)

        system_prompt = next(message["content"] for message in context if message["role"] == "system")
        prompt_text = "\n".join(message["content"] for message in context)
        self.assertIn("Honor the requested channel", system_prompt)
        self.assertIn("never future scheduled work", system_prompt)
        self.assertIn("schedules are not continuation bookmarks", system_prompt)
        self.assertIn("also use SQLite for scale, reuse, joins", system_prompt)
        self.assertIn("No N+1", system_prompt)
        self.assertIn("Use enabled tools", system_prompt)
        self.assertIn("Use supplied URLs directly", system_prompt)
        self.assertIn("reserve browsers for interactive, authenticated, rendered", system_prompt)
        self.assertIn("Create charts only when requested or materially clarifying", system_prompt)
        self.assertIn("immediately persist plain feedback, subtle corrections, and stable preferences", system_prompt)
        self.assertIn("new or fully rewritten charters 1-4 plain sentences under 600 characters", system_prompt)
        self.assertIn("patching only affected charter text", system_prompt)
        self.assertIn("do not search or reply first", system_prompt)
        self.assertIn("one pre-question `sqlite_batch` must set a concise charter and nonempty schedule", system_prompt)
        self.assertIn("ordinary scheduled runs preserve both", system_prompt)
        self.assertIn("Default cadence, target, and active delivery channel", system_prompt)
        self.assertIn("for DST-safe local time", system_prompt)
        self.assertIn("mutate nonempty charters in place", system_prompt)
        self.assertIn("Preserve unrelated config/clauses", system_prompt)
        self.assertIn("color, badges", system_prompt)
        self.assertIn("tasteful emoji/status labels", system_prompt)
        self.assertIn("use `charter || ...` to add or `replace(charter, 'exact old clause', 'new clause')` to correct", prompt_text)
        self.assertIn("never assign a full literal", prompt_text)
        self.assertIn("preserves every unrequested sentence verbatim", prompt_text)
        self.assertIn("even if a legacy charter is longer", prompt_text)
        self.assertIn("A config-only edit needs no tool discovery or role execution", prompt_text)
        self.assertIn("one update must set both charter and schedule before questions", prompt_text)
