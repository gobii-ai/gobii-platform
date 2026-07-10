from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.event_processing import _eval_mock_rule_matches, _resolve_eval_mock_result
from api.agent.system_skills.defaults import GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.google_sheets_native import (
    FORBIDDEN_DISCOVERY_TOOL_NAMES,
    GOOGLE_SHEETS_NATIVE_CASES,
    GOOGLE_SHEETS_NATIVE_APPEND_ROW,
    GOOGLE_SHEETS_NATIVE_CREATE_AND_FORMAT,
    GOOGLE_SHEETS_NATIVE_CHART_WITH_HELPER_DATA,
    GOOGLE_SHEETS_NATIVE_CREATE_DEFAULT_COLUMNS,
    GOOGLE_SHEETS_NATIVE_FORMAT_EXISTING_IDEMPOTENT,
    GOOGLE_SHEETS_NATIVE_LIST_TABS,
    GOOGLE_SHEETS_NATIVE_READ_RANGE,
    GOOGLE_SHEETS_NATIVE_MISSING_SELECTED_FILE,
    GOOGLE_SHEETS_NATIVE_SCENARIO_SLUGS,
    GOOGLE_SHEETS_NATIVE_SEARCH_TEST_BY_NAME,
    GOOGLE_SHEETS_NATIVE_SUITE_SLUG,
    HttpRequestExpectation,
    _call_has_partial_drive_query,
    _call_matches_expectation,
    _chart_helper_write_is_correct,
    _chart_request_binds_expected_ranges,
    _drive_spreadsheet_rule,
    _request_json,
)
from api.evals.scenarios.native_http import validate_http_call_set
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

    def test_create_and_format_response_accepts_formatting_language(self):
        create_case = next(case for case in GOOGLE_SHEETS_NATIVE_CASES if case.slug == GOOGLE_SHEETS_NATIVE_CREATE_AND_FORMAT)

        first_group = create_case.response_term_groups[0]
        self.assertIn("formatted", first_group)
        self.assertIn("styled", first_group)

    def test_format_mock_reports_banding_only_when_requested(self):
        case = next(
            case for case in GOOGLE_SHEETS_NATIVE_CASES
            if case.slug == GOOGLE_SHEETS_NATIVE_CREATE_AND_FORMAT
        )
        base_params = {
            "method": "POST",
            "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-local-llms:batchUpdate",
            "body": {
                "requests": [
                    {"updateSheetProperties": {"properties": {"gridProperties": {"frozenRowCount": 1}}}},
                    {"repeatCell": {}},
                    {"autoResizeDimensions": {}},
                ]
            },
        }
        without_banding = _resolve_eval_mock_result(case.mock_config(), "http_request", base_params)
        with_banding_params = {
            **base_params,
            "body": {
                "requests": [
                    *base_params["body"]["requests"],
                    {"addBanding": {"bandedRange": {"range": {"sheetId": 0}}}},
                ]
            },
        }
        with_banding = _resolve_eval_mock_result(
            case.mock_config(), "http_request", with_banding_params
        )
        reply_keys = lambda result: {key for reply in result["content"]["replies"] for key in reply}
        expectation = next(
            item for item in case.expected_http_requests if item.name == "format_spreadsheet"
        )

        self.assertNotIn("addBanding", reply_keys(without_banding))
        self.assertIn("addBanding", reply_keys(with_banding))
        self.assertFalse(_call_matches_expectation(SimpleNamespace(status="complete", tool_params=base_params), expectation))
        self.assertTrue(_call_matches_expectation(SimpleNamespace(status="complete", tool_params=with_banding_params), expectation))

    def test_new_sheet_values_accept_the_only_tab_without_repeating_its_name(self):
        case = next(case for case in GOOGLE_SHEETS_NATIVE_CASES if case.slug == GOOGLE_SHEETS_NATIVE_CREATE_AND_FORMAT)

        result = _resolve_eval_mock_result(
            case.mock_config(),
            "http_request",
            {
                "method": "PUT",
                "url": (
                    "https://sheets.googleapis.com/v4/spreadsheets/"
                    "sheet-local-llms/values/A1:E5?valueInputOption=USER_ENTERED"
                ),
                "body": {"values": [["Name", "Size", "License", "Link", "Release Date"]]},
            },
        )

        self.assertEqual(result["status"], "ok")

    def test_new_sheet_append_is_mocked_as_a_values_write_not_a_second_create(self):
        case = next(case for case in GOOGLE_SHEETS_NATIVE_CASES if case.slug == GOOGLE_SHEETS_NATIVE_CREATE_DEFAULT_COLUMNS)
        params = {
            "method": "POST",
            "url": (
                "https://sheets.googleapis.com/v4/spreadsheets/sheet-local-llms/"
                "values/Models!A1:D4:append?valueInputOption=USER_ENTERED"
            ),
            "body": {
                "values": [
                    ["Name", "Size", "License", "Link"],
                    ["Llama 3.1 8B", "8B parameters", "Llama license", "https://example.test/llama"],
                    ["Qwen2.5 7B", "7B parameters", "Apache", "https://example.test/qwen"],
                    ["Mistral 7B", "7B parameters", "Apache", "https://example.test/mistral"],
                ]
            },
        }

        result = _resolve_eval_mock_result(case.mock_config(), "http_request", params)
        expectation = next(
            item for item in case.expected_http_requests if item.name == "write_default_columns"
        )
        call = SimpleNamespace(status="complete", tool_params=params)

        self.assertEqual(result["content"]["updatedRows"], 4)
        self.assertTrue(_call_matches_expectation(call, expectation))

    def test_default_columns_case_allows_baseline_formatting(self):
        case = next(case for case in GOOGLE_SHEETS_NATIVE_CASES if case.slug == GOOGLE_SHEETS_NATIVE_CREATE_DEFAULT_COLUMNS)

        self.assertIn(
            "optional_default_sheet_formatting",
            {expectation.name for expectation in case.allowed_http_requests},
        )

    def test_formatting_guidance_stops_after_successful_batch_update(self):
        with patch("api.agent.system_skills.defaults._native_integration_connected", return_value=True):
            instructions = GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL.prompt_instructions_renderer(SimpleNamespace())

        self.assertIn("one metadata inspection is usually enough", instructions)
        self.assertIn("after a successful `batchUpdate`", instructions)
        self.assertIn("instead of doing extra readback verification", instructions)
        self.assertIn("chart.spec.hiddenDimensionStrategy=SHOW_ALL", instructions)
        self.assertIn("spreadsheet ID is literal and opaque regardless of shape", instructions)

    def test_existing_banding_case_rejects_duplicate_add_banding(self):
        case = next(
            case
            for case in GOOGLE_SHEETS_NATIVE_CASES
            if case.slug == GOOGLE_SHEETS_NATIVE_FORMAT_EXISTING_IDEMPOTENT
        )
        expectation = next(
            expectation
            for expectation in case.expected_http_requests
            if expectation.name == "format_without_duplicate_banding"
        )
        safe_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123:batchUpdate",
                "body": {"requests": [{"repeatCell": {}}, {"autoResizeDimensions": {}}]},
            },
        )
        duplicate_call = SimpleNamespace(
            status="complete",
            tool_params={
                **safe_call.tool_params,
                "body": {
                    "requests": [
                        {"repeatCell": {}},
                        {"addBanding": {"bandedRange": {"range": {"sheetId": 0}}}},
                        {"autoResizeDimensions": {}},
                    ]
                },
            },
        )

        self.assertTrue(_call_matches_expectation(safe_call, expectation))
        self.assertFalse(_call_matches_expectation(duplicate_call, expectation))

    def test_existing_banding_case_accepts_bounded_inspection_and_natural_completion_language(self):
        case = next(
            case
            for case in GOOGLE_SHEETS_NATIVE_CASES
            if case.slug == GOOGLE_SHEETS_NATIVE_FORMAT_EXISTING_IDEMPOTENT
        )
        inspection = next(
            expectation
            for expectation in case.expected_http_requests
            if expectation.name == "inspect_existing_formatting"
        )

        self.assertEqual(inspection.max_calls, 2)
        self.assertIn("formatting", case.response_term_groups[0])
        self.assertIn("styled", case.response_term_groups[0])
        self.assertIn("styling", case.response_term_groups[0])

    def test_missing_selected_file_requires_explicit_absence_language(self):
        case = next(
            case
            for case in GOOGLE_SHEETS_NATIVE_CASES
            if case.slug == GOOGLE_SHEETS_NATIVE_MISSING_SELECTED_FILE
        )
        absence_terms = case.response_term_groups[1]

        self.assertIn("not found", absence_terms)
        self.assertIn("no results", absence_terms)
        self.assertNotIn("connected", case.response_term_groups[2])

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
            self.assertTrue(
                any(expectation.name == "optional_drive_spreadsheet_preflight" for expectation in case.allowed_http_requests)
            )

    def test_append_case_mocks_harmless_sheets_preflight(self):
        append_case = next(case for case in GOOGLE_SHEETS_NATIVE_CASES if case.slug == GOOGLE_SHEETS_NATIVE_APPEND_ROW)
        rule_terms = [rule["url_contains"] for rule in append_case.mock_config()["http_request"]["rules"]]

        self.assertIn("sheets.googleapis.com/v4/spreadsheets/sheet-123", rule_terms)
        self.assertIn(
            ("sheets.googleapis.com/v4/spreadsheets/sheet-123/values", "leads"),
            rule_terms,
        )
        self.assertEqual(
            {expectation.name for expectation in append_case.allowed_http_requests},
            {
                "optional_drive_spreadsheet_preflight",
                "optional_leads_values_preflight",
                "optional_sheets_metadata_preflight",
            },
        )

        values_rule = next(
            rule
            for rule in append_case.http_rules
            if rule.get("url_contains")
            == ("sheets.googleapis.com/v4/spreadsheets/sheet-123/values", "leads")
        )
        self.assertNotIn(["Acme", "Sam", "High"], values_rule["result"]["content"]["values"])

    def test_append_guidance_preserves_append_semantics_and_avoids_stale_readback(self):
        with patch("api.agent.system_skills.defaults._native_integration_connected", return_value=True):
            instructions = GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL.prompt_instructions_renderer(SimpleNamespace())

        self.assertIn("explicit append request means add a new row", instructions)
        self.assertIn("do not read back or repeat the append", instructions)

    def test_read_range_case_mocks_and_allows_one_metadata_preflight(self):
        read_case = next(case for case in GOOGLE_SHEETS_NATIVE_CASES if case.slug == GOOGLE_SHEETS_NATIVE_READ_RANGE)

        self.assertTrue(
            any(
                rule.get("url_contains") == "sheets.googleapis.com/v4/spreadsheets/sheet-123"
                and rule.get("url_not_contains") == ("/values/", ":batchupdate")
                for rule in read_case.http_rules
            )
        )
        metadata_expectations = [
            expectation
            for expectation in read_case.allowed_http_requests
            if expectation.name == "optional_sheets_metadata_preflight"
        ]
        self.assertEqual(len(metadata_expectations), 1)
        self.assertEqual(metadata_expectations[0].max_calls, 1)

    def test_declared_read_preflight_is_allowed_but_does_not_satisfy_required_write(self):
        append_case = next(case for case in GOOGLE_SHEETS_NATIVE_CASES if case.slug == GOOGLE_SHEETS_NATIVE_APPEND_ROW)
        preflight = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "GET",
                "url": (
                    "https://www.googleapis.com/drive/v3/files?"
                    "q=mimeType%3D%27application%2Fvnd.google-apps.spreadsheet%27%20and%20trashed%3Dfalse"
                ),
            },
        )
        append = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": (
                    "https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/"
                    "Leads!A:D:append?valueInputOption=USER_ENTERED"
                ),
                "body": {"values": [["Acme", "Sam", "High"]]},
            },
        )
        declarations = (*append_case.expected_http_requests, *append_case.allowed_http_requests)

        violations, unmatched = validate_http_call_set([preflight, append], declarations)
        self.assertEqual(violations, [])
        self.assertEqual(unmatched, [])

        missing_write_violations, _ = validate_http_call_set([preflight], declarations)
        self.assertTrue(any("append_values_row expected at least 1" in item for item in missing_write_violations))

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

    def test_json_param_mock_rule_rejects_malformed_json(self):
        rule = {"json_params": ("body",)}

        self.assertTrue(_eval_mock_rule_matches(rule, {"body": '{"requests": []}'}))
        self.assertTrue(_eval_mock_rule_matches(rule, {"body": {"requests": []}}))
        self.assertFalse(_eval_mock_rule_matches(rule, {"body": '{"requests": []'}))

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

    def test_chart_semantics_require_exact_helper_values_and_bound_ranges(self):
        helper = SimpleNamespace(
            tool_params={
                "method": "PUT",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/Models!D1:D4",
                "body": {"values": [["Size (B)"], [8], [7], [7]]},
            }
        )
        wrong_helper = SimpleNamespace(
            tool_params={
                **helper.tool_params,
                "body": {"values": [["Size (B)"], [80], [70], [70]]},
            }
        )
        string_helper = SimpleNamespace(
            tool_params={
                **helper.tool_params,
                "url": (
                    "https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/"
                    "Models!D1:D4?valueInputOption=USER_ENTERED"
                ),
                "body": {"values": [["Size (B)"], ["8"], ["7"], ["7"]]},
            }
        )
        raw_string_helper = SimpleNamespace(
            tool_params={
                **string_helper.tool_params,
                "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/Models!D1:D4",
            }
        )
        helper_without_header = SimpleNamespace(
            tool_params={
                "method": "PUT",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/Models!D2:D4",
                "body": {"values": [[8], [7], [7]]},
            }
        )
        chart = SimpleNamespace(
            tool_params={
                "method": "POST",
                "body": {
                    "requests": [
                        {
                            "addChart": {
                                "chart": {
                                    "spec": {
                                        "hiddenDimensionStrategy": "SHOW_ALL",
                                        "basicChart": {
                                            "domains": [{"domain": {"sourceRange": {"sources": [{"startColumnIndex": 0, "endColumnIndex": 1}]}}}],
                                            "series": [{"series": {"sourceRange": {"sources": [{"startColumnIndex": 3, "endColumnIndex": 4}]}}}],
                                        }
                                    }
                                }
                            }
                        }
                    ]
                },
            }
        )

        self.assertTrue(_chart_helper_write_is_correct(helper))
        self.assertTrue(_chart_helper_write_is_correct(helper_without_header))
        self.assertTrue(_chart_helper_write_is_correct(string_helper))
        self.assertFalse(_chart_helper_write_is_correct(raw_string_helper))
        self.assertFalse(_chart_helper_write_is_correct(wrong_helper))
        self.assertTrue(_chart_request_binds_expected_ranges(chart))
        spec = chart.tool_params["body"]["requests"][0]["addChart"]["chart"]["spec"]
        spec["basicChart"]["series"][0]["series"]["sourceRange"]["sources"][0]["startColumnIndex"] = 2
        self.assertFalse(_chart_request_binds_expected_ranges(chart))
        spec["basicChart"]["series"][0]["series"]["sourceRange"]["sources"][0]["startColumnIndex"] = 3
        spec["basicChart"]["hiddenDimensionStrategy"] = spec.pop("hiddenDimensionStrategy")
        self.assertFalse(_chart_request_binds_expected_ranges(chart))

    def test_chart_case_allows_one_preflight_and_one_readback_verification(self):
        case = next(
            case
            for case in GOOGLE_SHEETS_NATIVE_CASES
            if case.slug == GOOGLE_SHEETS_NATIVE_CHART_WITH_HELPER_DATA
        )
        expectations = tuple(
            expectation
            for expectation in case.allowed_http_requests
            if expectation.name in {
                "optional_model_values_preflight",
                "optional_model_values_verification",
            }
        )
        calls = [
            SimpleNamespace(
                status="complete",
                tool_params={
                    "method": "GET",
                    "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/Models!A:B",
                },
            ),
            SimpleNamespace(
                status="complete",
                tool_params={
                    "method": "GET",
                    "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/Models!A1:D10",
                },
            ),
        ]

        violations, unmatched = validate_http_call_set(calls, expectations)

        self.assertEqual(violations, [])
        self.assertEqual(unmatched, [])

    def test_google_sheets_fingerprint_includes_request_json_decoder(self):
        scenario = ScenarioRegistry.get(GOOGLE_SHEETS_NATIVE_CHART_WITH_HELPER_DATA)

        self.assertIn(_request_json, scenario.fingerprint_dependencies)

    def test_chart_mock_does_not_report_update_as_a_new_chart(self):
        case = next(case for case in GOOGLE_SHEETS_NATIVE_CASES if case.slug == GOOGLE_SHEETS_NATIVE_CHART_WITH_HELPER_DATA)

        result = _resolve_eval_mock_result(
            case.mock_config(),
            "http_request",
            {
                "method": "POST",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123:batchUpdate",
                "body": {"requests": [{"updateChartSpec": {"chartId": 55, "spec": {}}}]},
            },
        )

        self.assertEqual(result["status"], "error")

    def test_chart_mock_enforces_hidden_dimension_strategy_on_chart_spec(self):
        case = next(
            case
            for case in GOOGLE_SHEETS_NATIVE_CASES
            if case.slug == GOOGLE_SHEETS_NATIVE_CHART_WITH_HELPER_DATA
        )
        spec = {
            "hiddenDimensionStrategy": "SHOW_ALL",
            "basicChart": {"domains": [], "series": []},
        }
        params = {
            "method": "POST",
            "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123:batchUpdate",
            "body": {"requests": [{"addChart": {"chart": {"spec": spec}}}]},
        }

        valid_result = _resolve_eval_mock_result(case.mock_config(), "http_request", params)
        spec["basicChart"]["hiddenDimensionStrategy"] = spec.pop("hiddenDimensionStrategy")
        invalid_result = _resolve_eval_mock_result(case.mock_config(), "http_request", params)

        self.assertEqual(valid_result["status"], "ok")
        self.assertEqual(invalid_result["status_code"], 400)

        malformed = _resolve_eval_mock_result(
            case.mock_config(),
            "http_request",
            {
                "method": "POST",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123:batchUpdate",
                "body": '{"requests":[{"addChart":{}}]',
            },
        )
        self.assertEqual(malformed["status"], "error")

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
