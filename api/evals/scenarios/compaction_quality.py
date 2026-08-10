"""Live-model evals for communication and step compaction prompts."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from api.agent.core.compaction import llm_summarise_comms
from api.agent.core.compaction_exceptions import CompactionSummaryError
from api.agent.core.step_compaction import (
    CronTriggerStep,
    GenericStep,
    StepData,
    SystemStep,
    ToolCallStep,
    llm_summarise_steps,
)
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.compaction_quality import (
    COMPACTION_QUALITY_CASES,
    COMPACTION_QUALITY_SCENARIO_SLUGS,
    COMPACTION_QUALITY_SUITE_SLUG,
    COMPACTION_QUALITY_TASK_NAME,
    CommsEvalEvent,
    CompactionQualityCase,
    StepEvalEvent,
    check_compaction_summary,
)
from api.evals.execution import ScenarioExecutionTools, get_eval_routing_profile_for_current_run
from api.evals.registry import ScenarioRegistry
from api.models import EvalRunTask, PersistentAgent


class CompactionQualityScenario(EvalScenario, ScenarioExecutionTools):
    tier = "extended"
    category = "compaction_quality"
    expected_runtime = "short"
    cost_class = "medium"
    owner = "agent-platform"
    area = "agent_memory"
    include_in_default_suites = False
    tags = ("compaction_quality", "llm_judge", "model_comparison")
    tasks = [
        ScenarioTask(
            name=COMPACTION_QUALITY_TASK_NAME,
            assertion_type="llm_judge",
            description="Generate and grade one production-shaped compaction summary.",
        ),
    ]
    case: CompactionQualityCase | None = None

    def run(self, run_id: str, agent_id: str) -> None:
        case = self._case()
        agent = PersistentAgent.objects.get(id=agent_id)
        routing_profile = get_eval_routing_profile_for_current_run()
        candidate_model, judge_model = self._profile_models(routing_profile)

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name=COMPACTION_QUALITY_TASK_NAME,
            observed_summary=f"Generating {case.kind} compaction summary for {case.name}.",
            artifacts={
                "candidate_model": candidate_model,
                "judge_model": judge_model,
                "batch_count": len(case.batches),
            },
        )

        try:
            summary = self.generate_summary(
                case,
                agent=agent,
                routing_profile=routing_profile,
                eval_run_id=run_id,
            )
        except CompactionSummaryError as exc:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.ERRORED,
                task_name=COMPACTION_QUALITY_TASK_NAME,
                observed_summary="Compaction model failed before producing a summary.",
                expected_summary=self._expected_summary(case),
                artifacts={
                    "candidate_model": candidate_model,
                    "judge_model": judge_model,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return

        checks = check_compaction_summary(case, summary)
        choice, reasoning = self.llm_judge(
            question=self._judge_question(case),
            context=self._judge_context(case, summary),
            options=("Pass", "Fail"),
        )
        if choice == "Error":
            status = EvalRunTask.Status.ERRORED
        elif checks.passed and choice == "Pass":
            status = EvalRunTask.Status.PASSED
        else:
            status = EvalRunTask.Status.FAILED

        task = self.record_task_result(
            run_id,
            None,
            status,
            task_name=COMPACTION_QUALITY_TASK_NAME,
            observed_summary=summary,
            expected_summary=self._expected_summary(case),
            artifacts={
                "candidate_model": candidate_model,
                "judge_model": judge_model,
                "summary_chars": len(summary),
                "hard_checks_passed": checks.passed,
                "hard_check_failures": list(checks.failures),
                "judge_choice": choice,
                "judge_reasoning": reasoning,
                "batch_count": len(case.batches),
            },
        )
        task.llm_model = judge_model
        task.llm_question = self._judge_question(case)
        task.llm_answer = f"{choice}: {reasoning}"
        task.save(update_fields=["llm_model", "llm_question", "llm_answer", "updated_at"])

    def generate_summary(
        self,
        case: CompactionQualityCase,
        *,
        agent: PersistentAgent,
        routing_profile,
        eval_run_id: str,
    ) -> str:
        summary = case.previous_summary
        for batch_index, batch in enumerate(case.batches):
            if case.kind == "comms":
                messages = [self._message_from_event(event) for event in batch]
                summary = llm_summarise_comms(
                    summary,
                    messages,
                    agent=agent,
                    routing_profile=routing_profile,
                    eval_run_id=eval_run_id,
                )
            else:
                steps = [
                    self._step_from_event(event, batch_index=batch_index, event_index=event_index)
                    for event_index, event in enumerate(batch)
                ]
                summary = llm_summarise_steps(
                    summary,
                    steps,
                    agent=agent,
                    routing_profile=routing_profile,
                    eval_run_id=eval_run_id,
                )
        return summary

    def _case(self) -> CompactionQualityCase:
        if self.case is None:
            raise ValueError("CompactionQualityScenario.case must be set.")
        return self.case

    @staticmethod
    def _message_from_event(event) -> SimpleNamespace:
        if not isinstance(event, CommsEvalEvent):
            raise TypeError("Communication compaction cases may only contain CommsEvalEvent values.")

        endpoint = SimpleNamespace(channel=event.channel, address=event.party)
        conversation = SimpleNamespace(
            channel=event.channel,
            address=event.party,
            is_peer_dm=event.peer_dm,
        )
        peer_agent = SimpleNamespace(name=event.party) if event.peer_dm else None
        raw_payload = {} if event.peer_dm else {
            "source_kind": event.channel,
            "source_label": event.party,
        }
        return SimpleNamespace(
            is_outbound=event.direction == "outbound",
            body=event.body,
            raw_payload=raw_payload,
            from_endpoint=None if event.direction == "outbound" else endpoint,
            to_endpoint=endpoint if event.direction == "outbound" else None,
            conversation=conversation,
            peer_agent=peer_agent,
        )

    @staticmethod
    def _step_from_event(event, *, batch_index: int, event_index: int) -> StepData:
        if not isinstance(event, StepEvalEvent):
            raise TypeError("Step compaction cases may only contain StepEvalEvent values.")

        created_at = datetime(2026, 9, 20, tzinfo=timezone.utc) + timedelta(
            days=batch_index,
            seconds=event_index,
        )
        base = {
            "step_id": f"eval-step-{batch_index}-{event_index}",
            "created_at": created_at,
            "description": event.description,
        }
        if event.kind == "tool":
            return ToolCallStep(
                **base,
                tool_name=event.tool_name,
                tool_params=event.tool_params,
                result_tail=event.result,
            )
        if event.kind == "cron":
            return CronTriggerStep(
                **base,
                cron_expression=event.cron_expression,
                schedule_key=event.schedule_key,
                schedule_name=event.schedule_name,
                schedule_instruction=event.schedule_instruction,
                scheduled_for=(
                    datetime.fromisoformat(event.scheduled_for)
                    if event.scheduled_for
                    else None
                ),
            )
        if event.kind == "system":
            return SystemStep(
                **base,
                code=event.system_code,
                notes=event.system_notes,
            )
        return GenericStep(**base)

    @staticmethod
    def _profile_models(routing_profile) -> tuple[str, str]:
        if routing_profile is None:
            return "", ""
        candidate_endpoint = routing_profile.summarization_endpoint
        judge_endpoint = routing_profile.eval_judge_endpoint
        return (
            candidate_endpoint.litellm_model if candidate_endpoint else "",
            judge_endpoint.litellm_model if judge_endpoint else "",
        )

    @staticmethod
    def _expected_summary(case: CompactionQualityCase) -> str:
        exact = ", ".join(case.required_exact)
        normalized = ", ".join(case.required_normalized) or "(none)"
        forbidden = ", ".join(case.forbidden_exact) or "(none)"
        return (
            f"Required exact values: {exact}. Required normalized facts: {normalized}. "
            f"Forbidden values: {forbidden}. "
            f"Maximum length: {case.max_chars} characters."
        )

    @staticmethod
    def _judge_question(case: CompactionQualityCase) -> str:
        requirements = "\n".join(f"- {item}" for item in case.semantic_requirements)
        return (
            "Does the candidate summary accurately represent the current state after every batch? "
            "It must preserve correct attribution, scope, durable outcomes, and unresolved work; remove superseded "
            "state and repetitive mechanics; remain concise; and introduce no facts unsupported by the source.\n\n"
            f"Case-specific requirements:\n{requirements}"
        )

    @staticmethod
    def _judge_context(case: CompactionQualityCase, summary: str) -> str:
        return f"Source history:\n{case.source_context()}\n\nCandidate summary:\n{summary}"


def _scenario_class(case: CompactionQualityCase):
    class _CompactionQualityCaseScenario(CompactionQualityScenario):
        slug = case.slug
        description = f"Evaluate {case.kind} compaction for {case.name.lower()}."
        tags = (
            "compaction_quality",
            "llm_judge",
            "model_comparison",
            f"{case.kind}_compaction",
        )

    _CompactionQualityCaseScenario.case = case
    _CompactionQualityCaseScenario.__name__ = "".join(
        part.title() for part in case.slug.split("_")
    ) + "Scenario"
    return _CompactionQualityCaseScenario


for compaction_quality_case in COMPACTION_QUALITY_CASES:
    ScenarioRegistry.register(_scenario_class(compaction_quality_case)())


__all__ = [
    "COMPACTION_QUALITY_SCENARIO_SLUGS",
    "COMPACTION_QUALITY_SUITE_SLUG",
    "CompactionQualityScenario",
]
