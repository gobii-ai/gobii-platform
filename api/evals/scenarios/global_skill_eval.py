import json

from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.global_skill_evals import GLOBAL_SKILL_EVAL_SCENARIO_SLUG
from api.evals.registry import register_scenario
from api.models import (
    EvalRunTask,
    GlobalAgentSkill,
    PersistentAgentMessage,
    PersistentAgentSkill,
    PersistentAgentToolCall,
)


def _preview_json(value, limit: int = 800) -> str:
    text = json.dumps(value, default=str, ensure_ascii=True)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


@register_scenario
class GlobalSkillEvalScenario(EvalScenario, ScenarioExecutionTools):
    slug = GLOBAL_SKILL_EVAL_SCENARIO_SLUG
    description = "Evaluates whether an agent enables and correctly uses a selected global skill."
    tasks = [
        ScenarioTask(name="inject_skill_task", assertion_type="manual"),
        ScenarioTask(name="verify_skill_enabled", assertion_type="manual"),
        ScenarioTask(name="verify_skill_tool_usage", assertion_type="manual"),
        ScenarioTask(name="judge_skill_execution", assertion_type="llm_judge"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        run = self.get_run(run_id)
        suite_run = run.suite_run
        launch_config = dict(suite_run.launch_config or {}) if suite_run else {}

        skill_id = str(launch_config.get("global_skill_id") or "").strip()
        skill_name = str(launch_config.get("global_skill_name") or "").strip()
        task_prompt = str(launch_config.get("task_prompt") or "").strip()
        if not skill_name or not task_prompt:
            raise ValueError("Global skill eval launch_config is missing skill metadata or task_prompt.")

        effective_tool_ids = [str(item).strip() for item in launch_config.get("effective_tool_ids") or [] if str(item).strip()]
        required_secret_status = list(launch_config.get("required_secret_status") or [])
        skill = None
        if skill_id:
            skill = GlobalAgentSkill.objects.filter(id=skill_id).first()
        if skill is None:
            skill = GlobalAgentSkill.objects.filter(name=skill_name).first()
        if skill is not None and not effective_tool_ids:
            effective_tool_ids = list(skill.get_effective_tool_ids())

        instructions = (
            "Use the exact global skill "
            f"'{skill_name}' for this task. First discover and enable that exact skill if it is not already enabled, "
            "then use one of the tools provided by that skill while completing the task. "
            f"Task: {task_prompt}"
        )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="inject_skill_task",
            expected_summary=f"Agent receives a task that requires enabling and using global skill '{skill_name}'.",
        )
        with self.wait_for_agent_idle(agent_id, timeout=180):
            inbound = self.inject_message(
                agent_id,
                instructions,
                trigger_processing=True,
                eval_run_id=run_id,
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_skill_task",
            observed_summary=f"Task injected for global skill '{skill_name}'.",
            artifacts={"message": inbound},
        )

        if skill_id:
            enabled_skill = PersistentAgentSkill.objects.filter(
                agent_id=agent_id,
                global_skill_id=skill_id,
                created_at__gte=inbound.timestamp,
            ).order_by("created_at").first()
        else:
            enabled_skill = PersistentAgentSkill.objects.filter(
                agent_id=agent_id,
                name=skill_name,
                created_at__gte=inbound.timestamp,
            ).order_by("created_at").first()

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_skill_enabled",
            expected_summary=f"The exact global skill '{skill_name}' is enabled during the eval run.",
        )
        if enabled_skill is not None:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_skill_enabled",
                observed_summary=f"Agent enabled global skill '{skill_name}'.",
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_skill_enabled",
                observed_summary=f"Agent did not enable the exact global skill '{skill_name}'.",
            )

        relevant_tool_calls = PersistentAgentToolCall.objects.filter(
            step__eval_run_id=run_id,
            step__created_at__gte=inbound.timestamp,
            tool_name__in=effective_tool_ids,
        ).select_related("step").order_by("step__created_at")

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_skill_tool_usage",
            expected_summary="Agent uses at least one effective tool from the selected skill after enablement.",
        )
        first_relevant_call = relevant_tool_calls.first()
        if first_relevant_call is not None:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_skill_tool_usage",
                observed_summary=f"Agent used skill tool '{first_relevant_call.tool_name}'.",
                artifacts={"step": first_relevant_call.step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_skill_tool_usage",
                observed_summary="Agent did not call any effective tool from the selected skill.",
            )

        final_response = PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            is_outbound=True,
            timestamp__gt=inbound.timestamp,
        ).order_by("-timestamp").first()
        post_prompt_calls = list(
            PersistentAgentToolCall.objects.filter(
                step__eval_run_id=run_id,
                step__created_at__gte=inbound.timestamp,
            )
            .select_related("step")
            .order_by("step__created_at")[:20]
        )
        relevant_call_summaries = [
            {
                "tool_name": call.tool_name,
                "tool_params": call.tool_params,
                "status": call.status,
            }
            for call in post_prompt_calls
        ]

        required_secret_labels = [secret.get("label") or secret.get("name") or "" for secret in required_secret_status]
        judge_context = {
            "task_prompt": task_prompt,
            "skill_name": skill_name,
            "skill_description": skill.description if skill else "",
            "skill_instructions": skill.instructions if skill else "",
            "effective_tool_ids": effective_tool_ids,
            "required_secrets": required_secret_labels,
            "enabled_skill_detected": enabled_skill is not None,
            "relevant_tool_call_detected": first_relevant_call is not None,
            "tool_calls_after_prompt": relevant_call_summaries,
            "final_response": final_response.body if final_response else "",
        }
        judge_question = (
            "Did the agent correctly execute the selected global skill for this task? "
            "Pass only if the transcript shows the exact named global skill was enabled, "
            "the agent used one of that skill's effective tools, and the final response completed the task using that skill. "
            "Fail if the task was solved without enabling or using the selected skill."
        )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="judge_skill_execution",
            expected_summary="Judge confirms the agent used the selected skill correctly and completed the task.",
        )
        choice, reasoning = self.llm_judge(
            question=judge_question,
            context=_preview_json(judge_context, limit=12000),
            options=("Pass", "Fail"),
        )
        judge_passed = choice == "Pass"
        hard_requirements_met = enabled_skill is not None and first_relevant_call is not None

        if hard_requirements_met and judge_passed:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="judge_skill_execution",
                observed_summary=f"Judge passed skill execution. Reasoning: {reasoning}",
                artifacts={"message": final_response} if final_response else {},
            )
            return

        failure_reasons: list[str] = []
        if enabled_skill is None:
            failure_reasons.append("skill was not enabled")
        if first_relevant_call is None:
            failure_reasons.append("no effective skill tool was used")
        if not judge_passed:
            failure_reasons.append(f"judge result={choice}: {reasoning}")
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="judge_skill_execution",
            observed_summary="; ".join(failure_reasons) or "Skill execution did not meet pass criteria.",
            artifacts={"message": final_response} if final_response else {},
        )
