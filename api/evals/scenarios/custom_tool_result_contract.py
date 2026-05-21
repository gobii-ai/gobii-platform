import json
from dataclasses import dataclass, field
from typing import Any

from api.agent.tools.custom_tools import normalize_custom_tool_name
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.models import EvalRunTask, PersistentAgent, PersistentAgentToolCall

CUSTOM_TOOL_RESULT_CONTRACT_SUITE_SLUG = "custom_tool_result_contract"


@dataclass(frozen=True)
class CustomToolResultContractCase:
    slug: str
    title: str
    real_world_basis: str
    user_task: str
    custom_tool_job: str
    required_result_traits: tuple[str, ...]
    required_param_names: tuple[str, ...]
    required_fields: tuple[str, ...] = field(
        default=(
            "status",
            "summary",
            "side_effects",
            "source",
            "verification",
        )
    )
    requires_manual_replay_prevention: bool = False
    requires_batching: bool = False

    @property
    def scenario_slug(self) -> str:
        return f"custom_tool_result_{self.slug}"


CUSTOM_TOOL_RESULT_CONTRACT_CASES = (
    CustomToolResultContractCase(
        slug="sheets_final_sync",
        title="Google Sheets final sync",
        real_world_basis=(
            "A Clay Pay signal-hunter agent used a custom final sync tool that appended today's SQLite findings "
            "to Google Sheets, then manually replayed stale google_sheets-add-rows calls because the custom tool "
            "result did not make the completed side effects unmistakable."
        ),
        user_task="Sync today's new ISV buying signals and run log from SQLite to the configured Google Sheet.",
        custom_tool_job=(
            "Read today's findings and run_log rows from SQLite, append new records to the signals and run_log "
            "worksheets, and report exactly what was written."
        ),
        required_result_traits=(
            "states that the Sheets append side effects are complete",
            "names the spreadsheet and worksheets",
            "reports per-worksheet rows appended",
            "reports source table filters or run_date used",
            "tells the agent not to manually append the same records again",
            "makes read-only verification/follow-up clear",
        ),
        required_param_names=("run_date",),
        requires_manual_replay_prevention=True,
    ),
    CustomToolResultContractCase(
        slug="sheets_backlog_sync",
        title="Chunked Sheets backlog sync",
        real_world_basis=(
            "Backlog sync custom tools need to move historical SQLite records to Sheets without timing out or "
            "losing the agent's place across runs."
        ),
        user_task="Backfill unsynced signal rows from the last week into Google Sheets in safe batches.",
        custom_tool_job=(
            "Append a bounded batch of unsynced rows, mark progress in SQLite, and leave clear state for the next "
            "invocation."
        ),
        required_result_traits=(
            "reports batch size and rows appended",
            "reports date range or ids processed",
            "reports remaining unsynced rows",
            "returns next_cursor or remaining_work for resuming",
            "reports skipped duplicates",
            "explains whether another write invocation is needed",
        ),
        required_param_names=("batch_size", "status_filter"),
        requires_manual_replay_prevention=True,
        requires_batching=True,
    ),
    CustomToolResultContractCase(
        slug="dedupe_format_signals",
        title="Dedupe and format research signals",
        real_world_basis=(
            "The Clay Pay agent used custom tools to dedupe and format candidate ISV signals before export; useful "
            "results need to explain what survived filtering and why records were skipped."
        ),
        user_task="Deduplicate raw ISV signal candidates, format accepted rows, and store them for later sync.",
        custom_tool_job=(
            "Read raw candidates, apply duplicate rules and quality gates, write accepted rows to SQLite, and "
            "return a compact quality summary."
        ),
        required_result_traits=(
            "reports input, accepted, rejected, and duplicate counts",
            "names the duplicate key or matching rule",
            "reports destination table and rows written",
            "includes representative rejection reasons",
            "makes clear that no external write happened yet",
            "states the verification or follow-up step",
        ),
        required_param_names=("input_table", "output_table", "run_date"),
    ),
    CustomToolResultContractCase(
        slug="scrape_url_normalization",
        title="Scrape URL normalization",
        real_world_basis=(
            "A judge suggestion found a scrape tool was called with domain names instead of fully qualified URLs; "
            "a custom validator should make accepted and rejected inputs clear."
        ),
        user_task="Normalize company domains into scrape-ready URLs before calling the markdown scrape tool.",
        custom_tool_job=(
            "Validate URL inputs, add schemes where safe, reject ambiguous values, and store the scrape-ready list."
        ),
        required_result_traits=(
            "reports normalized fully qualified URLs",
            "reports rejected inputs with reasons",
            "states no scrape was performed if it only validated",
            "reports destination table or file",
            "makes clear only accepted scrape-ready URLs should be used downstream",
        ),
        required_param_names=("input_table", "output_table", "default_scheme"),
    ),
    CustomToolResultContractCase(
        slug="linkedin_post_urls",
        title="LinkedIn post URL extraction",
        real_world_basis=(
            "A judge suggestion found a LinkedIn posts extractor was given member activity feed URLs instead of "
            "direct post URLs."
        ),
        user_task="Extract direct LinkedIn post URLs from search results and reject profile or activity feed URLs.",
        custom_tool_job=(
            "Classify candidate LinkedIn URLs, keep only direct `/posts/...` URLs for the structured posts tool, "
            "reject feed/update activity, profile, company, job, and pulse URLs, and explain what was rejected."
        ),
        required_result_traits=(
            "reports accepted direct post URLs",
            "reports rejected profile/activity/feed URLs",
            "explains the URL rule used",
            "states whether enough post URLs were found",
            "recommends targeted search if more direct post URLs are needed",
        ),
        required_param_names=("input_table", "output_table", "min_posts"),
    ),
    CustomToolResultContractCase(
        slug="chunked_mcp_fanout",
        title="Chunked MCP fan-out",
        real_world_basis=(
            "Custom tools often call search, scrape, or structured-data MCP tools in loops; helpful results need to "
            "distinguish completed work from partial progress."
        ),
        user_task="Run a bounded batch of MCP searches across candidate companies and save normalized findings.",
        custom_tool_job=(
            "Process a limited set of pending companies, call MCP tools through ctx.call_tool, upsert normalized "
            "results, and persist progress for later batches."
        ),
        required_result_traits=(
            "reports items attempted, succeeded, failed, and skipped",
            "reports output table and rows upserted",
            "reports retryable failures separately",
            "returns next_cursor or remaining count",
            "states whether rerunning the custom tool is appropriate",
        ),
        required_param_names=("batch_size", "status_filter", "next_cursor"),
        requires_batching=True,
    ),
)

CUSTOM_TOOL_RESULT_CONTRACT_SCENARIO_SLUGS = [
    case.scenario_slug for case in CUSTOM_TOOL_RESULT_CONTRACT_CASES
]

PARAM_NAME_ALIASES = {
    "batch_size": ("batch_size", "batch_limit", "limit", "max_items", "max_rows", "row_limit"),
    "default_scheme": ("default_scheme", "scheme", "url_scheme", "fallback_scheme"),
    "input_table": ("input_table", "source_table", "input_source", "source"),
    "min_posts": ("min_posts", "minimum_posts", "min_post_count", "target_post_count"),
    "next_cursor": ("next_cursor", "cursor", "page_cursor", "resume_cursor", "offset"),
    "output_table": ("output_table", "destination_table", "output_destination", "destination"),
    "run_date": ("run_date", "sync_date", "date_filter", "date", "created_date", "since_date"),
    "status_filter": ("status_filter", "status", "sync_status", "filter_status"),
}


class CustomToolResultContractScenario(EvalScenario, ScenarioExecutionTools):
    description = "Evaluates whether custom tools are designed to return helpful side-effect summaries."
    tier = "core"
    category = "custom_tools"
    expected_runtime = "medium"
    cost_class = "low"
    owner = "agent-platform"
    area = "custom_tools"
    tags = ("custom_tools", "result_contract", "llm_judge", "micro", "agent_processing")
    tasks = [
        ScenarioTask(
            name="inject_prompt",
            assertion_type="agent_processing",
            description="Injects the custom-tool task into the real agent event loop.",
        ),
        ScenarioTask(
            name="propose_result_contract",
            assertion_type="tool_call",
            description="Agent creates a custom tool with useful params, batching, and result contract.",
        ),
        ScenarioTask(
            name="invoke_custom_tool",
            assertion_type="tool_call",
            description="Agent invokes the custom tool with useful runtime params.",
        ),
        ScenarioTask(
            name="judge_result_helpfulness",
            assertion_type="llm_judge",
            description="Judge validates the actual custom tool design emitted by the agent.",
        ),
    ]
    case: CustomToolResultContractCase | None = None

    def run(self, run_id: str, agent_id: str) -> None:
        if self.case is None:
            raise ValueError("Custom tool result contract eval is missing case metadata.")

        case = self.case
        custom_tool_name = self._custom_tool_name(case)
        PersistentAgent.objects.filter(id=agent_id).update(
            charter=(
                "You are an eval agent. Use the real agent tool stack to satisfy the user's request. "
                "Do not simulate completed tool work in chat."
            ),
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=180):
            inbound = self.inject_message(
                agent_id,
                self._agent_prompt(case),
                trigger_processing=True,
                eval_run_id=run_id,
                eval_stop_policy=self._eval_stop_policy(case, custom_tool_name),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and real agent processing completed.",
            artifacts={"message": inbound},
        )

        tool_calls = self._tool_calls_for_run(run_id, after=inbound.timestamp)
        create_calls = [call for call in tool_calls if call.tool_name == "create_custom_tool"]
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="propose_result_contract",
            expected_summary="Agent should create a custom tool with useful params, batching, and result contract.",
        )
        if not create_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="propose_result_contract",
                observed_summary="Agent did not call create_custom_tool.",
            )
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.SKIPPED,
                task_name="invoke_custom_tool",
                observed_summary="Skipped because no custom tool was created.",
            )
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.SKIPPED,
                task_name="judge_result_helpfulness",
                observed_summary="Skipped because no custom tool design was available.",
            )
            return

        create_call = create_calls[-1]
        create_source_code = self._source_code_for_create_call(tool_calls, create_call)
        local_pass, local_reason = self._local_create_tool_check(
            case,
            create_call,
            custom_tool_name,
            source_code_override=create_source_code,
        )
        for candidate_call in reversed(create_calls):
            candidate_source_code = self._source_code_for_create_call(tool_calls, candidate_call)
            candidate_pass, candidate_reason = self._local_create_tool_check(
                case,
                candidate_call,
                custom_tool_name,
                source_code_override=candidate_source_code,
            )
            if candidate_pass:
                create_call = candidate_call
                create_source_code = candidate_source_code
                local_pass = candidate_pass
                local_reason = candidate_reason
                break

        proposal_status = EvalRunTask.Status.PASSED if local_pass else EvalRunTask.Status.FAILED
        self.record_task_result(
            run_id,
            None,
            proposal_status,
            task_name="propose_result_contract",
            observed_summary=local_reason,
            artifacts={
                "step": create_call.step,
                "create_params": create_call.tool_params,
                "create_tool_call_count": len(create_calls),
                "source_code_origin": self._source_code_origin(create_call, create_source_code),
            },
        )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="invoke_custom_tool",
            expected_summary="Agent should invoke the custom tool with useful runtime params.",
        )
        custom_call = next((call for call in reversed(tool_calls) if call.tool_name == custom_tool_name), None)
        if custom_call is None:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="invoke_custom_tool",
                observed_summary=f"Agent did not invoke {custom_tool_name}.",
            )
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="judge_result_helpfulness",
                observed_summary="Custom tool was not invoked, so no custom tool result was available to judge.",
            )
            return
        else:
            call_pass, call_reason = self._local_custom_call_check(case, custom_call)
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED if call_pass else EvalRunTask.Status.FAILED,
                task_name="invoke_custom_tool",
                observed_summary=call_reason,
                artifacts={"step": custom_call.step, "custom_tool_params": custom_call.tool_params},
            )

        if not local_pass or not call_pass:
            prerequisite_summary = self._local_prerequisite_failure_summary(
                create_pass=local_pass,
                create_reason=local_reason,
                invoke_pass=call_pass,
                invoke_reason=call_reason,
            )
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.SKIPPED,
                task_name="judge_result_helpfulness",
                observed_summary=(
                    "Skipped LLM judge because prerequisite local checks failed: "
                    f"{prerequisite_summary}"
                ),
                artifacts={
                    "step": create_call.step,
                    "create_params": create_call.tool_params,
                    "custom_tool_params": custom_call.tool_params,
                },
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="judge_result_helpfulness",
            expected_summary="LLM judge should confirm the actual custom tool design is helpful.",
        )
        choice, reasoning = self.llm_judge(
            question=(
                "Does the actual create_custom_tool design and custom tool invocation include useful params, "
                "batching when needed, and source code that would return helpful results to the agent?"
            ),
            context=self._agent_judge_context(
                case,
                create_call,
                custom_call,
                source_code_override=create_source_code,
            ),
            options=("Yes", "No"),
            params={"temperature": 0.0, "max_tokens": 700, "reasoning_effort": "low"},
        )

        if choice == "Yes":
            judge_status = EvalRunTask.Status.PASSED
        elif choice == "Error":
            judge_status = EvalRunTask.Status.ERRORED
        else:
            judge_status = EvalRunTask.Status.FAILED

        self.record_task_result(
            run_id,
            None,
            judge_status,
            task_name="judge_result_helpfulness",
            observed_summary=f"{choice}: {reasoning}",
            artifacts={"step": create_call.step, "create_params": create_call.tool_params},
        )

    @staticmethod
    def _custom_tool_name(case: CustomToolResultContractCase) -> str:
        normalized = normalize_custom_tool_name(case.slug)
        if normalized is None:
            return f"custom_{case.slug}"
        return normalized[1]

    @staticmethod
    def _eval_stop_policy(case: CustomToolResultContractCase, custom_tool_name: str) -> dict[str, Any]:
        policy: dict[str, Any] = {
            "max_relevant_tool_calls": 16,
            "ignored_tool_names": ["update_plan", "end_planning"],
        }
        if case.requires_batching:
            policy["stop_when_all_seen"] = [
                {
                    "tool_name": custom_tool_name,
                    "after_execution": True,
                    "required_params_any": list(PARAM_NAME_ALIASES["batch_size"]),
                }
            ]
        else:
            policy["stop_on_tool_names_after_execution"] = [custom_tool_name]
        return policy

    @classmethod
    def _agent_prompt(cls, case: CustomToolResultContractCase) -> str:
        custom_tool_name = cls._custom_tool_name(case)
        return (
            f"Build a reusable Python custom tool named `{custom_tool_name}` for this task, then run it once "
            "with realistic representative inputs.\n\n"
            f"Real-world basis: {case.real_world_basis}\n\n"
            f"User task: {case.user_task}\n\n"
            f"Custom tool job: {case.custom_tool_job}\n\n"
            "Eval safety constraints: use representative sample data instead of live external data, and do not "
            "perform real external writes or call real Google Sheets, LinkedIn, scraping, or MCP services. "
            "Make the tool reusable with runtime parameters for source inputs/tables, destination outputs/tables, "
            "dates/status/minimums/batch limits when relevant; when you run it, pass concrete representative values. "
            "Simulate any external side effects that would normally happen."
        )

    @staticmethod
    def _tool_calls_for_run(run_id: str, *, after=None) -> list[PersistentAgentToolCall]:
        queryset = PersistentAgentToolCall.objects.filter(step__eval_run_id=run_id)
        if after is not None:
            queryset = queryset.filter(step__created_at__gte=after)
        return list(queryset.select_related("step").order_by("step__created_at", "step__id"))

    @staticmethod
    def _schema_properties(parameters_schema: Any) -> dict[str, Any]:
        if not isinstance(parameters_schema, dict):
            return {}
        properties = parameters_schema.get("properties") or {}
        return properties if isinstance(properties, dict) else {}

    @staticmethod
    def _matching_param_names(param_name: str, available: set[str]) -> set[str]:
        aliases = PARAM_NAME_ALIASES.get(param_name, (param_name,))
        return {alias for alias in aliases if alias in available}

    @staticmethod
    def _decoded_tool_result(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    @staticmethod
    def _source_code_origin(create_call: PersistentAgentToolCall, source_code: str | None) -> str:
        params = create_call.tool_params or {}
        if str(params.get("source_code") or "").strip():
            return "create_custom_tool.source_code"
        if source_code:
            return "create_file.content"
        return "missing"

    @classmethod
    def _source_code_for_create_call(
        cls,
        tool_calls: list[PersistentAgentToolCall],
        create_call: PersistentAgentToolCall,
    ) -> str | None:
        params = create_call.tool_params or {}
        source_code = str(params.get("source_code") or "")
        if source_code.strip():
            return source_code

        source_path = params.get("source_path")
        if not isinstance(source_path, str) or not source_path:
            return None

        create_step = getattr(create_call, "step", None)
        create_time = getattr(create_step, "created_at", None)
        candidates: list[tuple[str, Any]] = []
        for tool_call in tool_calls:
            if tool_call.tool_name not in {"create_custom_tool", "create_file"}:
                continue
            step = getattr(tool_call, "step", None)
            call_time = getattr(step, "created_at", None)
            if create_time is not None and call_time is not None and call_time > create_time:
                continue
            call_params = tool_call.tool_params or {}
            if tool_call.tool_name == "create_custom_tool":
                if tool_call == create_call or call_params.get("source_path") != source_path:
                    continue
                content = call_params.get("source_code")
            else:
                decoded_result = cls._decoded_tool_result(tool_call.result)
                call_result = decoded_result if isinstance(decoded_result, dict) else {}
                file_path = call_params.get("file_path") or call_params.get("path")
                result_ref = call_result.get("file") or call_result.get("attach")
                if file_path != source_path and result_ref != f"$[{source_path}]":
                    continue
                content = call_params.get("content")
            if isinstance(content, str) and content.strip():
                candidates.append((content, call_time))

        if not candidates:
            return None

        content, content_time = candidates[-1]
        for tool_call in tool_calls:
            if tool_call.tool_name != "file_str_replace":
                continue
            step = getattr(tool_call, "step", None)
            call_time = getattr(step, "created_at", None)
            if content_time is not None and call_time is not None and call_time < content_time:
                continue
            if create_time is not None and call_time is not None and call_time > create_time:
                continue
            call_params = tool_call.tool_params or {}
            if call_params.get("path") != source_path:
                continue
            old_text = call_params.get("old_text")
            new_text = call_params.get("new_text")
            if not isinstance(old_text, str) or not isinstance(new_text, str) or old_text not in content:
                continue
            if call_params.get("replace_all"):
                content = content.replace(old_text, new_text)
            else:
                content = content.replace(old_text, new_text, 1)

        return content

    @classmethod
    def _local_create_tool_check(
        cls,
        case: CustomToolResultContractCase,
        create_call: PersistentAgentToolCall,
        expected_tool_name: str,
        *,
        source_code_override: str | None = None,
    ) -> tuple[bool, str]:
        params = create_call.tool_params or {}
        raw_name = params.get("name") or expected_tool_name
        normalized = normalize_custom_tool_name(raw_name)
        if normalized is None or normalized[1] != expected_tool_name:
            return False, f"Expected custom tool name {expected_tool_name}, saw {raw_name!r}."

        parameters_schema = params.get("parameters_schema") or {}
        properties = cls._schema_properties(parameters_schema)
        if not properties:
            return False, "create_custom_tool parameters_schema should expose useful runtime params."

        if case.requires_batching and not cls._matching_param_names("batch_size", set(properties)):
            return False, "batching-required tool schema must expose a batch_size or equivalent limit param."

        source_path = params.get("source_path")
        if not isinstance(source_path, str) or not source_path.startswith("/tools/") or not source_path.endswith(".py"):
            return False, "create_custom_tool should provide a /tools/*.py source_path."

        source_code = str(source_code_override if source_code_override is not None else params.get("source_code") or "")
        if not source_code.strip():
            return False, (
                "custom tool source must be inspectable via create_custom_tool.source_code or a prior "
                "create_file call for the same /tools/*.py source_path."
            )

        source_text = source_code.lower()
        required_runtime_terms = [
            "from _gobii_ctx import main",
            "def run(",
            "main(run)",
        ]
        missing_runtime_terms = [term for term in required_runtime_terms if term not in source_text]
        if missing_runtime_terms:
            return False, f"custom tool source is missing runtime wrapper term(s): {missing_runtime_terms}."

        result_categories = {
            "outcome": ("status", "summary", "message"),
            "counts": (
                "count",
                "rows_",
                "items_",
                "accepted",
                "rejected",
                "skipped",
                "duplicate",
                "total",
            ),
            "scope": (
                "source",
                "filter",
                "date",
                "input",
                "output",
                "table",
                "target",
                "worksheet",
                "url",
            ),
            "actionable_guidance": (
                "next_action",
                "next action",
                "follow_up",
                "follow-up",
                "verify",
                "verification",
                "read-only",
                "instructions",
                "scrape_ready",
                "valid_post_urls",
                "direct_post_urls",
                "remaining_work",
                "next_cursor",
            ),
        }
        missing_categories = [
            category
            for category, terms in result_categories.items()
            if not any(term in source_text for term in terms)
        ]
        if missing_categories:
            return False, (
                "custom tool source is missing helpful result signal category/categories: "
                f"{missing_categories}."
            )

        if case.requires_manual_replay_prevention:
            replay_prevention_terms = (
                "do_not_repeat_manually",
                "read-only",
                "read only",
                "do not repeat",
                "do not replay",
                "do not manually",
                "not another append",
                "not replay",
            )
            if not any(term in source_text for term in replay_prevention_terms):
                return False, (
                    "completed side-effect tool source must make manual replay prevention clear."
                )

        if case.requires_batching:
            if "batch_size" not in source_text and "limit" not in source_text:
                return False, "batching-required tool source must include batch_size or limit handling."
            if not any(term in source_text for term in ("remaining_work", "remaining", "next_cursor", "cursor")):
                return False, "batching-required tool source must include remaining-work or cursor handling."

        return True, "Agent created a custom tool with useful params, batching policy, and helpful result fields."

    @staticmethod
    def _local_prerequisite_failure_summary(
        *,
        create_pass: bool,
        create_reason: str,
        invoke_pass: bool,
        invoke_reason: str,
    ) -> str:
        failures = []
        if not create_pass:
            failures.append(f"propose_result_contract: {create_reason}")
        if not invoke_pass:
            failures.append(f"invoke_custom_tool: {invoke_reason}")
        return "; ".join(failures)

    @staticmethod
    def _local_custom_call_check(
        case: CustomToolResultContractCase,
        custom_call: PersistentAgentToolCall,
    ) -> tuple[bool, str]:
        params = custom_call.tool_params or {}
        decoded_result = CustomToolResultContractScenario._decoded_tool_result(custom_call.result)
        result = decoded_result if isinstance(decoded_result, dict) else {}
        if str(getattr(custom_call, "status", "") or "").lower() == "error":
            return False, f"custom tool invocation errored: {result.get('message') or 'unknown error'}"
        if str(result.get("status") or "").lower() == "error":
            return False, f"custom tool invocation errored: {result.get('message') or 'unknown error'}"

        param_names = set(params)
        if not param_names:
            return False, "custom tool invocation should pass useful runtime params."

        if case.requires_batching and not CustomToolResultContractScenario._matching_param_names(
            "batch_size",
            param_names,
        ):
            return False, "batching-required custom tool invocation must include batch_size or limit."

        return True, "Agent invoked the custom tool with useful runtime params."

    @classmethod
    def _agent_judge_context(
        cls,
        case: CustomToolResultContractCase,
        create_call: PersistentAgentToolCall,
        custom_call: PersistentAgentToolCall | None,
        *,
        source_code_override: str | None = None,
    ) -> str:
        create_params = create_call.tool_params or {}
        source_code = (
            source_code_override
            if source_code_override is not None
            else create_params.get("source_code")
        )
        custom_params = custom_call.tool_params if custom_call is not None else None
        custom_result = cls._decoded_tool_result(custom_call.result) if custom_call is not None else None
        return json.dumps(
            {
                "case": {
                    "slug": case.slug,
                    "title": case.title,
                    "real_world_basis": case.real_world_basis,
                    "user_task": case.user_task,
                    "custom_tool_job": case.custom_tool_job,
                    "helpful_param_concepts": list(case.required_param_names),
                    "requires_batching": case.requires_batching,
                    "must_prevent_manual_replay": case.requires_manual_replay_prevention,
                    "required_result_traits": list(case.required_result_traits),
                },
                "create_custom_tool": {
                    "params": {
                        "name": create_params.get("name"),
                        "description": create_params.get("description"),
                        "parameters_schema": create_params.get("parameters_schema"),
                        "source_code": source_code,
                        "source_code_origin": cls._source_code_origin(create_call, source_code),
                    }
                },
                "custom_tool_invocation": {
                    "tool_name": custom_call.tool_name if custom_call is not None else None,
                    "params": custom_params,
                    "result": custom_result,
                },
                "judge_rubric": [
                    "The agent must use create_custom_tool and invoke the resulting custom_* tool.",
                    "The parameters schema and invocation must expose useful runtime params rather than hardcoding ids, dates, filters, destinations, or batch settings. Equivalent parameter names are acceptable when they cover the same concept.",
                    "Batching-required cases must include bounded batch params and resumable remaining-work state.",
                    "The custom tool source/result must return helpful fields such as side effects, counts, source scope, verification/follow-up guidance, resumable remaining work, or ready-to-use accepted outputs.",
                    "Completed writes must include manual replay prevention. Read-only validators do not need replay-prevention guidance if they clearly say no external write occurred.",
                ],
            },
            indent=2,
            sort_keys=True,
        )


def _scenario_class(case: CustomToolResultContractCase):
    class _CustomToolResultContractScenario(CustomToolResultContractScenario):
        slug = case.scenario_slug
        description = f"Custom tool result contract should be helpful for {case.title.lower()}."

    _CustomToolResultContractScenario.case = case
    _CustomToolResultContractScenario.__name__ = "".join(
        part.title() for part in case.scenario_slug.split("_")
    ) + "Scenario"
    return _CustomToolResultContractScenario


for custom_tool_result_case in CUSTOM_TOOL_RESULT_CONTRACT_CASES:
    ScenarioRegistry.register(_scenario_class(custom_tool_result_case)())
