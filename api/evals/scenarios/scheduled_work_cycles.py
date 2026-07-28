import json
from dataclasses import dataclass

from django.contrib.auth import get_user_model

from api.agent.tools.sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from api.agent.tools.sqlite_state import agent_sqlite_db
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.models import (
    AgentPeerLink,
    BrowserUseAgent,
    EvalRunTask,
    PersistentAgent,
    PersistentAgentCronTrigger,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
)


SCHEDULED_EMPTY_QUEUE_SILENCE = "scheduled_empty_queue_silence"
SCHEDULED_OWNED_WORK_DISPATCH = "scheduled_owned_work_dispatch"
SCHEDULED_WORK_CYCLE_SUITE_SLUG = "scheduled_work_cycles"
SCHEDULED_WORK_CYCLE_SCENARIO_SLUGS = (
    SCHEDULED_EMPTY_QUEUE_SILENCE,
    SCHEDULED_OWNED_WORK_DISPATCH,
)


@dataclass(frozen=True)
class ScheduledWorkCycleCase:
    slug: str
    description: str
    pending_job: bool


SCHEDULED_WORK_CYCLE_CASES = (
    ScheduledWorkCycleCase(
        slug=SCHEDULED_EMPTY_QUEUE_SILENCE,
        description="An empty scheduled work cycle should consult owned state once and end without polling or chatter.",
        pending_job=False,
    ),
    ScheduledWorkCycleCase(
        slug=SCHEDULED_OWNED_WORK_DISPATCH,
        description="A scheduled work cycle with one ready item should dispatch that exact item without inbox polling.",
        pending_job=True,
    ),
)


class ScheduledWorkCycleScenario(EvalScenario, ScenarioExecutionTools):
    tier = "core"
    category = "scheduled_work_cycles"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("agent_behavior", "scheduling", "sqlite", "real_harness")
    tasks = [
        ScenarioTask(name="trigger_scheduled_cycle", assertion_type="agent_processing"),
        ScenarioTask(name="verify_owned_state_query", assertion_type="tool_call"),
        ScenarioTask(name="verify_cycle_outcome", assertion_type="tool_call"),
    ]

    def __init__(self, case: ScheduledWorkCycleCase):
        self.case = case
        self.slug = case.slug
        self.description = case.description

    @staticmethod
    def _seed_prior_run(agent_id: str) -> None:
        if PersistentAgentSystemStep.objects.filter(
            step__agent_id=agent_id,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        ).exists():
            return
        step = PersistentAgentStep.objects.create(agent_id=agent_id, description="Process events")
        PersistentAgentSystemStep.objects.create(
            step=step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        )

    @staticmethod
    def _seed_queue(agent_id: str, *, pending_job: bool) -> None:
        with agent_sqlite_db(str(agent_id)) as db_path:
            connection = open_guarded_sqlite_connection(db_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE work_queue (
                        job_id TEXT PRIMARY KEY,
                        account_name TEXT NOT NULL,
                        next_action TEXT NOT NULL,
                        status TEXT NOT NULL
                    )
                    """
                )
                if pending_job:
                    connection.execute(
                        """
                        INSERT INTO work_queue (job_id, account_name, next_action, status)
                        VALUES (?, ?, ?, ?)
                        """,
                        ("job-104", "Northstar Labs", "verify renewal owner", "ready"),
                    )
                connection.commit()
            finally:
                clear_guarded_connection(connection)
                connection.close()

    @staticmethod
    def _create_operations_peer(agent: PersistentAgent, run_id: str) -> PersistentAgent:
        if not agent.organization_id:
            raise ValueError("Scheduled work-cycle eval requires an organization-owned eval agent.")
        username = f"scheduled-ops-{run_id}@eval.local"
        user = get_user_model().objects.create_user(username=username, email=username)
        browser_agent = BrowserUseAgent.objects.create(
            user=user,
            name=f"Scheduled Ops Eval {str(run_id)[:8]}",
        )
        peer = PersistentAgent.objects.create(
            user=user,
            organization=agent.organization,
            name=f"Operations Agent {str(run_id)[:8]}",
            charter="Own operational follow-through handed off by Routing Agent.",
            browser_use_agent=browser_agent,
            planning_state=PersistentAgent.PlanningState.SKIPPED,
            is_active=False,
        )
        AgentPeerLink.objects.create(agent_a=agent, agent_b=peer, created_by=agent.user)
        return peer

    def _prepare_agent(self, agent_id: str, run_id: str) -> PersistentAgent:
        PersistentAgent.objects.filter(id=agent_id).update(
            name=f"Routing Agent {str(agent_id)[:8]}",
            charter=(
                "On each recurring wake, review outstanding routing work. Dispatch each ready operations handoff "
                "with its exact job ID, account, and next action. If no work is ready, wait for the next trigger."
            ),
            planning_state=PersistentAgent.PlanningState.SKIPPED,
            schedule="*/15 * * * *",
        )
        self._seed_prior_run(agent_id)
        self._seed_queue(agent_id, pending_job=self.case.pending_job)
        agent = PersistentAgent.objects.select_related("user", "organization").get(id=agent_id)
        self._create_operations_peer(agent, run_id)
        return agent

    @staticmethod
    def _tool_calls(run_id: str, after) -> list[PersistentAgentToolCall]:
        return list(
            PersistentAgentToolCall.objects.filter(
                step__eval_run_id=run_id,
                step__created_at__gte=after,
            )
            .select_related("step")
            .order_by("step__created_at", "step__id")
        )

    @staticmethod
    def _sqlite_text(call: PersistentAgentToolCall) -> str:
        return json.dumps(call.tool_params or {}, sort_keys=True)

    @staticmethod
    def _call_succeeded(call: PersistentAgentToolCall) -> bool:
        try:
            result = json.loads(call.result or "{}")
        except (TypeError, ValueError):
            return False
        return call.status == PersistentAgentToolCall.Status.COMPLETE and result.get("status") in {"ok", "success"}

    def run(self, run_id: str, agent_id: str) -> None:
        self._prepare_agent(agent_id, run_id)
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="trigger_scheduled_cycle")
        cron_step = PersistentAgentStep.objects.create(
            agent_id=agent_id,
            description="Cron trigger: */15 * * * *",
        )
        PersistentAgentCronTrigger.objects.create(step=cron_step, cron_expression="*/15 * * * *")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            self.trigger_processing(
                agent_id,
                eval_run_id=run_id,
                mock_config={},
                eval_stop_policy={
                    "stop_on_tool_names_after_finish": ["sleep_until_next_trigger"],
                    "ignored_tool_names": ["update_plan"],
                    "max_relevant_tool_calls": 6,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="trigger_scheduled_cycle",
            observed_summary="Scheduled wake completed through the real agent harness.",
            artifacts={"step": cron_step},
        )

        calls = self._tool_calls(run_id, cron_step.created_at)
        sqlite_calls = [call for call in calls if call.tool_name == "sqlite_batch"]
        sqlite_text = "\n".join(self._sqlite_text(call) for call in sqlite_calls).casefold()
        queried_owned_state = "work_queue" in sqlite_text
        polled_message_history = "__messages" in sqlite_text
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if queried_owned_state and not polled_message_history else EvalRunTask.Status.FAILED,
            task_name="verify_owned_state_query",
            observed_summary=(
                "Consulted the owned work queue without treating message history as a freshness feed."
                if queried_owned_state and not polled_message_history
                else (
                    f"owned_queue={queried_owned_state}; polled_message_history={polled_message_history}; "
                    f"sqlite_calls={len(sqlite_calls)}."
                )
            ),
            artifacts={"step": sqlite_calls[0].step} if sqlite_calls else {},
        )

        send_calls = [call for call in calls if call.tool_name == "send_agent_message"]
        outbound = list(
            PersistentAgentMessage.objects.filter(
                owner_agent_id=agent_id,
                is_outbound=True,
                timestamp__gte=cron_step.created_at,
            ).order_by("timestamp", "id")
        )
        if not self.case.pending_job:
            passed = not send_calls and not outbound
            summary = (
                "Empty cycle ended quietly."
                if passed
                else f"Empty cycle produced {len(send_calls)} peer call(s) and {len(outbound)} outbound message(s)."
            )
            artifacts = {"step": send_calls[0].step} if send_calls else {}
        else:
            params_text = "\n".join(json.dumps(call.tool_params or {}, sort_keys=True) for call in send_calls)
            exact_payload = all(value in params_text for value in ("job-104", "Northstar Labs", "verify renewal owner"))
            passed = (
                len(send_calls) == 1
                and self._call_succeeded(send_calls[0])
                and exact_payload
            )
            summary = (
                "Dispatched the exact ready item once."
                if passed
                else f"send_calls={len(send_calls)}; exact_payload={exact_payload}."
            )
            artifacts = {"step": send_calls[0].step} if send_calls else {}
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if passed else EvalRunTask.Status.FAILED,
            task_name="verify_cycle_outcome",
            observed_summary=summary,
            artifacts=artifacts,
        )


for case in SCHEDULED_WORK_CYCLE_CASES:
    ScenarioRegistry.register(ScheduledWorkCycleScenario(case))
