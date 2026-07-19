from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.system_skills.defaults import _hubspot_native_prompt_instructions
from api.evals.scenarios.hubspot_native import (
    FORBIDDEN_HUBSPOT_DISCOVERY_TOOL_NAMES,
    HUBSPOT_NATIVE_CASES,
    HUBSPOT_NATIVE_CREATE_CONTACT,
    HUBSPOT_NATIVE_MISSING_CONNECTION,
    HUBSPOT_NATIVE_SCENARIO_SLUGS,
    HUBSPOT_NATIVE_SUITE_SLUG,
    HubSpotHttpRequestExpectation,
    _call_matches_expectation,
)
from api.evals.scenarios.native_http import response_contains_term
from api.evals.registry import ScenarioRegistry
from api.evals.suites import SuiteRegistry


@tag("eval_sim")
class HubSpotNativeScenarioTests(SimpleTestCase):
    def test_hubspot_native_suite_contains_five_scenarios(self):
        suite = SuiteRegistry.get(HUBSPOT_NATIVE_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), HUBSPOT_NATIVE_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 5)

    @patch("api.agent.system_skills.defaults._native_integration_connected", return_value=False)
    @patch("api.agent.system_skills.defaults.settings.PUBLIC_SITE_URL", "https://app.example.test")
    def test_disconnected_hubspot_prompt_is_a_setup_gate(self, _mock_connected):
        instructions = _hubspot_native_prompt_instructions(SimpleNamespace())

        self.assertIn("Current state: HubSpot is not connected", instructions)
        self.assertIn("current requester in this conversation", instructions)
        self.assertIn("https://app.example.test/app/integrations", instructions)
        self.assertIn("Do not call `http_request`, `search_tools`", instructions)
        self.assertNotIn("API cookbook:", instructions)
        self.assertNotIn("crm/v3/objects", instructions)

    def test_generated_scenarios_have_expected_metadata(self):
        registered = ScenarioRegistry.list_all()

        for slug in HUBSPOT_NATIVE_SCENARIO_SLUGS:
            scenario = registered[slug]
            metadata = scenario.get_metadata()
            self.assertEqual(metadata.category, "hubspot_native")
            self.assertEqual(metadata.area, "system_skills")
            self.assertEqual(metadata.expected_runtime, "short")
            self.assertEqual(metadata.cost_class, "low")
            self.assertIn("hubspot_native", metadata.tags)
            self.assertIn("system_skill", metadata.tags)
            self.assertIn("http_request", metadata.tags)

    def test_cases_mock_only_http_request_for_hubspot_api_calls(self):
        for case in HUBSPOT_NATIVE_CASES:
            self.assertEqual(set(case.mock_config()), {"http_request"})
            mock = case.mock_config()["http_request"]
            self.assertEqual(bool(mock["rules"]), "missing_connection" not in case.tags)
            self.assertIn("default", mock)
            for rule in mock["rules"]:
                self.assertIn("url_contains", rule)
                self.assertIn("result", rule)

    def test_cases_expect_http_request_not_discovery_or_enablement(self):
        for case in HUBSPOT_NATIVE_CASES:
            self.assertEqual(bool(case.expected_http_requests), "missing_connection" not in case.tags)
            for expectation in case.expected_http_requests:
                self.assertTrue(expectation.url_terms)
                self.assertIn(expectation.method, {"GET", "POST", "PATCH", "PUT", "DELETE"})

            prompt_and_description = f"{case.prompt} {case.description}"
            for tool_name in FORBIDDEN_HUBSPOT_DISCOVERY_TOOL_NAMES:
                self.assertNotIn(tool_name, prompt_and_description)

    def test_eval_stop_policy_allows_sqlite_batch_for_result_shaping(self):
        scenario = ScenarioRegistry.get(HUBSPOT_NATIVE_SCENARIO_SLUGS[0])
        policy = scenario._eval_stop_policy()

        self.assertIn("sqlite_batch", policy["allowed_tool_names"])
        self.assertIn("http_request", policy["allowed_tool_names"])
        self.assertIn("send_chat_message", policy["allowed_tool_names"])
        for tool_name in FORBIDDEN_HUBSPOT_DISCOVERY_TOOL_NAMES:
            self.assertIn(tool_name, policy["stop_on_tool_names"])

    def test_expected_http_request_requires_completed_tool_call(self):
        expectation = HubSpotHttpRequestExpectation(
            name="contact_search",
            url_terms=("api.hubapi.com/crm/v3/objects/contacts/search",),
        )
        pending_call = SimpleNamespace(
            status="pending",
            tool_params={
                "method": "POST",
                "url": "https://api.hubapi.com/crm/v3/objects/contacts/search",
            },
        )
        complete_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.hubapi.com/crm/v3/objects/contacts/search",
            },
        )

        self.assertFalse(_call_matches_expectation(pending_call, expectation))
        self.assertTrue(_call_matches_expectation(complete_call, expectation))

    def test_missing_connection_uses_known_setup_state_without_doomed_api_call(self):
        case = next(case for case in HUBSPOT_NATIVE_CASES if case.slug == HUBSPOT_NATIVE_MISSING_CONNECTION)
        scenario = ScenarioRegistry.get(HUBSPOT_NATIVE_MISSING_CONNECTION)

        self.assertEqual(case.expected_http_requests, ())
        self.assertTrue(scenario.requires_personal_agent)
        self.assertIn(("api.hubapi.com",), case.forbidden_url_terms)
        self.assertNotIn("not connected", case.prompt.lower())
        self.assertIn(("/app/integrations",), case.response_term_groups)
        self.assertNotIn("connected", case.response_term_groups[-1])

    def test_response_term_matching_accepts_currency_formatting(self):
        self.assertTrue(response_contains_term("Amount is now $25,000.", "25000"))

    def test_response_term_matching_ignores_markdown_emphasis(self):
        self.assertTrue(response_contains_term("Only **2** qualify.", "only 2"))

    def test_expected_http_request_can_require_body_terms(self):
        create_case = next(case for case in HUBSPOT_NATIVE_CASES if case.slug == HUBSPOT_NATIVE_CREATE_CONTACT)
        expectation = create_case.expected_http_requests[0]
        missing_body_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.hubapi.com/crm/v3/objects/contacts",
                "body": {"properties": {"firstname": "Alex"}},
            },
        )
        complete_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.hubapi.com/crm/v3/objects/contacts",
                "body": {
                    "properties": {
                        "firstname": "Alex",
                        "lastname": "Morgan",
                        "email": "alex@example.test",
                    }
                },
            },
        )

        self.assertFalse(_call_matches_expectation(missing_body_call, expectation))
        self.assertTrue(_call_matches_expectation(complete_call, expectation))
