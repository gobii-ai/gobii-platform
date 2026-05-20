import json
from typing import Iterable

from django.db.models import Sum

from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_SERVER
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import register_scenario
from api.evals.stop_policy import (
    sqlite_batch_is_only_eval_bookkeeping_read,
    sqlite_batch_is_only_planning_state_mutation,
    sqlite_batch_is_only_planning_state_read,
    sqlite_batch_mutates_planning_state,
)
from api.models import (
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

EFFORT_CALIBRATION_SCENARIO_SLUGS = [
    EFFORT_TRIVIAL_ANSWER_STOPS,
    EFFORT_SIMPLE_LOOKUP_BOUNDED_TOOLS,
    EFFORT_SCHEDULED_BRIEFING_FINISHES,
    EFFORT_DEFAULTABLE_RESEARCH_NO_QUESTION_BATTERY,
    EFFORT_PARTIAL_BRIEFING_REPORTS_WITHOUT_SURVEY,
    EFFORT_CHART_REQUESTED_SINGLE_ARTIFACT,
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


def _human_input_requests_for_run(run_id: str, *, after=None):
    queryset = PersistentAgentHumanInputRequest.objects.filter(originating_step__eval_run_id=run_id)
    if after is not None:
        queryset = queryset.filter(created_at__gte=after)
    return list(queryset.order_by("created_at", "id"))


def _question_count(text: str) -> int:
    return (text or "").count("?")


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
    ) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        forbidden = set(forbidden_tool_names)
        bad_calls = [
            call
            for call in _relevant_tool_calls_for_run(run_id, after=after)
            if call.tool_name in forbidden
        ]
        if bad_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name=task_name,
                observed_summary=f"Unexpected overwork/tool call(s): {[call.tool_name for call in bad_calls]}",
                artifacts={"step": bad_calls[0].step},
            )
            return False

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name=task_name,
            observed_summary="No unnecessary artifact, plan, or human-input tools were used.",
        )
        return True

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
        if not requests and question_marks <= max_message_questions:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary=(
                    "No tracked human-input request and final message question count was "
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
                f"human_input_requests={len(requests)}, message_question_marks={question_marks}."
            ),
            artifacts={"message": outbound[-1]} if outbound else {},
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


@register_scenario
class EffortTrivialAnswerStopsScenario(EffortCalibrationScenario):
    slug = EFFORT_TRIVIAL_ANSWER_STOPS
    description = "A trivial user request should receive one minimal answer and stop without plans, artifacts, or follow-up questions."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
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
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
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
        ScenarioTask(name="trigger_scheduled_run", assertion_type="manual"),
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
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_research_bounded", assertion_type="manual"),
        ScenarioTask(name="verify_single_sourced_answer", assertion_type="manual"),
        ScenarioTask(name="verify_no_question_battery", assertion_type="manual"),
        ScenarioTask(name="verify_no_artifacts", assertion_type="manual"),
        ScenarioTask(name="verify_no_config_churn", assertion_type="manual"),
    ]

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
                eval_stop_policy={
                    "stop_on_tool_names": list(EFFORT_OVERWORK_TOOL_NAMES - {"update_plan"}),
                    "stop_on_sqlite_agent_config_mutation": True,
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

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_research_bounded")
        relevant = _relevant_tool_calls_for_run(
            run_id,
            after=inbound.timestamp,
            ignored_tool_names=MESSAGE_TOOL_NAMES | STOP_TOOL_NAMES,
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
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
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
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
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
