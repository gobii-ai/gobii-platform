import re

from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import register_scenario
from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentHumanInputRequest,
    PersistentAgentMessage,
    PersistentAgentToolCall,
)

PLANNING_FIRST_TURN_ASKS_BOUNDED_QUESTIONS = "planning_first_turn_asks_bounded_questions"
PLANNING_CLEAR_TASK_ENDS_PLANNING_FIRST = "planning_clear_task_ends_planning_first"
PLANNING_EXECUTE_REQUEST_STAYS_IN_PLANNING = "planning_execute_request_stays_in_planning"
PLANNING_NO_DIRECT_SCHEDULE_OR_CONFIG_UPDATES = "planning_no_direct_schedule_or_config_updates"

TOOL_CHOICE_EXACT_JSON_URL_USES_HTTP_REQUEST = "tool_choice_exact_json_url_uses_http_request"
TOOL_CHOICE_CSV_DELIVERABLE_USES_CREATE_CSV = "tool_choice_csv_deliverable_uses_create_csv"
TOOL_CHOICE_PDF_DELIVERABLE_USES_CREATE_PDF = "tool_choice_pdf_deliverable_uses_create_pdf"
TOOL_CHOICE_MISSING_RECIPIENT_USES_HUMAN_INPUT = "tool_choice_missing_recipient_uses_human_input"

PLANNING_MICRO_SCENARIO_SLUGS = [
    PLANNING_FIRST_TURN_ASKS_BOUNDED_QUESTIONS,
    PLANNING_CLEAR_TASK_ENDS_PLANNING_FIRST,
    PLANNING_EXECUTE_REQUEST_STAYS_IN_PLANNING,
    PLANNING_NO_DIRECT_SCHEDULE_OR_CONFIG_UPDATES,
]

TOOL_CHOICE_MICRO_SCENARIO_SLUGS = [
    TOOL_CHOICE_EXACT_JSON_URL_USES_HTTP_REQUEST,
    TOOL_CHOICE_CSV_DELIVERABLE_USES_CREATE_CSV,
    TOOL_CHOICE_PDF_DELIVERABLE_USES_CREATE_PDF,
    TOOL_CHOICE_MISSING_RECIPIENT_USES_HUMAN_INPUT,
]

BEHAVIOR_MICRO_SCENARIO_SLUGS = PLANNING_MICRO_SCENARIO_SLUGS + TOOL_CHOICE_MICRO_SCENARIO_SLUGS

BROWSER_OR_SEARCH_TOOL_NAMES = {
    "search_tools",
    "spawn_web_task",
    "mcp_brightdata_search_engine",
    "scraping_browser_navigate",
    "scraping_browser_snapshot",
    "scraping_browser_click_ref",
    "scraping_browser_type_ref",
    "scraping_browser_scroll",
    "scraping_browser_scroll_to_ref",
    "scraping_browser_wait_for_ref",
}

SUBSTANTIVE_WORK_TOOL_NAMES = BROWSER_OR_SEARCH_TOOL_NAMES | {
    "http_request",
    "read_file",
    "create_file",
    "create_csv",
    "create_pdf",
    "create_chart",
    "create_image",
    "create_video",
    "python_exec",
    "run_command",
    "send_email",
    "send_sms",
    "send_webhook_event",
    "request_contact_permission",
    "secure_credentials_request",
}

PLANNING_MUTATION_TOOL_NAMES = {
    "update_schedule",
    "update_charter",
    "update_plan",
}

IGNORED_FIRST_ACTION_TOOL_NAMES = {
    "send_chat_message",
    "sleep_until_next_trigger",
}

PLANNING_ALLOWED_FIRST_ACTION_TOOL_NAMES = {
    "request_human_input",
    "end_planning",
}

PLANNING_STATE_TABLE_NAMES = {
    "__agent_config",
}

SQL_MUTATION_RE = re.compile(r"\b(insert|update|delete|replace|alter|drop|create)\b", re.IGNORECASE)


def get_tool_calls_for_run(run_id, *, after=None, tool_names=None):
    queryset = PersistentAgentToolCall.objects.filter(step__eval_run_id=run_id)
    if after is not None:
        queryset = queryset.filter(step__created_at__gte=after)
    if tool_names is not None:
        queryset = queryset.filter(tool_name__in=list(tool_names))
    return list(queryset.select_related("step").order_by("step__created_at", "step__id"))


def get_first_relevant_tool_call(run_id, *, after=None, ignored_tool_names=None):
    ignored = set(ignored_tool_names or ())
    for call in get_tool_calls_for_run(run_id, after=after):
        if call.tool_name not in ignored:
            return call
    return None


def get_forbidden_calls_before_end_planning(run_id, *, after=None, forbidden_tool_names=None):
    forbidden = set(forbidden_tool_names or ())
    calls = []
    for call in get_tool_calls_for_run(run_id, after=after):
        if call.tool_name == "end_planning":
            break
        if call.tool_name in forbidden:
            calls.append(call)
    return calls


def sqlite_batch_mutates_planning_state(tool_call):
    if tool_call.tool_name != "sqlite_batch":
        return False
    params = tool_call.tool_params or {}
    sql = str(params.get("sql") or "")
    if not sql:
        return False
    lowered = sql.lower()
    if not any(table in lowered for table in PLANNING_STATE_TABLE_NAMES):
        return False
    return bool(SQL_MUTATION_RE.search(sql))


def get_planning_mutation_calls_before_end_planning(run_id, *, after=None):
    calls = []
    for call in get_tool_calls_for_run(run_id, after=after):
        if call.tool_name == "end_planning":
            break
        if call.tool_name in PLANNING_MUTATION_TOOL_NAMES or sqlite_batch_mutates_planning_state(call):
            calls.append(call)
    return calls


def get_pending_human_input_requests(agent_id, run_id, *, after=None):
    queryset = PersistentAgentHumanInputRequest.objects.filter(
        agent_id=agent_id,
        originating_step__eval_run_id=run_id,
        status=PersistentAgentHumanInputRequest.Status.PENDING,
    )
    if after is not None:
        queryset = queryset.filter(created_at__gte=after)
    return list(queryset.order_by("created_at", "id"))


def all_requests_have_options(requests):
    return all(isinstance(request.options_json, list) and len(request.options_json) > 0 for request in requests)


class BehaviorMicroScenario(EvalScenario, ScenarioExecutionTools):
    def _set_planning_state(self, agent_id, state):
        PersistentAgent.objects.filter(id=agent_id).update(planning_state=state)

    def _enable_builtin_tools(self, agent_id, tool_names):
        agent = PersistentAgent.objects.get(id=agent_id)
        for tool_name in tool_names:
            mark_tool_enabled_without_discovery(agent, tool_name)

    def _planning_guardrail_mocks(self):
        return {
            "search_tools": {"status": "error", "message": "Search disabled during planning eval."},
            "spawn_web_task": {"status": "error", "message": "Browser work disabled during planning eval."},
            "http_request": {"status": "error", "message": "HTTP work disabled during planning eval."},
            "send_email": {"status": "error", "message": "Outbound email disabled during planning eval."},
            "send_sms": {"status": "error", "message": "Outbound SMS disabled during planning eval."},
            "send_webhook_event": {"status": "error", "message": "Webhook delivery disabled during planning eval."},
            "create_csv": {"status": "error", "message": "CSV creation disabled during planning eval."},
            "create_pdf": {"status": "error", "message": "PDF creation disabled during planning eval."},
            "create_chart": {"status": "error", "message": "Chart creation disabled during planning eval."},
            "sqlite_batch": {"status": "error", "message": "SQLite mutation disabled during planning eval."},
        }

    def _record_forbidden_before_end(self, run_id, after, task_name, forbidden_tool_names):
        forbidden = get_forbidden_calls_before_end_planning(
            run_id,
            after=after,
            forbidden_tool_names=forbidden_tool_names,
        )
        mutations = get_planning_mutation_calls_before_end_planning(run_id, after=after)
        bad_calls = []
        seen_ids = set()
        for call in [*forbidden, *mutations]:
            if call.id in seen_ids:
                continue
            seen_ids.add(call.id)
            bad_calls.append(call)
        if bad_calls:
            seen = [call.tool_name for call in bad_calls]
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name=task_name,
                observed_summary=f"Forbidden planning-mode tool calls before end_planning: {seen}",
                artifacts={"step": bad_calls[0].step},
            )
            return False

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name=task_name,
            observed_summary="No forbidden work occurred before planning ended.",
        )
        return True


@register_scenario
class PlanningFirstTurnAsksBoundedQuestionsScenario(BehaviorMicroScenario):
    slug = PLANNING_FIRST_TURN_ASKS_BOUNDED_QUESTIONS
    description = "Planning mode should welcome the user, ask 1-3 tracked questions with options, and not start work."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_welcome_message", assertion_type="manual"),
        ScenarioTask(name="verify_bounded_questions", assertion_type="manual"),
        ScenarioTask(name="verify_no_substantive_work", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.PLANNING)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                (
                    "I want you to monitor competitors and keep me updated, but I am not sure "
                    "which competitors or what kind of updates matter yet."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=self._planning_guardrail_mocks(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_welcome_message")
        outbound = PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            conversation_id=inbound.conversation_id,
            is_outbound=True,
            timestamp__gt=inbound.timestamp,
        ).order_by("timestamp").first()
        if outbound:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_welcome_message",
                observed_summary="Agent sent an outbound welcome/planning message.",
                artifacts={"message": outbound},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_welcome_message",
                observed_summary="No outbound welcome/planning message was sent.",
            )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_bounded_questions")
        requests = get_pending_human_input_requests(agent_id, run_id, after=inbound.timestamp)
        if 1 <= len(requests) <= 3 and all_requests_have_options(requests):
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_bounded_questions",
                observed_summary=f"Agent asked {len(requests)} tracked planning question(s), each with options.",
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_bounded_questions",
                observed_summary=(
                    f"Expected 1-3 pending planning questions with options; found {len(requests)} "
                    f"with options={all_requests_have_options(requests)}."
                ),
            )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_no_substantive_work")
        self._record_forbidden_before_end(
            run_id,
            inbound.timestamp,
            "verify_no_substantive_work",
            SUBSTANTIVE_WORK_TOOL_NAMES,
        )


@register_scenario
class PlanningClearTaskEndsPlanningFirstScenario(BehaviorMicroScenario):
    slug = PLANNING_CLEAR_TASK_ENDS_PLANNING_FIRST
    description = "A clear task in planning mode should call end_planning before doing substantive work."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_end_planning", assertion_type="manual"),
        ScenarioTask(name="verify_no_work_before_end_planning", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.PLANNING)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                (
                    "Set up a daily 9am ET digest of SEC enforcement press releases. "
                    "Summarize new actions, include links, and skip days with no updates."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=self._planning_guardrail_mocks(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_end_planning")
        end_call = PersistentAgentToolCall.objects.filter(
            step__eval_run_id=run_id,
            step__created_at__gte=inbound.timestamp,
            tool_name="end_planning",
        ).select_related("step").order_by("step__created_at", "step_id").first()
        if end_call:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_end_planning",
                observed_summary="Agent called end_planning.",
                artifacts={"step": end_call.step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_end_planning",
                observed_summary="Agent did not call end_planning for a clear planning-mode task.",
            )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_no_work_before_end_planning",
        )
        self._record_forbidden_before_end(
            run_id,
            inbound.timestamp,
            "verify_no_work_before_end_planning",
            SUBSTANTIVE_WORK_TOOL_NAMES,
        )


@register_scenario
class PlanningExecuteRequestStaysInPlanningScenario(BehaviorMicroScenario):
    slug = PLANNING_EXECUTE_REQUEST_STAYS_IN_PLANNING
    description = "An execute-now prompt should still either ask planning questions or end planning before work."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_first_meaningful_action", assertion_type="manual"),
        ScenarioTask(name="verify_no_execution_before_planning_exit", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.PLANNING)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                "Do not ask questions. Just execute now: research five competitors and email me the findings.",
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=self._planning_guardrail_mocks(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_first_meaningful_action",
        )
        first_call = get_first_relevant_tool_call(
            run_id,
            after=inbound.timestamp,
            ignored_tool_names=IGNORED_FIRST_ACTION_TOOL_NAMES,
        )
        if first_call and first_call.tool_name in PLANNING_ALLOWED_FIRST_ACTION_TOOL_NAMES:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_first_meaningful_action",
                observed_summary=f"First meaningful tool call was {first_call.tool_name}.",
                artifacts={"step": first_call.step},
            )
        else:
            seen = first_call.tool_name if first_call else "none"
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_first_meaningful_action",
                observed_summary=f"Expected request_human_input or end_planning first; saw {seen}.",
                artifacts={"step": first_call.step} if first_call else {},
            )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_no_execution_before_planning_exit",
        )
        self._record_forbidden_before_end(
            run_id,
            inbound.timestamp,
            "verify_no_execution_before_planning_exit",
            BROWSER_OR_SEARCH_TOOL_NAMES | {"http_request", "send_email", "send_sms"},
        )


@register_scenario
class PlanningNoDirectScheduleOrConfigUpdatesScenario(BehaviorMicroScenario):
    slug = PLANNING_NO_DIRECT_SCHEDULE_OR_CONFIG_UPDATES
    description = "Planning mode should not update schedule, charter, or runtime plan before end_planning."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_no_planning_state_mutations", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.PLANNING)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                (
                    "Set this up to monitor Hacker News every hour for posts about vector databases "
                    "and email me a digest whenever there are new relevant posts."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=self._planning_guardrail_mocks(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_no_planning_state_mutations",
        )
        mutations = get_planning_mutation_calls_before_end_planning(run_id, after=inbound.timestamp)
        if mutations:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_no_planning_state_mutations",
                observed_summary=f"Planning state was mutated before end_planning: {[c.tool_name for c in mutations]}",
                artifacts={"step": mutations[0].step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_no_planning_state_mutations",
                observed_summary="No schedule/config/plan mutation was attempted before end_planning.",
            )


@register_scenario
class ToolChoiceExactJsonUrlUsesHttpRequestScenario(BehaviorMicroScenario):
    slug = TOOL_CHOICE_EXACT_JSON_URL_USES_HTTP_REQUEST
    description = "An exact JSON API URL should be fetched with http_request, not search or browser tools."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_http_request_first", assertion_type="manual"),
        ScenarioTask(name="verify_exact_url", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.SKIPPED)
        self._enable_builtin_tools(agent_id, ["http_request"])

        target_url = "https://api.example.test/inventory/widget-123.json"
        mock_config = {
            "http_request": {"status": "ok", "status_code": 200, "content": '{"inventory_count": 42}'},
            "search_tools": {"status": "error", "message": "Search should not be needed for an exact API URL."},
            "spawn_web_task": {"status": "error", "message": "Browser task should not be needed for an exact API URL."},
            "mcp_brightdata_search_engine": {"status": "error", "message": "Search should not be needed."},
        }

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                f"Fetch {target_url} and tell me the inventory_count. Use the URL exactly; do not search.",
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_http_request_first")
        first_call = get_first_relevant_tool_call(
            run_id,
            after=inbound.timestamp,
            ignored_tool_names=IGNORED_FIRST_ACTION_TOOL_NAMES,
        )
        if first_call and first_call.tool_name == "http_request":
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_http_request_first",
                observed_summary="First meaningful tool call was http_request.",
                artifacts={"step": first_call.step},
            )
        else:
            seen = first_call.tool_name if first_call else "none"
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_http_request_first",
                observed_summary=f"Expected http_request first; saw {seen}.",
                artifacts={"step": first_call.step} if first_call else {},
            )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_exact_url")
        http_calls = get_tool_calls_for_run(run_id, after=inbound.timestamp, tool_names={"http_request"})
        matching = [
            call for call in http_calls
            if (call.tool_params or {}).get("url") == target_url
        ]
        if matching:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_exact_url",
                observed_summary="http_request used the exact target URL.",
                artifacts={"step": matching[0].step},
            )
        else:
            seen_urls = [(call.tool_params or {}).get("url") for call in http_calls]
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_exact_url",
                observed_summary=f"Expected URL {target_url}; saw {seen_urls}.",
            )


@register_scenario
class ToolChoiceCsvDeliverableUsesCreateCsvScenario(BehaviorMicroScenario):
    slug = TOOL_CHOICE_CSV_DELIVERABLE_USES_CREATE_CSV
    description = "A downloadable CSV request should use create_csv."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_create_csv", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.SKIPPED)
        self._enable_builtin_tools(agent_id, ["create_csv"])

        mock_config = {
            "create_csv": {
                "status": "ok",
                "file": {"path": "/exports/q1-leads.csv"},
                "message": "CSV created.",
            },
            "create_file": {"status": "error", "message": "Use create_csv for CSV deliverables."},
        }

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                (
                    "Create a downloadable CSV at /exports/q1-leads.csv with these rows: "
                    "company,priority\\nAcme,high\\nGlobex,medium\\nInitech,low."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_create_csv")
        create_csv_calls = get_tool_calls_for_run(run_id, after=inbound.timestamp, tool_names={"create_csv"})
        if create_csv_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_create_csv",
                observed_summary="Agent used create_csv for the CSV deliverable.",
                artifacts={"step": create_csv_calls[0].step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_create_csv",
                observed_summary="Agent did not use create_csv for the CSV deliverable.",
            )


@register_scenario
class ToolChoicePdfDeliverableUsesCreatePdfScenario(BehaviorMicroScenario):
    slug = TOOL_CHOICE_PDF_DELIVERABLE_USES_CREATE_PDF
    description = "A formatted PDF request should use create_pdf."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_create_pdf", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.SKIPPED)
        self._enable_builtin_tools(agent_id, ["create_pdf"])

        mock_config = {
            "create_pdf": {
                "status": "ok",
                "file": {"path": "/exports/status-report.pdf"},
                "message": "PDF created.",
            },
            "create_file": {"status": "error", "message": "Use create_pdf for PDF deliverables."},
        }

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                (
                    "Create a formatted one-page PDF at /exports/status-report.pdf. "
                    "Title it 'Weekly Status' and include sections for wins, risks, and next steps."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_create_pdf")
        create_pdf_calls = get_tool_calls_for_run(run_id, after=inbound.timestamp, tool_names={"create_pdf"})
        if create_pdf_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_create_pdf",
                observed_summary="Agent used create_pdf for the PDF deliverable.",
                artifacts={"step": create_pdf_calls[0].step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_create_pdf",
                observed_summary="Agent did not use create_pdf for the PDF deliverable.",
            )


@register_scenario
class ToolChoiceMissingRecipientUsesHumanInputScenario(BehaviorMicroScenario):
    slug = TOOL_CHOICE_MISSING_RECIPIENT_USES_HUMAN_INPUT
    description = "A missing-recipient email task should ask for tracked human input instead of sending email."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_human_input", assertion_type="manual"),
        ScenarioTask(name="verify_no_send_email", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.SKIPPED)

        mock_config = {
            "send_email": {"status": "error", "message": "Missing-recipient eval forbids sending email."},
        }

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                "Email the client a short project status report. Use the latest status and keep it concise.",
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_human_input")
        requests = get_pending_human_input_requests(agent_id, run_id, after=inbound.timestamp)
        if requests:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_human_input",
                observed_summary=f"Agent requested missing recipient/details via {len(requests)} human input request(s).",
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_human_input",
                observed_summary="Agent did not create a tracked human input request for missing email details.",
            )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_no_send_email")
        send_email_calls = get_tool_calls_for_run(run_id, after=inbound.timestamp, tool_names={"send_email"})
        if send_email_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_no_send_email",
                observed_summary="Agent attempted send_email despite missing recipient/details.",
                artifacts={"step": send_email_calls[0].step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_no_send_email",
                observed_summary="Agent did not attempt send_email.",
            )
