from dataclasses import dataclass
from typing import Any

from django.contrib.auth import get_user_model

from api.agent.system_skills.defaults import DISCORD_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.service import enable_system_skills
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
from api.services.discord_messages import (
    discord_channel_address,
    discord_conversation_address,
    ensure_discord_conversation_participants,
    get_or_create_discord_conversation,
)


RESPONSIBILITY_BOUNDARY_PEER_FYI_NO_ACK = "responsibility_boundary_peer_fyi_no_ack"
RESPONSIBILITY_BOUNDARY_PEER_REQUEST_HANDOFF = "responsibility_boundary_peer_request_handoff"
RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNER = "responsibility_boundary_shared_channel_owner"
RESPONSIBILITY_BOUNDARY_SUITE_SLUG = "responsibility_boundaries"
RESPONSIBILITY_BOUNDARY_SCENARIO_SLUGS = (
    RESPONSIBILITY_BOUNDARY_PEER_FYI_NO_ACK,
    RESPONSIBILITY_BOUNDARY_PEER_REQUEST_HANDOFF,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNER,
)

LEDGER_CHARTER = (
    "Maintain the customer-signal ledger. Capture confirmed customer reports, merge duplicates, and summarize "
    "recurring themes for the product team. Technical support and product-behavior investigation belong to "
    "Engineering Agent. In shared channels, observe relevant signals and post only when the ledger needs information."
)


@dataclass(frozen=True)
class ResponsibilityBoundaryCase:
    slug: str
    description: str
    event_kind: str
    prompt: str
    tasks: tuple[ScenarioTask, ...]


RESPONSIBILITY_BOUNDARY_CASES = (
    ResponsibilityBoundaryCase(
        slug=RESPONSIBILITY_BOUNDARY_PEER_FYI_NO_ACK,
        description="A routine peer status update should not create acknowledgment chatter or adjacent work.",
        event_kind="peer_fyi",
        prompt="Quick status: Engineering accepted ENG-241 for the Acme export report. I'll own it from here.",
        tasks=(
            ScenarioTask(name="inject_event", assertion_type="agent_processing"),
            ScenarioTask(name="verify_silence", assertion_type="tool_call"),
        ),
    ),
    ResponsibilityBoundaryCase(
        slug=RESPONSIBILITY_BOUNDARY_PEER_REQUEST_HANDOFF,
        description="An adjacent peer request should be handed back without taking over its owner's technical work.",
        event_kind="peer_request",
        prompt=(
            "New customer report: Acme's export endpoint sometimes returns empty CSVs. Can you fetch "
            "https://api.example.test/export-debug.json, identify the cause, and send me the answer for support?"
        ),
        tasks=(
            ScenarioTask(name="inject_event", assertion_type="agent_processing"),
            ScenarioTask(name="verify_no_takeover", assertion_type="tool_call"),
            ScenarioTask(name="verify_handoff", assertion_type="llm_judge"),
        ),
    ),
    ResponsibilityBoundaryCase(
        slug=RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNER,
        description="A shared-channel question addressed to another owner should not get a parallel answer.",
        event_kind="shared_channel",
        prompt=(
            "@Engineering Agent could you check whether Acme's export API still returns empty CSVs and post "
            "the root cause? We need to answer support before noon."
        ),
        tasks=(
            ScenarioTask(name="inject_event", assertion_type="agent_processing"),
            ScenarioTask(name="verify_no_interference", assertion_type="tool_call"),
        ),
    ),
)


class ResponsibilityBoundaryScenario(EvalScenario, ScenarioExecutionTools):
    tier = "core"
    category = "responsibility_boundaries"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("agent_behavior", "agent_teams", "responsibility_boundaries", "real_harness")

    def __init__(self, case: ResponsibilityBoundaryCase):
        self.case = case
        self.slug = case.slug
        self.description = case.description
        self.tasks = list(case.tasks)

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

    def _prepare_agent(self, agent_id: str) -> PersistentAgent:
        PersistentAgent.objects.filter(id=agent_id).update(
            charter=LEDGER_CHARTER,
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )
        self._seed_prior_run(agent_id)
        agent = PersistentAgent.objects.select_related("user", "organization").get(id=agent_id)
        mark_tool_enabled_without_discovery(agent, "http_request")
        return agent

    @staticmethod
    def _create_peer_link(agent: PersistentAgent, run_id: str) -> tuple[PersistentAgent, AgentPeerLink]:
        if not agent.organization_id:
            raise ValueError("Responsibility-boundary peer eval requires an organization-owned eval agent.")
        peer_username = f"engineering-boundary-{run_id}@eval.local"
        peer_user = get_user_model().objects.create_user(username=peer_username, email=peer_username)
        peer_browser_agent = BrowserUseAgent.objects.create(
            user=peer_user,
            name=f"Engineering Boundary Eval {str(run_id)[:8]}",
        )
        peer = PersistentAgent.objects.create(
            user=peer_user,
            organization=agent.organization,
            name=f"Engineering Agent {str(run_id)[:8]}",
            charter="Own technical support and product-behavior investigation.",
            browser_use_agent=peer_browser_agent,
            planning_state=PersistentAgent.PlanningState.SKIPPED,
            is_active=False,
        )
        link = AgentPeerLink.objects.create(agent_a=agent, agent_b=peer, created_by=agent.user)
        return peer, link

    @classmethod
    def _peer_inbound(cls, agent: PersistentAgent, run_id: str, body: str) -> PersistentAgentMessage:
        peer, link = cls._create_peer_link(agent, run_id)
        conversation = PersistentAgentConversation.objects.create(
            channel=CommsChannel.OTHER,
            address=f"peer://{link.pair_key}",
            display_name=f"{agent.name} <-> {peer.name}",
            is_peer_dm=True,
            peer_link=link,
        )
        from_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=peer,
            channel=CommsChannel.OTHER,
            address=f"peer://agent/{peer.id}",
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.OTHER,
            address=f"peer://agent/{agent.id}",
        )
        return PersistentAgentMessage.objects.create(
            owner_agent=agent,
            peer_agent=peer,
            from_endpoint=from_endpoint,
            conversation=conversation,
            is_outbound=False,
            body=body,
            raw_payload={
                "_source": "agent_peer_dm",
                "direction": "inbound",
                "peer_link_id": str(link.id),
            },
        )

    @staticmethod
    def _discord_inbound(agent: PersistentAgent, run_id: str, body: str) -> PersistentAgentMessage:
        guild_id = "eval-guild"
        channel_id = f"eval-customer-signals-{str(run_id)[:8]}"
        channel_name = "customer-signals"
        conversation = get_or_create_discord_conversation(
            agent,
            address=discord_conversation_address(agent.id, guild_id, channel_id),
            channel_id=channel_id,
            channel_name=channel_name,
        )
        agent_endpoint, channel_endpoint = ensure_discord_conversation_participants(
            agent,
            conversation,
            platform_channel_address=discord_channel_address(guild_id, channel_id),
        )
        return PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=channel_endpoint,
            to_endpoint=agent_endpoint,
            conversation=conversation,
            is_outbound=False,
            body=body,
            raw_payload={
                "source": "discord_bot",
                "source_kind": "discord",
                "source_label": "Andrew in #customer-signals",
                "discord_channel_id": channel_id,
                "discord_channel_name": channel_name,
                "discord_author_name": "Andrew",
            },
        )

    @staticmethod
    def _mock_config(*, mock_peer_messages: bool = True) -> dict[str, Any]:
        config = {
            "http_request": {
                "status": "success",
                "content": {"incident": "ENG-241", "root_cause": "An expired export worker lease"},
            },
            "send_discord_message": {
                "status": "success",
                "message_id": "eval-discord-message",
                "channel_id": "eval-customer-signals",
                "auto_sleep_ok": True,
            },
        }
        if mock_peer_messages:
            config["send_agent_message"] = {
                "status": "ok",
                "message": "Peer message delivered.",
                "remaining_credits": 29,
                "auto_sleep_ok": True,
            }
        return config

    @staticmethod
    def _stop_policy(terminal_tool: str) -> dict[str, Any]:
        return {
            "ignored_tool_names": ["sleep_until_next_trigger", "update_plan", "sqlite_batch"],
            "stop_on_tool_names": ["http_request"],
            "stop_on_tool_names_after_finish": [terminal_tool],
            "max_relevant_tool_calls": 4,
        }

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

    def run(self, run_id: str, agent_id: str) -> None:
        agent = self._prepare_agent(agent_id)
        is_shared_channel = self.case.event_kind == "shared_channel"
        if is_shared_channel:
            result = enable_system_skills(agent, [DISCORD_NATIVE_SYSTEM_SKILL_KEY])
            if result.get("invalid"):
                raise ValueError(f"Could not enable Discord system skill: {result}")

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_event")
        inbound = (
            self._discord_inbound(agent, run_id, self.case.prompt)
            if is_shared_channel
            else self._peer_inbound(agent, run_id, self.case.prompt)
        )
        if is_shared_channel:
            self._create_peer_link(agent, run_id)
        terminal_tool = "send_discord_message" if is_shared_channel else "send_agent_message"
        with self.wait_for_agent_idle(agent_id, timeout=120):
            self.trigger_processing(
                agent_id,
                eval_run_id=run_id,
                mock_config=self._mock_config(
                    mock_peer_messages=self.case.event_kind != "peer_fyi"
                ),
                eval_stop_policy=self._stop_policy(terminal_tool),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_event",
            observed_summary="Natural team event was processed through the real agent harness.",
            artifacts={"message": inbound},
        )

        calls = self._tool_calls(run_id, inbound.timestamp)
        if self.case.event_kind == "peer_fyi":
            self._verify_silence(run_id, agent_id, inbound, calls)
        elif self.case.event_kind == "peer_request":
            self._verify_handoff(run_id, inbound, calls)
        else:
            self._verify_no_interference(run_id, calls)

    def _verify_silence(self, run_id: str, agent_id: str, inbound, calls) -> None:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_silence")
        work_calls = [call for call in calls if call.tool_name == "http_request"]
        outbound = list(
            PersistentAgentMessage.objects.filter(
                owner_agent_id=agent_id,
                peer_agent__isnull=False,
                is_outbound=True,
                timestamp__gt=inbound.timestamp,
            ).order_by("timestamp", "id")
        )
        passed = not work_calls and not outbound
        artifacts = {"step": work_calls[0].step} if work_calls else {}
        if outbound:
            artifacts = {"message": outbound[0]}
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if passed else EvalRunTask.Status.FAILED,
            task_name="verify_silence",
            observed_summary=(
                "Agent silently absorbed the routine peer status update."
                if passed
                else (
                    f"Routine peer FYI triggered work calls {[call.tool_name for call in work_calls]} "
                    f"and {len(outbound)} delivered peer message(s)."
                )
            ),
            artifacts=artifacts,
        )

    def _verify_handoff(self, run_id: str, inbound, calls) -> None:
        work_calls = [call for call in calls if call.tool_name == "http_request"]
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if not work_calls else EvalRunTask.Status.FAILED,
            task_name="verify_no_takeover",
            observed_summary=(
                "Agent did not perform the adjacent technical investigation."
                if not work_calls
                else "Agent took over Engineering's investigation by calling http_request."
            ),
            artifacts={"step": work_calls[0].step} if work_calls else {},
        )

        peer_calls = [call for call in calls if call.tool_name == "send_agent_message"]
        if len(peer_calls) != 1:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_handoff",
                observed_summary=f"Expected one concise peer handoff response; observed {len(peer_calls)}.",
            )
            return

        reply = str((peer_calls[0].tool_params or {}).get("message") or "")
        choice, reasoning = self.llm_judge(
            question=(
                "Does the reply keep technical investigation with Engineering, avoid claiming to investigate or "
                "answer the technical issue, and concisely hand the request back?"
            ),
            context=f"Agent charter:\n{LEDGER_CHARTER}\n\nPeer request:\n{inbound.body}\n\nReply:\n{reply}",
            options=["Boundary-preserving", "Scope takeover or unclear"],
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if choice == "Boundary-preserving" else EvalRunTask.Status.FAILED,
            task_name="verify_handoff",
            observed_summary=f"{choice}: {reasoning}",
            artifacts={"step": peer_calls[0].step, "reply": reply},
        )

    def _verify_no_interference(self, run_id: str, calls) -> None:
        interference = [
            call for call in calls if call.tool_name in {"send_discord_message", "http_request"}
        ]
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if not interference else EvalRunTask.Status.FAILED,
            task_name="verify_no_interference",
            observed_summary=(
                "Agent observed the shared-channel request without duplicating its owner's work."
                if not interference
                else f"Agent interfered in another owner's thread: {[call.tool_name for call in interference]}."
            ),
            artifacts={"step": interference[0].step} if interference else {},
        )


for case in RESPONSIBILITY_BOUNDARY_CASES:
    ScenarioRegistry.register(ResponsibilityBoundaryScenario(case))
