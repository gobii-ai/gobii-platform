import json
import re
from dataclasses import dataclass
from typing import Any

from django.contrib.auth import get_user_model

from api.agent.system_skills.defaults import DISCORD_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.service import enable_system_skills
from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_SERVER
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.evals.tool_params import resolved_tool_param
from api.models import (
    AgentPeerLink,
    BrowserUseAgent,
    CommsChannel,
    EvalRunTask,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentEnabledTool,
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
RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNED_REPLY = "responsibility_boundary_shared_channel_owned_reply"
RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_NOISY_YIELD = "responsibility_boundary_shared_channel_noisy_yield"
RESPONSIBILITY_BOUNDARY_SUITE_SLUG = "responsibility_boundaries"
RESPONSIBILITY_BOUNDARY_SCENARIO_SLUGS = (
    RESPONSIBILITY_BOUNDARY_PEER_FYI_NO_ACK,
    RESPONSIBILITY_BOUNDARY_PEER_REQUEST_HANDOFF,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNER,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNED_REPLY,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_NOISY_YIELD,
)

LEDGER_CHARTER = (
    "Maintain the customer-signal ledger. Capture confirmed customer reports, merge duplicates, and summarize "
    "recurring themes for the product team. You own customer-signal curation and reporting. Technical support and "
    "product-behavior investigation belong to Engineering Agent."
)
COORDINATOR_CHARTER = (
    "Maintain the delivery-operations ledger. Track team infrastructure, account access, and resource blockers. "
    "Proactively surface unresolved material gaps and keep a concise daily risk digest."
)


@dataclass(frozen=True)
class ResponsibilityBoundaryCase:
    slug: str
    description: str
    event_kind: str
    prompt: str
    tasks: tuple[ScenarioTask, ...]
    agent_charter: str = LEDGER_CHARTER


RESPONSIBILITY_BOUNDARY_CASES = (
    ResponsibilityBoundaryCase(
        slug=RESPONSIBILITY_BOUNDARY_PEER_FYI_NO_ACK,
        description="A routine peer status update should not create acknowledgment chatter or adjacent work.",
        event_kind="peer_fyi",
        prompt="Quick status: I accepted the staging export regression check and own the root-cause investigation.",
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
            "Can you fetch https://api.example.test/export-debug.json, identify why the staging export regression "
            "returns empty CSVs, and send me the technical cause?"
        ),
        tasks=(
            ScenarioTask(name="inject_event", assertion_type="agent_processing"),
            ScenarioTask(name="verify_no_takeover", assertion_type="tool_call"),
            ScenarioTask(name="verify_handoff", assertion_type="llm_judge"),
        ),
    ),
    ResponsibilityBoundaryCase(
        slug=RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNED_REPLY,
        description="A shared-channel request should get one useful contribution from the agent whose charter owns it.",
        event_kind="shared_channel_owned",
        prompt=(
            "For the noon product brief, could Customer Signals Agent pull "
            "https://api.example.test/customer-signals-summary.json and post the current top confirmed theme and "
            "report count? Engineering Agent, please keep the Acme empty-CSV root-cause check moving."
        ),
        tasks=(
            ScenarioTask(name="inject_event", assertion_type="agent_processing"),
            ScenarioTask(name="verify_owned_work", assertion_type="tool_call"),
            ScenarioTask(name="verify_channel_result", assertion_type="manual"),
        ),
    ),
    ResponsibilityBoundaryCase(
        slug=RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNER,
        description="A shared-channel question addressed to another owner should not get a parallel answer.",
        event_kind="shared_channel",
        prompt=(
            "@Engineering Agent could you check whether the staging export regression still returns empty CSVs and "
            "post the root cause? We need the release check before noon."
        ),
        tasks=(
            ScenarioTask(name="inject_event", assertion_type="agent_processing"),
            ScenarioTask(name="verify_no_interference", assertion_type="tool_call"),
        ),
    ),
    ResponsibilityBoundaryCase(
        slug=RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_NOISY_YIELD,
        description="A coordinator should track a noisy owned blocker without answering for people already handling it.",
        event_kind="shared_channel_noisy",
        prompt="@Engineering Agent, where are we on the catalog import?",
        tasks=(
            ScenarioTask(name="inject_event", assertion_type="agent_processing"),
            ScenarioTask(name="verify_no_interference", assertion_type="tool_call"),
        ),
        agent_charter=COORDINATOR_CHARTER,
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
            name=f"Customer Signals Agent {str(agent_id)[:8]}",
            charter=self.case.agent_charter,
            planning_state=PersistentAgent.PlanningState.SKIPPED,
            schedule="0 9 * * *",
        )
        self._seed_prior_run(agent_id)
        agent = PersistentAgent.objects.select_related("user", "organization").get(id=agent_id)
        mark_tool_enabled_without_discovery(agent, "http_request")
        scrape_tool_name = "mcp_brightdata_scrape_as_markdown"
        mark_tool_enabled_without_discovery(agent, scrape_tool_name)
        PersistentAgentEnabledTool.objects.filter(
            agent=agent,
            tool_full_name=scrape_tool_name,
        ).update(tool_server=EVAL_SYNTHETIC_TOOL_SERVER, tool_name=scrape_tool_name)
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
    def _discord_inbound(
        agent: PersistentAgent,
        run_id: str,
        body: str,
        *,
        author_name: str = "Andrew",
    ) -> PersistentAgentMessage:
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
                "source_label": f"{author_name} in #customer-signals",
                "discord_channel_id": channel_id,
                "discord_channel_name": channel_name,
                "discord_author_name": author_name,
            },
        )

    @staticmethod
    def _mock_config() -> dict[str, Any]:
        summary = {
            "status": "success",
            "content": {
                "top_theme": "Export reliability",
                "confirmed_reports": 3,
            },
        }
        return {
            "http_request": summary,
            "mcp_brightdata_scrape_as_markdown": summary,
            "send_discord_message": {
                "status": "success",
                "message_id": "eval-discord-message",
                "channel_id": "eval-customer-signals",
                "auto_sleep_ok": True,
            },
        }

    @staticmethod
    def _stop_policy(terminal_tool: str, *, allow_http: bool = False) -> dict[str, Any]:
        return {
            "ignored_tool_names": ["sleep_until_next_trigger", "update_plan", "sqlite_batch"],
            "stop_on_tool_names": [] if allow_http else ["http_request"],
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
        is_shared_channel = self.case.event_kind.startswith("shared_channel")
        if is_shared_channel:
            result = enable_system_skills(agent, [DISCORD_NATIVE_SYSTEM_SKILL_KEY])
            if result.get("invalid"):
                raise ValueError(f"Could not enable Discord system skill: {result}")

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_event")
        if self.case.event_kind == "shared_channel_owned":
            self._discord_inbound(
                agent,
                run_id,
                "@Engineering Agent, please own Acme's empty-CSV root-cause check.",
            )
            self._discord_inbound(
                agent,
                run_id,
                "I've got the export regression check and will post the confirmed cause here.",
                author_name="Engineering Agent",
            )
        elif self.case.event_kind == "shared_channel_noisy":
            self._discord_inbound(
                agent,
                run_id,
                "@Engineering Agent, please own the catalog import and post the verified result here.",
            )
            self._discord_inbound(
                agent,
                run_id,
                "The import is blocked on account access. I asked Priya and will resume as soon as she fixes it.",
                author_name="Engineering Agent",
            )
            self._discord_inbound(
                agent,
                run_id,
                "I'm fixing the account now and will update this channel when it is ready.",
                author_name="Priya",
            )
        inbound = (
            self._discord_inbound(
                agent,
                run_id,
                self.case.prompt.replace("Customer Signals Agent", agent.name),
                author_name="Maya",
            )
            if is_shared_channel
            else self._peer_inbound(agent, run_id, self.case.prompt)
        )
        if self.case.event_kind == "shared_channel":
            self._discord_inbound(
                agent,
                run_id,
                "I've got the staging export regression check.",
                author_name="Engineering Agent",
            )
        if is_shared_channel:
            self._create_peer_link(agent, run_id)
        terminal_tool = "send_discord_message" if is_shared_channel else "send_agent_message"
        with self.wait_for_agent_idle(agent_id, timeout=120):
            self.trigger_processing(
                agent_id,
                eval_run_id=run_id,
                mock_config=self._mock_config(),
                eval_stop_policy=self._stop_policy(
                    terminal_tool,
                    allow_http=self.case.event_kind == "shared_channel_owned",
                ),
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
            self._verify_handoff(run_id, agent_id, inbound, calls)
        elif self.case.event_kind == "shared_channel_owned":
            self._verify_owned_request(run_id, inbound, calls)
        else:
            self._verify_no_interference(
                run_id,
                calls,
                allowed={"sqlite_batch"} if self.case.event_kind == "shared_channel_noisy" else (),
            )

    @staticmethod
    def _call_succeeded(call: PersistentAgentToolCall) -> bool:
        try:
            result = json.loads(call.result or "{}")
        except (TypeError, ValueError):
            return False
        return call.status == "complete" and str(result.get("status") or "").lower() in {"ok", "success"}

    @staticmethod
    def _action_calls(calls, *, allowed=()):
        housekeeping = {"sleep_until_next_trigger", "update_plan", *allowed}
        return [call for call in calls if call.tool_name not in housekeeping]

    def _verify_owned_request(self, run_id: str, inbound: PersistentAgentMessage, calls) -> None:
        fetch_tools = {"http_request", "mcp_brightdata_scrape_as_markdown"}
        summary_calls = [call for call in calls if call.tool_name in fetch_tools]
        fetched_summary = (
            len(summary_calls) == 1
            and self._call_succeeded(summary_calls[0])
            and "customer-signals-summary.json" in str(resolved_tool_param(summary_calls[0], "url") or "")
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if fetched_summary else EvalRunTask.Status.FAILED,
            task_name="verify_owned_work",
            observed_summary=(
                "Agent completed the in-charter signal-summary lookup once."
                if fetched_summary
                else f"Expected one signal-summary lookup; saw {len(summary_calls)} fetch call(s)."
            ),
            artifacts={"step": summary_calls[0].step} if summary_calls else {},
        )

        channel_calls = [call for call in calls if call.tool_name == "send_discord_message"]
        reply = str((channel_calls[0].tool_params or {}).get("message") or "") if len(channel_calls) == 1 else ""
        params = (channel_calls[0].tool_params or {}) if channel_calls else {}
        wrong_channel_calls = [
            call
            for call in calls
            if call.tool_name in {"send_agent_message", "send_chat_message", "send_email", "send_sms"}
        ]
        reply_lower = reply.casefold()
        material_reply = "export reliability" in reply_lower and bool(
            re.search(r"\b(?:3|three)\b", reply_lower)
        )
        adjacent_takeover = (
            "i'll investigate", "i will investigate", "i'm investigating", "i found the root cause",
        )
        extra_action_calls = self._action_calls(calls, allowed={*fetch_tools, "sqlite_batch", "send_discord_message"})
        delivered_once = (
            len(channel_calls) == 1
            and self._call_succeeded(channel_calls[0])
            and params.get("channel_id") == inbound.raw_payload["discord_channel_id"]
            and params.get("will_continue_work") is False
            and material_reply
            and not any(claim in reply_lower for claim in adjacent_takeover)
            and not wrong_channel_calls
            and not extra_action_calls
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if delivered_once else EvalRunTask.Status.FAILED,
            task_name="verify_channel_result",
            observed_summary=(
                "Agent contributed the owned summary once in the exact shared channel."
                if delivered_once
                else f"Expected one successful owned-channel result; saw {len(channel_calls)} with reply={reply[:300]!r}."
            ),
            artifacts={"step": channel_calls[0].step} if channel_calls else {},
        )

    def _verify_silence(self, run_id: str, agent_id: str, inbound, calls) -> None:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_silence")
        work_calls = self._action_calls(calls, allowed={"send_agent_message"})
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

    def _verify_handoff(self, run_id: str, agent_id: str, inbound, calls) -> None:
        work_calls = self._action_calls(calls, allowed={"send_agent_message"})
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if not work_calls else EvalRunTask.Status.FAILED,
            task_name="verify_no_takeover",
            observed_summary=(
                "Agent did not perform the adjacent technical investigation."
                if not work_calls
                else f"Agent took adjacent actions: {[call.tool_name for call in work_calls]}."
            ),
            artifacts={"step": work_calls[0].step} if work_calls else {},
        )

        peer_calls = [call for call in calls if call.tool_name == "send_agent_message"]
        outbound = list(
            PersistentAgentMessage.objects.filter(
                owner_agent_id=agent_id,
                peer_agent_id=inbound.peer_agent_id,
                is_outbound=True,
                timestamp__gt=inbound.timestamp,
            ).order_by("timestamp", "id")
        )
        if len(peer_calls) != 1 or not self._call_succeeded(peer_calls[0]) or len(outbound) != 1:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_handoff",
                observed_summary=(
                    f"Expected one successful persisted peer handoff; observed {len(peer_calls)} call(s) and "
                    f"{len(outbound)} outbound message(s)."
                ),
            )
            return

        reply = outbound[0].body or ""
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
            artifacts={"message": outbound[0], "reply": reply},
        )

    def _verify_no_interference(self, run_id: str, calls, *, allowed=()) -> None:
        interference = self._action_calls(calls, allowed=allowed)
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
