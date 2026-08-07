import json

from api.evals.base import ScenarioTask
from api.evals.registry import register_scenario
from api.evals.scenarios.behavior_micro import (
    CharterMemoryScenario,
    get_tool_calls_for_run,
)
from api.models import EvalRunTask, PersistentAgent


LASTING_FEEDBACK_META_INSTRUCTION_KEEPS_WORK = "lasting_feedback_meta_instruction_keeps_work"
LASTING_FEEDBACK_ALREADY_SATISFIED_KEEPS_WORK = "lasting_feedback_already_satisfied_keeps_work"
LASTING_FEEDBACK_SCENARIO_SLUGS = (
    LASTING_FEEDBACK_META_INSTRUCTION_KEEPS_WORK,
    LASTING_FEEDBACK_ALREADY_SATISFIED_KEEPS_WORK,
)
LASTING_FEEDBACK_SUITE_SLUG = "lasting_feedback"

WORK_TOOL_NAME = "eval_prepare_next_batch"
WORK_TOOL_RESULT = {
    "status": "ok",
    "prepared_count": 12,
    "remaining_work": 0,
    "batch_id": "eval-batch-12",
}


class LastingFeedbackScenario(CharterMemoryScenario):
    category = "memory"
    tags = ("agent_behavior", "charter", "memory", "forward_progress")
    prior_outbound_body = "I will keep waiting for reviewer approval before preparing the batch."
    expect_charter_mutation = True

    def _seed_charter_agent(self, agent_id):
        super()._seed_charter_agent(agent_id)
        self._enable_eval_synthetic_tools(agent_id, [WORK_TOOL_NAME])

    def _eval_stop_policy(self):
        return {
            "ignore_sqlite_agent_config_mutations": False,
            "stop_when_all_seen": [
                {"tool_name": WORK_TOOL_NAME, "after_execution": True},
            ],
            "stop_on_unexpected_relevant_tool": True,
            "allowed_tool_names": [
                "sqlite_batch",
                WORK_TOOL_NAME,
                "send_chat_message",
                "sleep_until_next_trigger",
            ],
            "max_relevant_tool_calls": 6,
        }

    @staticmethod
    def _work_call_succeeded(call):
        try:
            result = json.loads(call.result or "{}")
        except json.JSONDecodeError:
            return False
        return call.status == "complete" and result.get("status") == "ok"

    def _record_work_result(self, run_id, inbound):
        calls = get_tool_calls_for_run(
            run_id,
            after=inbound.timestamp,
            tool_names=[WORK_TOOL_NAME],
        )
        passed = len(calls) == 1 and self._work_call_succeeded(calls[0])
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if passed else EvalRunTask.Status.FAILED,
            task_name="verify_active_work_completed",
            observed_summary=(
                "The correction did not displace the requested batch preparation."
                if passed
                else f"Expected one successful batch preparation; observed {len(calls)} call(s)."
            ),
            artifacts={"step": calls[0].step} if calls else {},
        )
        return passed

    def run(self, run_id, agent_id):
        self._seed_charter_agent(agent_id)
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=180):
            inbound = self.inject_message(
                agent_id,
                self.prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config={WORK_TOOL_NAME: WORK_TOOL_RESULT},
                eval_stop_policy=self._eval_stop_policy(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Feedback and active work were submitted in one turn.",
            artifacts={"message": inbound},
        )

        mutation_calls = self._charter_mutation_calls(run_id, inbound)
        agent = PersistentAgent.objects.get(id=agent_id)
        passed, detail = self._charter_check(agent, mutation_calls)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if passed else EvalRunTask.Status.FAILED,
            task_name=self.verification_task_name,
            observed_summary=self.success_summary if passed else f"{self.failure_summary}: {detail}",
            artifacts={"step": mutation_calls[0].step} if mutation_calls else {},
        )
        self._record_work_result(run_id, inbound)


@register_scenario
class LastingFeedbackMetaInstructionKeepsWorkScenario(LastingFeedbackScenario):
    slug = LASTING_FEEDBACK_META_INSTRUCTION_KEEPS_WORK
    description = (
        "A request to remember an operating correction should save the operative rule, not the update mechanism, "
        "and should still complete the work requested in the same turn."
    )
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_operating_rule_saved", assertion_type="llm_judge"),
        ScenarioTask(name="verify_active_work_completed", assertion_type="manual"),
    ]
    existing_charter = (
        "Prepare evidence-backed outreach batches. "
        "Treat reviewer approval as a mandatory gate before preparing any batch. "
        "Keep source provenance attached to every draft."
    )
    prompt = (
        "Going forward, reviewer feedback is coaching, not permission. Prepare assigned batches and send them for "
        "asynchronous review. If no changes arrive, continue after your quality checks. The next assigned follow-up "
        "batch is ready; prepare it now. Update your charter once without changing unrelated guidance."
    )
    verification_task_name = "verify_operating_rule_saved"
    success_summary = "The charter captured the operating correction without storing charter-editing instructions."
    failure_summary = "Expected one substantive charter update without a meta-charter rule"

    def _charter_check(self, agent, mutation_calls):
        choice, reasoning = self.llm_judge(
            question=(
                "Does the updated charter replace the mandatory reviewer-permission gate with asynchronous coaching "
                "and safe forward progress, while preserving provenance? Does it avoid adding a rule whose subject is "
                "how to update, patch, rewrite, or preserve the charter itself?"
            ),
            context=(
                f"Original charter:\n{self.existing_charter}\n\n"
                f"User feedback and task:\n{self.prompt}\n\n"
                f"Updated charter:\n{agent.charter or ''}"
            ),
            options=["Correct operational update", "Incorrect or meta update"],
        )
        passed = len(mutation_calls) == 1 and choice == "Correct operational update"
        return passed, f"mutation_count={len(mutation_calls)}, judge={choice}: {reasoning}"


@register_scenario
class LastingFeedbackAlreadySatisfiedKeepsWorkScenario(LastingFeedbackScenario):
    slug = LASTING_FEEDBACK_ALREADY_SATISFIED_KEEPS_WORK
    description = (
        "Equivalent lasting feedback should be consumed without a no-op charter edit and without displacing active work."
    )
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_no_redundant_charter_edit", assertion_type="manual"),
        ScenarioTask(name="verify_active_work_completed", assertion_type="manual"),
    ]
    existing_charter = (
        "Prepare evidence-backed outreach batches. "
        "Reviewer feedback is asynchronous coaching, not central permission; after normal quality checks, "
        "continue valid assigned work without waiting for signoff. "
        "Keep source provenance attached to every draft."
    )
    prompt = (
        "Going forward, reviewer feedback is coaching, not central permission. "
        "That same rule is already in your charter. "
        "Update your charter only if needed, then prepare the next follow-up batch now."
    )
    verification_task_name = "verify_no_redundant_charter_edit"
    success_summary = "Equivalent feedback caused no charter mutation and the requested work continued."
    failure_summary = "Expected zero charter mutations for an already-satisfied rule"
    expect_charter_mutation = False

    def _charter_check(self, agent, mutation_calls):
        passed = not mutation_calls and agent.charter == self.existing_charter
        return passed, f"mutation_count={len(mutation_calls)}, charter_changed={agent.charter != self.existing_charter}"
