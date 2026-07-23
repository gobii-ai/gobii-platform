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
    COMMON_USE_CASE_EVAL_CASES,
    PLANNING_FIRST_TURN_ASKS_BOUNDED_QUESTIONS,
    PLANNING_INTEGRATION_SETUP_SEARCHES_BEFORE_QUESTION,
    PLANNING_MICRO_SCENARIO_SLUGS,
    PLANNING_SECURE_CREDENTIAL_REQUEST,
    TOOL_CHOICE_MICRO_SCENARIO_SLUGS,
)
from api.evals.suites import SuiteRegistry


APOLLO_CONNECT_SEARCH = "common_use_case_136_apollo_connect_tool_search"
SLACK_CONNECT_SEARCH = "common_use_case_137_slack_connect_tool_search"
SQLITE_EXPORT_QUERY_CSV = "common_use_case_086_sqlite_export_query_csv"
MONITORING_SCOPE_QUESTION = "common_use_case_099_request_monitoring_scope"


@tag("eval_sim")
class BehaviorMicroScenarioTests(SimpleTestCase):
    def test_final_report_eval_does_not_count_skipped_send_as_delivered(self):
        scenario = ScenarioRegistry.get("planning_final_report_completes_visible_plan")
        skipped_call = SimpleNamespace(result=json.dumps({"status": "ok", "skipped": True}))
        delivered_call = SimpleNamespace(result=json.dumps({"status": "ok"}))

        self.assertFalse(scenario._message_call_was_delivered(skipped_call))
        self.assertTrue(scenario._message_call_was_delivered(delivered_call))

    def test_planning_questions_eval_exercises_normal_wait_lifecycle(self):
        scenario = ScenarioRegistry.get(PLANNING_FIRST_TURN_ASKS_BOUNDED_QUESTIONS)
        policy = scenario._eval_stop_policy()

        self.assertNotIn("stop_on_human_input_request", policy)
        self.assertIn(
            "verify_questions_remain_pending",
            [task.name for task in scenario.tasks],
        )

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
        self.assertIn("populated SQLite leads table", scenario.case.prompt)

    def test_sqlite_export_uses_real_tools_without_changing_generic_mocks(self):
        scenario = ScenarioRegistry.get(SQLITE_EXPORT_QUERY_CSV)
        generic_sqlite_scenario = ScenarioRegistry.get("common_use_case_083_sqlite_query_counts")

        self.assertNotIn("sqlite_batch", scenario._build_mock_config())
        self.assertNotIn("create_csv", scenario._build_mock_config())
        self.assertIn("sqlite_batch", generic_sqlite_scenario._build_mock_config())

    def test_sqlite_export_stop_policy_waits_for_both_executed_tools(self):
        scenario = ScenarioRegistry.get(SQLITE_EXPORT_QUERY_CSV)

        self.assertEqual(
            scenario._build_eval_stop_policy()["stop_when_all_seen"],
            [
                {"tool_name": "sqlite_batch", "after_execution": True},
                {"tool_name": "create_csv", "after_execution": True},
            ],
        )

    def test_sqlite_export_rejects_failed_or_incomplete_tool_results(self):
        scenario = ScenarioRegistry.get(SQLITE_EXPORT_QUERY_CSV)
        successful_sqlite = SimpleNamespace(
            tool_name="sqlite_batch",
            tool_params={},
            status="complete",
            result=json.dumps({"status": "ok", "results": [{"result": [{"company": "Acme"}]}]}),
        )
        successful_csv = SimpleNamespace(
            tool_name="create_csv",
            tool_params={},
            status="complete",
            result=json.dumps({"status": "ok", "file": "$[/exports/open-leads.csv]", "attach": "$[/exports/open-leads.csv]"}),
        )
        failed_sqlite = SimpleNamespace(
            tool_name="sqlite_batch",
            tool_params={},
            status="error",
            result=json.dumps({"status": "error", "message": "query failed"}),
        )
        incomplete_csv = SimpleNamespace(
            tool_name="create_csv",
            tool_params={},
            status="complete",
            result=json.dumps({"status": "ok"}),
        )

        self.assertTrue(scenario._call_satisfies_expected_tool(successful_sqlite, "sqlite_batch"))
        self.assertTrue(scenario._call_satisfies_expected_tool(successful_csv, "create_csv"))
        self.assertFalse(scenario._call_satisfies_expected_tool(failed_sqlite, "sqlite_batch"))
        self.assertFalse(scenario._call_satisfies_expected_tool(incomplete_csv, "create_csv"))

    def test_monitoring_scope_chat_alternative_is_relevant_and_satisfies_expectation(self):
        scenario = ScenarioRegistry.get(MONITORING_SCOPE_QUESTION)
        chat_call = SimpleNamespace(
            tool_name="send_chat_message",
            tool_params={"body": "Which competitors and update types should I monitor?"},
        )

        self.assertTrue(scenario._call_satisfies_expected_tool(chat_call, "request_human_input"))
        self.assertNotIn("send_chat_message", scenario._build_eval_stop_policy()["ignored_tool_names"])

        chat_call.tool_params = {"body": ""}
        self.assertFalse(scenario._call_satisfies_expected_tool(chat_call, "request_human_input"))

    def test_integration_discovery_scenarios_are_registered_in_expected_suites(self):
        planning_suite = SuiteRegistry.get("planning_micro")
        tool_choice_suite = SuiteRegistry.get("tool_choice_micro")
        behavior_suite = SuiteRegistry.get("agent_behavior_micro")

        self.assertIn(PLANNING_INTEGRATION_SETUP_SEARCHES_BEFORE_QUESTION, PLANNING_MICRO_SCENARIO_SLUGS)
        self.assertIn(PLANNING_INTEGRATION_SETUP_SEARCHES_BEFORE_QUESTION, planning_suite.scenario_slugs)
        self.assertIn(APOLLO_CONNECT_SEARCH, TOOL_CHOICE_MICRO_SCENARIO_SLUGS)
        self.assertIn(SLACK_CONNECT_SEARCH, TOOL_CHOICE_MICRO_SCENARIO_SLUGS)
        self.assertIn(APOLLO_CONNECT_SEARCH, tool_choice_suite.scenario_slugs)
        self.assertIn(SLACK_CONNECT_SEARCH, tool_choice_suite.scenario_slugs)
        self.assertIn(PLANNING_INTEGRATION_SETUP_SEARCHES_BEFORE_QUESTION, BEHAVIOR_MICRO_SCENARIO_SLUGS)
        self.assertIn(APOLLO_CONNECT_SEARCH, behavior_suite.scenario_slugs)
        self.assertIn(SLACK_CONNECT_SEARCH, behavior_suite.scenario_slugs)

    def test_planning_secure_credential_scenario_is_registered(self):
        scenario = ScenarioRegistry.get(PLANNING_SECURE_CREDENTIAL_REQUEST)
        planning_suite = SuiteRegistry.get("planning_micro")

        self.assertIn(PLANNING_SECURE_CREDENTIAL_REQUEST, planning_suite.scenario_slugs)
        self.assertIn("credentials", scenario.tags)
        self.assertEqual(scenario._eval_stop_policy()["stop_on_tool_names"], ["request_human_input"])

    def test_planning_integration_discovery_metadata_and_stop_policy(self):
        scenario = ScenarioRegistry.get(PLANNING_INTEGRATION_SETUP_SEARCHES_BEFORE_QUESTION)
        metadata = scenario.get_metadata()
        policy = scenario._eval_stop_policy()
        mock_config = scenario._mock_config()

        self.assertEqual(metadata.category, "planning")
        self.assertEqual(metadata.area, "agent_behavior")
        self.assertEqual(metadata.expected_runtime, "short")
        self.assertEqual(metadata.cost_class, "low")
        self.assertIn("integration_discovery", metadata.tags)
        self.assertTrue(policy["stop_on_first_relevant_tool"])
        self.assertTrue(policy["stop_on_human_input_request"])
        self.assertIn("send_chat_message", policy["ignored_tool_names"])
        self.assertIn("search_tools", mock_config)

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
            self.assertEqual(policy["stop_when_all_seen"], [{"tool_name": "search_tools"}])
