import json
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.behavior_micro import (
    BEHAVIOR_MICRO_SCENARIO_SLUGS,
    CHARTER_MEMORY_MICRO_SCENARIO_SLUGS,
    CHARTER_PATCHES_EXPLICIT_AMENDMENT_UNDER_PRESSURE,
    CHARTER_PATCHES_AND_COMPLETES_IMMEDIATE_TASK,
    CHARTER_CORRECTION_GOVERNS_NEXT_PEER_ASSIGNMENT,
    CHARTER_REFINES_EXISTING_GUIDANCE_FROM_NATURAL_FEEDBACK,
    COMMON_USE_CASE_EVAL_CASES,
    GUIDED_PLANNING_BOUNDED_WHEN_REQUESTED,
    GUIDED_PLANNING_MICRO_SCENARIO_SLUGS,
    LEGACY_PLANNING_STATE_EXECUTES_DIRECTLY,
    TOOL_CHOICE_MICRO_SCENARIO_SLUGS,
    _successful_call,
    _uses_one_focused_charter_patch,
)
from api.evals.suites import SuiteRegistry


APOLLO_CONNECT_SEARCH = "common_use_case_136_apollo_connect_tool_search"
SLACK_CONNECT_SEARCH = "common_use_case_137_slack_connect_tool_search"
SQLITE_EXPORT_QUERY_CSV = "common_use_case_086_sqlite_export_query_csv"
MONITORING_SCOPE_QUESTION = "common_use_case_099_request_monitoring_scope"
SMALL_SOURCING_DELIVERS_BEFORE_AUTOMATION = (
    "common_use_case_140_small_sourcing_delivers_before_automation"
)


@tag("eval_sim")
class BehaviorMicroScenarioTests(SimpleTestCase):
    def test_successful_call_ignores_correctable_tool_retries(self):
        failed_attempt = SimpleNamespace(
            status="error",
            result=json.dumps({"status": "error", "retryable": True}),
        )
        successful_attempt = SimpleNamespace(
            status="complete",
            result=json.dumps({"status": "ok"}),
        )

        self.assertFalse(_successful_call(failed_attempt))
        self.assertTrue(_successful_call(successful_attempt))

    def test_guided_planning_scenarios_are_registered(self):
        suite = SuiteRegistry.get("guided_planning_micro")

        self.assertEqual(suite.scenario_slugs, GUIDED_PLANNING_MICRO_SCENARIO_SLUGS)
        self.assertIn(GUIDED_PLANNING_BOUNDED_WHEN_REQUESTED, BEHAVIOR_MICRO_SCENARIO_SLUGS)
        self.assertIn(LEGACY_PLANNING_STATE_EXECUTES_DIRECTLY, BEHAVIOR_MICRO_SCENARIO_SLUGS)

    def test_natural_charter_refinement_is_in_memory_suite(self):
        self.assertIn(
            CHARTER_REFINES_EXISTING_GUIDANCE_FROM_NATURAL_FEEDBACK,
            CHARTER_MEMORY_MICRO_SCENARIO_SLUGS,
        )
        self.assertIn(
            CHARTER_PATCHES_AND_COMPLETES_IMMEDIATE_TASK,
            CHARTER_MEMORY_MICRO_SCENARIO_SLUGS,
        )
        self.assertIn(
            CHARTER_CORRECTION_GOVERNS_NEXT_PEER_ASSIGNMENT,
            CHARTER_MEMORY_MICRO_SCENARIO_SLUGS,
        )
        self.assertIn(
            CHARTER_PATCHES_EXPLICIT_AMENDMENT_UNDER_PRESSURE,
            CHARTER_MEMORY_MICRO_SCENARIO_SLUGS,
        )

    def test_correction_assignment_fixture_uses_a_role_aligned_peer(self):
        scenario = ScenarioRegistry.get(CHARTER_CORRECTION_GOVERNS_NEXT_PEER_ASSIGNMENT)

        self.assertIn("outreach", scenario.assignment_peer_name_prefix.casefold())
        self.assertIn("outreach", scenario.assignment_peer_charter.casefold())
        self.assertIn("prospect", scenario.assignment_peer_charter.casefold())
        self.assertNotIn("technical support", scenario.assignment_peer_charter.casefold())

    def test_focused_charter_patch_allows_same_batch_verification_read(self):
        call = SimpleNamespace(
            tool_params={
                "sql": (
                    "UPDATE __agent_config "
                    "SET charter=patch_text(charter, 'old rule', 'new rule') WHERE id=1; "
                    "SELECT patch_text(charter, '', '') AS updated_charter "
                    "FROM __agent_config WHERE id=1"
                )
            }
        )

        self.assertTrue(_uses_one_focused_charter_patch([call], "role. old rule."))

    def test_sqlite_export_case_seeds_exact_lead_fixture(self):
        scenario = ScenarioRegistry.get(SQLITE_EXPORT_QUERY_CSV)

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.db"

            @contextmanager
            def local_agent_db(_agent_id):
                yield str(db_path)

            with patch("api.evals.scenarios.behavior_micro.agent_sqlite_db", local_agent_db):
                scenario._seed_sqlite_export_context("agent-123")

            with sqlite3.connect(db_path) as conn:
                rows = conn.execute("SELECT company, status FROM leads ORDER BY company;").fetchall()

        self.assertEqual(rows, [("Acme", "open"), ("Globex", "open"), ("Initech", "contacted")])
        self.assertIn("Export the open rows", scenario.case.prompt)

    def test_sqlite_export_uses_real_tools_without_changing_generic_mocks(self):
        scenario = ScenarioRegistry.get(SQLITE_EXPORT_QUERY_CSV)
        generic_sqlite_scenario = ScenarioRegistry.get("common_use_case_083_sqlite_query_counts")

        self.assertNotIn("sqlite_batch", scenario._build_mock_config())
        self.assertNotIn("create_csv", scenario._build_mock_config())
        self.assertIn("sqlite_batch", generic_sqlite_scenario._build_mock_config())

    def test_sqlite_count_case_names_its_available_table_and_column(self):
        scenario = ScenarioRegistry.get("common_use_case_083_sqlite_query_counts")

        self.assertIn("leads table", scenario.case.prompt)
        self.assertIn("priority column", scenario.case.prompt)

    def test_sqlite_export_stop_policy_waits_for_query_backed_csv(self):
        scenario = ScenarioRegistry.get(SQLITE_EXPORT_QUERY_CSV)

        self.assertEqual(
            scenario._build_eval_stop_policy()["stop_when_all_seen"],
            [{"tool_name": "create_csv", "after_execution": True}],
        )

    def test_sqlite_export_rejects_failed_or_incomplete_tool_results(self):
        scenario = ScenarioRegistry.get(SQLITE_EXPORT_QUERY_CSV)
        successful_csv = SimpleNamespace(
            tool_name="create_csv",
            tool_params={},
            status="complete",
            result=json.dumps({"status": "ok", "file": "$[/exports/open-leads.csv]", "attach": "$[/exports/open-leads.csv]"}),
        )
        incomplete_csv = SimpleNamespace(
            tool_name="create_csv",
            tool_params={},
            status="complete",
            result=json.dumps({"status": "ok"}),
        )

        self.assertTrue(scenario._call_satisfies_expected_tool(successful_csv, "create_csv"))
        self.assertFalse(scenario._call_satisfies_expected_tool(incomplete_csv, "create_csv"))

    def test_weekly_trend_http_mock_contains_rows_for_sqlite(self):
        scenario = ScenarioRegistry.get("common_use_case_126_http_sqlite_weekly_trend")

        result = scenario._mock_for_tool("http_request")

        self.assertEqual(len(result["content"]["signups"]), 5)
        self.assertIn("call sqlite_batch next", result["content"]["next_step"])

    def test_maps_review_mock_contains_rows_for_sqlite_dedupe(self):
        scenario = ScenarioRegistry.get("common_use_case_128_maps_reviews_sqlite_dedupe")

        result = scenario._mock_for_tool("mcp_brightdata_web_data_google_maps_reviews")

        self.assertEqual(len(result["content"]["reviews"]), 3)
        self.assertEqual(result["content"]["reviews"][0]["snippet"], result["content"]["reviews"][1]["snippet"])
        self.assertIn("call sqlite_batch next", result["content"]["next_step"])

    def test_downstream_sqlite_research_mocks_contain_processable_rows(self):
        cases = (
            ("common_use_case_129_reddit_posts_sqlite_sentiment", "mcp_brightdata_web_data_reddit_posts", "posts"),
            ("common_use_case_130_yahoo_finance_sqlite_calc", "mcp_brightdata_web_data_yahoo_finance_business", "market_cap"),
            ("common_use_case_133_http_sqlite_dedupe_report", "http_request", "accounts"),
            ("common_use_case_134_file_support_group_report", "read_file", None),
        )

        for slug, tool_name, content_key in cases:
            with self.subTest(slug=slug):
                result = ScenarioRegistry.get(slug)._mock_for_tool(tool_name)
                if content_key is None:
                    self.assertIn("tickets", json.loads(result["content"]))
                else:
                    self.assertTrue(result["content"][content_key])

    def test_monitoring_scope_chat_alternative_is_relevant_and_satisfies_expectation(self):
        scenario = ScenarioRegistry.get(MONITORING_SCOPE_QUESTION)
        chat_call = SimpleNamespace(
            tool_name="send_chat_message",
            tool_params={"body": "Which competitors and update types should I monitor?"},
            status="complete",
        )

        self.assertTrue(scenario._call_satisfies_expected_tool(chat_call, "request_human_input"))
        self.assertNotIn("send_chat_message", scenario._build_eval_stop_policy()["ignored_tool_names"])

        chat_call.tool_params = {"body": ""}
        self.assertFalse(scenario._call_satisfies_expected_tool(chat_call, "request_human_input"))

    def test_integration_discovery_scenarios_are_registered_in_expected_suites(self):
        tool_choice_suite = SuiteRegistry.get("tool_choice_micro")
        behavior_suite = SuiteRegistry.get("agent_behavior_micro")

        self.assertIn(APOLLO_CONNECT_SEARCH, TOOL_CHOICE_MICRO_SCENARIO_SLUGS)
        self.assertIn(SLACK_CONNECT_SEARCH, TOOL_CHOICE_MICRO_SCENARIO_SLUGS)
        self.assertIn(APOLLO_CONNECT_SEARCH, tool_choice_suite.scenario_slugs)
        self.assertIn(SLACK_CONNECT_SEARCH, tool_choice_suite.scenario_slugs)
        self.assertIn(APOLLO_CONNECT_SEARCH, behavior_suite.scenario_slugs)
        self.assertIn(SLACK_CONNECT_SEARCH, behavior_suite.scenario_slugs)

    def test_guided_planning_metadata_is_explicitly_optional(self):
        scenario = ScenarioRegistry.get(GUIDED_PLANNING_BOUNDED_WHEN_REQUESTED)
        metadata = scenario.get_metadata()

        self.assertEqual(metadata.category, "guided_planning")
        self.assertEqual(metadata.area, "agent_behavior")
        self.assertEqual(metadata.expected_runtime, "short")
        self.assertEqual(metadata.cost_class, "low")
        self.assertIn("guided_planning", metadata.tags)

    def test_common_integration_discovery_cases_expect_tool_search_not_questions(self):
        cases = {case.slug: case for case in COMMON_USE_CASE_EVAL_CASES}

        for slug in (APOLLO_CONNECT_SEARCH, SLACK_CONNECT_SEARCH):
            case = cases[slug]
            self.assertEqual(case.category, "integration_discovery")
            self.assertEqual(case.expected_tools, ("search_tools",))
            self.assertFalse(case.plan_expected)
            self.assertIn("request_human_input", case.forbidden_tools)
            self.assertIn("secure_credentials_request", case.forbidden_tools)
            self.assertIn("spawn_web_task", case.forbidden_tools)

    def test_common_integration_discovery_stop_policy_targets_tool_search(self):
        for slug in (APOLLO_CONNECT_SEARCH, SLACK_CONNECT_SEARCH):
            scenario = ScenarioRegistry.get(slug)
            policy = scenario._build_eval_stop_policy()

            self.assertIn("search_tools", policy["allowed_tool_names"])
            self.assertIn("request_human_input", policy["stop_on_tool_names"])
            self.assertIn("secure_credentials_request", policy["stop_on_tool_names"])
            self.assertIn("spawn_web_task", policy["stop_on_tool_names"])
            self.assertEqual(
                policy["stop_when_all_seen"],
                [{"tool_name": "search_tools", "after_execution": True}],
            )

    def test_small_sourcing_prefers_direct_research_over_meta_work(self):
        cases = {case.slug: case for case in COMMON_USE_CASE_EVAL_CASES}
        case = cases[SMALL_SOURCING_DELIVERS_BEFORE_AUTOMATION]
        scenario = ScenarioRegistry.get(SMALL_SOURCING_DELIVERS_BEFORE_AUTOMATION)

        self.assertEqual(
            case.expected_tools,
            ("mcp_brightdata_web_data_linkedin_people_search",),
        )
        self.assertIn("create_custom_tool", case.forbidden_tools)
        self.assertIn("create_custom_tool", scenario._tool_names_to_enable())
        self.assertIn(
            "create_custom_tool",
            scenario._build_eval_stop_policy()["stop_on_tool_names"],
        )
