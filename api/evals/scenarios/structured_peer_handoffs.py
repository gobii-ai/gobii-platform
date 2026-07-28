import json
from dataclasses import dataclass
from typing import Any

from django.contrib.auth import get_user_model

from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.models import (
    AgentPeerLink,
    BrowserUseAgent,
    CommsChannel,
    EvalRunTask,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
)


STRUCTURED_PEER_SINGLE_RECORD = "structured_peer_single_record"
STRUCTURED_PEER_RECORD_BATCH = "structured_peer_record_batch"
STRUCTURED_PEER_PLAIN_QUESTION = "structured_peer_plain_question"
STRUCTURED_PEER_FILE_HANDOFF = "structured_peer_file_handoff"
STRUCTURED_PEER_NEGATIVE_DECISION = "structured_peer_negative_decision"
STRUCTURED_PEER_MIXED_DECISIONS = "structured_peer_mixed_decisions"
STRUCTURED_PEER_HANDOFF_SUITE_SLUG = "structured_peer_handoffs"
STRUCTURED_PEER_HANDOFF_SCENARIO_SLUGS = (
    STRUCTURED_PEER_SINGLE_RECORD,
    STRUCTURED_PEER_RECORD_BATCH,
    STRUCTURED_PEER_PLAIN_QUESTION,
    STRUCTURED_PEER_FILE_HANDOFF,
    STRUCTURED_PEER_NEGATIVE_DECISION,
    STRUCTURED_PEER_MIXED_DECISIONS,
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
    expects_attachment: bool = False


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
    StructuredPeerHandoffCase(
        slug=STRUCTURED_PEER_FILE_HANDOFF,
        description="A generated file reaches the explicitly named peer on the first send attempt.",
        prompt=(
            "Prepare a plain-text handoff note for Ledger Agent with release Northstar, 18 records, "
            "status ready_for_review, and no blockers. Save it as /exports/northstar-handoff.txt, "
            "then deliver that file to Ledger Agent."
        ),
        expects_attachment=True,
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

    @staticmethod
    def _create_linked_peer(
        agent: PersistentAgent,
        run_id: str,
        *,
        role: str,
        charter: str,
    ) -> PersistentAgent:
        slug = role.lower().replace(" ", "-")
        peer_username = f"structured-{slug}-{run_id}@eval.local"
        peer_user = get_user_model().objects.create_user(
            username=peer_username,
            email=peer_username,
        )
        peer = PersistentAgent.objects.create(
            user=peer_user,
            organization=agent.organization,
            name=f"{role} {str(run_id)[:8]}",
            charter=charter,
            browser_use_agent=BrowserUseAgent.objects.create(
                user=peer_user,
                name=f"Structured {role} Eval {str(run_id)[:8]}",
            ),
            planning_state=PersistentAgent.PlanningState.SKIPPED,
            is_active=False,
        )
        AgentPeerLink.objects.create(agent_a=agent, agent_b=peer, created_by=agent.user)
        return peer

    def _prepare_agents(self, agent_id: str, run_id: str) -> tuple[PersistentAgent, PersistentAgent]:
        PersistentAgent.objects.filter(id=agent_id).update(
            name=f"Handoff Coordinator {str(agent_id)[:8]}",
            charter=HANDOFF_CHARTER,
            planning_state=PersistentAgent.PlanningState.SKIPPED,
            schedule="0 9 * * *",
        )
        self._seed_prior_run(agent_id)
        agent = PersistentAgent.objects.select_related("user", "organization").get(id=agent_id)
        peer = self._create_linked_peer(
            agent,
            run_id,
            role="Ledger Agent",
            charter="Maintain finalized operational records and answer reconciliation questions.",
        )
        if self.case.expects_attachment:
            self._create_linked_peer(
                agent,
                run_id,
                role="Archive Agent",
                charter="Maintain long-term archival records when explicitly assigned.",
            )
            mark_tool_enabled_without_discovery(agent, "create_file")
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
                    "max_relevant_tool_calls": 4 if self.case.expects_attachment else 2,
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

            if self.case.expects_attachment:
                attachment_paths = params.get("attachments") or []
                outbound_attachments = list(outbound[0].attachments.all())
                received_attachments = list(received[0].attachments.all())
                passed = (
                    tool_payload is None
                    and len(attachment_paths) == 1
                    and len(outbound_attachments) == 1
                    and len(received_attachments) == 1
                    and outbound_attachments[0].filespace_node.path == "/exports/northstar-handoff.txt"
                    and received_attachments[0].filename == "northstar-handoff.txt"
                )
                observed = (
                    "The generated file reached the named peer on the first send attempt."
                    if passed
                    else "The file send failed, targeted another peer, or lost the attachment."
                )
            elif self.case.expected_record is not None:
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


@dataclass(frozen=True)
class StructuredDecisionRoutingCase:
    slug: str
    description: str
    records: tuple[dict[str, Any], ...]
    expected_forward: dict[str, Any] | None = None


STRUCTURED_DECISION_ROUTING_CASES = (
    StructuredDecisionRoutingCase(
        slug=STRUCTURED_PEER_NEGATIVE_DECISION,
        description="A negative peer decision must not be promoted from an adjacent no-match field.",
        records=(
            {
                "record_id": "prospect-17",
                "decision": "unresolved",
                "crm_match": "no_match",
                "outbound_eligible": False,
                "reason": "prior outreach still unknown",
            },
            {
                "record_id": "prospect-29",
                "decision": "blocked",
                "crm_match": "prior_contact",
                "outbound_eligible": False,
                "reason": "existing relationship",
            },
        ),
    ),
    StructuredDecisionRoutingCase(
        slug=STRUCTURED_PEER_MIXED_DECISIONS,
        description="A mixed decision batch should forward only the exact eligible record.",
        records=(
            {
                "record_id": "prospect-41",
                "decision": "cleared",
                "crm_match": "no_match",
                "outbound_eligible": True,
                "owner": "north",
            },
            {
                "record_id": "prospect-52",
                "decision": "unresolved",
                "crm_match": "no_match",
                "outbound_eligible": False,
                "owner": None,
            },
        ),
        expected_forward={
            "record_id": "prospect-41",
            "decision": "cleared",
            "crm_match": "no_match",
            "outbound_eligible": True,
            "owner": "north",
        },
    ),
)


class StructuredDecisionRoutingScenario(EvalScenario, ScenarioExecutionTools):
    tier = "core"
    category = "structured_peer_handoffs"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = (
        "agent_behavior",
        "agent_teams",
        "structured_handoffs",
        "decision_integrity",
        "real_harness",
    )

    def __init__(self, case: StructuredDecisionRoutingCase):
        self.case = case
        self.slug = case.slug
        self.description = case.description
        self.tasks = [
            ScenarioTask(name="inject_decisions", assertion_type="agent_processing"),
            ScenarioTask(name="verify_exact_routing", assertion_type="tool_call"),
        ]

    @staticmethod
    def _create_peer(
        agent: PersistentAgent,
        run_id: str,
        *,
        role: str,
        charter: str,
    ) -> tuple[PersistentAgent, AgentPeerLink]:
        username = f"{role}-{run_id}@eval.local"
        user = get_user_model().objects.create_user(username=username, email=username)
        peer = PersistentAgent.objects.create(
            user=user,
            organization=agent.organization,
            name=f"{role.title()} Agent {str(run_id)[:8]}",
            charter=charter,
            browser_use_agent=BrowserUseAgent.objects.create(
                user=user,
                name=f"{role.title()} Eval {str(run_id)[:8]}",
            ),
            planning_state=PersistentAgent.PlanningState.SKIPPED,
            is_active=False,
        )
        return peer, AgentPeerLink.objects.create(
            agent_a=agent,
            agent_b=peer,
            created_by=agent.user,
        )

    def run(self, run_id: str, agent_id: str) -> None:
        PersistentAgent.objects.filter(id=agent_id).update(
            name=f"Routing Coordinator {str(agent_id)[:8]}",
            charter=(
                "Coordinate finalized prospect handoffs. Clearance Agent owns eligibility decisions. "
                "Forward only records it marks outbound_eligible=true to Sales Agent, preserving the record exactly. "
                "A CRM no-match is evidence, not clearance."
            ),
            planning_state=PersistentAgent.PlanningState.SKIPPED,
            schedule="0 9 * * *",
        )
        StructuredPeerHandoffScenario._seed_prior_run(agent_id)
        agent = PersistentAgent.objects.select_related("user", "organization").get(id=agent_id)
        clearance, clearance_link = self._create_peer(
            agent,
            run_id,
            role="clearance",
            charter="Make final eligibility decisions and send exact decision records.",
        )
        sales, _ = self._create_peer(
            agent,
            run_id,
            role="sales",
            charter="Receive cleared prospect records and act on them.",
        )
        conversation = PersistentAgentConversation.objects.create(
            channel=CommsChannel.OTHER,
            address=f"peer://{clearance_link.pair_key}",
            display_name=f"{agent.name} <-> {clearance.name}",
            is_peer_dm=True,
            peer_link=clearance_link,
        )
        from_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=clearance,
            channel=CommsChannel.OTHER,
            address=f"peer://agent/{clearance.id}",
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.OTHER,
            address=f"peer://agent/{agent.id}",
        )
        payload = {"kind": "clearance_decisions", "records": list(self.case.records)}
        inbound = PersistentAgentMessage.objects.create(
            owner_agent=agent,
            peer_agent=clearance,
            from_endpoint=from_endpoint,
            conversation=conversation,
            is_outbound=False,
            body="Final clearance decisions for the current batch.",
            raw_payload={
                "_source": "agent_peer_dm",
                "direction": "inbound",
                "peer_link_id": str(clearance_link.id),
                "structured_payload": payload,
            },
        )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="inject_decisions",
        )
        with self.wait_for_agent_idle(agent_id, timeout=120):
            self.trigger_processing(
                agent_id,
                eval_run_id=run_id,
                eval_stop_policy={
                    "ignored_tool_names": ["sleep_until_next_trigger", "update_plan", "sqlite_batch"],
                    "stop_on_tool_names_after_finish": ["send_agent_message"],
                    "max_relevant_tool_calls": 3,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_decisions",
            observed_summary="Structured peer decisions were processed through the real agent harness.",
            artifacts={"message": inbound},
        )

        calls = StructuredPeerHandoffScenario._tool_calls(run_id, inbound.timestamp)
        sales_calls = [
            call
            for call in calls
            if str((call.tool_params or {}).get("peer_agent_id") or "") == str(sales.id)
        ]
        source_calls = [
            call
            for call in calls
            if str((call.tool_params or {}).get("peer_agent_id") or "") == str(clearance.id)
        ]
        passed = not source_calls
        if self.case.expected_forward is None:
            passed = passed and not sales_calls
            observed = (
                "Negative decisions stayed negative and produced no downstream handoff."
                if passed
                else "A negative decision was acknowledged or promoted into a downstream handoff."
            )
        else:
            forwarded_payload = (
                (sales_calls[0].tool_params or {}).get("structured_payload")
                if len(sales_calls) == 1
                else None
            )
            passed = (
                passed
                and len(sales_calls) == 1
                and StructuredPeerHandoffScenario._call_succeeded(sales_calls[0])
                and StructuredPeerHandoffScenario._contains_record(
                    forwarded_payload,
                    self.case.expected_forward,
                )
                and all(
                    record["record_id"] not in json.dumps(forwarded_payload)
                    for record in self.case.records
                    if record != self.case.expected_forward
                )
            )
            observed = (
                "Only the exact cleared record reached Sales Agent."
                if passed
                else "The mixed decision batch was dropped, broadened, or changed in routing."
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if passed else EvalRunTask.Status.FAILED,
            task_name="verify_exact_routing",
            observed_summary=observed,
            artifacts={"step": calls[0].step} if calls else {},
        )


for case in STRUCTURED_DECISION_ROUTING_CASES:
    ScenarioRegistry.register(StructuredDecisionRoutingScenario(case))
