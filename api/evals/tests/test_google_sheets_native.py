from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.event_processing import _eval_mock_rule_matches, _resolve_eval_mock_result
from api.agent.system_skills.defaults import _google_sheets_native_prompt_instructions
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.google_sheets_native import (
    FORBIDDEN_DISCOVERY_TOOL_NAMES,
    GOOGLE_SHEETS_NATIVE_CASES,
    GOOGLE_SHEETS_NATIVE_APPEND_ROW,
    GOOGLE_SHEETS_NATIVE_CREATE_AND_FORMAT,
    GOOGLE_SHEETS_NATIVE_CHART_WITH_HELPER_DATA,
    GOOGLE_SHEETS_NATIVE_CREATE_DEFAULT_COLUMNS,
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
                self.assertTrue("url_contains" in rule or "url_decoded_contains" in rule)
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

    def test_eval_stop_policy_ignores_update_plan(self):
        scenario = ScenarioRegistry.get(GOOGLE_SHEETS_NATIVE_SCENARIO_SLUGS[0])
        policy = scenario._eval_stop_policy()

        self.assertIn("update_plan", policy["ignored_tool_names"])

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

    def test_get_method_mock_rule_accepts_omitted_method(self):
        rule = {"param_equals": {"method": "GET"}}

        self.assertTrue(_eval_mock_rule_matches(rule, {}))
        self.assertFalse(_eval_mock_rule_matches(rule, {"method": "POST"}))

    def test_url_not_contains_prevents_broad_metadata_rule_from_matching_values(self):
        rule = {
            "url_contains": "sheets.googleapis.com/v4/spreadsheets/sheet-123",
            "url_not_contains": "/values/",
        }

        self.assertTrue(
            _eval_mock_rule_matches(
                rule,
                {"url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123"},
            )
        )
        self.assertFalse(
            _eval_mock_rule_matches(
                rule,
                {"url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/Models!A1:C6"},
            )
        )

    def test_create_default_columns_prompt_provides_source_rows(self):
        case = next(
            case
            for case in GOOGLE_SHEETS_NATIVE_CASES
            if case.slug == GOOGLE_SHEETS_NATIVE_CREATE_DEFAULT_COLUMNS
        )

        self.assertIn("Use these rows", case.prompt)
        self.assertNotIn("latest", case.prompt.lower())

    def test_create_and_format_prompt_provides_complete_source_data(self):
        case = next(
            case
            for case in GOOGLE_SHEETS_NATIVE_CASES
            if case.slug == GOOGLE_SHEETS_NATIVE_CREATE_AND_FORMAT
        )

        self.assertIn("Use only this provided dataset", case.prompt)
        self.assertIn("no external research is needed", case.prompt)
        self.assertIn("2024-07-23", case.prompt)
        self.assertNotIn("latest", case.prompt.lower())

    def test_chart_value_read_mock_does_not_return_write_result(self):
        case = next(
            case
            for case in GOOGLE_SHEETS_NATIVE_CASES
            if case.slug == GOOGLE_SHEETS_NATIVE_CHART_WITH_HELPER_DATA
        )
        mock_config = case.mock_config()

        read_result = _resolve_eval_mock_result(
            mock_config,
            "http_request",
            {
                "method": "GET",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/Models!A1:C6",
            },
        )
        write_result = _resolve_eval_mock_result(
            mock_config,
            "http_request",
            {
                "method": "PUT",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/Leads!F1:F10",
            },
        )

        self.assertIn("values", read_result["content"])
        self.assertIn("updatedRows", write_result["content"])

    def test_chart_helper_value_reads_do_not_prepopulate_empty_helper_column(self):
        case = next(
            case
            for case in GOOGLE_SHEETS_NATIVE_CASES
            if case.slug == GOOGLE_SHEETS_NATIVE_CHART_WITH_HELPER_DATA
        )
        mock_config = case.mock_config()

        helper_column_result = _resolve_eval_mock_result(
            mock_config,
            "http_request",
            {
                "method": "GET",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/Models!D1:D4",
            },
        )
        full_range_result = _resolve_eval_mock_result(
            mock_config,
            "http_request",
            {
                "method": "GET",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/Models!A1:D10",
            },
        )

        self.assertEqual(helper_column_result["content"]["values"], [])
        self.assertEqual(full_range_result["content"]["values"][0], ["Model", "Size", "Downloads"])

    def test_chart_case_points_to_models_tab_and_does_not_mock_other_tabs_as_model_data(self):
        case = next(
            case
            for case in GOOGLE_SHEETS_NATIVE_CASES
            if case.slug == GOOGLE_SHEETS_NATIVE_CHART_WITH_HELPER_DATA
        )
        mock_config = case.mock_config()

        self.assertIn("Models tab", case.prompt)
        self.assertIn("empty helper column D", case.prompt)
        other_tab_result = _resolve_eval_mock_result(
            mock_config,
            "http_request",
            {
                "method": "GET",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/Leads",
            },
        )

        self.assertEqual(other_tab_result["status"], "error")

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

    def test_expected_http_request_accepts_body_term_alternatives(self):
        expectation = HttpRequestExpectation(
            name="write_default_columns",
            method="PUT",
            url_terms=("sheets.googleapis.com/v4/spreadsheets/sheet-local-llms/values", "models"),
            body_terms=("name", "license", "link"),
            body_term_groups=(("size", "parameters"),),
        )
        parameters_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "PUT",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-local-llms/values/Models!A1:D4",
                "body": '{"values":[["Name","Parameters","License","Source / Link"]]}',
            },
        )
        missing_size_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "PUT",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-local-llms/values/Models!A1:D4",
                "body": '{"values":[["Name","License","Source / Link"]]}',
            },
        )

        self.assertTrue(_call_matches_expectation(parameters_call, expectation))
        self.assertFalse(_call_matches_expectation(missing_size_call, expectation))

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

    def test_google_sheets_prompt_includes_live_error_guardrails(self):
        with patch("api.agent.system_skills.defaults._native_integration_connected", return_value=True):
            instructions = _google_sheets_native_prompt_instructions(SimpleNamespace())

        self.assertIn("never call `GET https://sheets.googleapis.com/v4/spreadsheets`", instructions)
        self.assertIn("POST https://sheets.googleapis.com/v4/spreadsheets", instructions)
        self.assertIn("do not use `/v1/spreadsheets`", instructions)
        self.assertIn("Do not assume a tab is named `Sheet1`", instructions)
        self.assertIn("do not mix legacy color fields", instructions)
