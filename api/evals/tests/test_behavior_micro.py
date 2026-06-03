from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.behavior_micro import (
    BEHAVIOR_MICRO_SCENARIO_SLUGS,
    COMMON_USE_CASE_EVAL_CASES,
    PLANNING_INTEGRATION_SETUP_SEARCHES_BEFORE_QUESTION,
    PLANNING_MICRO_SCENARIO_SLUGS,
    TOOL_CHOICE_MICRO_SCENARIO_SLUGS,
)
from api.evals.suites import SuiteRegistry


APOLLO_CONNECT_SEARCH = "common_use_case_136_apollo_connect_tool_search"
SLACK_CONNECT_SEARCH = "common_use_case_137_slack_connect_tool_search"


@tag("eval_sim")
class BehaviorMicroScenarioTests(SimpleTestCase):
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
