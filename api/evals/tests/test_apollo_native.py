from types import SimpleNamespace

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.evals.scenarios.apollo_native import (
    APOLLO_NATIVE_CASES,
    APOLLO_NATIVE_CREATE_CONTACT,
    APOLLO_NATIVE_SCENARIO_SLUGS,
    APOLLO_NATIVE_SUITE_SLUG,
    FORBIDDEN_APOLLO_DISCOVERY_TOOL_NAMES,
    ApolloHttpRequestExpectation,
    _call_matches_expectation,
)
from api.evals.registry import ScenarioRegistry
from api.evals.suites import SuiteRegistry


@tag("eval_sim")
class ApolloNativeScenarioTests(SimpleTestCase):
    def test_apollo_native_suite_contains_five_scenarios(self):
        suite = SuiteRegistry.get(APOLLO_NATIVE_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), APOLLO_NATIVE_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 5)

    def test_generated_scenarios_have_expected_metadata(self):
        registered = ScenarioRegistry.list_all()

        for slug in APOLLO_NATIVE_SCENARIO_SLUGS:
            scenario = registered[slug]
            metadata = scenario.get_metadata()
            self.assertEqual(metadata.category, "apollo_native")
            self.assertEqual(metadata.area, "system_skills")
            self.assertEqual(metadata.expected_runtime, "short")
            self.assertEqual(metadata.cost_class, "low")
            self.assertIn("apollo_native", metadata.tags)
            self.assertIn("system_skill", metadata.tags)
            self.assertIn("http_request", metadata.tags)

    def test_cases_mock_only_http_request_for_apollo_api_calls(self):
        for case in APOLLO_NATIVE_CASES:
            self.assertEqual(set(case.mock_config()), {"http_request"})
            mock = case.mock_config()["http_request"]
            self.assertTrue(mock["rules"])
            self.assertIn("default", mock)
            for rule in mock["rules"]:
                self.assertIn("url_contains", rule)
                self.assertIn("result", rule)

    def test_cases_expect_http_request_not_legacy_apollo_tools_or_enablement(self):
        for case in APOLLO_NATIVE_CASES:
            self.assertTrue(case.expected_http_requests)
            for expectation in case.expected_http_requests:
                self.assertEqual(expectation.name.startswith("apollo_io-"), False)
                self.assertTrue(expectation.url_terms)
                self.assertIn(expectation.method, {"GET", "POST", "PATCH", "PUT", "DELETE"})

            prompt_and_description = f"{case.prompt} {case.description}"
            self.assertNotIn("apollo_io-", prompt_and_description)
            for tool_name in FORBIDDEN_APOLLO_DISCOVERY_TOOL_NAMES:
                self.assertNotIn(tool_name, prompt_and_description)

    def test_eval_stop_policy_allows_sqlite_batch_for_result_shaping(self):
        scenario = ScenarioRegistry.get(APOLLO_NATIVE_SCENARIO_SLUGS[0])
        policy = scenario._eval_stop_policy()

        self.assertIn("sqlite_batch", policy["allowed_tool_names"])
        self.assertIn("http_request", policy["allowed_tool_names"])
        self.assertIn("send_chat_message", policy["allowed_tool_names"])
        for tool_name in FORBIDDEN_APOLLO_DISCOVERY_TOOL_NAMES:
            self.assertIn(tool_name, policy["stop_on_tool_names"])

    def test_expected_http_request_requires_completed_tool_call(self):
        expectation = ApolloHttpRequestExpectation(
            name="people_search",
            url_terms=("api.apollo.io/api/v1/mixed_people/api_search",),
        )
        pending_call = SimpleNamespace(
            status="pending",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/mixed_people/api_search",
            },
        )
        complete_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/mixed_people/api_search",
            },
        )

        self.assertFalse(_call_matches_expectation(pending_call, expectation))
        self.assertTrue(_call_matches_expectation(complete_call, expectation))

    def test_missing_connection_expected_http_request_accepts_error_tool_call(self):
        expectation = ApolloHttpRequestExpectation(
            name="apollo_search_attempt",
            url_terms=("api.apollo.io/api/v1/mixed_people/api_search",),
            allowed_statuses=("error",),
        )
        error_call = SimpleNamespace(
            status="error",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/mixed_people/api_search",
            },
        )
        complete_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/mixed_people/api_search",
            },
        )

        self.assertTrue(_call_matches_expectation(error_call, expectation))
        self.assertFalse(_call_matches_expectation(complete_call, expectation))

    def test_expected_http_request_can_require_body_terms(self):
        create_case = next(case for case in APOLLO_NATIVE_CASES if case.slug == APOLLO_NATIVE_CREATE_CONTACT)
        expectation = create_case.expected_http_requests[0]
        missing_body_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/contacts",
                "body": {"first_name": "Alex"},
            },
        )
        complete_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/contacts",
                "body": {
                    "first_name": "Alex",
                    "last_name": "Morgan",
                    "email": "alex@example.test",
                },
            },
        )

        self.assertFalse(_call_matches_expectation(missing_body_call, expectation))
        self.assertTrue(_call_matches_expectation(complete_call, expectation))
