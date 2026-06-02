import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlparse

from api.agent.system_skills.defaults import GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.service import enable_system_skills
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentSystemStep,
    PersistentAgentStep,
    PersistentAgentToolCall,
)


GOOGLE_SHEETS_NATIVE_SUITE_SLUG = "google_sheets_native"

GOOGLE_SHEETS_NATIVE_FIND_SHEET_BY_NAME = "google_sheets_native_find_sheet_by_name"
GOOGLE_SHEETS_NATIVE_LIST_TABS = "google_sheets_native_list_tabs"
GOOGLE_SHEETS_NATIVE_READ_RANGE = "google_sheets_native_read_range"
GOOGLE_SHEETS_NATIVE_APPEND_ROW = "google_sheets_native_append_row"
GOOGLE_SHEETS_NATIVE_MISSING_SELECTED_FILE = "google_sheets_native_missing_selected_file"

FORBIDDEN_DISCOVERY_TOOL_NAMES = ("search_tools", "enable_system_skills")


@dataclass(frozen=True)
class HttpRequestExpectation:
    name: str
    url_terms: tuple[str, ...]
    method: str = "GET"
    body_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class GoogleSheetsNativeCase:
    slug: str
    prompt: str
    description: str
    http_rules: tuple[dict[str, Any], ...]
    expected_http_requests: tuple[HttpRequestExpectation, ...]
    forbidden_url_terms: tuple[tuple[str, ...], ...] = ()
    response_term_groups: tuple[tuple[str, ...], ...] = ()
    tags: tuple[str, ...] = field(default_factory=tuple)

    def mock_config(self) -> dict[str, dict[str, Any]]:
        return {
            "http_request": {
                "rules": list(self.http_rules),
                "default": {
                    "status": "error",
                    "status_code": 404,
                    "message": "Unexpected Google Sheets native eval URL.",
                    "content": {"ok": False},
                },
            }
        }


def _json_body(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


def _http_result(url: str, content: Any, *, status_code: int = 200) -> dict[str, Any]:
    return {
        "status": "ok",
        "status_code": status_code,
        "url": url,
        "content": content,
    }


DRIVE_SEARCH_URL = "https://www.googleapis.com/drive/v3/files"
SALES_TRACKER_ID = "sheet-sales-q2"
GENERIC_TRACKER_ID = "sheet-123"


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


def _tool_calls_for_run(run_id: str, *, after=None, tool_names=None):
    queryset = PersistentAgentToolCall.objects.filter(step__eval_run_id=run_id)
    if after is not None:
        queryset = queryset.filter(step__created_at__gte=after)
    if tool_names is not None:
        queryset = queryset.filter(tool_name__in=list(tool_names))
    return list(queryset.select_related("step").order_by("step__created_at", "step__id"))


def _decoded_url(call: PersistentAgentToolCall) -> str:
    params = call.tool_params or {}
    return unquote_plus(str(params.get("url") or "")).lower()


def _query_value(call: PersistentAgentToolCall, key: str) -> str:
    raw_url = str((call.tool_params or {}).get("url") or "")
    parsed = urlparse(raw_url)
    values = parse_qs(parsed.query).get(key) or []
    if not values:
        return ""
    return unquote_plus(str(values[0] or "")).strip().lower()


def _request_body(call: PersistentAgentToolCall) -> str:
    body = (call.tool_params or {}).get("body")
    if isinstance(body, str):
        return body.lower()
    if body is None:
        return ""
    return _json_body(body).lower()


def _request_method(call: PersistentAgentToolCall) -> str:
    return str((call.tool_params or {}).get("method") or "GET").strip().upper()


def _call_matches_expectation(call: PersistentAgentToolCall, expectation: HttpRequestExpectation) -> bool:
    if str(getattr(call, "status", "") or "").lower() != "complete":
        return False
    url = _decoded_url(call)
    body = _request_body(call)
    if _request_method(call) != expectation.method.upper():
        return False
    if not all(term.lower() in url for term in expectation.url_terms):
        return False
    if not all(term.lower() in body for term in expectation.body_terms):
        return False
    return True


def _call_matches_url_terms(call: PersistentAgentToolCall, url_terms: tuple[str, ...]) -> bool:
    url = _decoded_url(call)
    return all(term.lower() in url for term in url_terms)


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


class GoogleSheetsNativeScenario(EvalScenario, ScenarioExecutionTools):
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

    def _seed_prior_processing_run(self, agent_id: str) -> None:
        if PersistentAgentSystemStep.objects.filter(
            step__agent_id=agent_id,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        ).exists():
            return

        prior_step = PersistentAgentStep.objects.create(
            agent_id=agent_id,
            description="Process events",
        )
        PersistentAgentSystemStep.objects.create(
            step=prior_step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        )

    def _prepare_agent(self, agent_id: str) -> None:
        PersistentAgent.objects.filter(id=agent_id).update(planning_state=PersistentAgent.PlanningState.SKIPPED)
        self._seed_prior_processing_run(agent_id)
        agent = PersistentAgent.objects.get(id=agent_id)
        result = enable_system_skills(agent, [GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY])
        if result.get("invalid"):
            raise ValueError(f"Could not enable Google Sheets native system skill: {result}")

    def _eval_stop_policy(self) -> dict[str, Any]:
        return {
            "allowed_tool_names": ["http_request", "send_chat_message"],
            "ignored_tool_names": ["sleep_until_next_trigger"],
            "stop_on_unexpected_relevant_tool": True,
            "stop_on_tool_names": list(FORBIDDEN_DISCOVERY_TOOL_NAMES),
            "stop_on_tool_names_after_finish": ["send_chat_message"],
            "max_relevant_tool_calls": 12,
        }

    def _record_expected_http_requests(self, run_id: str, inbound) -> None:
        case = self.case
        if case is None:
            raise ValueError("GoogleSheetsNativeScenario.case must be set.")

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_expected_http_requests",
        )
        http_calls = _tool_calls_for_run(run_id, after=inbound.timestamp, tool_names=["http_request"])
        missing = [
            expectation.name
            for expectation in case.expected_http_requests
            if not any(_call_matches_expectation(call, expectation) for call in http_calls)
        ]
        if not missing:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_expected_http_requests",
                observed_summary="Agent completed the expected Google Drive/Sheets REST request(s).",
                artifacts={"step": http_calls[0].step} if http_calls else {},
            )
            return

        seen = [
            {
                "method": _request_method(call),
                "url": (call.tool_params or {}).get("url"),
                "body": _request_body(call)[:500],
            }
            for call in http_calls
        ]
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="verify_expected_http_requests",
            observed_summary=f"Missing expected HTTP request(s): {missing}; saw {seen}.",
            artifacts={"step": http_calls[0].step} if http_calls else {},
        )

    def _record_no_partial_drive_queries(self, run_id: str, inbound) -> None:
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

    def _record_forbidden_absence(self, run_id: str, inbound) -> None:
        case = self.case
        if case is None:
            raise ValueError("GoogleSheetsNativeScenario.case must be set.")

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_no_forbidden_tools",
        )
        calls = _tool_calls_for_run(run_id, after=inbound.timestamp)
        forbidden_tool_calls = [
            call
            for call in calls
            if call.tool_name.startswith("google_sheets-") or call.tool_name in FORBIDDEN_DISCOVERY_TOOL_NAMES
        ]
        forbidden_http_calls = [
            call
            for call in calls
            if call.tool_name == "http_request"
            and any(_call_matches_url_terms(call, terms) for terms in case.forbidden_url_terms)
        ]
        bad_calls = [*forbidden_tool_calls, *forbidden_http_calls]
        if bad_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_no_forbidden_tools",
                observed_summary=(
                    "Agent used forbidden tool or URL: "
                    f"{[(call.tool_name, (call.tool_params or {}).get('url')) for call in bad_calls]}."
                ),
                artifacts={"step": bad_calls[0].step},
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_no_forbidden_tools",
            observed_summary="Agent avoided legacy Sheets tools, skill discovery, and forbidden Google API URLs.",
        )

    def _record_response(self, run_id: str, agent_id: str, inbound) -> None:
        case = self.case
        if case is None:
            raise ValueError("GoogleSheetsNativeScenario.case must be set.")

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_response")
        if not case.response_term_groups:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_response",
                observed_summary="No response content terms configured for this case.",
            )
            return

        final_response = (
            PersistentAgentMessage.objects
            .filter(owner_agent_id=agent_id, is_outbound=True, timestamp__gt=inbound.timestamp)
            .order_by("-timestamp")
            .first()
        )
        body = final_response.body if final_response else ""
        missing_groups = [
            terms
            for terms in case.response_term_groups
            if not any(term.lower() in body.lower() for term in terms)
        ]
        if not missing_groups:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_response",
                observed_summary="Final response included the expected mocked Sheets result or setup guidance.",
                artifacts={"message": final_response} if final_response else {},
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="verify_response",
            observed_summary=f"Final response missing expected term group(s) {missing_groups}; body={body[:800]!r}.",
            artifacts={"message": final_response} if final_response else {},
        )

    def run(self, run_id: str, agent_id: str) -> None:
        case = self.case
        if case is None:
            raise ValueError("GoogleSheetsNativeScenario.case must be set.")

        self._prepare_agent(agent_id)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                case.prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=case.mock_config(),
                eval_stop_policy=self._eval_stop_policy(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self._record_expected_http_requests(run_id, inbound)
        self._record_no_partial_drive_queries(run_id, inbound)
        self._record_forbidden_absence(run_id, inbound)
        self._record_response(run_id, agent_id, inbound)


def _google_sheets_native_scenario_class(case: GoogleSheetsNativeCase):
    class _GoogleSheetsNativeCaseScenario(GoogleSheetsNativeScenario):
        slug = case.slug
        description = case.description
        tags = GoogleSheetsNativeScenario.tags + case.tags

    _GoogleSheetsNativeCaseScenario.case = case
    _GoogleSheetsNativeCaseScenario.__name__ = "".join(part.title() for part in case.slug.split("_")) + "Scenario"
    return _GoogleSheetsNativeCaseScenario


for google_sheets_native_case in GOOGLE_SHEETS_NATIVE_CASES:
    ScenarioRegistry.register(_google_sheets_native_scenario_class(google_sheets_native_case)())
