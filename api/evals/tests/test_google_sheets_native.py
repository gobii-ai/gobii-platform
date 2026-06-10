from types import SimpleNamespace

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.event_processing import _eval_mock_rule_matches
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.google_sheets_native import (
    FORBIDDEN_DISCOVERY_TOOL_NAMES,
    GOOGLE_SHEETS_NATIVE_CASES,
    GOOGLE_SHEETS_NATIVE_APPEND_ROW,
    GOOGLE_SHEETS_NATIVE_LIST_TABS,
    GOOGLE_SHEETS_NATIVE_READ_RANGE,
    GOOGLE_SHEETS_NATIVE_SCENARIO_SLUGS,
    GOOGLE_SHEETS_NATIVE_SEARCH_TEST_BY_NAME,
    GOOGLE_SHEETS_NATIVE_SUITE_SLUG,
    HttpRequestExpectation,
    _call_has_partial_drive_query,
    _call_matches_expectation,
    _drive_spreadsheet_rule,
)
from api.evals.suites import SuiteRegistry


@tag("eval_sim")
class GoogleSheetsNativeScenarioTests(SimpleTestCase):
    def test_google_sheets_native_suite_contains_ten_scenarios(self):
        suite = SuiteRegistry.get(GOOGLE_SHEETS_NATIVE_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), GOOGLE_SHEETS_NATIVE_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 10)

    def test_generated_scenarios_have_expected_metadata(self):
        registered = ScenarioRegistry.list_all()

        for slug in GOOGLE_SHEETS_NATIVE_SCENARIO_SLUGS:
            scenario = registered[slug]
            metadata = scenario.get_metadata()
            self.assertEqual(metadata.category, "google_sheets_native")
            self.assertEqual(metadata.area, "system_skills")
            self.assertEqual(metadata.expected_runtime, "short")
            self.assertEqual(metadata.cost_class, "low")
            self.assertIn("google_sheets_native", metadata.tags)
            self.assertIn("system_skill", metadata.tags)
            self.assertIn("http_request", metadata.tags)

    def test_cases_mock_only_http_request_for_google_api_calls(self):
        for case in GOOGLE_SHEETS_NATIVE_CASES:
            self.assertEqual(set(case.mock_config()), {"http_request"})
            mock = case.mock_config()["http_request"]
            self.assertTrue(mock["rules"])
            self.assertIn("default", mock)
            for rule in mock["rules"]:
                self.assertIn("url_contains", rule)
                self.assertIn("result", rule)

    def test_cases_expect_http_request_not_legacy_sheets_tools_or_enablement(self):
        for case in GOOGLE_SHEETS_NATIVE_CASES:
            self.assertTrue(case.expected_http_requests)
            for expectation in case.expected_http_requests:
                self.assertEqual(expectation.name.startswith("google_sheets-"), False)
                self.assertTrue(expectation.url_terms)
                self.assertIn(expectation.method, {"GET", "POST", "PUT"})

            prompt_and_description = f"{case.prompt} {case.description}"
            self.assertNotIn("google_sheets-", prompt_and_description)
            for tool_name in FORBIDDEN_DISCOVERY_TOOL_NAMES:
                self.assertNotIn(tool_name, prompt_and_description)

    def test_known_id_cases_allow_drive_preflight(self):
        known_id_cases = {
            case.slug: case
            for case in GOOGLE_SHEETS_NATIVE_CASES
            if case.slug in {
                GOOGLE_SHEETS_NATIVE_APPEND_ROW,
                GOOGLE_SHEETS_NATIVE_LIST_TABS,
                GOOGLE_SHEETS_NATIVE_READ_RANGE,
            }
        }

        self.assertEqual(len(known_id_cases), 3)
        for case in known_id_cases.values():
            self.assertNotIn(("www.googleapis.com/drive/v3/files",), case.forbidden_url_terms)

    def test_append_case_mocks_harmless_sheets_preflight(self):
        append_case = next(case for case in GOOGLE_SHEETS_NATIVE_CASES if case.slug == GOOGLE_SHEETS_NATIVE_APPEND_ROW)
        rule_terms = [rule["url_contains"] for rule in append_case.mock_config()["http_request"]["rules"]]

        self.assertIn("sheets.googleapis.com/v4/spreadsheets/sheet-123", rule_terms)
        self.assertIn(
            ("sheets.googleapis.com/v4/spreadsheets/sheet-123/values", "leads"),
            rule_terms,
        )

    def test_search_test_case_requires_complete_drive_q_filter(self):
        search_case = next(
            case for case in GOOGLE_SHEETS_NATIVE_CASES if case.slug == GOOGLE_SHEETS_NATIVE_SEARCH_TEST_BY_NAME
        )

        expectation = search_case.expected_http_requests[0]
        self.assertIn("www.googleapis.com/drive/v3/files", expectation.url_terms)
        self.assertIn("application/vnd.google-apps.spreadsheet", expectation.url_terms)
        self.assertIn("trashed", expectation.url_terms)
        self.assertIn("false", expectation.url_terms)
        self.assertIn("name", expectation.url_terms)
        self.assertIn("test", expectation.url_terms)

    def test_drive_mock_requires_exact_spreadsheet_mime_type(self):
        rule = _drive_spreadsheet_rule([])
        good_query = (
            "https://www.googleapis.com/drive/v3/files?"
            "q=mimeType%20%3D%20%27application%2Fvnd.google-apps.spreadsheet%27"
            "%20and%20trashed%20%3D%20false"
        )
        bad_query = (
            "https://www.googleapis.com/drive/v3/files?"
            "q=mimeType%20%3D%20%27application%2Fvnd%2Fgoogle-apps%2Espreadsheet%27"
            "%20and%20trashed%20%3D%20false"
        )

        self.assertTrue(_eval_mock_rule_matches(rule, {"url": good_query}))
        self.assertFalse(_eval_mock_rule_matches(rule, {"url": bad_query}))

    def test_expected_http_request_requires_completed_tool_call(self):
        expectation = HttpRequestExpectation(
            name="read_values_range",
            url_terms=("sheets.googleapis.com/v4/spreadsheets/sheet-123/values", "leads", "a1:d5"),
        )
        pending_call = SimpleNamespace(
            status="pending",
            tool_params={
                "method": "GET",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/Leads!A1:D5",
            },
        )
        complete_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "GET",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/Leads!A1:D5",
            },
        )

        self.assertFalse(_call_matches_expectation(pending_call, expectation))
        self.assertTrue(_call_matches_expectation(complete_call, expectation))

    def test_partial_drive_query_detector_flags_incomplete_q_filters(self):
        partial_call = SimpleNamespace(
            tool_name="http_request",
            tool_params={"url": "https://www.googleapis.com/drive/v3/files?q=mimeType%20%3D%20"},
        )
        complete_call = SimpleNamespace(
            tool_name="http_request",
            tool_params={
                "url": (
                    "https://www.googleapis.com/drive/v3/files?"
                    "q=mimeType%20%3D%20%27application%2Fvnd.google-apps.spreadsheet%27"
                    "%20and%20trashed%20%3D%20false"
                )
            },
        )

        self.assertTrue(_call_has_partial_drive_query(partial_call))
        self.assertFalse(_call_has_partial_drive_query(complete_call))

    def test_partial_drive_query_detector_flags_repeated_malformed_q_filter(self):
        repeated_calls = [
            SimpleNamespace(
                tool_name="http_request",
                tool_params={"url": "https://www.googleapis.com/drive/v3/files?q=mimeType%20%3D%20"},
            ),
            SimpleNamespace(
                tool_name="http_request",
                tool_params={"url": "https://www.googleapis.com/drive/v3/files?q=mimeType%20%3D%20"},
            ),
        ]

        self.assertTrue(all(_call_has_partial_drive_query(call) for call in repeated_calls))
