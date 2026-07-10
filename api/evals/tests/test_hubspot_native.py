from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.event_processing import _resolve_eval_mock_result
from api.agent.system_skills.defaults import _hubspot_native_prompt_instructions
from api.agent.system_skills.native_api_cookbooks import render_native_api_cookbook
from api.evals.scenarios.hubspot_native import (
    FORBIDDEN_HUBSPOT_DISCOVERY_TOOL_NAMES,
    HUBSPOT_NATIVE_CASES,
    HUBSPOT_NATIVE_CREATE_CONTACT,
    HUBSPOT_NATIVE_DEAL_UPDATE,
    HUBSPOT_NATIVE_MISSING_CONNECTION,
    HUBSPOT_NATIVE_SCENARIO_SLUGS,
    HUBSPOT_NATIVE_SUITE_SLUG,
    HubSpotHttpRequestExpectation,
    _call_matches_expectation,
)
from api.evals.scenarios.native_http import (
    false_readiness_claims,
    false_readiness_claims_before_first_http,
    response_contains_term,
    validate_http_call_set,
)
from api.evals.registry import ScenarioRegistry
from api.evals.suites import SuiteRegistry


@tag("eval_sim")
class HubSpotNativeScenarioTests(SimpleTestCase):
    def test_hubspot_native_suite_contains_five_scenarios(self):
        suite = SuiteRegistry.get(HUBSPOT_NATIVE_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), HUBSPOT_NATIVE_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 5)

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
            self.assertTrue(mock["rules"])
            self.assertIn("default", mock)
            for rule in mock["rules"]:
                self.assertIn("url_contains", rule)
                self.assertIn("result", rule)

    def test_cases_expect_http_request_not_discovery_or_enablement(self):
        for case in HUBSPOT_NATIVE_CASES:
            self.assertTrue(case.expected_http_requests)
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

    def test_only_missing_connection_scenario_scores_false_readiness_claims(self):
        missing = ScenarioRegistry.get("hubspot_native_missing_connection")
        normal = ScenarioRegistry.get(HUBSPOT_NATIVE_SCENARIO_SLUGS[0])

        self.assertIn("verify_no_false_connection_claim", [task.name for task in missing.tasks])
        self.assertNotIn("verify_no_false_connection_claim", [task.name for task in normal.tasks])

    def test_false_readiness_check_catches_hubspot_pre_call_claim(self):
        false_claim = SimpleNamespace(
            tool_name="send_chat_message",
            tool_params={"body": "**HubSpot** is connected and ready to go!"},
        )
        request = SimpleNamespace(tool_name="http_request", tool_params={})

        self.assertEqual(
            false_readiness_claims_before_first_http([false_claim, request], "HubSpot"),
            [false_claim],
        )
        self.assertEqual(false_readiness_claims([request, false_claim], "HubSpot"), [false_claim])

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

    def test_missing_connection_expected_http_request_accepts_error_tool_call(self):
        case = next(case for case in HUBSPOT_NATIVE_CASES if case.slug == HUBSPOT_NATIVE_MISSING_CONNECTION)
        expectation = case.expected_http_requests[0]
        error_call = SimpleNamespace(
            status="error",
            tool_params={
                "method": "GET",
                "url": "https://api.hubapi.com/crm/v3/properties/contacts",
            },
        )
        complete_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.hubapi.com/crm/v3/objects/contacts/search",
            },
        )

        self.assertTrue(_call_matches_expectation(error_call, expectation))
        self.assertFalse(_call_matches_expectation(complete_call, expectation))

        result = _resolve_eval_mock_result(case.mock_config(), "http_request", error_call.tool_params)
        self.assertEqual(result["status_code"], 401)
        self.assertIn("not connected", result["message"])

    def test_response_term_matching_accepts_currency_formatting(self):
        self.assertTrue(response_contains_term("Amount is now $25,000.", "25000"))
        self.assertFalse(response_contains_term("Amount is now $125,000.", "25000"))

    def test_deal_update_uses_realistic_numeric_hubspot_object_id(self):
        deal_case = next(case for case in HUBSPOT_NATIVE_CASES if case.slug == HUBSPOT_NATIVE_DEAL_UPDATE)

        self.assertIn("deal ID 123", deal_case.prompt)
        self.assertIn("objects/deals/123", deal_case.expected_http_requests[0].url_terms[0])
        self.assertNotIn("deal_123", f"{deal_case.prompt} {deal_case.expected_http_requests[0].url_terms}")

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

    def test_deal_update_rejects_numeric_substring_collision(self):
        case = next(case for case in HUBSPOT_NATIVE_CASES if case.slug == HUBSPOT_NATIVE_DEAL_UPDATE)
        expectation = case.expected_http_requests[0]
        wrong_amount = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "PATCH",
                "url": "https://api.hubapi.com/crm/v3/objects/deals/123",
                "body": {"properties": {"amount": "125000"}},
            },
        )
        correct_amount = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "PATCH",
                "url": "https://api.hubapi.com/crm/v3/objects/deals/123",
                "body": {"properties": {"amount": 25000}},
            },
        )

        self.assertFalse(_call_matches_expectation(wrong_amount, expectation))
        self.assertTrue(_call_matches_expectation(correct_amount, expectation))

        pre_read = SimpleNamespace(
            status="complete",
            tool_params={"method": "GET", "url": "https://api.hubapi.com/crm/v3/objects/deals/123"},
        )
        violations, unmatched = validate_http_call_set([pre_read, correct_amount], (expectation,))
        self.assertTrue(violations)
        self.assertEqual(unmatched[0]["method"], "GET")

    def test_hubspot_guidance_stops_on_auth_and_skips_redundant_exact_write_reads(self):
        cookbook = render_native_api_cookbook("hubspot")
        with patch("api.agent.system_skills.defaults._native_integration_connected", return_value=True):
            instructions = _hubspot_native_prompt_instructions(SimpleNamespace())

        self.assertIn("make no other HubSpot or discovery call", instructions)
        self.assertIn("approved exact ID/property/value update goes straight to PATCH", cookbook)
        self.assertIn("do not pre-read or read back", cookbook)
