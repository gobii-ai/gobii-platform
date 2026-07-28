import json
from dataclasses import dataclass
from typing import Any

from django.contrib.auth import get_user_model

from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.models import (
    AgentPeerLink,
    BrowserUseAgent,
    EvalRunTask,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
)


STRUCTURED_PEER_SINGLE_RECORD = "structured_peer_single_record"
STRUCTURED_PEER_RECORD_BATCH = "structured_peer_record_batch"
STRUCTURED_PEER_PLAIN_QUESTION = "structured_peer_plain_question"
STRUCTURED_PEER_HANDOFF_SUITE_SLUG = "structured_peer_handoffs"
STRUCTURED_PEER_HANDOFF_SCENARIO_SLUGS = (
    STRUCTURED_PEER_SINGLE_RECORD,
    STRUCTURED_PEER_RECORD_BATCH,
    STRUCTURED_PEER_PLAIN_QUESTION,
)

HANDOFF_CHARTER = (
    "Coordinate finalized operational handoffs. Route completed records and direct coordination questions to the "
    "linked Ledger Agent without changing their meaning or taking over the ledger's work."
)


@dataclass(frozen=True)
class StructuredPeerHandoffCase:
    slug: str
    description: str
    prompt: str
    expected_record: dict[str, Any] | None = None
    expected_records: tuple[dict[str, Any], ...] = ()


STRUCTURED_PEER_HANDOFF_CASES = (
    StructuredPeerHandoffCase(
        slug=STRUCTURED_PEER_SINGLE_RECORD,
        description="A finalized record is handed to a peer with every named field preserved structurally.",
        prompt=(
            "Please pass this finalized intake to Ledger Agent exactly as supplied: "
            "record_id R-2048; status ready_for_review; owner_queue north; attempt 3; "
            "observed_at 2026-07-28T15:42:00Z."
        ),
        expected_record={
            "record_id": "R-2048",
            "status": "ready_for_review",
            "owner_queue": "north",
            "attempt": 3,
            "observed_at": "2026-07-28T15:42:00Z",
        },
    ),
    StructuredPeerHandoffCase(
        slug=STRUCTURED_PEER_RECORD_BATCH,
        description="A multi-record handoff preserves record boundaries and exact field values.",
        prompt=(
            "Transfer this completed two-row inventory intake to Ledger Agent in one handoff. "
            "The fields are item_id, warehouse, state, and quantity. "
            "Row one: item_id IT-81, warehouse east-2, state reserved, quantity 4. "
            "Row two: item_id IT-93, warehouse west-1, state available, quantity 11."
        ),
        expected_records=(
            {
                "item_id": "IT-81",
                "warehouse": "east-2",
                "state": "reserved",
                "quantity": 4,
            },
            {
                "item_id": "IT-93",
                "warehouse": "west-1",
                "state": "available",
                "quantity": 11,
            },
        ),
    ),
    StructuredPeerHandoffCase(
        slug=STRUCTURED_PEER_PLAIN_QUESTION,
        description="A plain coordination question remains prose instead of manufacturing a data payload.",
        prompt=(
            "Ask Ledger Agent whether today's reconciliation window is still 4 PM, "
            "and have them reply when they know."
        ),
    ),
)


class StructuredPeerHandoffScenario(EvalScenario, ScenarioExecutionTools):
    tier = "core"
    category = "structured_peer_handoffs"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("agent_behavior", "agent_teams", "structured_handoffs", "real_harness")

    def __init__(self, case: StructuredPeerHandoffCase):
        self.case = case
        self.slug = case.slug
        self.description = case.description
        self.tasks = [
            ScenarioTask(name="inject_request", assertion_type="agent_processing"),
            ScenarioTask(name="verify_peer_handoff", assertion_type="tool_call"),
        ]

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

    def _prepare_agents(self, agent_id: str, run_id: str) -> tuple[PersistentAgent, PersistentAgent]:
        PersistentAgent.objects.filter(id=agent_id).update(
            name=f"Handoff Coordinator {str(agent_id)[:8]}",
            charter=HANDOFF_CHARTER,
            planning_state=PersistentAgent.PlanningState.SKIPPED,
            schedule="0 9 * * *",
        )
        self._seed_prior_run(agent_id)
        agent = PersistentAgent.objects.select_related("user", "organization").get(id=agent_id)

        peer_username = f"structured-ledger-{run_id}@eval.local"
        peer_user = get_user_model().objects.create_user(
            username=peer_username,
            email=peer_username,
        )
        peer = PersistentAgent.objects.create(
            user=peer_user,
            organization=agent.organization,
            name=f"Ledger Agent {str(run_id)[:8]}",
            charter="Maintain finalized operational records and answer reconciliation questions.",
            browser_use_agent=BrowserUseAgent.objects.create(
                user=peer_user,
                name=f"Structured Ledger Eval {str(run_id)[:8]}",
            ),
            planning_state=PersistentAgent.PlanningState.SKIPPED,
            is_active=False,
        )
        AgentPeerLink.objects.create(agent_a=agent, agent_b=peer, created_by=agent.user)
        return agent, peer

    @staticmethod
    def _tool_calls(run_id: str, after) -> list[PersistentAgentToolCall]:
        return list(
            PersistentAgentToolCall.objects.filter(
                step__eval_run_id=run_id,
                step__created_at__gte=after,
                tool_name="send_agent_message",
            )
            .select_related("step")
            .order_by("step__created_at", "step__id")
        )

    @staticmethod
    def _call_succeeded(call: PersistentAgentToolCall) -> bool:
        try:
            result = json.loads(call.result or "{}")
        except (TypeError, ValueError):
            return False
        return call.status == "complete" and str(result.get("status") or "").lower() == "ok"

    @staticmethod
    def _contains_record(value: Any, expected: dict[str, Any]) -> bool:
        if isinstance(value, dict):
            if value == expected:
                return True
            return any(
                StructuredPeerHandoffScenario._contains_record(child, expected)
                for child in value.values()
            )
        if isinstance(value, list):
            return any(
                StructuredPeerHandoffScenario._contains_record(child, expected)
                for child in value
            )
        return False

    @staticmethod
    def _contains_record_batch(value: Any, expected_records: tuple[dict[str, Any], ...]) -> bool:
        if isinstance(value, list) and value == list(expected_records):
            return True
        if isinstance(value, dict):
            return any(
                StructuredPeerHandoffScenario._contains_record_batch(child, expected_records)
                for child in value.values()
            )
        if isinstance(value, list):
            return any(
                StructuredPeerHandoffScenario._contains_record_batch(child, expected_records)
                for child in value
            )
        return False

    def run(self, run_id: str, agent_id: str) -> None:
        agent, peer = self._prepare_agents(agent_id, run_id)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="inject_request",
        )
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                self.case.prompt,
                eval_run_id=run_id,
                eval_stop_policy={
                    "ignored_tool_names": ["sleep_until_next_trigger", "update_plan", "sqlite_batch"],
                    "stop_on_tool_names_after_finish": ["send_agent_message"],
                    "max_relevant_tool_calls": 2,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_request",
            observed_summary="Natural peer-handoff request was processed through the real agent harness.",
            artifacts={"message": inbound},
        )

        calls = self._tool_calls(run_id, inbound.timestamp)
        outbound = list(
            PersistentAgentMessage.objects.filter(
                owner_agent=agent,
                peer_agent=peer,
                is_outbound=True,
                timestamp__gt=inbound.timestamp,
            ).order_by("timestamp", "id")
        )
        received = list(
            PersistentAgentMessage.objects.filter(
                owner_agent=peer,
                peer_agent=agent,
                is_outbound=False,
                timestamp__gt=inbound.timestamp,
            ).order_by("timestamp", "id")
        )

        passed = len(calls) == len(outbound) == len(received) == 1 and self._call_succeeded(calls[0])
        observed = "Expected exactly one successful, persisted peer handoff."
        artifacts: dict[str, Any] = {}
        if calls:
            artifacts["step"] = calls[0].step
        if outbound:
            artifacts["message"] = outbound[0]

        if passed:
            params = calls[0].tool_params or {}
            tool_payload = params.get("structured_payload")
            outbound_payload = (outbound[0].raw_payload or {}).get("structured_payload")
            received_payload = (received[0].raw_payload or {}).get("structured_payload")
            payloads_match = tool_payload == outbound_payload == received_payload

            if self.case.expected_record is not None:
                passed = (
                    payloads_match
                    and self._contains_record(tool_payload, self.case.expected_record)
                )
                observed = (
                    "Single record was sent structurally and persisted unchanged for both agents."
                    if passed
                    else "The single-record handoff omitted, changed, or prose-encoded required fields."
                )
            elif self.case.expected_records:
                passed = (
                    payloads_match
                    and self._contains_record_batch(tool_payload, self.case.expected_records)
                )
                observed = (
                    "Record batch was sent structurally with boundaries and values preserved."
                    if passed
                    else "The record batch lost its structure, boundaries, or exact field values."
                )
            else:
                passed = (
                    tool_payload is None
                    and outbound_payload is None
                    and received_payload is None
                    and bool(str(params.get("message") or "").strip())
                )
                observed = (
                    "Plain coordination question remained a prose peer message."
                    if passed
                    else "Plain coordination question unnecessarily created structured data or lost its prose."
                )
            artifacts["tool_params"] = params

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if passed else EvalRunTask.Status.FAILED,
            task_name="verify_peer_handoff",
            observed_summary=observed,
            artifacts=artifacts,
        )


for case in STRUCTURED_PEER_HANDOFF_CASES:
    ScenarioRegistry.register(StructuredPeerHandoffScenario(case))
