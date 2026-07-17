from types import SimpleNamespace
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.event_processing import _get_completed_process_run_count
from api.agent.core.prompt_context import _get_system_instruction, build_prompt_context_preview
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
    EffortCalibrationScenario,
    EffortTrivialAnswerStopsScenario,
    _find_near_duplicate_texts,
    _hierarchical_report_shape,
    _question_count,
    _sqlite_result_text_reads,
    _web_query_value,
)
from api.evals.scenarios.monitor_pollution import BACKGROUND_DRAIN_TIMEOUT_SECONDS
from api.evals.scenarios.sqlite_tool_results import (
    INVENTORY_URLS,
    LISTING_URLS,
    SQLITE_ITEM_LINK_REPORT,
    SQLITE_TOOL_RESULT_SCENARIO_SLUGS,
    SQLITE_TOOL_RESULT_SUITE_SLUG,
    SqliteDedupeRequeryScenario,
    SqliteIntermediateWorkingTableScenario,
    SqliteItemLinkReportScenario,
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

    def test_dedupe_requery_answer_assertion_does_not_force_specific_claim_category(self):
        self.assertEqual(SqliteDedupeRequeryScenario.required_terms, ())

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
                    SELECT json_extract(result_json, '$.content.vendor') FROM __tool_results WHERE result_id='r1';
                    DROP TABLE IF EXISTS plan_candidates;
                    CREATE TABLE plan_candidates AS
                    SELECT json_extract(result_json, '$.content.vendor') AS vendor,
                           json_extract(p.value, '$.plan') AS plan
                    FROM __tool_results, json_each(result_json, '$.content.plans') AS p;
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

    def test_deep_work_update_check_requires_useful_kickoff_and_milestone(self):
        scenario, recorded = EffortCalibrationScenario(), []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        calls = [
            SimpleNamespace(
                tool_name="send_chat_message",
                tool_params={
                    "body": (
                        "I’m digging into Northstar’s position now. I’ll pressure test the market angle before "
                        "I give you a recommendation."
                    ),
                    "will_continue_work": True,
                },
                step="kickoff",
            ),
            SimpleNamespace(tool_name="mcp_brightdata_search_engine", tool_params={}, step="search"),
            SimpleNamespace(tool_name="mcp_brightdata_scrape_as_markdown", tool_params={}, step="source-one"),
            SimpleNamespace(tool_name="mcp_brightdata_scrape_as_markdown", tool_params={}, step="source-two"),
            SimpleNamespace(
                tool_name="send_chat_message",
                tool_params={
                    "body": (
                        "The brownfield wedge is holding up, and interoperability looks like the real buying "
                        "driver. I’m checking whether the customer proof supports that thesis."
                    ),
                    "will_continue_work": True,
                },
                step="milestone",
            ),
            SimpleNamespace(tool_name="mcp_brightdata_scrape_as_markdown", tool_params={}, step="source-three"),
        ]
        with patch("api.evals.scenarios.effort_calibration._tool_calls_for_run", return_value=calls):
            passed = scenario._record_deep_work_updates(
                "run",
                after=None,
                task_name="verify_deep_work_updates",
                work_tool_names={"mcp_brightdata_search_engine", "mcp_brightdata_scrape_as_markdown"},
                update_tool_name="send_chat_message",
            )

        self.assertTrue(passed, recorded[-1][1]["observed_summary"])
        self.assertIn("one before work and one after a material phase", recorded[-1][1]["observed_summary"])

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

        self.assertIn("when the user requests a chart or a visual is materially necessary", description)
        self.assertIn("Do not use this for routine summaries just because numbers are present", description)

    def test_plan_tool_description_excludes_simple_one_shot_work(self):
        description = get_update_plan_tool()["function"]["description"]

        self.assertIn("real multi-step work", description)
        self.assertIn("Do not use for quick lookups", description)
        self.assertIn("one-shot chart requests", description)

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
        self.assertIn("where a visible plan helps", instructions)
        self.assertIn("Keep plans short, current, and verifiable", instructions)
        self.assertIn("each call replaces the full active plan", instructions)
        self.assertIn("Send the final user-facing report before any final completion update", instructions)

    def test_contact_permission_description_defers_setup_only_future_sends(self):
        description = get_request_contact_permission_tool()["function"]["description"]

        self.assertIn("do not request contact permission during setup", description)
        self.assertIn("when an actual outbound send is needed", description)

    def test_human_input_description_excludes_category_choice_surveys(self):
        description = get_request_human_input_tool()["function"]["description"]

        self.assertIn("category example choices", description)
        self.assertIn("which vendor/company", description)
        self.assertIn("choose and disclose", description)
        self.assertIn("explicitly asks you to ask for targets/scope before setup", description)
        self.assertIn("missing targets/scope block a recurring monitor", description)

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

        self.assertIn("remaining_work", outreach_description)
        self.assertIn("set a resume schedule", outreach_description)
        self.assertIn("only makes sense when a schedule exists", schedule_description)

    def test_fresh_full_tool_result_wrapper_discourages_redundant_sqlite_rereads(self):
        wrapped = _wrap_as_sqlite_result('{"answer": "ready"}', 19)

        self.assertIn("reply directly in the next message", wrapped)
        self.assertIn("Do not query __tool_results or sqlite_batch just to reread", wrapped)
        self.assertIn("use SQL only for real filtering", wrapped)


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

    def test_partial_source_policy_allows_plan_before_candidate_batch(self):
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
                "stop_on_tool_names": list(
                    (EFFORT_OVERWORK_TOOL_NAMES - {"update_plan"})
                    | ARTIFACT_TOOL_NAMES
                    | RESEARCH_TOOL_NAMES
                ),
                "stop_on_unexpected_relevant_tool": True,
                "allowed_tool_names": ["eval_verify_candidate_batch", "sqlite_batch", "update_plan"],
                "max_relevant_tool_calls": 6,
            },
        )

        self.assertFalse(should_stop)
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

    def test_future_work_preserved_accepts_persisted_sqlite_resume_state(self):
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
                    "(next_cursor TEXT, remaining_work INTEGER); "
                    "INSERT INTO candidate_verification_state (next_cursor, remaining_work) "
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
        self.assertIn("schedule is empty", task.observed_summary)

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
        self.assertIn("do not repeat it", result["message"])
        self.assertIn("deliver the substantive reply in this web chat", result["message"])
        self.assertIn("do not switch to email or SMS", result["message"])
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
        self.assertIn("## Configuration Discipline (CRITICAL)", system_prompt)
        self.assertIn("## Plan Discipline (CRITICAL)", system_prompt)
        self.assertNotIn("Before ANY tool calls", system_prompt)
        self.assertNotIn("Greeting comes first, always", system_prompt)
        self.assertNotIn("Schedule: When in doubt, set one", system_prompt)
        self.assertNotIn("Without a schedule, you die", system_prompt)

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
        self.assertIn("For clear requests other than named integration setup/use", system_prompt)
        self.assertIn("including one-off factual/research questions", system_prompt)
        self.assertIn("Do not validate, fetch, parse, or test provided URLs", system_prompt)
        self.assertIn("call end_planning in the same response as any welcome", system_prompt)
        self.assertIn("Do not say you will check, validate, test, fetch, or inspect a provided feed", system_prompt)

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
        self.assertIn("Use the requested channel; otherwise reply on the latest inbound channel", system_prompt)
        self.assertIn("A skipped web send never permits switching", system_prompt)
        self.assertIn("Set false after delivery/config and no active work", system_prompt)
        self.assertIn("Do not set a schedule merely to continue or remember a single research question", system_prompt)
        self.assertIn("explicit SQLite/database request and sqlite_batch is callable", system_prompt)
        self.assertIn("do not search for a SQLite/database tool", system_prompt)
        self.assertIn("enabled tool fits -> use directly", system_prompt)
        self.assertIn("public exact URL + http/scrape tool callable", system_prompt)
        self.assertIn("spawn_web_task only after access/render/login blockage", system_prompt)
        self.assertIn("exact docs/blog/changelog/release-notes URL", system_prompt)
        self.assertIn("Charts: create only when requested/materially useful", system_prompt)
        self.assertIn("Finished answers/briefings/charts/lookups/one-off research are not charter changes", system_prompt)
        self.assertIn("Email/SMS imperatives map directly to send_email/send_sms", system_prompt)
        self.assertIn("Do not downgrade requested email/SMS delivery to chat", system_prompt)
