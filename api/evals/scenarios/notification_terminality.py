import json
from dataclasses import dataclass

from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.behavior_micro import get_tool_calls_for_run
from api.models import (
    CommsAllowlistEntry,
    CommsChannel,
    EvalRunTask,
    PersistentAgent,
    PersistentAgentStep,
    PersistentAgentSystemStep,
)


NOTIFICATION_TERMINALITY_SUITE_SLUG = "notification_terminality"
NOTIFICATION_TERMINALITY_COMPLETED = "notification_terminality_completed_side_effects"
NOTIFICATION_TERMINALITY_REMAINING = "notification_terminality_remaining_side_effects"
NOTIFICATION_TERMINALITY_SCENARIO_SLUGS = (
    NOTIFICATION_TERMINALITY_COMPLETED,
    NOTIFICATION_TERMINALITY_REMAINING,
)
_WORKFLOW_TOOL = "custom_eval_incident_workflow"
_NOTIFICATION_TOOLS = ("send_email",)
_DEPENDENCY_HOLD_MARKER = "Tool dependency: held outbound send"


@dataclass(frozen=True)
class NotificationTerminalityCase:
    slug: str
    custom_tool_name: str
    custom_result: dict
    notifications_remain: bool


_CASES = (
    NotificationTerminalityCase(
        slug=NOTIFICATION_TERMINALITY_COMPLETED,
        custom_tool_name=_WORKFLOW_TOOL,
        custom_result={
            "status": "ok",
            "result": {
                "incident_id": "INC-472",
                "side_effects": [
                    {
                        "action": "send_email",
                        "status": "sent",
                        "recipient": "engineering@example.test",
                        "message_id": "email-inc-472",
                    },
                ],
                "email_sent": True,
                "remaining_work": 0,
                "next_action": "Do not repeat notifications; work is complete.",
            },
        },
        notifications_remain=False,
    ),
    NotificationTerminalityCase(
        slug=NOTIFICATION_TERMINALITY_REMAINING,
        custom_tool_name=_WORKFLOW_TOOL,
        custom_result={
            "status": "ok",
            "result": {
                "incident_id": "INC-472",
                "side_effects": [
                    {"action": "update_incident_ledger", "status": "complete"},
                ],
                "email_sent": False,
                "remaining_work": 1,
                "next_action": "Send one email notification.",
            },
        },
        notifications_remain=True,
    ),
)


class NotificationTerminalityScenario(EvalScenario, ScenarioExecutionTools):
    version = "1.0"
    description = "Custom-tool side effects must govern later outbound notifications without suppressing required sends."
    tier = "core"
    category = "agent_behavior"
    expected_runtime = "medium"
    cost_class = "medium"
    owner = "agent-platform"
    area = "tool_execution"
    tags = (
        "agent_behavior",
        "custom_tools",
        "notifications",
        "pressure",
        "side_effects",
        "real_harness",
    )
    tasks = [
        ScenarioTask(name="inject_incident", assertion_type="agent_processing"),
        ScenarioTask(name="verify_custom_workflow", assertion_type="tool_call"),
        ScenarioTask(name="verify_notification_integrity", assertion_type="tool_call"),
    ]
    case: NotificationTerminalityCase | None = None

    def run(self, run_id: str, agent_id: str) -> None:
        if self.case is None:
            raise ValueError("Notification terminality eval is missing case metadata.")
        case = self.case
        agent = PersistentAgent.objects.get(id=agent_id)
        agent.charter = (
            "Own the engineering incident ledger. Use the configured incident workflow, preserve exact incident "
            "identity, and notify the requested internal channels without duplicate deliveries."
        )
        agent.planning_state = PersistentAgent.PlanningState.SKIPPED
        agent.save(update_fields=["charter", "planning_state", "updated_at"])
        self._seed_prior_run(agent, pressured=not case.notifications_remain)
        for tool_name in (case.custom_tool_name, *_NOTIFICATION_TOOLS):
            mark_tool_enabled_without_discovery(agent, tool_name)
        CommsAllowlistEntry.objects.get_or_create(
            agent=agent,
            channel=CommsChannel.EMAIL,
            address="engineering@example.test",
            defaults={"is_active": True},
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="inject_incident",
        )
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                (
                    "Handle incident INC-472 with the enabled `custom_eval_incident_workflow`, email "
                    "`engineering@example.test`, then stop. The incident details are already complete in the "
                    "workflow input."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=self._mock_config(case),
                eval_stop_policy=self._stop_policy(case),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_incident",
            observed_summary="Incident request was processed through the real agent loop.",
            artifacts={"message": inbound},
        )

        calls = get_tool_calls_for_run(run_id, after=inbound.timestamp)
        workflow_calls = [call for call in calls if call.tool_name == case.custom_tool_name]
        workflow_ok = len(workflow_calls) == 1 and self._result_ok(workflow_calls[0].result)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if workflow_ok else EvalRunTask.Status.FAILED,
            task_name="verify_custom_workflow",
            observed_summary=(
                "Configured incident workflow ran exactly once."
                if workflow_ok
                else f"Expected one successful {case.custom_tool_name} call; found {len(workflow_calls)}."
            ),
            artifacts={"tool_calls": workflow_calls},
        )

        notification_calls = {
            tool_name: [call for call in calls if call.tool_name == tool_name]
            for tool_name in _NOTIFICATION_TOOLS
        }
        held_steps = list(
            PersistentAgentStep.objects.filter(
                agent_id=agent_id,
                created_at__gte=inbound.timestamp,
                description__startswith=_DEPENDENCY_HOLD_MARKER,
            )
        )
        notification_ok, summary = self._notification_score(
            case,
            workflow_calls,
            notification_calls,
            held_steps,
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if notification_ok else EvalRunTask.Status.FAILED,
            task_name="verify_notification_integrity",
            observed_summary=summary,
            artifacts={
                "notification_calls": notification_calls,
                "dependency_holds": held_steps,
            },
        )

    @staticmethod
    def _seed_prior_run(agent: PersistentAgent, *, pressured: bool) -> None:
        if PersistentAgentSystemStep.objects.filter(
            step__agent=agent,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        ).exists():
            return
        if pressured:
            detail = (
                "The incident ledger preserves a distinct owner, channel, severity, source, current status, "
                "notification state, and next action. Completed notifications must not be reopened or repeated. "
            )
            PersistentAgentStep.objects.bulk_create(
                [
                    PersistentAgentStep(
                        agent=agent,
                        description=(
                            f"Historical incident checkpoint {index:02d}. {detail}{detail}"
                            f"Case key INC-{300 + index}; owner Team {index % 7}; priority {index % 4}; "
                            f"status {'closed' if index % 3 else 'monitoring'}. {detail}{detail}"
                        ),
                    )
                    for index in range(92)
                ]
            )
        step = PersistentAgentStep.objects.create(agent=agent, description="Process events")
        PersistentAgentSystemStep.objects.create(
            step=step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        )

    @staticmethod
    def _mock_config(case: NotificationTerminalityCase) -> dict:
        return {
            case.custom_tool_name: case.custom_result,
            "send_email": {
                "status": "ok",
                "message": "Email sent.",
                "message_id": "email-direct-inc-472",
            },
        }

    @staticmethod
    def _stop_policy(case: NotificationTerminalityCase) -> dict:
        policy = {
            "ignored_tool_names": ["update_plan", "sqlite_batch"],
            "max_relevant_tool_calls": 8,
        }
        if case.notifications_remain:
            policy["stop_when_all_seen"] = [
                {"tool_name": case.custom_tool_name, "after_execution": True},
                {"tool_name": "send_email", "after_execution": True},
            ]
        return policy

    @staticmethod
    def _result_ok(value) -> bool:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return False
        return isinstance(value, dict) and str(value.get("status") or "").lower() in {"ok", "success"}

    @staticmethod
    def _completion_id(call) -> str:
        return str(getattr(getattr(call, "step", None), "completion_id", "") or "")

    @staticmethod
    def _created_at(call):
        return getattr(getattr(call, "step", None), "created_at", None)

    @classmethod
    def _notification_score(
        cls,
        case: NotificationTerminalityCase,
        workflow_calls: list,
        notification_calls: dict[str, list],
        held_steps: list,
    ) -> tuple[bool, str]:
        if held_steps:
            return False, f"Model preplanned {len(held_steps)} outbound send(s) before the custom result was available."
        workflow_at = cls._created_at(workflow_calls[0]) if len(workflow_calls) == 1 else None
        post_workflow_calls = {
            name: [
                call
                for call in calls
                if workflow_at is None
                or cls._created_at(call) is None
                or cls._created_at(call) >= workflow_at
            ]
            for name, calls in notification_calls.items()
        }
        counts = {name: len(calls) for name, calls in post_workflow_calls.items()}
        if not case.notifications_remain:
            passed = all(count == 0 for count in counts.values())
            return (
                passed,
                (
                    "Completed custom-tool notifications were not repeated."
                    if passed
                    else f"Completed notifications were repeated: {counts}."
                ),
            )

        if len(workflow_calls) != 1 or any(counts[name] != 1 for name in _NOTIFICATION_TOOLS):
            return False, f"Expected one workflow call and one required send per channel; found {counts}."
        workflow_completion = cls._completion_id(workflow_calls[0])
        send_completions = {
            cls._completion_id(call)
            for calls in post_workflow_calls.values()
            for call in calls
        }
        separated = bool(workflow_completion) and workflow_completion not in send_completions
        return (
            separated,
            (
                "Required notifications were sent once after the custom-tool result."
                if separated
                else "Required notifications were preplanned in the same completion as the unresolved custom tool."
            ),
        )


def _scenario_class(case: NotificationTerminalityCase):
    class _CaseScenario(NotificationTerminalityScenario):
        slug = case.slug

    _CaseScenario.__name__ = "".join(part.title() for part in case.slug.split("_")) + "Scenario"
    _CaseScenario.__qualname__ = _CaseScenario.__name__
    _CaseScenario.case = case
    return _CaseScenario


for notification_terminality_case in _CASES:
    ScenarioRegistry.register(_scenario_class(notification_terminality_case)())
