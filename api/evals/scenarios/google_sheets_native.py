from typing import Any

from api.agent.system_skills.defaults import GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY
from api.evals.base import ScenarioTask
from api.evals.scenarios.native_http import (
    HttpRequestExpectation, NativeHttpCase as GoogleSheetsNativeCase, NativeHttpScenarioBase, call_matches_expectation as _call_matches_expectation, decoded_url as _decoded_url,
    query_value as _query_value, register_native_http_scenarios, tool_calls_for_run as _tool_calls_for_run,
)
from api.models import EvalRunTask, PersistentAgentToolCall


GOOGLE_SHEETS_NATIVE_SUITE_SLUG = "google_sheets_native"

GOOGLE_SHEETS_NATIVE_FIND_SHEET_BY_NAME = "google_sheets_native_find_sheet_by_name"
GOOGLE_SHEETS_NATIVE_SEARCH_TEST_BY_NAME = "google_sheets_native_search_test_by_name"
GOOGLE_SHEETS_NATIVE_LIST_TABS = "google_sheets_native_list_tabs"
GOOGLE_SHEETS_NATIVE_READ_RANGE = "google_sheets_native_read_range"
GOOGLE_SHEETS_NATIVE_APPEND_ROW = "google_sheets_native_append_row"
GOOGLE_SHEETS_NATIVE_CREATE_DEFAULT_COLUMNS = "google_sheets_native_create_default_columns"
GOOGLE_SHEETS_NATIVE_CREATE_AND_FORMAT = "google_sheets_native_create_and_format"
GOOGLE_SHEETS_NATIVE_FORMAT_EXISTING_IDEMPOTENT = "google_sheets_native_format_existing_idempotent"
GOOGLE_SHEETS_NATIVE_CHART_WITH_HELPER_DATA = "google_sheets_native_chart_with_helper_data"
GOOGLE_SHEETS_NATIVE_MISSING_SELECTED_FILE = "google_sheets_native_missing_selected_file"

FORBIDDEN_DISCOVERY_TOOL_NAMES = ("search_tools", "enable_system_skills")


def _http_result(url: str, content: Any, *, status_code: int = 200) -> dict[str, Any]:
    return {
        "status": "ok",
        "status_code": status_code,
        "url": url,
        "content": content,
    }


def _method_equals(method: str) -> dict[str, dict[str, str]]:
    return {"param_equals": {"method": method.upper()}}


DRIVE_SEARCH_URL = "https://www.googleapis.com/drive/v3/files"
SALES_TRACKER_ID = "sheet-sales-q2"
GENERIC_TRACKER_ID = "sheet-123"
LOCAL_LLMS_SHEET_ID = "sheet-local-llms"


def _drive_files_result(files: list[dict[str, str]]) -> dict[str, Any]:
    return _http_result(DRIVE_SEARCH_URL, {"files": files})


def _drive_spreadsheet_rule(files: list[dict[str, str]], *extra_url_terms: str) -> dict[str, Any]:
    return {
        "url_contains": "www.googleapis.com/drive/v3/files",
        "url_decoded_contains": (
            "mimetype",
            "application/vnd.google-apps.spreadsheet",
            "trashed",
            "false",
            *extra_url_terms,
        ),
        "result": _drive_files_result(files),
    }


def _generic_tracker_metadata_rule() -> dict[str, Any]:
    return {
        "url_contains": f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}",
        "url_not_contains": ("/values/", ":batchupdate"),
        **_method_equals("GET"),
        "result": _http_result(
            f"https://sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}",
            {
                "spreadsheetId": GENERIC_TRACKER_ID,
                "properties": {"title": "Ops Tracker"},
                "sheets": [
                    {"properties": {"title": "Leads"}},
                    {"properties": {"title": "Tasks"}},
                    {"properties": {"title": "Archive"}},
                ],
            },
        ),
    }


def _generic_tracker_leads_values_rule() -> dict[str, Any]:
    return {
        "url_contains": (
            f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}/values",
            "leads",
        ),
        **_method_equals("GET"),
        "result": _http_result(
            f"https://sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}/values/Leads!A1:Z",
            {
                "range": "Leads!A1:Z",
                "values": [
                    ["Company", "Owner", "Priority"],
                    ["Existing Co", "Mina", "Medium"],
                ],
            },
        ),
    }


def _model_sizes_metadata_rule() -> dict[str, Any]:
    return {
        "url_contains": f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}",
        "url_not_contains": ("/values/", ":batchupdate"),
        **_method_equals("GET"),
        "result": _http_result(
            f"https://sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}",
            {
                "spreadsheetId": GENERIC_TRACKER_ID,
                "properties": {"title": "Model Tracker"},
                "sheets": [{"properties": {"sheetId": 0, "title": "Models"}}],
            },
        ),
    }


def _model_sizes_values_rule() -> dict[str, Any]:
    return {
        "url_contains": (
            f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}/values",
            "models",
        ),
        **_method_equals("GET"),
        "result": _http_result(
            f"https://sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}/values/Models!A1:C6",
            {
                "range": "Models!A1:C6",
                "values": [
                    ["Model", "Size", "Downloads"],
                    ["Llama 3.1 8B", "8B", "125000"],
                    ["Qwen2.5 7B", "7B", "98000"],
                    ["Mistral 7B", "7B", "87000"],
                ],
            },
        ),
    }


def _model_sizes_helper_column_values_rule() -> dict[str, Any]:
    return {
        "url_decoded_contains": f"/values/models!d",
        **_method_equals("GET"),
        "result": _http_result(
            f"https://sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}/values/Models!D1:D4",
            {
                "range": "Models!D1:D4",
                "values": [],
            },
        ),
    }


def _create_local_llms_rule() -> dict[str, Any]:
    return {
        "url_contains": "sheets.googleapis.com/v4/spreadsheets",
        **_method_equals("POST"),
        "result": _http_result(
            "https://sheets.googleapis.com/v4/spreadsheets",
            {
                "spreadsheetId": LOCAL_LLMS_SHEET_ID,
                "spreadsheetUrl": f"https://docs.google.com/spreadsheets/d/{LOCAL_LLMS_SHEET_ID}/edit",
                "properties": {"title": "Top Local LLM Models"},
                "sheets": [{"properties": {"sheetId": 0, "title": "Models"}}],
            },
        ),
    }


def _local_llms_values_update_rule() -> dict[str, Any]:
    return {
        "url_contains": (
            f"sheets.googleapis.com/v4/spreadsheets/{LOCAL_LLMS_SHEET_ID}/values",
            "models",
        ),
        **_method_equals("PUT"),
        "result": _http_result(
            f"https://sheets.googleapis.com/v4/spreadsheets/{LOCAL_LLMS_SHEET_ID}/values/Models!A1:F10",
            {"spreadsheetId": LOCAL_LLMS_SHEET_ID, "updatedRows": 6, "updatedCells": 30},
        ),
    }


def _local_llms_format_rule() -> dict[str, Any]:
    return {
        "url_contains": (
            f"sheets.googleapis.com/v4/spreadsheets/{LOCAL_LLMS_SHEET_ID}:batchUpdate",
        ),
        **_method_equals("POST"),
        "result": _http_result(
            f"https://sheets.googleapis.com/v4/spreadsheets/{LOCAL_LLMS_SHEET_ID}:batchUpdate",
            {
                "spreadsheetId": LOCAL_LLMS_SHEET_ID,
                "replies": [
                    {"updateSheetProperties": {}},
                    {"repeatCell": {}},
                    {"addBanding": {"bandedRange": {"bandedRangeId": 88}}},
                    {"autoResizeDimensions": {}},
                ],
            },
        ),
    }


GENERIC_TRACKER_FILE = {
    "id": GENERIC_TRACKER_ID,
    "name": "Ops Tracker",
    "mimeType": "application/vnd.google-apps.spreadsheet",
    "webViewLink": "https://docs.google.com/spreadsheets/d/sheet-123/edit",
}

SALES_TRACKER_FILE = {
    "id": SALES_TRACKER_ID,
    "name": "Q2 Sales Tracker",
    "mimeType": "application/vnd.google-apps.spreadsheet",
    "webViewLink": "https://docs.google.com/spreadsheets/d/sheet-sales-q2/edit",
}

TEST_TRACKER_FILE = {
    "id": "sheet-test-2026",
    "name": "Gobii Google Sheets Integration Test 2026-06-02",
    "mimeType": "application/vnd.google-apps.spreadsheet",
    "webViewLink": "https://docs.google.com/spreadsheets/d/sheet-test-2026/edit",
}


GOOGLE_SHEETS_NATIVE_CASES = (
    GoogleSheetsNativeCase(
        slug=GOOGLE_SHEETS_NATIVE_FIND_SHEET_BY_NAME,
        description="Find a selected spreadsheet by name through Drive, then inspect Sheets metadata.",
        prompt=(
            "Find my Google Sheet named Q2 Sales Tracker and list the worksheet tabs. "
            "Use the native Google Sheets integration."
        ),
        http_rules=(
            _drive_spreadsheet_rule([SALES_TRACKER_FILE], "q2", "sales", "tracker"),
            {
                "url_contains": f"sheets.googleapis.com/v4/spreadsheets/{SALES_TRACKER_ID}",
                "url_not_contains": ("/values/", ":batchupdate"),
                **_method_equals("GET"),
                "result": _http_result(
                    f"https://sheets.googleapis.com/v4/spreadsheets/{SALES_TRACKER_ID}",
                    {
                        "spreadsheetId": SALES_TRACKER_ID,
                        "properties": {"title": "Q2 Sales Tracker"},
                        "sheets": [
                            {"properties": {"title": "Leads"}},
                            {"properties": {"title": "Pipeline"}},
                            {"properties": {"title": "Won Deals"}},
                        ],
                    },
                ),
            },
        ),
        expected_http_requests=(
            HttpRequestExpectation(
                name="drive_spreadsheet_search",
                url_terms=(
                    "www.googleapis.com/drive/v3/files",
                    "mimetype",
                    "application/vnd.google-apps.spreadsheet",
                    "trashed",
                    "false",
                    "name",
                    "q2 sales tracker",
                ),
            ),
            HttpRequestExpectation(
                name="sheets_metadata_after_drive_match",
                url_terms=(f"sheets.googleapis.com/v4/spreadsheets/{SALES_TRACKER_ID}",),
            ),
        ),
        response_term_groups=(("Leads",), ("Pipeline",)),
        tags=("drive_discovery", "metadata"),
    ),
    GoogleSheetsNativeCase(
        slug=GOOGLE_SHEETS_NATIVE_SEARCH_TEST_BY_NAME,
        description="Search for a selected spreadsheet named Test with a complete Drive q filter.",
        prompt=(
            "Search for my Google Sheet named Test and tell me which matching spreadsheets you can access. "
            "Use the native Google Sheets integration."
        ),
        http_rules=(
            _drive_spreadsheet_rule([TEST_TRACKER_FILE], "name", "test"),
        ),
        expected_http_requests=(
            HttpRequestExpectation(
                name="drive_test_spreadsheet_search",
                url_terms=(
                    "www.googleapis.com/drive/v3/files",
                    "mimetype",
                    "application/vnd.google-apps.spreadsheet",
                    "trashed",
                    "false",
                    "name",
                    "test",
                ),
            ),
        ),
        response_term_groups=(("Gobii Google Sheets Integration Test", "sheet-test-2026"),),
        tags=("drive_discovery", "q_regression"),
    ),
    GoogleSheetsNativeCase(
        slug=GOOGLE_SHEETS_NATIVE_LIST_TABS,
        description="List tabs for a known spreadsheet ID through Sheets metadata.",
        prompt="For Google spreadsheet sheet-123, list the worksheet tabs.",
        http_rules=(
            _drive_spreadsheet_rule([GENERIC_TRACKER_FILE]),
            _generic_tracker_metadata_rule(),
        ),
        expected_http_requests=(
            HttpRequestExpectation(
                name="sheets_metadata",
                url_terms=(f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}",),
            ),
        ),
        response_term_groups=(("Leads",), ("Tasks",), ("Archive",)),
        tags=("metadata",),
    ),
    GoogleSheetsNativeCase(
        slug=GOOGLE_SHEETS_NATIVE_READ_RANGE,
        description="Read a specific range through the Sheets values API.",
        prompt="Read Leads!A1:D5 from Google spreadsheet sheet-123 and summarize the rows.",
        http_rules=(
            _drive_spreadsheet_rule([GENERIC_TRACKER_FILE]),
            {
                "url_contains": f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}/values",
                **_method_equals("GET"),
                "result": _http_result(
                    f"https://sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}/values/Leads!A1:D5",
                    {
                        "range": "Leads!A1:D5",
                        "values": [
                            ["Company", "Owner", "Priority", "Status"],
                            ["Acme", "Sam", "High", "Qualified"],
                            ["Globex", "Priya", "Medium", "Contacted"],
                        ],
                    },
                ),
            },
        ),
        expected_http_requests=(
            HttpRequestExpectation(
                name="read_values_range",
                url_terms=(
                    f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}/values",
                    "leads",
                    "a1:d5",
                ),
            ),
        ),
        response_term_groups=(("Acme",), ("Globex",)),
        tags=("values_read",),
    ),
    GoogleSheetsNativeCase(
        slug=GOOGLE_SHEETS_NATIVE_APPEND_ROW,
        description="Append a row through the Sheets values append API.",
        prompt=(
            "Append this row to Leads in Google spreadsheet sheet-123: "
            "Company Acme, Owner Sam, Priority High. Use user-entered values."
        ),
        http_rules=(
            _drive_spreadsheet_rule([GENERIC_TRACKER_FILE]),
            {
                "url_contains": (
                    f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}/values",
                    "append",
                ),
                **_method_equals("POST"),
                "result": _http_result(
                    f"https://sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}/values/Leads!A:D:append",
                    {
                        "spreadsheetId": GENERIC_TRACKER_ID,
                        "tableRange": "Leads!A1:D10",
                        "updates": {"updatedRows": 1, "updatedCells": 3},
                    },
                ),
            },
            _generic_tracker_leads_values_rule(),
            _generic_tracker_metadata_rule(),
        ),
        expected_http_requests=(
            HttpRequestExpectation(
                name="append_values_row",
                method="POST",
                url_terms=(
                    f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}/values",
                    "leads",
                    "append",
                    "valueinputoption=user_entered",
                ),
                body_terms=("values", "acme", "sam", "high"),
            ),
        ),
        response_term_groups=(("row", "appended", "updated"),),
        tags=("values_write",),
    ),
    GoogleSheetsNativeCase(
        slug=GOOGLE_SHEETS_NATIVE_CREATE_DEFAULT_COLUMNS,
        description="Create a useful spreadsheet with safe default columns instead of asking for preferences.",
        prompt=(
            "Create a Google Sheet named Top Local LLM Models with sensible default columns. "
            "Use these rows: Llama 3.1 8B, Qwen2.5 7B, and Mistral 7B. Include columns for name, size, "
            "license, and links."
        ),
        http_rules=(
            _local_llms_values_update_rule(),
            _local_llms_format_rule(),
            _create_local_llms_rule(),
        ),
        expected_http_requests=(
            HttpRequestExpectation(
                name="create_spreadsheet",
                method="POST",
                url_terms=("sheets.googleapis.com/v4/spreadsheets",),
                body_terms=("top local llm models",),
            ),
            HttpRequestExpectation(
                name="write_default_columns",
                method="PUT",
                url_terms=(
                    f"sheets.googleapis.com/v4/spreadsheets/{LOCAL_LLMS_SHEET_ID}/values",
                    "models",
                    "valueinputoption=user_entered",
                ),
                body_terms=("name", "license", "link"),
                body_term_groups=(("size", "parameters"),),
            ),
        ),
        response_term_groups=(("Top Local LLM Models", "created", "ready"),),
        tags=("create", "defaults"),
    ),
    GoogleSheetsNativeCase(
        slug=GOOGLE_SHEETS_NATIVE_CREATE_AND_FORMAT,
        description="Create, populate, and apply baseline formatting to a new spreadsheet.",
        prompt=(
            "Make a polished Google Sheet called Top Local LLM Models with columns for name, size, license, links, "
            "and release date. Use only this provided dataset; no external research is needed: "
            "Llama 3.1 8B | 8B | Llama 3.1 Community License | "
            "https://huggingface.co/meta-llama/Llama-3.1-8B | 2024-07-23; "
            "Qwen2.5 7B | 7B | Apache 2.0 | https://huggingface.co/Qwen/Qwen2.5-7B | 2024-09-19; "
            "Mistral 7B | 7B | Apache 2.0 | https://huggingface.co/mistralai/Mistral-7B-v0.1 | 2023-09-27."
        ),
        http_rules=(
            _local_llms_values_update_rule(),
            _local_llms_format_rule(),
            _create_local_llms_rule(),
        ),
        expected_http_requests=(
            HttpRequestExpectation(
                name="create_spreadsheet",
                method="POST",
                url_terms=("sheets.googleapis.com/v4/spreadsheets",),
                body_terms=("top local llm models",),
            ),
            HttpRequestExpectation(
                name="write_values",
                method="PUT",
                url_terms=(f"sheets.googleapis.com/v4/spreadsheets/{LOCAL_LLMS_SHEET_ID}/values",),
                body_terms=("release date", "license", "link"),
            ),
            HttpRequestExpectation(
                name="format_spreadsheet",
                method="POST",
                url_terms=(f"sheets.googleapis.com/v4/spreadsheets/{LOCAL_LLMS_SHEET_ID}:batchupdate",),
                body_terms=("frozenrowcount", "repeatcell", "addbanding", "bandedrange", "autoresizedimensions"),
            ),
        ),
        response_term_groups=(("polishing", "polished", "formatting", "formatted", "styled"), ("Top Local LLM Models", "sheet")),
        tags=("create", "format"),
    ),
    GoogleSheetsNativeCase(
        slug=GOOGLE_SHEETS_NATIVE_FORMAT_EXISTING_IDEMPOTENT,
        description="Inspect existing formatting before adding banding to avoid duplicate banded ranges.",
        prompt="Format Google spreadsheet sheet-123 so it looks polished. Avoid breaking existing alternating row colors.",
        http_rules=(
            {
                "url_contains": (
                    f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}/values",
                    "leads",
                ),
                **_method_equals("GET"),
                "result": _http_result(
                    f"https://sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}/values/Leads!A1:D10",
                    {
                        "range": "Leads!A1:D10",
                        "values": [
                            ["Company", "Owner", "Priority", "Status"],
                            ["Acme", "Sam", "High", "Qualified"],
                            ["Globex", "Priya", "Medium", "Contacted"],
                        ],
                    },
                ),
            },
            {
                "url_contains": f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}",
                "url_not_contains": ("/values/", ":batchupdate"),
                **_method_equals("GET"),
                "result": _http_result(
                    f"https://sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}",
                    {
                        "spreadsheetId": GENERIC_TRACKER_ID,
                        "properties": {"title": "Ops Tracker"},
                        "sheets": [
                            {
                                "properties": {"sheetId": 0, "title": "Leads"},
                                "bandedRanges": [{"bandedRangeId": 77, "range": {"sheetId": 0}}],
                            }
                        ],
                    },
                ),
            },
            {
                "url_contains": f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}:batchUpdate",
                **_method_equals("POST"),
                "result": _http_result(
                    f"https://sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}:batchUpdate",
                    {"spreadsheetId": GENERIC_TRACKER_ID, "replies": [{"repeatCell": {}}, {"autoResizeDimensions": {}}]},
                ),
            },
        ),
        expected_http_requests=(
            HttpRequestExpectation(
                name="inspect_existing_formatting",
                url_terms=(f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}",),
            ),
            HttpRequestExpectation(
                name="format_without_duplicate_banding",
                method="POST",
                url_terms=(f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}:batchupdate",),
                body_terms=("repeatcell", "autoresizedimensions"),
            ),
        ),
        response_term_groups=(("formatted", "polished"),),
        tags=("format", "idempotent"),
    ),
    GoogleSheetsNativeCase(
        slug=GOOGLE_SHEETS_NATIVE_CHART_WITH_HELPER_DATA,
        description="Create a chart that binds numeric helper data even when helper columns are hidden.",
        prompt=(
            "In Google spreadsheet sheet-123, the Models tab has model names in A and size labels in B. "
            "Add numeric helper values for the sizes to empty helper column D, then add a chart visualizing model sizes. "
            "Make sure the chart reads the helper values even if that helper column is hidden."
        ),
        http_rules=(
            _model_sizes_helper_column_values_rule(),
            _model_sizes_values_rule(),
            _model_sizes_metadata_rule(),
            {
                "url_contains": f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}/values",
                **_method_equals("PUT"),
                "result": _http_result(
                    f"https://sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}/values/Models!D1:D10",
                    {"spreadsheetId": GENERIC_TRACKER_ID, "updatedRows": 6, "updatedCells": 6},
                ),
            },
            {
                "url_contains": f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}:batchUpdate",
                **_method_equals("POST"),
                "result": _http_result(
                    f"https://sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}:batchUpdate",
                    {"spreadsheetId": GENERIC_TRACKER_ID, "replies": [{"addChart": {"chart": {"chartId": 55}}}]},
                ),
            },
        ),
        expected_http_requests=(
            HttpRequestExpectation(
                name="write_numeric_helper_data",
                method="PUT",
                url_terms=(f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}/values",),
                body_terms=("values",),
            ),
            HttpRequestExpectation(
                name="add_bound_chart",
                method="POST",
                url_terms=(f"sheets.googleapis.com/v4/spreadsheets/{GENERIC_TRACKER_ID}:batchupdate",),
                body_terms=("addchart", "basicchart", "domains", "series", "hiddendimensionstrategy", "show_all"),
            ),
        ),
        response_term_groups=(("chart",),),
        tags=("chart", "helper_data"),
    ),
    GoogleSheetsNativeCase(
        slug=GOOGLE_SHEETS_NATIVE_MISSING_SELECTED_FILE,
        description="Report setup guidance when Drive cannot see the requested spreadsheet.",
        prompt=(
            "Find my Google Sheet named Board Budget 2026 and read the Summary tab. "
            "Use the native Google Sheets integration."
        ),
        http_rules=(
            _drive_spreadsheet_rule([], "board", "budget", "2026"),
            _drive_spreadsheet_rule([]),
        ),
        expected_http_requests=(
            HttpRequestExpectation(
                name="drive_spreadsheet_search",
                url_terms=(
                    "www.googleapis.com/drive/v3/files",
                    "mimetype",
                    "application/vnd.google-apps.spreadsheet",
                    "trashed",
                    "false",
                    "name",
                    "board budget 2026",
                ),
            ),
        ),
        forbidden_url_terms=(("sheets.googleapis.com/v4/spreadsheets/",),),
        response_term_groups=(
            ("Google Drive", "integration"),
            ("choose", "select", "connect", "connected"),
            ("spreadsheet", "sheet"),
        ),
        tags=("drive_discovery", "missing_file"),
    ),
)

GOOGLE_SHEETS_NATIVE_SCENARIO_SLUGS = tuple(case.slug for case in GOOGLE_SHEETS_NATIVE_CASES)


def _call_has_partial_drive_query(call: PersistentAgentToolCall) -> bool:
    if call.tool_name != "http_request":
        return False
    if "www.googleapis.com/drive/v3/files" not in _decoded_url(call):
        return False

    query = _query_value(call, "q")
    if not query:
        return False
    if query.endswith("="):
        return True
    if "mimetype" in query and "application/vnd.google-apps.spreadsheet" not in query:
        return True
    if "name" in query and "mimetype" not in query:
        return True
    return False


class GoogleSheetsNativeScenario(NativeHttpScenarioBase):
    tier = "core"
    category = "google_sheets_native"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "system_skills"
    tags = ("google_sheets_native", "system_skill", "micro", "http_request")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_expected_http_requests", assertion_type="tool_call"),
        ScenarioTask(name="verify_no_partial_drive_queries", assertion_type="tool_call"),
        ScenarioTask(name="verify_no_forbidden_tools", assertion_type="tool_call"),
        ScenarioTask(name="verify_response", assertion_type="exact_match"),
    ]
    case: GoogleSheetsNativeCase | None = None
    system_skill_key = GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY
    system_skill_name = "Google Sheets"
    native_provider_key = "google_drive"
    forbidden_tool_names = FORBIDDEN_DISCOVERY_TOOL_NAMES
    forbidden_tool_prefixes = ("google_sheets-",)
    expected_requests_summary = "Agent completed the expected Google Drive/Sheets REST request(s)."
    forbidden_pass_summary = "Agent avoided legacy Sheets tools, skill discovery, and forbidden Google API URLs."
    response_pass_summary = "Final response included the expected mocked Sheets result or setup guidance."

    def _extra_checks(self, run_id: str, inbound) -> None:
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_no_partial_drive_queries",
        )
        bad_calls = [
            call
            for call in _tool_calls_for_run(run_id, after=inbound.timestamp, tool_names=["http_request"])
            if _call_has_partial_drive_query(call)
        ]
        if bad_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_no_partial_drive_queries",
                observed_summary=(
                    "Agent made partial Google Drive q filter request(s): "
                    f"{[(call.tool_params or {}).get('url') for call in bad_calls]}."
                ),
                artifacts={"step": bad_calls[0].step},
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_no_partial_drive_queries",
            observed_summary="Agent avoided partial Google Drive q filter URLs.",
        )

register_native_http_scenarios(GOOGLE_SHEETS_NATIVE_CASES, GoogleSheetsNativeScenario)
