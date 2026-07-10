import json
import re
from decimal import Decimal
from typing import Any, Iterable

from django.db.models import Sum

from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_SERVER
from api.agent.tools.sqlite_query_quality import summarize_sqlite_tool_result_calls
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.agent.tools.web_chat_sender import _looks_like_routine_progress_message
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import register_scenario
from api.evals.stop_policy import (
    split_sql_statements,
    sqlite_batch_is_only_eval_bookkeeping_read,
    sqlite_batch_is_only_planning_state_mutation,
    sqlite_batch_is_only_planning_state_read,
    sqlite_batch_mutates_planning_state,
    sqlite_batch_sql,
)
from api.models import (
    EvalRun,
    EvalRunTask,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentCronTrigger,
    PersistentAgentEnabledTool,
    PersistentAgentHumanInputRequest,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
)

EFFORT_TRIVIAL_ANSWER_STOPS = "effort_trivial_answer_stops"
EFFORT_SIMPLE_LOOKUP_BOUNDED_TOOLS = "effort_simple_lookup_bounded_tools"
EFFORT_SCHEDULED_BRIEFING_FINISHES = "effort_scheduled_briefing_finishes"
EFFORT_DEFAULTABLE_RESEARCH_NO_QUESTION_BATTERY = "effort_defaultable_research_no_question_battery"
EFFORT_PARTIAL_BRIEFING_REPORTS_WITHOUT_SURVEY = "effort_partial_briefing_reports_without_survey"
EFFORT_CHART_REQUESTED_SINGLE_ARTIFACT = "effort_chart_requested_single_artifact"
EFFORT_SIMPLE_CURRENT_YC_BATCH_REPORT = "effort_simple_current_yc_batch_report"
EFFORT_SIMPLE_CURRENT_COMPANY_REPORT = "effort_simple_current_company_report"
EFFORT_EXPLICIT_DEEP_RESEARCH_REMAINS_CAPABLE = "effort_explicit_deep_research_remains_capable"
EFFORT_UNSCHEDULED_REMAINING_WORK_SETS_RESUME = "effort_unscheduled_remaining_work_sets_resume"
EFFORT_PARTIAL_SOURCE_BLOCK_REPORTS_AND_RESUMES = "effort_partial_source_block_reports_and_resumes"
EFFORT_TOOL_WAIT_NEXT_SCHEDULE_REQUIRES_SCHEDULE = "effort_tool_wait_next_schedule_requires_schedule"

EFFORT_CALIBRATION_SCENARIO_SLUGS = [
    EFFORT_TRIVIAL_ANSWER_STOPS,
    EFFORT_SIMPLE_LOOKUP_BOUNDED_TOOLS,
    EFFORT_SCHEDULED_BRIEFING_FINISHES,
    EFFORT_DEFAULTABLE_RESEARCH_NO_QUESTION_BATTERY,
    EFFORT_PARTIAL_BRIEFING_REPORTS_WITHOUT_SURVEY,
    EFFORT_CHART_REQUESTED_SINGLE_ARTIFACT,
    EFFORT_SIMPLE_CURRENT_YC_BATCH_REPORT,
    EFFORT_SIMPLE_CURRENT_COMPANY_REPORT,
    EFFORT_EXPLICIT_DEEP_RESEARCH_REMAINS_CAPABLE,
    EFFORT_UNSCHEDULED_REMAINING_WORK_SETS_RESUME,
    EFFORT_PARTIAL_SOURCE_BLOCK_REPORTS_AND_RESUMES,
    EFFORT_TOOL_WAIT_NEXT_SCHEDULE_REQUIRES_SCHEDULE,
]

MESSAGE_TOOL_NAMES = {
    "send_chat_message",
    "send_email",
    "send_sms",
}
STOP_TOOL_NAMES = {
    "sleep_until_next_trigger",
}
ARTIFACT_TOOL_NAMES = {
    "create_chart",
    "create_csv",
    "create_file",
    "create_image",
    "create_pdf",
    "create_video",
}
RESEARCH_TOOL_NAMES = {
    "http_request",
    "mcp_brightdata_scrape_as_markdown",
    "mcp_brightdata_search_engine",
    "search_engine",
    "search_engine_batch",
    "search_tools",
    "spawn_web_task",
}
EFFORT_OVERWORK_TOOL_NAMES = {
    *ARTIFACT_TOOL_NAMES,
    "request_human_input",
    "secure_credentials_request",
    "spawn_agent",
    "update_plan",
}
PARTIAL_SOURCE_FORBIDDEN_TOOL_NAMES = (
    (EFFORT_OVERWORK_TOOL_NAMES - {"update_plan"}) | ARTIFACT_TOOL_NAMES | RESEARCH_TOOL_NAMES
)
WEB_QUERY_PARAM_NAMES = ("query", "keyword", "prompt")
_WORD_RE = re.compile(r"[a-z0-9]+")
_SQL_TOOL_RESULT_TEXT_RE = re.compile(
    r"\bfrom\s+__tool_results\b[^;]*\bresult_text\b|\bresult_text\b[^;]*\bfrom\s+__tool_results\b",
    re.IGNORECASE | re.DOTALL,
)
_SQL_RESULT_ID_RE = re.compile(r"\bresult_id\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
_HEADING_RE = re.compile(r"(?im)^\s{0,3}#{1,4}\s+\S|^\s*\*\*[^*\n]{3,80}\*\*:?\s*$|<h[1-4]\b")
_LIST_OR_TABLE_RE = re.compile(
    r"(?im)^\s*(?:[-*]|\d+[.)])\s+\S|^\s*\*\*\d+[.)]\s+\S|^\s*\|.+\|\s*$|<\s*(?:ul|ol|li|table)\b"
)
_MARKDOWN_URL_LINK_RE = re.compile(
    r"\[[^\]]*(?:https?://|www\.|[a-z0-9.-]+\.[a-z]{2,}/)[^\]]*\]\(https?://[^)]+\)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"(?:https?://|www\.)\S+")


def _normalize_similarity_text(value: str) -> str:
    return " ".join(_WORD_RE.findall((value or "").casefold()))


def _token_set(value: str) -> set[str]:
    return set(_normalize_similarity_text(value).split())


def _near_duplicate_text(first: str, second: str) -> bool:
    first_normalized = _normalize_similarity_text(first)
    second_normalized = _normalize_similarity_text(second)
    if not first_normalized or not second_normalized:
        return False
    if first_normalized == second_normalized:
        return True

    first_tokens = _token_set(first_normalized)
    second_tokens = _token_set(second_normalized)
    if len(first_tokens) < 4 or len(second_tokens) < 4:
        return False

    overlap = len(first_tokens & second_tokens)
    union = len(first_tokens | second_tokens)
    containment = overlap / min(len(first_tokens), len(second_tokens))
    jaccard = overlap / union if union else 0
    return containment >= 0.9 or jaccard >= 0.82


def _find_near_duplicate_texts(values: Iterable[str]) -> list[tuple[str, str]]:
    seen: list[str] = []
    duplicates = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        for prior in seen:
            if _near_duplicate_text(prior, text):
                duplicates.append((prior, text))
                break
        seen.append(text)
    return duplicates


def _web_query_value(params: dict) -> str | None:
    for param_name in WEB_QUERY_PARAM_NAMES:
        value = params.get(param_name)
        if value:
            return str(value)
    return None


def _sqlite_result_text_reads(sql: str) -> list[str]:
    reads = []
    for statement in split_sql_statements(sql):
        if not _SQL_TOOL_RESULT_TEXT_RE.search(statement):
            continue
        result_ids = _SQL_RESULT_ID_RE.findall(statement)
        if result_ids:
            reads.extend(result_ids)
        else:
            reads.append("__tool_results.result_text")
    return reads


_PREEXECUTION_SQLITE_RESULT_SHAPE_REJECTIONS = frozenset(
    {
        "repeated_unshaped_tool_result_projection",
        "unshaped_multi_result_payload",
    }
)


def _tool_call_was_preexecution_shape_rejection(call: PersistentAgentToolCall) -> bool:
    try:
        result = json.loads(call.result or "")
    except (TypeError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(result, dict)
        and str(result.get("status") or "").casefold() == "error"
        and result.get("error_type") in _PREEXECUTION_SQLITE_RESULT_SHAPE_REJECTIONS
    )


def _hierarchical_report_shape(
    body: str,
    *,
    source_urls: Iterable[str],
    min_source_count: int,
    min_chars: int,
    max_chars: int,
    required_any_groups: Iterable[Iterable[str]] = (),
) -> tuple[bool, str]:
    text = body or ""
    linked_sources = [url for url in source_urls if _source_url_mentioned(text, url)]
    missing_groups = []
    text_folded = text.casefold()
    for group in required_any_groups:
        options = [option for option in group if option]
        if options and not any(option.casefold() in text_folded for option in options):
            missing_groups.append(options)

    heading_count = len(_HEADING_RE.findall(text))
    has_heading = heading_count > 0
    has_list_or_table = bool(_LIST_OR_TABLE_RE.search(text))
    has_structured_detail = has_list_or_table or heading_count >= 2
    has_sections = text.count("\n\n") >= 2 or heading_count >= 2
    failures = []
    if len(text) < min_chars:
        failures.append(f"too short ({len(text)} chars < {min_chars})")
    if len(text) > max_chars:
        failures.append(f"too long ({len(text)} chars > {max_chars})")
    if len(linked_sources) < min_source_count:
        failures.append(f"too few source links ({len(linked_sources)} < {min_source_count})")
    if missing_groups:
        failures.append(f"missing required concept group(s): {missing_groups}")
    if not has_heading:
        failures.append("missing visible heading")
    if not has_structured_detail:
        failures.append("missing bullets, numbered list, table, or multiple visible sections")
    if not has_sections:
        failures.append("missing section breaks")

    if failures:
        return False, "; ".join(failures)
    return True, f"Structured report with {len(text)} chars and {len(linked_sources)} source link(s)."


def _source_url_mentioned(text: str, url: str) -> bool:
    if not url:
        return False
    if url in text:
        return True
    bare_url = re.sub(r"^https?://", "", url).rstrip("/")
    return bool(bare_url and bare_url in text)


def _tool_calls_for_run(run_id: str, *, after=None, tool_names: Iterable[str] | None = None):
    queryset = PersistentAgentToolCall.objects.filter(step__eval_run_id=run_id)
    if after is not None:
        queryset = queryset.filter(step__created_at__gte=after)
    if tool_names is not None:
        queryset = queryset.filter(tool_name__in=list(tool_names))
    return list(queryset.select_related("step").order_by("step__created_at", "step__id"))


def _relevant_tool_calls_for_run(
    run_id: str,
    *,
    after=None,
    ignored_tool_names: Iterable[str] = (),
):
    ignored = set(ignored_tool_names)
    relevant = []
    for call in _tool_calls_for_run(run_id, after=after):
        if call.tool_name in ignored:
            continue
        if sqlite_batch_is_only_eval_bookkeeping_read(call):
            continue
        if sqlite_batch_is_only_planning_state_read(call):
            continue
        if sqlite_batch_is_only_planning_state_mutation(call):
            continue
        relevant.append(call)
    return relevant


def _outbound_messages_after(agent_id: str, after):
    return list(
        PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            is_outbound=True,
            timestamp__gt=after,
        ).order_by("timestamp", "id")
    )


def _sqlite_call_persists_resume_state(call: PersistentAgentToolCall) -> bool:
    if call.tool_name != "sqlite_batch":
        return False
    if str(call.status or "").lower() != "complete":
        return False

    sql = sqlite_batch_sql(call)
    lowered = sql.casefold()
    if "__agent_config" in lowered or re.search(r"\bcharter\b", lowered):
        return False

    for raw_statement in split_sql_statements(sql):
        statement = re.sub(r"/\*.*?\*/|--[^\n]*", " ", raw_statement, flags=re.DOTALL).casefold()
        has_remaining_count = bool(
            re.search(
                r"\b(?:remaining(?:_(?:work|count|items|rows))?|pending_count|items_remaining|work_remaining)\b",
                statement,
            )
        )
        if not has_remaining_count or not re.search(r"\b(?:next_)?cursor\b", statement):
            continue

        mutation_targets = re.findall(
            r"\b(?:insert(?:\s+or\s+\w+)?\s+into|replace\s+into|update)\s+"
            r'["`\[]?([a-z_][\w$]*)',
            statement,
            re.IGNORECASE,
        )
        mutation_targets.extend(
            re.findall(
                r"\bcreate\s+(?:temp(?:orary)?\s+)?table\s+(?:if\s+not\s+exists\s+)?"
                r'["`\[]?([a-z_][\w$]*)["`\]]?\s+as\s+(?:with\b|select\b|values\b)',
                statement,
                re.IGNORECASE,
            )
        )
        if any(not table_name.startswith("__") for table_name in mutation_targets):
            return True
    return False


def _human_input_requests_for_run(run_id: str, *, after=None):
    queryset = PersistentAgentHumanInputRequest.objects.filter(originating_step__eval_run_id=run_id)
    if after is not None:
        queryset = queryset.filter(created_at__gte=after)
    return list(queryset.order_by("created_at", "id"))


def _question_count(text: str) -> int:
    without_urls = _MARKDOWN_URL_LINK_RE.sub("", text or "")
    without_urls = _URL_RE.sub("", without_urls)
    return without_urls.count("?")


def _orchestrator_completion_count(run_id: str) -> int:
    return PersistentAgentCompletion.objects.filter(
        eval_run_id=run_id,
        completion_type=PersistentAgentCompletion.CompletionType.ORCHESTRATOR,
    ).count()


def _completion_token_summary(run_id: str) -> dict[str, int]:
    totals = PersistentAgentCompletion.objects.filter(eval_run_id=run_id).aggregate(
        prompt_tokens=Sum("prompt_tokens"),
        completion_tokens=Sum("completion_tokens"),
        total_tokens=Sum("total_tokens"),
    )
    return {key: int(value or 0) for key, value in totals.items()}


class EffortCalibrationScenario(EvalScenario, ScenarioExecutionTools):
    fingerprint_dependencies = (
        summarize_sqlite_tool_result_calls,
        _looks_like_routine_progress_message,
        split_sql_statements,
        sqlite_batch_is_only_eval_bookkeeping_read,
        sqlite_batch_is_only_planning_state_mutation,
        sqlite_batch_is_only_planning_state_read,
        sqlite_batch_mutates_planning_state,
        sqlite_batch_sql,
        _normalize_similarity_text,
        _token_set,
        _near_duplicate_text,
        _find_near_duplicate_texts,
        _web_query_value,
        _sqlite_result_text_reads,
        _hierarchical_report_shape,
        _source_url_mentioned,
        _tool_calls_for_run,
        _relevant_tool_calls_for_run,
        _outbound_messages_after,
        _sqlite_call_persists_resume_state,
        _tool_call_was_preexecution_shape_rejection,
        _human_input_requests_for_run,
        _question_count,
        _orchestrator_completion_count,
        _completion_token_summary,
    )
    fingerprint_data = {
        "message_tool_names": MESSAGE_TOOL_NAMES,
        "stop_tool_names": STOP_TOOL_NAMES,
        "artifact_tool_names": ARTIFACT_TOOL_NAMES,
        "research_tool_names": RESEARCH_TOOL_NAMES,
        "effort_overwork_tool_names": EFFORT_OVERWORK_TOOL_NAMES,
        "partial_source_forbidden_tool_names": PARTIAL_SOURCE_FORBIDDEN_TOOL_NAMES,
        "web_query_param_names": WEB_QUERY_PARAM_NAMES,
        "word_pattern": _WORD_RE.pattern,
        "sqlite_result_text_pattern": _SQL_TOOL_RESULT_TEXT_RE.pattern,
        "preexecution_sqlite_result_shape_rejections": sorted(
            _PREEXECUTION_SQLITE_RESULT_SHAPE_REJECTIONS
        ),
        "sqlite_result_id_pattern": _SQL_RESULT_ID_RE.pattern,
        "heading_pattern": _HEADING_RE.pattern,
        "list_or_table_pattern": _LIST_OR_TABLE_RE.pattern,
        "markdown_url_link_pattern": _MARKDOWN_URL_LINK_RE.pattern,
        "url_pattern": _URL_RE.pattern,
    }
    tier = "core"
    category = "effort_calibration"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("agent_behavior", "effort_calibration", "overwork")

    def _ready_agent(self, agent_id: str, *, charter: str = "Answer user requests directly.", schedule: str | None = None):
        PersistentAgent.objects.filter(id=agent_id).update(
            charter=charter,
            schedule=schedule,
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )
        if PersistentAgentStep.objects.filter(agent_id=agent_id, system_step__code="PROCESS_EVENTS").exists():
            return
        prior_step = PersistentAgentStep.objects.create(
            agent_id=agent_id,
            description="Process events",
        )
        PersistentAgentSystemStep.objects.create(
            step=prior_step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        )

    def _enable_builtin_tools(self, agent_id: str, tool_names: Iterable[str]) -> None:
        agent = PersistentAgent.objects.get(id=agent_id)
        for tool_name in tool_names:
            mark_tool_enabled_without_discovery(agent, tool_name)

    def _enable_eval_synthetic_tools(self, agent_id: str, tool_names: Iterable[str]) -> None:
        agent = PersistentAgent.objects.get(id=agent_id)
        for tool_name in tool_names:
            mark_tool_enabled_without_discovery(agent, tool_name)
            PersistentAgentEnabledTool.objects.filter(
                agent=agent,
                tool_full_name=tool_name,
            ).update(
                tool_server=EVAL_SYNTHETIC_TOOL_SERVER,
                tool_name=tool_name,
            )

    def _seed_recent_high_burn(self, agent_id: str, *, credits: str = "45") -> PersistentAgentStep:
        step = PersistentAgentStep.objects.create(
            agent_id=agent_id,
            description="Prior high-burn eval work chunk",
        )
        PersistentAgentStep.objects.filter(id=step.id).update(credits_cost=Decimal(credits))
        step.credits_cost = Decimal(credits)
        return step

    def _is_simulated(self, run_id: str) -> bool:
        run = EvalRun.objects.select_related("suite_run").get(id=run_id)
        suite_run = run.suite_run
        return bool(suite_run and (suite_run.launch_config or {}).get("mode") == "simulated")

    def _record_simulated_tool_call(
        self,
        run_id: str,
        agent_id: str,
        *,
        tool_name: str,
        tool_params: dict,
        result: dict | None = None,
    ) -> None:
        step = PersistentAgentStep.objects.create(
            agent_id=agent_id,
            eval_run_id=run_id,
            description=f"Simulated eval tool call: {tool_name}",
        )
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name=tool_name,
            tool_params=tool_params,
            result=json.dumps(result or {"status": "ok"}),
        )

    def _send_simulated_web_report(self, agent_id: str, body: str) -> None:
        from api.agent.tools.web_chat_sender import execute_send_chat_message

        agent = PersistentAgent.objects.get(id=agent_id)
        result = execute_send_chat_message(
            agent,
            {
                "body": body,
                "will_continue_work": False,
            },
        )
        if result.get("status") != "ok":
            raise RuntimeError(f"Simulated web report failed: {result.get('message')}")

    def _record_single_concise_reply(
        self,
        run_id: str,
        *,
        agent_id: str,
        after,
        task_name: str,
        max_chars: int,
        required_snippets: Iterable[str] = (),
    ) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        outbound = _outbound_messages_after(agent_id, after)
        if len(outbound) != 1:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name=task_name,
                observed_summary=f"Expected exactly one outbound reply; found {len(outbound)}.",
                artifacts={"message": outbound[0]} if outbound else {},
            )
            return False

        body = outbound[0].body or ""
        body_for_match = body.casefold()
        missing = [
            snippet
            for snippet in required_snippets
            if snippet.casefold() not in body_for_match
        ]
        if len(body) <= max_chars and not missing:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary=f"One concise outbound reply ({len(body)} chars).",
                artifacts={"message": outbound[0]},
            )
            return True

        reasons = []
        if len(body) > max_chars:
            reasons.append(f"reply had {len(body)} chars, above max {max_chars}")
        if missing:
            reasons.append(f"missing expected snippet(s): {missing}")
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name=task_name,
            observed_summary="; ".join(reasons),
            artifacts={"message": outbound[0]},
        )
        return False

    def _record_no_overwork_tools(
        self,
        run_id: str,
        *,
        after,
        task_name: str,
        forbidden_tool_names: Iterable[str] = EFFORT_OVERWORK_TOOL_NAMES,
        max_plan_updates: int = 1,
    ) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        forbidden = set(forbidden_tool_names)
        relevant = _relevant_tool_calls_for_run(run_id, after=after)
        bad_calls = [
            call
            for call in relevant
            if call.tool_name in forbidden
        ]
        plan_calls = [call for call in relevant if call.tool_name == "update_plan"]
        if bad_calls or len(plan_calls) > max_plan_updates:
            details = [f"forbidden tool(s): {[call.tool_name for call in bad_calls]}"] if bad_calls else []
            if len(plan_calls) > max_plan_updates:
                details.append(f"plan updates {len(plan_calls)}/{max_plan_updates}")
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name=task_name,
                observed_summary=f"Unexpected overwork: {'; '.join(details)}.",
                artifacts={"step": (bad_calls or plan_calls)[0].step},
            )
            return False

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name=task_name,
            observed_summary=(
                "No unnecessary artifact or human-input tools were used; "
                f"plan updates={len(plan_calls)}/{max_plan_updates}."
            ),
        )
        return True

    def _record_future_work_preserved(
        self,
        run_id: str,
        *,
        agent_id: str,
        after,
        task_name: str,
        work_tool_names: Iterable[str],
        min_work_calls_if_unscheduled: int = 2,
    ) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        agent = PersistentAgent.objects.get(id=agent_id)
        if (agent.schedule or "").strip():
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary=f"Unfinished durable work has resume schedule: {agent.schedule}.",
                artifacts={"agent": agent},
            )
            return True

        work_tools = set(work_tool_names)
        work_calls = [
            call
            for call in _tool_calls_for_run(run_id, after=after)
            if call.tool_name in work_tools
        ]
        if len(work_calls) >= min_work_calls_if_unscheduled:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary=(
                    "No schedule was set, but the agent continued bounded work instead of stopping "
                    f"after the first unfinished batch ({len(work_calls)} work calls)."
                ),
                artifacts={"step": work_calls[-1].step},
            )
            return True

        resume_state_calls = [
            call
            for call in _tool_calls_for_run(run_id, after=after, tool_names=["sqlite_batch"])
            if _sqlite_call_persists_resume_state(call)
        ]
        if resume_state_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary="No schedule was set, but the agent persisted cursor/remaining-work resume state.",
                artifacts={"step": resume_state_calls[-1].step},
            )
            return True

        outbound = _outbound_messages_after(agent_id, after)
        artifacts = {"message": outbound[-1]} if outbound else {}
        if not artifacts and work_calls:
            artifacts = {"step": work_calls[0].step}
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name=task_name,
            observed_summary=(
                "Expected another bounded work call, a valid resume schedule, or a dedicated SQLite resume "
                "table containing a remaining-work count and cursor. Storing task state in __agent_config/charter does "
                f"not count; schedule is empty and work_calls={len(work_calls)}."
            ),
            artifacts=artifacts,
        )
        return False

    def _record_no_unscheduled_wait_claim(
        self,
        run_id: str,
        *,
        agent_id: str,
        after,
        task_name: str,
    ) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        agent = PersistentAgent.objects.get(id=agent_id)
        if (agent.schedule or "").strip():
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary="Agent has a schedule, so scheduled-run language is valid.",
            )
            return True

        bad_messages = [
            message
            for message in _outbound_messages_after(agent_id, after)
            if "next scheduled run" in (message.body or "").casefold()
            or "next schedule" in (message.body or "").casefold()
        ]
        if not bad_messages:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary="No unscheduled 'next scheduled run' claim was sent.",
            )
            return True

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name=task_name,
            observed_summary="Agent claimed work would wait for a scheduled run while no schedule exists.",
            artifacts={"message": bad_messages[0]},
        )
        return False

    def _record_no_question_battery(
        self,
        run_id: str,
        *,
        agent_id: str,
        after,
        task_name: str,
        max_message_questions: int = 1,
    ) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        requests = _human_input_requests_for_run(run_id, after=after)
        outbound = _outbound_messages_after(agent_id, after)
        question_marks = sum(_question_count(message.body or "") for message in outbound)
        progress_messages = [
            message for message in outbound if _looks_like_routine_progress_message(message.body or "")
        ]
        if not requests and not progress_messages and question_marks <= max_message_questions:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary=(
                    "No tracked human-input request, progress-only message, and final message question count was "
                    f"{question_marks}/{max_message_questions}."
                ),
                artifacts={"message": outbound[-1]} if outbound else {},
            )
            return True

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name=task_name,
            observed_summary=(
                f"Expected no request_human_input and at most {max_message_questions} question mark(s); "
                f"human_input_requests={len(requests)}, progress_messages={len(progress_messages)}, "
                f"message_question_marks={question_marks}."
            ),
            artifacts={"message": progress_messages[0] if progress_messages else outbound[-1]} if outbound else {},
        )
        return False

    def _record_no_agent_config_mutation(
        self,
        run_id: str,
        *,
        after,
        task_name: str,
    ) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        mutations = [
            call
            for call in _tool_calls_for_run(run_id, after=after, tool_names={"sqlite_batch"})
            if sqlite_batch_mutates_planning_state(call)
        ]
        if not mutations:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary="No charter or schedule mutation occurred for this completed one-off/scheduled task.",
            )
            return True

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name=task_name,
            observed_summary=f"Unexpected __agent_config mutation count: {len(mutations)}.",
            artifacts={"step": mutations[0].step},
        )
        return False

    def _record_orchestrator_budget(
        self,
        run_id: str,
        *,
        task_name: str,
        max_completions: int,
    ) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        count = _orchestrator_completion_count(run_id)
        token_summary = _completion_token_summary(run_id)
        if count <= max_completions:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary=(
                    f"Used {count} orchestrator completion(s), within budget {max_completions}. "
                    f"Tokens: {token_summary}."
                ),
            )
            return True

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name=task_name,
            observed_summary=(
                f"Used {count} orchestrator completions; expected at most {max_completions}. "
                f"Tokens: {token_summary}."
            ),
        )
        return False

    def _record_plan_update_budget(
        self,
        run_id: str,
        *,
        after,
        task_name: str,
        max_updates: int,
    ) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        plan_calls = _tool_calls_for_run(run_id, after=after, tool_names={"update_plan"})
        if len(plan_calls) <= max_updates:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary=f"Used {len(plan_calls)} plan update(s), within budget {max_updates}.",
            )
            return True

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name=task_name,
            observed_summary=f"Expected at most {max_updates} plan update(s); saw {len(plan_calls)}.",
            artifacts={"step": plan_calls[0].step},
        )
        return False

    def _record_no_repetitive_web_queries(
        self,
        run_id: str,
        *,
        after,
        task_name: str,
    ) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        query_values = []
        for call in _tool_calls_for_run(run_id, after=after):
            if call.tool_name == "search_tools":
                continue
            params = call.tool_params or {}
            value = _web_query_value(params)
            if value:
                query_values.append(value)

        duplicates = _find_near_duplicate_texts(query_values)
        if not duplicates:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary=(
                    "No identical or near-identical web query loop across "
                    f"{len(query_values)} query value(s)."
                ),
            )
            return True

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name=task_name,
            observed_summary=f"Near-duplicate web queries detected: {duplicates[:2]}.",
        )
        return False

    def _record_no_sqlite_result_text_reread_loop(
        self,
        run_id: str,
        *,
        after,
        task_name: str,
        max_result_text_reads: int = 0,
    ) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        reads = []
        first_bad_call = None
        sqlite_calls = [
            call for call in _tool_calls_for_run(run_id, after=after, tool_names={"sqlite_batch"})
            if not _tool_call_was_preexecution_shape_rejection(call)
        ]
        for call in sqlite_calls:
            call_reads = _sqlite_result_text_reads(sqlite_batch_sql(call))
            if call_reads and first_bad_call is None:
                first_bad_call = call
            reads.extend(call_reads)

        usage = summarize_sqlite_tool_result_calls(sqlite_calls)
        duplicate_reads = [first for first, _second in _find_near_duplicate_texts(reads)]
        duplicate_blob_fetches = usage.duplicate_direct_fetches
        if len(reads) <= max_result_text_reads and not duplicate_reads and not duplicate_blob_fetches:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary=(
                    f"Observed {len(reads)} __tool_results.result_text read(s), "
                    f"within budget {max_result_text_reads}, with no reread loop. "
                    f"Smart aggregate queries={usage.smart_tool_result_queries}."
                ),
            )
            return True

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name=task_name,
            observed_summary=(
                f"Expected at most {max_result_text_reads} result_text read(s) and no duplicate reads; "
                f"saw reads={reads[:8]}, duplicate_blob_fetches={duplicate_blob_fetches}."
            ),
            artifacts={"step": first_bad_call.step} if first_bad_call else {},
        )
        return False

    def _record_hierarchical_report(
        self,
        run_id: str,
        *,
        agent_id: str,
        after,
        task_name: str,
        source_urls: Iterable[str],
        min_source_count: int,
        min_chars: int,
        max_chars: int,
        required_any_groups: Iterable[Iterable[str]] = (),
    ) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        outbound = _outbound_messages_after(agent_id, after)
        if len(outbound) != 1:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name=task_name,
                observed_summary=f"Expected exactly one final outbound report; found {len(outbound)}.",
                artifacts={"message": outbound[0]} if outbound else {},
            )
            return False

        body = outbound[0].body or ""
        ok, summary = _hierarchical_report_shape(
            body,
            source_urls=source_urls,
            min_source_count=min_source_count,
            min_chars=min_chars,
            max_chars=max_chars,
            required_any_groups=required_any_groups,
        )
        if ok:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary=summary,
                artifacts={"message": outbound[0]},
            )
            return True

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name=task_name,
            observed_summary=summary,
            artifacts={"message": outbound[0]},
        )
        return False

    def _record_research_tool_budget(
        self,
        run_id: str,
        *,
        after,
        task_name: str,
        allowed_tool_names: Iterable[str],
        min_relevant_calls: int,
        max_relevant_calls: int,
    ) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        relevant = _relevant_tool_calls_for_run(
            run_id,
            after=after,
            ignored_tool_names=MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES,
        )
        allowed = set(allowed_tool_names)
        unexpected = [call.tool_name for call in relevant if call.tool_name not in allowed]
        if min_relevant_calls <= len(relevant) <= max_relevant_calls and not unexpected:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary=f"Relevant tool calls were {[call.tool_name for call in relevant]}.",
            )
            return True

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name=task_name,
            observed_summary=(
                f"Expected {min_relevant_calls}-{max_relevant_calls} relevant calls from {sorted(allowed)}; "
                f"saw {[call.tool_name for call in relevant]}, unexpected={unexpected}."
            ),
            artifacts={"step": relevant[0].step} if relevant else {},
        )
        return False


@register_scenario
class EffortTrivialAnswerStopsScenario(EffortCalibrationScenario):
    slug = EFFORT_TRIVIAL_ANSWER_STOPS
    description = "A trivial user request should receive one minimal answer and stop without plans, artifacts, or follow-up questions."
    tasks = [
        ScenarioTask.setup(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_single_minimal_reply", assertion_type="manual"),
        ScenarioTask(name="verify_no_unnecessary_tools", assertion_type="manual"),
        ScenarioTask(name="verify_turn_budget", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                "What is 2 + 2? Reply with only the answer.",
                trigger_processing=True,
                eval_run_id=run_id,
                eval_stop_policy={
                    "stop_on_tool_names": list(EFFORT_OVERWORK_TOOL_NAMES | RESEARCH_TOOL_NAMES),
                    "max_relevant_tool_calls": 4,
                    "ignored_tool_names": list(MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES),
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self._record_single_concise_reply(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_single_minimal_reply",
            max_chars=80,
            required_snippets=("4",),
        )
        self._record_no_overwork_tools(run_id, after=inbound.timestamp, task_name="verify_no_unnecessary_tools")
        self._record_orchestrator_budget(run_id, task_name="verify_turn_budget", max_completions=2)


@register_scenario
class EffortSimpleLookupBoundedToolsScenario(EffortCalibrationScenario):
    slug = EFFORT_SIMPLE_LOOKUP_BOUNDED_TOOLS
    description = "A simple exact-URL lookup should use bounded tool breadth and return one concise answer."
    tasks = [
        ScenarioTask.setup(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_exact_fetch_once", assertion_type="manual"),
        ScenarioTask(name="verify_bounded_tool_breadth", assertion_type="manual"),
        ScenarioTask(name="verify_single_concise_reply", assertion_type="manual"),
        ScenarioTask(name="verify_no_config_churn", assertion_type="manual"),
        ScenarioTask(name="verify_turn_budget", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        self._enable_builtin_tools(agent_id, ["http_request"])
        target_url = "https://status.example.test/api/summary.json"
        mock_config = {
            "http_request": {
                "status": "ok",
                "status_code": 200,
                "content": json.dumps(
                    {
                        "status": "operational",
                        "updated_at": "2026-05-17T13:00:00Z",
                        "source_url": target_url,
                    }
                ),
            },
            "search_tools": {"status": "error", "message": "Exact URL lookup should not search."},
            "spawn_web_task": {"status": "error", "message": "Exact URL lookup should not use browser automation."},
        }

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                f"Fetch {target_url} and tell me the service status in one sentence. Use the URL exactly.",
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
                eval_stop_policy={
                    "stop_on_tool_names": list(EFFORT_OVERWORK_TOOL_NAMES | (RESEARCH_TOOL_NAMES - {"http_request"})),
                    "stop_on_sqlite_agent_config_mutation": True,
                    "max_relevant_tool_calls": 5,
                    "ignored_tool_names": list(MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES),
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_exact_fetch_once")
        http_calls = _tool_calls_for_run(run_id, after=inbound.timestamp, tool_names={"http_request"})
        exact_calls = [call for call in http_calls if (call.tool_params or {}).get("url") == target_url]
        if len(exact_calls) == 1 and len(http_calls) == 1:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_exact_fetch_once",
                observed_summary="Used one http_request call with the exact URL.",
                artifacts={"step": exact_calls[0].step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_exact_fetch_once",
                observed_summary=f"Expected one exact http_request; saw URLs {[(c.tool_params or {}).get('url') for c in http_calls]}.",
                artifacts={"step": http_calls[0].step} if http_calls else {},
            )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_bounded_tool_breadth")
        relevant = _relevant_tool_calls_for_run(
            run_id,
            after=inbound.timestamp,
            ignored_tool_names=MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES,
        )
        unexpected = [
            call.tool_name
            for call in relevant
            if call.tool_name != "http_request" or call.tool_name in EFFORT_OVERWORK_TOOL_NAMES
        ]
        if len(relevant) <= 1 and not unexpected:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_bounded_tool_breadth",
                observed_summary=f"Relevant non-message tool calls were bounded: {[call.tool_name for call in relevant]}.",
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_bounded_tool_breadth",
                observed_summary=f"Expected only one fetch tool; saw {[call.tool_name for call in relevant]}.",
                artifacts={"step": relevant[0].step} if relevant else {},
            )

        self._record_single_concise_reply(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_single_concise_reply",
            max_chars=400,
            required_snippets=("operational",),
        )
        self._record_no_agent_config_mutation(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_config_churn",
        )
        self._record_orchestrator_budget(run_id, task_name="verify_turn_budget", max_completions=3)


@register_scenario
class EffortScheduledBriefingFinishesScenario(EffortCalibrationScenario):
    slug = EFFORT_SCHEDULED_BRIEFING_FINISHES
    description = "A scheduled daily briefing should send one concise sourced report, create no artifacts, ask no follow-up questions, and finish."
    tasks = [
        ScenarioTask.setup(name="trigger_scheduled_run", assertion_type="manual"),
        ScenarioTask(name="verify_one_sourced_report", assertion_type="manual"),
        ScenarioTask(name="verify_no_human_input_after_report", assertion_type="manual"),
        ScenarioTask(name="verify_final_message_question_shape", assertion_type="manual"),
        ScenarioTask(name="verify_no_artifacts_or_plan", assertion_type="manual"),
        ScenarioTask(name="verify_no_config_churn", assertion_type="manual"),
        ScenarioTask(name="verify_bounded_run", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        schedule = "0 9 * * *"
        briefing_url = "https://briefing.example.test/api/daily.json"
        source_urls = (
            "https://news.example.test/global-election",
            "https://news.example.test/central-bank",
            "https://markets.example.test/world-cup-odds",
        )
        self._ready_agent(
            agent_id,
            charter=(
                "On each scheduled daily trigger, fetch the exact JSON feed at "
                f"{briefing_url} and send one concise web-chat briefing with three bullets and source links. "
                "Use send_chat_message for delivery; do not email or text this briefing. "
                "Do not create charts, files, or follow-up questions unless the feed is unavailable or a real blocker appears."
            ),
            schedule=schedule,
        )
        self._enable_builtin_tools(agent_id, ["http_request", "create_chart", "create_csv", "create_file", "create_pdf"])
        mock_config = {
            "http_request": {
                "status": "ok",
                "status_code": 200,
                "content": json.dumps(
                    {
                        "date": "2026-05-17",
                        "items": [
                            {
                                "headline": "Global election coalition talks continue",
                                "market_note": "Prediction market consensus moved to 58 percent.",
                                "source_url": source_urls[0],
                            },
                            {
                                "headline": "Central bank signals rate hold",
                                "market_note": "Rate-cut odds fell to 22 percent.",
                                "source_url": source_urls[1],
                            },
                            {
                                "headline": "World Cup favorite odds narrow",
                                "market_note": "Top favorite is priced at 18 percent.",
                                "source_url": source_urls[2],
                            },
                        ],
                    }
                ),
            },
            "search_tools": {"status": "error", "message": "Scheduled briefing feed is exact; search is unnecessary."},
            "spawn_web_task": {"status": "error", "message": "Scheduled briefing feed is exact; browser work is unnecessary."},
            "send_email": {"status": "error", "message": "Scheduled briefing eval requires web-chat delivery; use send_chat_message."},
            "send_sms": {"status": "error", "message": "Scheduled briefing eval requires web-chat delivery; use send_chat_message."},
            "create_chart": {"status": "error", "message": "Charts are not requested for this concise briefing."},
            "create_csv": {"status": "error", "message": "Files are not requested for this concise briefing."},
            "create_file": {"status": "error", "message": "Files are not requested for this concise briefing."},
            "create_pdf": {"status": "error", "message": "Files are not requested for this concise briefing."},
        }

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="trigger_scheduled_run")
        cron_step = PersistentAgentStep.objects.create(
            agent_id=agent_id,
            description=f"Cron trigger: {schedule}",
        )
        PersistentAgentCronTrigger.objects.create(step=cron_step, cron_expression=schedule)
        with self.wait_for_agent_idle(agent_id, timeout=180):
            self.trigger_processing(
                agent_id,
                eval_run_id=run_id,
                mock_config=mock_config,
                eval_stop_policy={
                    "stop_on_tool_names": list(
                        EFFORT_OVERWORK_TOOL_NAMES
                        | (RESEARCH_TOOL_NAMES - {"http_request"})
                        | {"send_email", "send_sms"}
                    ),
                    "stop_on_sqlite_agent_config_mutation": True,
                    "max_relevant_tool_calls": 6,
                    "ignored_tool_names": list(MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES),
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="trigger_scheduled_run",
            observed_summary="Cron trigger recorded and processing completed.",
            artifacts={"step": cron_step},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_one_sourced_report")
        outbound = _outbound_messages_after(agent_id, cron_step.created_at)
        report = outbound[0].body if outbound else ""
        linked_sources = [url for url in source_urls if url in report]
        if len(outbound) == 1 and len(report or "") <= 2500 and len(linked_sources) >= 2:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_one_sourced_report",
                observed_summary=(
                    f"Sent one concise report ({len(report)} chars) with {len(linked_sources)} source link(s)."
                ),
                artifacts={"message": outbound[0]},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_one_sourced_report",
                observed_summary=(
                    f"Expected one concise sourced report; outbound={len(outbound)}, "
                    f"chars={len(report or '')}, linked_sources={len(linked_sources)}."
                ),
                artifacts={"message": outbound[0]} if outbound else {},
            )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_no_human_input_after_report")
        requests = _human_input_requests_for_run(run_id, after=cron_step.created_at)
        if not requests:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_no_human_input_after_report",
                observed_summary="No human-input request was created after the completed scheduled run.",
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_no_human_input_after_report",
                observed_summary=f"Unexpected human-input request count: {len(requests)}.",
            )

        self._record_no_question_battery(
            run_id,
            agent_id=agent_id,
            after=cron_step.created_at,
            task_name="verify_final_message_question_shape",
            max_message_questions=1,
        )

        self._record_no_overwork_tools(
            run_id,
            after=cron_step.created_at,
            task_name="verify_no_artifacts_or_plan",
            forbidden_tool_names=EFFORT_OVERWORK_TOOL_NAMES | {"search_tools", "spawn_web_task"},
        )
        self._record_no_agent_config_mutation(
            run_id,
            after=cron_step.created_at,
            task_name="verify_no_config_churn",
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_bounded_run")
        http_calls = _tool_calls_for_run(run_id, after=cron_step.created_at, tool_names={"http_request"})
        exact_http_calls = [call for call in http_calls if (call.tool_params or {}).get("url") == briefing_url]
        orchestrator_count = _orchestrator_completion_count(run_id)
        if len(exact_http_calls) == 1 and len(http_calls) == 1 and orchestrator_count <= 4:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_bounded_run",
                observed_summary=(
                    f"One exact fetch and {orchestrator_count} orchestrator completion(s). "
                    f"Tokens: {_completion_token_summary(run_id)}."
                ),
                artifacts={"step": exact_http_calls[0].step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_bounded_run",
                observed_summary=(
                    f"Expected one exact fetch and <=4 completions; "
                    f"http_urls={[(c.tool_params or {}).get('url') for c in http_calls]}, "
                    f"orchestrator_count={orchestrator_count}."
                ),
                artifacts={"step": http_calls[0].step} if http_calls else {},
            )


@register_scenario
class EffortDefaultableResearchNoQuestionBatteryScenario(EffortCalibrationScenario):
    slug = EFFORT_DEFAULTABLE_RESEARCH_NO_QUESTION_BATTERY
    description = "A defaultable research request should proceed with reasonable defaults, not ask a multi-question survey."
    tasks = [
        ScenarioTask.setup(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_research_bounded", assertion_type="manual"),
        ScenarioTask(name="verify_single_sourced_answer", assertion_type="manual"),
        ScenarioTask(name="verify_no_question_battery", assertion_type="manual"),
        ScenarioTask(name="verify_no_artifacts", assertion_type="manual"),
        ScenarioTask(name="verify_no_config_churn", assertion_type="manual"),
    ]

    @staticmethod
    def _eval_stop_policy() -> dict[str, Any]:
        return {
            "stop_on_tool_names": list(EFFORT_OVERWORK_TOOL_NAMES),
            "stop_on_sqlite_agent_config_mutation": True,
            "max_relevant_tool_calls": 6,
            "ignored_tool_names": list(MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES),
        }

    @staticmethod
    def _research_calls_for_scoring(run_id: str, *, after):
        return _relevant_tool_calls_for_run(
            run_id,
            after=after,
            ignored_tool_names=MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES,
        )

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        self._enable_eval_synthetic_tools(
            agent_id,
            ["mcp_brightdata_search_engine", "mcp_brightdata_scrape_as_markdown"],
        )
        article_url = "https://news.example.test/acme-series-a"
        mock_config = {
            "mcp_brightdata_search_engine": {
                "status": "ok",
                "results": [
                    {
                        "title": "Acme Prediction Labs raises Series A",
                        "url": article_url,
                        "snippet": "Acme Prediction Labs announced a 24 million dollar Series A led by Example Ventures.",
                    }
                ],
            },
            "mcp_brightdata_scrape_as_markdown": {
                "rules": [
                    {
                        "url_contains": "acme-series-a",
                        "result": {
                            "status": "ok",
                            "markdown": (
                                "# Acme Prediction Labs raises Series A\n\n"
                                "Acme Prediction Labs announced a $24M Series A led by Example Ventures. "
                                "The company said it will expand market data coverage for enterprise teams."
                            ),
                            "url": article_url,
                        },
                    }
                ],
                "default": {"status": "error", "message": "Unexpected scrape URL."},
            },
            "search_tools": {
                "status": "ok",
                "tools": [
                    {
                        "name": "mcp_brightdata_search_engine",
                        "description": "Search deterministic eval web results.",
                    },
                    {
                        "name": "mcp_brightdata_scrape_as_markdown",
                        "description": "Scrape deterministic eval article pages.",
                    },
                ],
            },
        }

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=180):
            inbound = self.inject_message(
                agent_id,
                (
                    "Research whether Acme Prediction Labs announced a Series A and give me a concise sourced answer. "
                    "Use reasonable defaults; do not ask me preference questions."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
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

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_research_bounded")
        relevant = self._research_calls_for_scoring(
            run_id,
            after=inbound.timestamp,
        )
        allowed = {"search_tools", "mcp_brightdata_search_engine", "mcp_brightdata_scrape_as_markdown"}
        unexpected = [call.tool_name for call in relevant if call.tool_name not in allowed]
        if len(relevant) <= 3 and not unexpected:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_research_bounded",
                observed_summary=f"Research stayed bounded: {[call.tool_name for call in relevant]}.",
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_research_bounded",
                observed_summary=f"Expected <=3 search/scrape calls; saw {[call.tool_name for call in relevant]}.",
                artifacts={"step": relevant[0].step} if relevant else {},
            )

        self._record_single_concise_reply(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_single_sourced_answer",
            max_chars=1200,
            required_snippets=("Acme",),
        )
        self._record_no_question_battery(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_no_question_battery",
            max_message_questions=1,
        )
        self._record_no_overwork_tools(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_artifacts",
            forbidden_tool_names=ARTIFACT_TOOL_NAMES | {"request_human_input"},
        )
        self._record_no_agent_config_mutation(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_config_churn",
        )


@register_scenario
class EffortPartialBriefingReportsWithoutSurveyScenario(EffortCalibrationScenario):
    slug = EFFORT_PARTIAL_BRIEFING_REPORTS_WITHOUT_SURVEY
    description = "A partial deterministic briefing feed should produce a concise partial report, not a preference survey or human-input request."
    tasks = [
        ScenarioTask.setup(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_one_partial_report", assertion_type="manual"),
        ScenarioTask(name="verify_no_question_battery", assertion_type="manual"),
        ScenarioTask(name="verify_no_artifacts_or_extra_research", assertion_type="manual"),
        ScenarioTask(name="verify_no_config_churn", assertion_type="manual"),
        ScenarioTask(name="verify_turn_budget", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        self._enable_builtin_tools(agent_id, ["http_request"])
        feed_url = "https://briefing.example.test/api/partial.json"
        mock_config = {
            "http_request": {
                "status": "ok",
                "status_code": 206,
                "content": json.dumps(
                    {
                        "date": "2026-05-17",
                        "provider_warnings": [
                            "Prediction-market provider returned only partial odds."
                        ],
                        "items": [
                            {
                                "headline": "Central bank signals rate hold",
                                "summary": "The policy committee signaled a wait-and-see stance.",
                                "source_url": "https://news.example.test/rate-hold",
                            },
                            {
                                "headline": "Election coalition talks continue",
                                "summary": "Market odds were unavailable in this partial feed response.",
                                "source_url": "https://news.example.test/coalition-talks",
                            },
                        ],
                    }
                ),
            },
            "search_tools": {"status": "error", "message": "Do not broaden research for this partial-feed eval."},
            "mcp_brightdata_search_engine": {"status": "error", "message": "Do not broaden research for this partial-feed eval."},
            "spawn_web_task": {"status": "error", "message": "Do not use browser automation for this partial-feed eval."},
        }

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                (
                    f"Fetch {feed_url} and send me a concise briefing. If provider data is partial, "
                    "report what is available and note the limitation briefly instead of asking me what to do."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
                eval_stop_policy={
                    "stop_on_tool_names": list(
                        EFFORT_OVERWORK_TOOL_NAMES
                        | (RESEARCH_TOOL_NAMES - {"http_request"})
                    ),
                    "stop_on_sqlite_agent_config_mutation": True,
                    "max_relevant_tool_calls": 5,
                    "ignored_tool_names": list(MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES),
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self._record_single_concise_reply(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_one_partial_report",
            max_chars=1600,
            required_snippets=("Central bank",),
        )
        self._record_no_question_battery(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_no_question_battery",
            max_message_questions=1,
        )
        self._record_no_overwork_tools(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_artifacts_or_extra_research",
            forbidden_tool_names=EFFORT_OVERWORK_TOOL_NAMES | (RESEARCH_TOOL_NAMES - {"http_request"}),
        )
        self._record_no_agent_config_mutation(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_config_churn",
        )
        self._record_orchestrator_budget(run_id, task_name="verify_turn_budget", max_completions=3)


@register_scenario
class EffortChartRequestedSingleArtifactScenario(EffortCalibrationScenario):
    slug = EFFORT_CHART_REQUESTED_SINGLE_ARTIFACT
    description = "When a chart is explicitly requested, the agent may create one chart artifact without broadening into extra files or questions."
    tasks = [
        ScenarioTask.setup(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_chart_created_once", assertion_type="manual"),
        ScenarioTask(name="verify_bounded_tool_breadth", assertion_type="manual"),
        ScenarioTask(name="verify_no_question_battery", assertion_type="manual"),
        ScenarioTask(name="verify_no_extra_artifacts", assertion_type="manual"),
        ScenarioTask(name="verify_no_config_churn", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        self._enable_builtin_tools(agent_id, ["http_request", "create_chart"])
        metrics_url = "https://metrics.example.test/signups.json"
        mock_config = {
            "http_request": {
                "status": "ok",
                "status_code": 200,
                "content": json.dumps(
                    {
                        "series": [
                            {"day": "Mon", "signups": 4},
                            {"day": "Tue", "signups": 7},
                            {"day": "Wed", "signups": 5},
                            {"day": "Thu", "signups": 11},
                            {"day": "Fri", "signups": 13},
                        ]
                    }
                ),
            },
            "sqlite_batch": {
                "status": "ok",
                "result": [
                    {"day": "Mon", "signups": 4},
                    {"day": "Tue", "signups": 7},
                    {"day": "Wed", "signups": 5},
                    {"day": "Thu", "signups": 11},
                    {"day": "Fri", "signups": 13},
                ],
                "auto_sleep_ok": False,
            },
            "create_chart": {
                "status": "ok",
                "file": "$[/charts/signups_line.svg]",
                "inline": "![]($[/charts/signups_line.svg])",
                "inline_html": "<img src='$[/charts/signups_line.svg]'>",
                "attach": "$[/charts/signups_line.svg]",
            },
            "create_csv": {"status": "error", "message": "Only one chart artifact was requested."},
            "create_pdf": {"status": "error", "message": "Only one chart artifact was requested."},
            "create_file": {"status": "error", "message": "Only one chart artifact was requested."},
        }

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=180):
            inbound = self.inject_message(
                agent_id,
                (
                    f"Fetch {metrics_url}, create one line chart of signups by day, and send it here with a one-sentence takeaway. "
                    "Do not create a PDF or CSV."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
                eval_stop_policy={
                    "stop_on_tool_names": list(
                        (EFFORT_OVERWORK_TOOL_NAMES - {"create_chart"})
                        | (ARTIFACT_TOOL_NAMES - {"create_chart"})
                    ),
                    "stop_on_unexpected_relevant_tool": True,
                    "allowed_tool_names": ["http_request", "sqlite_batch", "create_chart"],
                    "stop_on_sqlite_agent_config_mutation": True,
                    "max_relevant_tool_calls": 4,
                    "ignored_tool_names": list(MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES),
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_chart_created_once")
        chart_calls = _tool_calls_for_run(run_id, after=inbound.timestamp, tool_names={"create_chart"})
        if len(chart_calls) == 1:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_chart_created_once",
                observed_summary="Exactly one chart artifact was created for the explicit chart request.",
                artifacts={"step": chart_calls[0].step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_chart_created_once",
                observed_summary=f"Expected exactly one create_chart call; saw {len(chart_calls)}.",
                artifacts={"step": chart_calls[0].step} if chart_calls else {},
            )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_bounded_tool_breadth")
        relevant = _relevant_tool_calls_for_run(
            run_id,
            after=inbound.timestamp,
            ignored_tool_names=MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES,
        )
        allowed = {"http_request", "sqlite_batch", "create_chart"}
        unexpected = [call.tool_name for call in relevant if call.tool_name not in allowed]
        if len(relevant) <= 4 and not unexpected:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_bounded_tool_breadth",
                observed_summary=f"Chart request stayed bounded: {[call.tool_name for call in relevant]}.",
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_bounded_tool_breadth",
                observed_summary=(
                    "Expected at most four fetch/data-prep/chart calls; "
                    f"saw {[call.tool_name for call in relevant]}."
                ),
                artifacts={"step": relevant[0].step} if relevant else {},
            )

        self._record_no_question_battery(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_no_question_battery",
            max_message_questions=1,
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_no_extra_artifacts")
        extra_artifact_calls = _tool_calls_for_run(
            run_id,
            after=inbound.timestamp,
            tool_names=ARTIFACT_TOOL_NAMES - {"create_chart"},
        )
        if not extra_artifact_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_no_extra_artifacts",
                observed_summary="No extra file/PDF/CSV/image/video artifacts were created.",
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_no_extra_artifacts",
                observed_summary=f"Unexpected extra artifact tool(s): {[call.tool_name for call in extra_artifact_calls]}.",
                artifacts={"step": extra_artifact_calls[0].step},
            )

        self._record_no_agent_config_mutation(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_config_churn",
        )


@register_scenario
class EffortUnscheduledRemainingWorkSetsResumeScenario(EffortCalibrationScenario):
    slug = EFFORT_UNSCHEDULED_REMAINING_WORK_SETS_RESUME
    description = (
        "An unscheduled outreach agent with high burn rate and remaining queued work should not send a terminal "
        "status and disappear; it should continue bounded work or set a resume schedule."
    )
    tasks = [
        ScenarioTask.setup(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_future_work_preserved", assertion_type="manual"),
        ScenarioTask(name="verify_no_question_or_progress_message", assertion_type="manual"),
        ScenarioTask(name="verify_no_overwork_tools", assertion_type="manual"),
        ScenarioTask(name="verify_no_unscheduled_wait_claim", assertion_type="manual"),
        ScenarioTask(name="verify_turn_budget", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(
            agent_id,
            charter=(
                "Continue approved Gobii outreach in small batches. Keep the owner updated only for real "
                "findings, blockers, or useful status changes."
            ),
            schedule=None,
        )
        PersistentAgent.objects.filter(id=agent_id).update(daily_credit_limit=450)
        self._seed_recent_high_burn(agent_id)
        self._enable_eval_synthetic_tools(agent_id, ["eval_send_outreach_batch"])
        mock_config = {
            "eval_send_outreach_batch": {
                "status": "ok",
                "summary": "Sent 4 approved personalized outreach emails.",
                "sent_count": 4,
                "remaining_work": 999,
                "next_action": (
                    "Continue in bounded batches or set a resume schedule before pausing; "
                    "do not abandon the remaining queue."
                ),
                "verification": "The returned sent_count is authoritative for this completed batch.",
            }
        }

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=180):
            inbound = self.inject_message(
                agent_id,
                (
                    "Keep sending the approved outreach queue in small batches. We have 999 researched leads "
                    "remaining after this batch and no schedule is configured. Burn rate is high, so pace the work "
                    "without abandoning it."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
                eval_stop_policy={
                    "stop_on_tool_names": list(PARTIAL_SOURCE_FORBIDDEN_TOOL_NAMES),
                    "stop_when_all_seen": [
                        {"tool_name": "eval_send_outreach_batch", "after_execution": True},
                        {
                            "tool_name": "sqlite_batch",
                            "agent_config_field": "schedule",
                            "after_execution": True,
                        },
                    ],
                    "ignore_sqlite_agent_config_mutations": False,
                    "stop_on_human_input_request": True,
                    "stop_on_unexpected_relevant_tool": True,
                    "allowed_tool_names": ["eval_send_outreach_batch", "sqlite_batch", "update_plan"],
                    "max_relevant_tool_calls": 5,
                    "ignored_tool_names": list(MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES),
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self._record_future_work_preserved(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_future_work_preserved",
            work_tool_names={"eval_send_outreach_batch"},
        )
        self._record_no_question_battery(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_no_question_or_progress_message",
            max_message_questions=0,
        )
        self._record_no_overwork_tools(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_overwork_tools",
            forbidden_tool_names=PARTIAL_SOURCE_FORBIDDEN_TOOL_NAMES,
            max_plan_updates=1,
        )
        self._record_no_unscheduled_wait_claim(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_no_unscheduled_wait_claim",
        )
        self._record_orchestrator_budget(run_id, task_name="verify_turn_budget", max_completions=5)


@register_scenario
class EffortPartialSourceBlockReportsAndResumesScenario(EffortCalibrationScenario):
    slug = EFFORT_PARTIAL_SOURCE_BLOCK_REPORTS_AND_RESUMES
    description = (
        "When source limitations block a full sourcing target under high burn rate, the agent should report "
        "verified partial results and preserve future work instead of looping or stopping permanently."
    )
    tasks = [
        ScenarioTask.setup(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_partial_report", assertion_type="manual"),
        ScenarioTask(name="verify_future_work_preserved", assertion_type="manual"),
        ScenarioTask(name="verify_no_question_or_progress_message", assertion_type="manual"),
        ScenarioTask(name="verify_no_overwork_tools", assertion_type="manual"),
        ScenarioTask(name="verify_turn_budget", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(
            agent_id,
            charter=(
                "Source qualified insurance candidates and report verified candidates with source limitations. "
                "Preserve unfinished candidate verification work when the full target cannot be completed in one pass."
            ),
            schedule=None,
        )
        self._seed_recent_high_burn(agent_id)
        self._enable_eval_synthetic_tools(agent_id, ["eval_verify_candidate_batch"])
        mock_config = {
            "eval_verify_candidate_batch": {
                "status": "partial",
                "verified_candidates": [
                    {
                        "name": "Kathleen Clatworthy",
                        "company": "American Family",
                        "location": "Janesville, WI",
                        "tenure": "2 years",
                        "linkedin_url": "https://www.linkedin.com/in/kathleen-clatworthy",
                    },
                    {
                        "name": "Rogelio Perez",
                        "company": "Farmers Insurance",
                        "location": "Kenosha, WI",
                        "tenure": "1 year",
                        "linkedin_url": "https://www.linkedin.com/in/rogelio-perez",
                    },
                    {
                        "name": "Chris Hanson",
                        "company": "State Farm",
                        "location": "Fort Atkinson, WI",
                        "tenure": "6 months",
                        "linkedin_url": "https://www.linkedin.com/in/chris-hanson",
                    },
                ],
                "blocked_reason": "Most public LinkedIn pages did not expose start dates or tenure.",
                "remaining_work": 12,
                "next_cursor": "candidate-offset-3",
                "next_action": (
                    "Report the verified partial set with the source limitation, then continue bounded "
                    "verification or set a resume schedule."
                ),
            }
        }

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=180):
            inbound = self.inject_message(
                agent_id,
                (
                    "Verify the next queued batch of captive insurance candidates from State Farm, Allstate, "
                    "American Family, and Farmers in South/Southeast Wisconsin with less than 5 years tenure. "
                    "I asked for 15; if source access only verifies a partial set, report the verified candidates "
                    "and keep the remaining verification from getting lost."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
                eval_stop_policy={
                    "stop_on_tool_names": list(PARTIAL_SOURCE_FORBIDDEN_TOOL_NAMES),
                    "stop_on_human_input_request": True,
                    "stop_on_unexpected_relevant_tool": True,
                    "allowed_tool_names": ["eval_verify_candidate_batch", "sqlite_batch", "update_plan"],
                    "max_relevant_tool_calls": 6,
                    "ignored_tool_names": list(MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES),
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self._record_single_concise_reply(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_partial_report",
            max_chars=2500,
            required_snippets=("Kathleen", "Rogelio", "Chris"),
        )
        self._record_future_work_preserved(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_future_work_preserved",
            work_tool_names={"eval_verify_candidate_batch"},
        )
        self._record_no_question_battery(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_no_question_or_progress_message",
            max_message_questions=0,
        )
        self._record_no_overwork_tools(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_overwork_tools",
            forbidden_tool_names=PARTIAL_SOURCE_FORBIDDEN_TOOL_NAMES,
        )
        self._record_orchestrator_budget(run_id, task_name="verify_turn_budget", max_completions=6)


@register_scenario
class EffortToolWaitNextScheduleRequiresScheduleScenario(EffortCalibrationScenario):
    slug = EFFORT_TOOL_WAIT_NEXT_SCHEDULE_REQUIRES_SCHEDULE
    description = (
        "A batch tool's 'wait for next scheduled run' guidance is unsafe when the agent has no schedule; "
        "the agent should set one or continue bounded work before stopping."
    )
    tasks = [
        ScenarioTask.setup(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_future_work_preserved", assertion_type="manual"),
        ScenarioTask(name="verify_no_unscheduled_wait_claim", assertion_type="manual"),
        ScenarioTask(name="verify_no_question_or_progress_message", assertion_type="manual"),
        ScenarioTask(name="verify_no_overwork_tools", assertion_type="manual"),
        ScenarioTask(name="verify_turn_budget", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(
            agent_id,
            charter=(
                "Prepare recurring-style batch work from SQLite and keep unfinished batches resumable. "
                "Do not rely on a scheduled wake-up unless a schedule exists."
            ),
            schedule=None,
        )
        self._seed_recent_high_burn(agent_id)
        self._enable_eval_synthetic_tools(agent_id, ["eval_prepare_next_batch"])
        mock_config = {
            "eval_prepare_next_batch": {
                "status": "ok",
                "prepared_count": 25,
                "remaining_work": 75,
                "next_cursor": "lead-025",
                "next_action": "Wait for next scheduled run; do not repeat manually.",
                "verification": "The returned counts and cursor are authoritative for this prepared batch.",
            }
        }

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=180):
            inbound = self.inject_message(
                agent_id,
                (
                    "Prepare the next bounded batch. The tool may tell you to wait for the next scheduled run, "
                    "but no schedule is configured right now. Make sure the remaining work can actually resume."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
                eval_stop_policy={
                    "stop_on_tool_names": list(PARTIAL_SOURCE_FORBIDDEN_TOOL_NAMES),
                    "stop_when_all_seen": [
                        {"tool_name": "eval_prepare_next_batch", "after_execution": True},
                        {
                            "tool_name": "sqlite_batch",
                            "agent_config_field": "schedule",
                            "after_execution": True,
                        },
                    ],
                    "ignore_sqlite_agent_config_mutations": False,
                    "stop_on_human_input_request": True,
                    "stop_on_unexpected_relevant_tool": True,
                    "allowed_tool_names": ["eval_prepare_next_batch", "sqlite_batch", "update_plan"],
                    "max_relevant_tool_calls": 5,
                    "ignored_tool_names": list(MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES),
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self._record_future_work_preserved(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_future_work_preserved",
            work_tool_names={"eval_prepare_next_batch"},
        )
        self._record_no_unscheduled_wait_claim(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_no_unscheduled_wait_claim",
        )
        self._record_no_question_battery(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_no_question_or_progress_message",
            max_message_questions=0,
        )
        self._record_no_overwork_tools(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_overwork_tools",
            forbidden_tool_names=PARTIAL_SOURCE_FORBIDDEN_TOOL_NAMES,
            max_plan_updates=1,
        )
        self._record_orchestrator_budget(run_id, task_name="verify_turn_budget", max_completions=5)


@register_scenario
class EffortSimpleCurrentYCBatchReportScenario(EffortCalibrationScenario):
    slug = EFFORT_SIMPLE_CURRENT_YC_BATCH_REPORT
    supports_simulation = True
    description = (
        "Production-seeded latest-YC-batch report should stay in bounded current-research mode: "
        "a few good sources, one structured report, no progress-only message, charts, or retrieval loops."
    )
    tasks = [
        ScenarioTask.setup(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_bounded_research_budget", assertion_type="manual"),
        ScenarioTask(name="verify_single_hierarchical_report", assertion_type="manual"),
        ScenarioTask(name="verify_no_question_or_progress_message", assertion_type="manual"),
        ScenarioTask(name="verify_no_artifacts_or_plan", assertion_type="manual"),
        ScenarioTask(name="verify_no_query_or_sqlite_loops", assertion_type="manual"),
        ScenarioTask(name="verify_no_config_churn", assertion_type="manual"),
        ScenarioTask(name="verify_turn_budget", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        self._enable_eval_synthetic_tools(
            agent_id,
            ["mcp_brightdata_search_engine", "mcp_brightdata_scrape_as_markdown"],
        )
        self._enable_builtin_tools(agent_id, ["create_chart", "create_csv", "create_file", "create_pdf"])
        source_urls = (
            "https://www.ycombinator.com/companies?batch=Winter%202026",
            "https://www.ycombinator.com/blog/yc-winter-2026-demo-day",
            "https://techcrunch.example.test/yc-w26-demo-day-highlights",
            "https://research.example.test/yc-w26-batch-analysis",
        )
        mock_config = {
            "mcp_brightdata_search_engine": {
                "status": "ok",
                "results": [
                    {
                        "title": "Y Combinator Winter 2026 companies",
                        "url": source_urls[0],
                        "snippet": "YC's latest completed batch is Winter 2026 (W26), with roughly 198 companies.",
                    },
                    {
                        "title": "YC Winter 2026 Demo Day",
                        "url": source_urls[1],
                        "snippet": "Demo Day took place in late March 2026 and highlighted AI, B2B, devtools, healthcare, and fintech startups.",
                    },
                    {
                        "title": "Highlights from YC W26 Demo Day",
                        "url": source_urls[2],
                        "snippet": "Selected standouts included infrastructure, robotics, health AI, and new financial workflow products.",
                    },
                    {
                        "title": "YC W26 batch analysis",
                        "url": source_urls[3],
                        "snippet": "The batch skewed toward B2B and applied AI, with smaller clusters in healthcare, fintech, and climate.",
                    },
                ],
            },
            "mcp_brightdata_scrape_as_markdown": {
                "rules": [
                    {
                        "url_contains": "companies?batch=Winter%202026",
                        "result": {
                            "status": "ok",
                            "url": source_urls[0],
                            "markdown": (
                                "# YC Winter 2026 Companies\n\n"
                                "The latest completed YC batch is Winter 2026 (W26). The directory lists about "
                                "198 companies. Common tags include B2B, AI, developer tools, healthcare, fintech, "
                                "robotics, and climate. Example companies include Byteport, Graze Robotics, "
                                "Helix Health AI, LedgerFlow, and Orbital Grid."
                            ),
                        },
                    },
                    {
                        "url_contains": "yc-winter-2026-demo-day",
                        "result": {
                            "status": "ok",
                            "url": source_urls[1],
                            "markdown": (
                                "# YC Winter 2026 Demo Day\n\n"
                                "Demo Day ran in late March 2026. YC described the batch as heavily weighted toward "
                                "applied AI and B2B software, with infrastructure and vertical workflow companies "
                                "showing up across sectors."
                            ),
                        },
                    },
                    {
                        "url_contains": "yc-w26-demo-day-highlights",
                        "result": {
                            "status": "ok",
                            "url": source_urls[2],
                            "markdown": (
                                "# YC W26 highlights\n\n"
                                "Investors highlighted startups building logistics automation, satellite power, "
                                "clinical documentation, developer infrastructure, and finance operations. The most "
                                "common pattern was AI embedded into narrow operational workflows."
                            ),
                        },
                    },
                    {
                        "url_contains": "yc-w26-batch-analysis",
                        "result": {
                            "status": "ok",
                            "url": source_urls[3],
                            "markdown": (
                                "# YC W26 batch analysis\n\n"
                                "B2B and AI were the dominant themes. Healthcare, fintech, and climate were present "
                                "but smaller. The batch continued YC's shift toward technical founders and concrete "
                                "workflow automation."
                            ),
                        },
                    },
                ],
                "default": {
                    "status": "ok",
                    "url": "https://research.example.test/yc-w26-source",
                    "markdown": "YC W26 source note: latest batch, AI/B2B heavy, with healthcare, fintech, climate, and robotics examples.",
                },
            },
            "search_tools": {
                "status": "ok",
                "tools": [
                    {
                        "name": "mcp_brightdata_search_engine",
                        "description": "Search deterministic eval web results.",
                    },
                    {
                        "name": "mcp_brightdata_scrape_as_markdown",
                        "description": "Scrape deterministic eval web pages.",
                    },
                ],
            },
            "create_chart": {"status": "error", "message": "Charts are not requested for this simple report."},
            "create_csv": {"status": "error", "message": "Files are not requested for this simple report."},
            "create_file": {"status": "error", "message": "Files are not requested for this simple report."},
            "create_pdf": {"status": "error", "message": "Files are not requested for this simple report."},
        }
        prompt = (
            "Tell me about the latest YC batch of companies. Give me a concise but substantive structured "
            "report with key themes, representative examples, and sources. Treat this as bounded current-info "
            "research, not exhaustive research; use at most one search query and answer from the first reliable "
            "source set."
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        if self._is_simulated(run_id):
            inbound = self.inject_message(
                agent_id,
                prompt,
                trigger_processing=False,
                eval_run_id=run_id,
            )
            self._record_simulated_tool_call(
                run_id,
                agent_id,
                tool_name="mcp_brightdata_search_engine",
                tool_params={"query": "latest YC batch companies W26", "will_continue_work": True},
                result=mock_config["mcp_brightdata_search_engine"],
            )
            self._record_simulated_tool_call(
                run_id,
                agent_id,
                tool_name="mcp_brightdata_scrape_as_markdown",
                tool_params={"url": source_urls[0]},
                result=mock_config["mcp_brightdata_scrape_as_markdown"]["rules"][0]["result"],
            )
            self._record_simulated_tool_call(
                run_id,
                agent_id,
                tool_name="mcp_brightdata_scrape_as_markdown",
                tool_params={"url": source_urls[1]},
                result=mock_config["mcp_brightdata_scrape_as_markdown"]["rules"][1]["result"],
            )
            self._send_simulated_web_report(
                agent_id,
                (
                    "## YC Winter 2026 Batch\n\n"
                    "**Bottom line:** YC's latest completed batch is Winter 2026 (W26), with about 198 "
                    "companies. The batch reads as an applied-AI and B2B software cohort rather than a broad "
                    "consumer wave.\n\n"
                    "### Key Takeaways\n\n"
                    "- **Themes:** B2B, AI, developer tools, healthcare, fintech, robotics, and climate were "
                    "the main clusters.\n"
                    "- **What changed:** More companies are packaging AI into specific operating workflows, "
                    "such as logistics, clinical documentation, finance ops, and infrastructure.\n"
                    "- **How to read it:** The interesting signal is less \"AI everywhere\" and more vertical "
                    "workflow automation with concrete buyers.\n\n"
                    "| Area | What stood out |\n"
                    "| --- | --- |\n"
                    "| Batch | Winter 2026 / W26, roughly 198 companies |\n"
                    "| Center of gravity | B2B software and applied AI |\n"
                    "| Examples | Byteport, Graze Robotics, Helix Health AI, LedgerFlow, Orbital Grid |\n\n"
                    "### Sources\n\n"
                    f"- {source_urls[0]}\n"
                    f"- {source_urls[1]}\n"
                    f"- {source_urls[2]}"
                ),
            )
        else:
            with self.wait_for_agent_idle(agent_id, timeout=180):
                inbound = self.inject_message(
                    agent_id,
                    prompt,
                    trigger_processing=True,
                    eval_run_id=run_id,
                    mock_config=mock_config,
                    eval_stop_policy={
                        "stop_on_tool_names": list(EFFORT_OVERWORK_TOOL_NAMES | ARTIFACT_TOOL_NAMES),
                        "stop_on_sqlite_agent_config_mutation": True,
                        "stop_on_human_input_request": True,
                        "stop_on_unexpected_relevant_tool": True,
                        "allowed_tool_names": [
                            "search_tools",
                            "mcp_brightdata_search_engine",
                            "mcp_brightdata_scrape_as_markdown",
                        ],
                        "max_relevant_tool_calls": 6,
                        "ignored_tool_names": list(MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES),
                    },
                )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self._record_research_tool_budget(
            run_id,
            after=inbound.timestamp,
            task_name="verify_bounded_research_budget",
            allowed_tool_names={
                "search_tools",
                "mcp_brightdata_search_engine",
                "mcp_brightdata_scrape_as_markdown",
            },
            min_relevant_calls=1,
            max_relevant_calls=5,
        )
        self._record_hierarchical_report(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_single_hierarchical_report",
            source_urls=source_urls,
            min_source_count=2,
            min_chars=700,
            max_chars=3200,
            required_any_groups=(("Winter 2026", "W26"), ("YC", "Y Combinator")),
        )
        self._record_no_question_battery(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_no_question_or_progress_message",
            max_message_questions=0,
        )
        self._record_no_overwork_tools(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_artifacts_or_plan",
            forbidden_tool_names=EFFORT_OVERWORK_TOOL_NAMES | ARTIFACT_TOOL_NAMES,
        )
        no_query_loop = self._record_no_repetitive_web_queries(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_query_or_sqlite_loops",
        )
        if no_query_loop:
            self._record_no_sqlite_result_text_reread_loop(
                run_id,
                after=inbound.timestamp,
                task_name="verify_no_query_or_sqlite_loops",
                max_result_text_reads=0,
            )
        self._record_no_agent_config_mutation(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_config_churn",
        )
        self._record_orchestrator_budget(run_id, task_name="verify_turn_budget", max_completions=5)


@register_scenario
class EffortSimpleCurrentCompanyReportScenario(EffortCalibrationScenario):
    slug = EFFORT_SIMPLE_CURRENT_COMPANY_REPORT
    supports_simulation = True
    description = (
        "A generic current company/news report should use bounded source collection and stop, "
        "preventing the YC failure fix from overfitting to one wording."
    )
    tasks = [
        ScenarioTask.setup(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_bounded_research_budget", assertion_type="manual"),
        ScenarioTask(name="verify_single_hierarchical_report", assertion_type="manual"),
        ScenarioTask(name="verify_no_question_or_progress_message", assertion_type="manual"),
        ScenarioTask(name="verify_no_artifacts_or_plan", assertion_type="manual"),
        ScenarioTask(name="verify_no_query_or_sqlite_loops", assertion_type="manual"),
        ScenarioTask(name="verify_no_config_churn", assertion_type="manual"),
        ScenarioTask(name="verify_turn_budget", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        self._enable_eval_synthetic_tools(
            agent_id,
            ["mcp_brightdata_search_engine", "mcp_brightdata_scrape_as_markdown"],
        )
        self._enable_builtin_tools(agent_id, ["create_chart", "create_csv", "create_file", "create_pdf"])
        source_urls = (
            "https://northstar.example.test/blog/atlas-launch",
            "https://news.example.test/northstar-series-b",
            "https://customers.example.test/northstar-warehouse-rollout",
        )
        mock_config = {
            "mcp_brightdata_search_engine": {
                "status": "ok",
                "results": [
                    {
                        "title": "Northstar Robotics launches Atlas routing system",
                        "url": source_urls[0],
                        "snippet": "Northstar Robotics announced Atlas, a warehouse robot-routing system for mixed fleets.",
                    },
                    {
                        "title": "Northstar Robotics raises Series B",
                        "url": source_urls[1],
                        "snippet": "The company raised a 42 million dollar Series B to expand deployments in food and pharma logistics.",
                    },
                    {
                        "title": "Northstar warehouse rollout customer note",
                        "url": source_urls[2],
                        "snippet": "A regional distributor reported 18 percent faster pick-pack cycles after a Northstar pilot.",
                    },
                ],
            },
            "mcp_brightdata_scrape_as_markdown": {
                "rules": [
                    {
                        "url_contains": "atlas-launch",
                        "result": {
                            "status": "ok",
                            "url": source_urls[0],
                            "markdown": (
                                "# Northstar Robotics launches Atlas\n\n"
                                "Northstar Robotics launched Atlas, software that coordinates warehouse robots from "
                                "multiple vendors. The launch focuses on reducing congestion and improving fulfillment "
                                "throughput without replacing existing fleets."
                            ),
                        },
                    },
                    {
                        "url_contains": "northstar-series-b",
                        "result": {
                            "status": "ok",
                            "url": source_urls[1],
                            "markdown": (
                                "# Northstar Robotics raises Series B\n\n"
                                "Northstar Robotics raised $42M in Series B funding. The company said proceeds will "
                                "support deployments with food, pharma, and third-party logistics operators."
                            ),
                        },
                    },
                    {
                        "url_contains": "warehouse-rollout",
                        "result": {
                            "status": "ok",
                            "url": source_urls[2],
                            "markdown": (
                                "# Customer rollout note\n\n"
                                "A regional distributor reported an 18 percent improvement in pick-pack cycles and "
                                "fewer aisle conflicts after a six-week Atlas pilot."
                            ),
                        },
                    },
                ],
                "default": {
                    "status": "ok",
                    "url": "https://northstar.example.test/source",
                    "markdown": "Northstar Robotics current note: Atlas launch, Series B, and early customer rollout data.",
                },
            },
            "search_tools": {
                "status": "ok",
                "tools": [
                    {
                        "name": "mcp_brightdata_search_engine",
                        "description": "Search deterministic eval web results.",
                    },
                    {
                        "name": "mcp_brightdata_scrape_as_markdown",
                        "description": "Scrape deterministic eval web pages.",
                    },
                ],
            },
            "create_chart": {"status": "error", "message": "Charts are not requested for this simple report."},
            "create_csv": {"status": "error", "message": "Files are not requested for this simple report."},
            "create_file": {"status": "error", "message": "Files are not requested for this simple report."},
            "create_pdf": {"status": "error", "message": "Files are not requested for this simple report."},
        }
        prompt = (
            "Tell me what is new with Northstar Robotics right now. "
            "Give me a concise company/news report with sources."
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        if self._is_simulated(run_id):
            inbound = self.inject_message(
                agent_id,
                prompt,
                trigger_processing=False,
                eval_run_id=run_id,
            )
            self._record_simulated_tool_call(
                run_id,
                agent_id,
                tool_name="mcp_brightdata_search_engine",
                tool_params={"query": "Northstar Robotics latest company news", "will_continue_work": True},
                result=mock_config["mcp_brightdata_search_engine"],
            )
            self._record_simulated_tool_call(
                run_id,
                agent_id,
                tool_name="mcp_brightdata_scrape_as_markdown",
                tool_params={"url": source_urls[0]},
                result=mock_config["mcp_brightdata_scrape_as_markdown"]["rules"][0]["result"],
            )
            self._record_simulated_tool_call(
                run_id,
                agent_id,
                tool_name="mcp_brightdata_scrape_as_markdown",
                tool_params={"url": source_urls[1]},
                result=mock_config["mcp_brightdata_scrape_as_markdown"]["rules"][1]["result"],
            )
            self._send_simulated_web_report(
                agent_id,
                (
                    "## Northstar Robotics Update\n\n"
                    "**Bottom line:** Northstar Robotics has three current signals worth noting: the Atlas "
                    "mixed-fleet routing launch, a $42M Series B, and early customer evidence from a warehouse "
                    "pilot.\n\n"
                    "### What Changed\n\n"
                    "- **Product:** Atlas coordinates warehouse robots from multiple vendors, which makes it a "
                    "brownfield software layer rather than a full hardware replacement.\n"
                    "- **Funding:** The Series B gives Northstar more room to expand in food, pharma, and 3PL "
                    "warehouse deployments.\n"
                    "- **Customer proof:** A distributor reported an 18 percent pick-pack cycle improvement in a "
                    "six-week pilot.\n\n"
                    "| Signal | Why it matters |\n"
                    "| --- | --- |\n"
                    "| Atlas launch | Clearer product wedge around interoperability |\n"
                    "| Series B | More deployment capacity and enterprise credibility |\n"
                    "| Pilot result | Early ROI story for operations buyers |\n\n"
                    "### Sources\n\n"
                    f"- {source_urls[0]}\n"
                    f"- {source_urls[1]}\n"
                    f"- {source_urls[2]}"
                ),
            )
        else:
            with self.wait_for_agent_idle(agent_id, timeout=180):
                inbound = self.inject_message(
                    agent_id,
                    prompt,
                    trigger_processing=True,
                    eval_run_id=run_id,
                    mock_config=mock_config,
                    eval_stop_policy={
                        "stop_on_tool_names": list(EFFORT_OVERWORK_TOOL_NAMES | ARTIFACT_TOOL_NAMES),
                        "stop_on_sqlite_agent_config_mutation": True,
                        "stop_on_human_input_request": True,
                        "stop_on_unexpected_relevant_tool": True,
                        "allowed_tool_names": [
                            "search_tools",
                            "mcp_brightdata_search_engine",
                            "mcp_brightdata_scrape_as_markdown",
                        ],
                        "max_relevant_tool_calls": 6,
                        "ignored_tool_names": list(MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES),
                    },
                )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self._record_research_tool_budget(
            run_id,
            after=inbound.timestamp,
            task_name="verify_bounded_research_budget",
            allowed_tool_names={
                "search_tools",
                "mcp_brightdata_search_engine",
                "mcp_brightdata_scrape_as_markdown",
            },
            min_relevant_calls=1,
            max_relevant_calls=5,
        )
        self._record_hierarchical_report(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_single_hierarchical_report",
            source_urls=source_urls,
            min_source_count=2,
            min_chars=650,
            max_chars=3000,
            required_any_groups=(("Northstar Robotics", "Northstar"),),
        )
        self._record_no_question_battery(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_no_question_or_progress_message",
            max_message_questions=0,
        )
        self._record_no_overwork_tools(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_artifacts_or_plan",
            forbidden_tool_names=EFFORT_OVERWORK_TOOL_NAMES | ARTIFACT_TOOL_NAMES,
        )
        no_query_loop = self._record_no_repetitive_web_queries(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_query_or_sqlite_loops",
        )
        if no_query_loop:
            self._record_no_sqlite_result_text_reread_loop(
                run_id,
                after=inbound.timestamp,
                task_name="verify_no_query_or_sqlite_loops",
                max_result_text_reads=0,
            )
        self._record_no_agent_config_mutation(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_config_churn",
        )
        self._record_orchestrator_budget(run_id, task_name="verify_turn_budget", max_completions=5)


@register_scenario
class EffortExplicitDeepResearchRemainsCapableScenario(EffortCalibrationScenario):
    slug = EFFORT_EXPLICIT_DEEP_RESEARCH_REMAINS_CAPABLE
    supports_simulation = True
    description = (
        "An explicitly deep/exhaustive current-research ask may use a larger source budget and produce "
        "a richer memo, proving bounded mode does not flatten true deep work."
    )
    prompt = (
        "Do deep current research on Northstar Robotics in warehouse automation and produce a decision-useful, "
        "source-backed investment memo. Compare Northstar with at least four meaningful competitors, explain "
        "the market, thesis, risks, and open diligence questions, cite at least four strong sources, and include "
        "a compact comparison table. Keep the memo dense and under 4,800 characters, avoid redundant research "
        "or repeated source reads, and do not create files or charts."
    )
    tasks = [
        ScenarioTask.setup(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_deep_research_budget", assertion_type="manual"),
        ScenarioTask(name="verify_rich_hierarchical_memo", assertion_type="manual"),
        ScenarioTask(name="verify_plan_budget", assertion_type="manual"),
        ScenarioTask(name="verify_no_unrequested_artifacts_or_input", assertion_type="manual"),
        ScenarioTask(name="verify_no_query_or_sqlite_loops", assertion_type="manual"),
        ScenarioTask(name="verify_no_config_churn", assertion_type="manual"),
        ScenarioTask(name="verify_turn_budget", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        self._enable_eval_synthetic_tools(
            agent_id,
            ["mcp_brightdata_search_engine", "mcp_brightdata_scrape_as_markdown"],
        )
        self._enable_builtin_tools(agent_id, ["create_chart", "create_csv", "create_file", "create_pdf"])
        source_urls = (
            "https://northstar.example.test/blog/atlas-launch",
            "https://news.example.test/northstar-series-b",
            "https://market.example.test/warehouse-automation-2026",
            "https://competitors.example.test/orion-fulfillment-ai",
            "https://competitors.example.test/vector-pick-systems",
            "https://competitors.example.test/ranger-autonomous-lifts",
            "https://competitors.example.test/legacy-wms-robotics",
            "https://customers.example.test/northstar-warehouse-rollout",
        )
        mock_config = {
            "mcp_brightdata_search_engine": {
                "status": "ok",
                "results": [
                    {
                        "title": "Northstar Robotics launches Atlas",
                        "url": source_urls[0],
                        "snippet": "Atlas coordinates mixed warehouse robot fleets and targets congestion reduction.",
                    },
                    {
                        "title": "Northstar Robotics raises Series B",
                        "url": source_urls[1],
                        "snippet": "Northstar raised $42M to expand food, pharma, and 3PL deployments.",
                    },
                    {
                        "title": "Warehouse automation market 2026",
                        "url": source_urls[2],
                        "snippet": "The market is consolidating around orchestration software, fleet interoperability, and labor productivity.",
                    },
                    {
                        "title": "Orion Fulfillment AI product update",
                        "url": source_urls[3],
                        "snippet": "Orion focuses on warehouse slotting and demand forecasting rather than robot fleet control.",
                    },
                    {
                        "title": "Vector Pick Systems customer expansion",
                        "url": source_urls[4],
                        "snippet": "Vector sells modular pick stations and works best in greenfield warehouse deployments.",
                    },
                    {
                        "title": "Ranger Autonomous Lifts fleet update",
                        "url": source_urls[5],
                        "snippet": "Ranger focuses on autonomous forklifts and pallet movement for high-throughput warehouses.",
                    },
                    {
                        "title": "Legacy WMS vendors add robotics modules",
                        "url": source_urls[6],
                        "snippet": "Warehouse management incumbents are adding robot orchestration modules to defend installed accounts.",
                    },
                    {
                        "title": "Northstar customer rollout note",
                        "url": source_urls[7],
                        "snippet": "A customer reported 18 percent faster pick-pack cycles after a six-week Atlas pilot.",
                    },
                ],
            },
            "mcp_brightdata_scrape_as_markdown": {
                "rules": [
                    {
                        "url_contains": "atlas-launch",
                        "result": {
                            "status": "ok",
                            "url": source_urls[0],
                            "markdown": (
                                "# Northstar Atlas launch\n\n"
                                "Atlas coordinates robots across vendors, reducing aisle congestion and improving "
                                "warehouse throughput. Northstar positions the product as a software layer for "
                                "brownfield automation programs."
                            ),
                        },
                    },
                    {
                        "url_contains": "northstar-series-b",
                        "result": {
                            "status": "ok",
                            "url": source_urls[1],
                            "markdown": (
                                "# Northstar Series B\n\n"
                                "Northstar raised $42M to expand deployments in food, pharma, and third-party logistics. "
                                "The round emphasized interoperability and fast ROI for existing warehouse footprints."
                            ),
                        },
                    },
                    {
                        "url_contains": "warehouse-automation-2026",
                        "result": {
                            "status": "ok",
                            "url": source_urls[2],
                            "markdown": (
                                "# Warehouse automation market 2026\n\n"
                                "Buyers are prioritizing labor productivity, fleet interoperability, and software that "
                                "layers over existing hardware. Integration risk and change management remain major "
                                "sales blockers."
                            ),
                        },
                    },
                    {
                        "url_contains": "orion-fulfillment-ai",
                        "result": {
                            "status": "ok",
                            "url": source_urls[3],
                            "markdown": (
                                "# Orion Fulfillment AI\n\n"
                                "Orion optimizes slotting, demand forecasts, and labor plans. It competes for operations "
                                "budget but does not directly control heterogeneous robot fleets."
                            ),
                        },
                    },
                    {
                        "url_contains": "vector-pick-systems",
                        "result": {
                            "status": "ok",
                            "url": source_urls[4],
                            "markdown": (
                                "# Vector Pick Systems\n\n"
                                "Vector sells modular pick stations and robotics bundles. It is strong in greenfield "
                                "deployments but less flexible for brownfield mixed-fleet warehouses."
                            ),
                        },
                    },
                    {
                        "url_contains": "ranger-autonomous-lifts",
                        "result": {
                            "status": "ok",
                            "url": source_urls[5],
                            "markdown": (
                                "# Ranger Autonomous Lifts\n\n"
                                "Ranger builds autonomous forklifts and pallet movement systems for high-throughput "
                                "warehouses. It competes more directly on heavy material movement than on mixed-fleet "
                                "software orchestration."
                            ),
                        },
                    },
                    {
                        "url_contains": "legacy-wms-robotics",
                        "result": {
                            "status": "ok",
                            "url": source_urls[6],
                            "markdown": (
                                "# Legacy WMS vendors add robotics modules\n\n"
                                "Warehouse management incumbents are adding robotics modules to protect installed "
                                "accounts. Their advantage is procurement access; their risk is slower robotics-native "
                                "workflow depth."
                            ),
                        },
                    },
                    {
                        "url_contains": "warehouse-rollout",
                        "result": {
                            "status": "ok",
                            "url": source_urls[7],
                            "markdown": (
                                "# Northstar rollout\n\n"
                                "A regional distributor reported an 18 percent improvement in pick-pack cycles after "
                                "a six-week Atlas pilot, with fewer aisle conflicts and less supervisor intervention."
                            ),
                        },
                    },
                ],
                "default": {
                    "status": "ok",
                    "url": "https://market.example.test/source",
                    "markdown": "Deep research source note for warehouse automation, competitors, and Northstar Robotics.",
                },
            },
            "search_tools": {
                "status": "ok",
                "tools": [
                    {
                        "name": "mcp_brightdata_search_engine",
                        "description": "Search deterministic eval web results.",
                    },
                    {
                        "name": "mcp_brightdata_scrape_as_markdown",
                        "description": "Scrape deterministic eval web pages.",
                    },
                ],
            },
            "create_chart": {"status": "error", "message": "The prompt requested no chart artifacts."},
            "create_csv": {"status": "error", "message": "The prompt requested no file artifacts."},
            "create_file": {"status": "error", "message": "The prompt requested no file artifacts."},
            "create_pdf": {"status": "error", "message": "The prompt requested no file artifacts."},
        }
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        if self._is_simulated(run_id):
            inbound = self.inject_message(
                agent_id,
                self.prompt,
                trigger_processing=False,
                eval_run_id=run_id,
            )
            self._record_simulated_tool_call(
                run_id,
                agent_id,
                tool_name="update_plan",
                tool_params={
                    "plan": [
                        {"step": "Collect current source set", "status": "done"},
                        {"step": "Compare market position", "status": "doing"},
                        {"step": "Write memo", "status": "todo"},
                    ],
                    "will_continue_work": True,
                },
            )
            self._record_simulated_tool_call(
                run_id,
                agent_id,
                tool_name="mcp_brightdata_search_engine",
                tool_params={
                    "query": "Northstar Robotics warehouse automation competitors 2026",
                    "will_continue_work": True,
                },
                result=mock_config["mcp_brightdata_search_engine"],
            )
            for index in range(5):
                self._record_simulated_tool_call(
                    run_id,
                    agent_id,
                    tool_name="mcp_brightdata_scrape_as_markdown",
                    tool_params={"url": source_urls[index]},
                    result=mock_config["mcp_brightdata_scrape_as_markdown"]["rules"][index]["result"],
                )
            self._send_simulated_web_report(
                agent_id,
                (
                    "## Northstar Robotics Investment Memo\n\n"
                    "**Thesis:** Northstar is positioned as a brownfield warehouse orchestration layer. That is "
                    "more attractive than a pure hardware wedge because buyers can improve existing fleets without "
                    "a full facility redesign.\n\n"
                    "### Key Takeaways\n\n"
                    "- Northstar's Atlas launch targets mixed-fleet control, reducing aisle congestion and "
                    "supervisor intervention.\n"
                    "- The $42M Series B gives the company enough capital to push deployments in food, pharma, "
                    "and 3PL accounts where downtime is expensive.\n"
                    "- The market is moving toward interoperability and labor productivity rather than isolated "
                    "robot purchases.\n"
                    "- Orion, Vector, and other competitors overlap on operations budget, but their wedges are "
                    "slotting software or greenfield hardware bundles rather than heterogeneous fleet control.\n\n"
                    "### Competitive Table\n\n"
                    "| Company | Wedge | Strength | Risk |\n"
                    "| --- | --- | --- | --- |\n"
                    "| Northstar Robotics | Mixed-fleet robot routing | Brownfield ROI and interoperability | Needs proof across more sites |\n"
                    "| Orion Fulfillment AI | Slotting and demand forecasting | Software-only deployment | Less direct control of robot movement |\n"
                    "| Vector Pick Systems | Modular pick stations | Strong greenfield bundle | Harder sell into existing fleets |\n"
                    "| Legacy WMS vendors | Existing warehouse footprint | Installed base | Slower robotics-native workflow depth |\n\n"
                    "### Evidence\n\n"
                    "Atlas is framed as a software layer for existing fleets, which supports a lower-friction "
                    "deployment motion. The funding source suggests expansion into regulated and high-throughput "
                    "verticals, while market analysis points to interoperability as a buyer priority. The customer "
                    "rollout signal gives Northstar an early ROI story, but the main diligence question is whether "
                    "that result repeats across larger and messier warehouse networks.\n\n"
                    "### Sources\n\n"
                    f"- {source_urls[0]}\n"
                    f"- {source_urls[1]}\n"
                    f"- {source_urls[2]}\n"
                    f"- {source_urls[3]}\n"
                    f"- {source_urls[4]}"
                ),
            )
        else:
            with self.wait_for_agent_idle(agent_id, timeout=240):
                inbound = self.inject_message(
                    agent_id,
                    self.prompt,
                    trigger_processing=True,
                    eval_run_id=run_id,
                    mock_config=mock_config,
                    eval_stop_policy={
                        "stop_on_tool_names": list(
                            (EFFORT_OVERWORK_TOOL_NAMES - {"update_plan"})
                            | ARTIFACT_TOOL_NAMES
                        ),
                        "stop_on_sqlite_agent_config_mutation": True,
                        "stop_on_human_input_request": True,
                        "stop_on_unexpected_relevant_tool": True,
                        "allowed_tool_names": [
                            "search_tools",
                            "mcp_brightdata_search_engine",
                            "mcp_brightdata_scrape_as_markdown",
                            "sqlite_batch",
                            "update_plan",
                        ],
                        "max_relevant_tool_calls": 14,
                        "ignored_tool_names": list(MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES),
                    },
                )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self._record_research_tool_budget(
            run_id,
            after=inbound.timestamp,
            task_name="verify_deep_research_budget",
            allowed_tool_names={
                "search_tools",
                "mcp_brightdata_search_engine",
                "mcp_brightdata_scrape_as_markdown",
                "sqlite_batch",
                "update_plan",
            },
            min_relevant_calls=4,
            max_relevant_calls=14,
        )
        self._record_hierarchical_report(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_rich_hierarchical_memo",
            source_urls=source_urls,
            min_source_count=4,
            min_chars=1700,
            max_chars=5200,
            required_any_groups=(("Northstar Robotics", "Northstar"), ("|", "<table")),
        )
        self._record_plan_update_budget(
            run_id,
            after=inbound.timestamp,
            task_name="verify_plan_budget",
            max_updates=2,
        )
        self._record_no_overwork_tools(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_unrequested_artifacts_or_input",
            forbidden_tool_names=ARTIFACT_TOOL_NAMES
            | {"request_human_input", "secure_credentials_request", "spawn_agent"},
        )
        no_query_loop = self._record_no_repetitive_web_queries(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_query_or_sqlite_loops",
        )
        if no_query_loop:
            self._record_no_sqlite_result_text_reread_loop(
                run_id,
                after=inbound.timestamp,
                task_name="verify_no_query_or_sqlite_loops",
                max_result_text_reads=1,
            )
        self._record_no_agent_config_mutation(
            run_id,
            after=inbound.timestamp,
            task_name="verify_no_config_churn",
        )
        self._record_orchestrator_budget(run_id, task_name="verify_turn_budget", max_completions=9)
