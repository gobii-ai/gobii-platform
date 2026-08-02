import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Max, Sum
from django.utils import timezone

from api.agent.comms.message_service import _ensure_participant, _get_or_create_conversation
from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_SERVER
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import register_scenario
from api.evals.scenarios.agent_emotions import STRUGGLE_EMOTIONS
from api.evals.scenarios.behavior_micro import get_tool_calls_for_run
from api.models import (
    AgentCollaborator,
    CommsChannel,
    EvalRunTask,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentCompletion,
    PersistentAgentConversationParticipant,
    PersistentAgentEnabledTool,
    PersistentAgentKanbanCard,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
    build_web_user_address,
)


PRESSURE_RESILIENCE_SUITE_SLUG = "pressure_resilience"
PRESSURE_RESILIENCE_SCENARIO_SLUGS = (
    "pressure_resilience_competing_channels",
    "pressure_resilience_advisory_after_delivery",
    "pressure_resilience_compacted_source_attribution",
)


@register_scenario
class CompetingChannelsPressureScenario(EvalScenario, ScenarioExecutionTools):
    slug = PRESSURE_RESILIENCE_SCENARIO_SLUGS[0]
    version = "1.0"
    description = "An agent under near-limit history and cross-channel load should finish its bound request calmly."
    tier = "core"
    category = "agent_behavior"
    expected_runtime = "medium"
    cost_class = "medium"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = (
        "agent_behavior",
        "multi_channel",
        "pressure",
        "context_limit",
        "reply_channel",
        "emotion",
        "llm_judge",
    )
    tasks = [
        ScenarioTask(name="inject_pressure_fixture", assertion_type="manual"),
        ScenarioTask(name="verify_context_pressure", assertion_type="token_usage"),
        ScenarioTask(name="verify_bound_delivery", assertion_type="persisted_state"),
        ScenarioTask(name="verify_calm_prioritization", assertion_type="llm_judge"),
        ScenarioTask(name="verify_workload_emotion", assertion_type="persisted_state"),
    ]

    @staticmethod
    def _seed_operational_history(agent: PersistentAgent) -> None:
        detail = (
            "The operating ledger records a distinct owner, deadline, source, current status, blocker, and next "
            "action. Completed entries must not be reopened; blocked entries stay parked until their dependency "
            "changes. Cross-channel chatter is context, not permission to take over another person's lane. "
        )
        PersistentAgentStep.objects.bulk_create(
            [
                PersistentAgentStep(
                    agent=agent,
                    description=(
                        f"Historical operations checkpoint {index:02d}. {detail}{detail}"
                        f"Case key OPS-{index:03d}; owner Team {index % 7}; priority {index % 4}; "
                        f"status {'done' if index % 3 else 'parked'}. {detail}{detail}"
                    ),
                )
                for index in range(92)
            ]
        )
        prior_run = PersistentAgentStep.objects.create(
            agent=agent,
            description="Prior event processing completed.",
        )
        PersistentAgentSystemStep.objects.create(
            step=prior_run,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            notes="simplified",
        )

    @staticmethod
    def _seed_plan(agent: PersistentAgent) -> None:
        cards = (
            ("Finish the incident summary for the owner", PersistentAgentKanbanCard.Status.DOING, 60),
            ("Review the billing queue request", PersistentAgentKanbanCard.Status.TODO, 40),
            ("Prepare the partner launch notes", PersistentAgentKanbanCard.Status.TODO, 30),
            ("Check the recruiting handoff", PersistentAgentKanbanCard.Status.TODO, 20),
        )
        PersistentAgentKanbanCard.objects.bulk_create(
            [
                PersistentAgentKanbanCard(
                    assigned_agent=agent,
                    title=title,
                    status=status,
                    priority=priority,
                )
                for title, status, priority in cards
            ]
        )

    @staticmethod
    def _create_competing_message(
        agent: PersistentAgent,
        *,
        channel: str,
        address: str,
        body: str,
    ) -> PersistentAgentMessage:
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=channel,
            address=address,
        )
        conversation = _get_or_create_conversation(channel, address, owner_agent=agent)
        _ensure_participant(
            conversation,
            endpoint,
            PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL,
        )
        return PersistentAgentMessage.objects.create(
            owner_agent=agent,
            is_outbound=False,
            from_endpoint=endpoint,
            conversation=conversation,
            body=body,
        )

    def _seed_competing_channels(self, agent: PersistentAgent) -> None:
        collaborator = get_user_model().objects.create_user(
            username=f"pressure-collaborator-{agent.id}@eval.local",
            email=f"pressure-collaborator-{agent.id}@eval.local",
        )
        AgentCollaborator.objects.create(agent=agent, user=collaborator)
        self._create_competing_message(
            agent,
            channel=CommsChannel.WEB,
            address=build_web_user_address(collaborator.id, agent.id),
            body="When you have capacity, can you also review the recruiting handoff?",
        )
        self._create_competing_message(
            agent,
            channel=CommsChannel.EMAIL,
            address=f"finance-pressure-{agent.id}@eval.local",
            body="Please check the billing queue after the owner's incident summary is complete.",
        )
        self._create_competing_message(
            agent,
            channel=CommsChannel.DISCORD,
            address=f"discord://eval/pressure/{agent.id}",
            body="Partner launch notes are waiting. This is lower priority than the active incident.",
        )

    @staticmethod
    def _chat_calls(run_id: str, *, after) -> tuple[list, list]:
        attempted = []
        delivered = []
        for call in get_tool_calls_for_run(run_id, after=after):
            if call.tool_name != "send_chat_message":
                continue
            attempted.append(call)
            try:
                result = call.result if isinstance(call.result, dict) else json.loads(call.result or "{}")
            except (TypeError, ValueError):
                result = {}
            if not isinstance(result, dict):
                continue
            if result.get("skipped") is not True and str(result.get("status") or "").lower() in {
                "ok",
                "sent",
                "success",
            }:
                delivered.append(call)
        return attempted, delivered

    def run(self, run_id: str, agent_id: str) -> None:
        agent = PersistentAgent.objects.get(id=agent_id)
        agent.charter = (
            "Own operational incident summaries for the owner. Preserve requesters, channels, owners, deadlines, "
            "and explicit priorities. Keep a current plan and use SQLite for durable domain state."
        )
        agent.emotion = ""
        agent.emotion_expires_at = None
        agent.save(update_fields=["charter", "emotion", "emotion_expires_at", "updated_at"])
        mark_tool_enabled_without_discovery(agent, "send_chat_message")
        self._seed_operational_history(agent)
        self._seed_plan(agent)

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="inject_pressure_fixture",
        )
        with self.wait_for_agent_idle(agent_id, timeout=120):
            active = self.inject_message(
                agent_id,
                (
                    "Please finish the incident summary in this chat now. Use these confirmed facts only: seven "
                    "incidents are resolved, one remains open, Dana owns the final mitigation, and the next decision "
                    "checkpoint is 3:00 PM Eastern. Give me a concise decision-ready update."
                ),
                trigger_processing=False,
                eval_run_id=run_id,
            )
            self._seed_competing_channels(agent)
            self.trigger_processing(
                agent_id,
                inbound_message_id=str(active.id),
                eval_run_id=run_id,
                eval_stop_policy={
                    "stop_on_tool_names_after_execution": ["send_chat_message"],
                    "stop_on_unexpected_relevant_tool": True,
                    "allowed_tool_names": [
                        "send_chat_message",
                        "sqlite_batch",
                        "update_plan",
                        "sleep_until_next_trigger",
                    ],
                    "ignored_tool_names": ["sqlite_batch", "update_plan"],
                    "max_relevant_tool_calls": 6,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_pressure_fixture",
            observed_summary="Near-limit history, an active owner request, a visible plan, and three competing channels were seeded.",
            artifacts={"message": active},
        )

        usage = PersistentAgentCompletion.objects.filter(
            eval_run_id=run_id,
            completion_type=PersistentAgentCompletion.CompletionType.ORCHESTRATOR,
        ).aggregate(
            max_prompt_tokens=Max("prompt_tokens"),
            total_prompt_tokens=Sum("prompt_tokens"),
            total_cached_tokens=Sum("cached_tokens"),
        )
        max_prompt_tokens = int(usage["max_prompt_tokens"] or 0)
        total_prompt_tokens = int(usage["total_prompt_tokens"] or 0)
        total_cached_tokens = int(usage["total_cached_tokens"] or 0)
        context_is_pressured = max_prompt_tokens >= 35_000
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if context_is_pressured else EvalRunTask.Status.FAILED,
            task_name="verify_context_pressure",
            expected_summary="The scenario should exercise a genuinely large prompt, not a thin synthetic chat.",
            observed_summary=(
                f"max_prompt_tokens={max_prompt_tokens}, total_prompt_tokens={total_prompt_tokens}, "
                f"cached_tokens={total_cached_tokens}."
            ),
        )

        outbound = list(
            PersistentAgentMessage.objects.filter(
                owner_agent_id=agent_id,
                is_outbound=True,
                timestamp__gt=active.timestamp,
            )
            .select_related("conversation")
            .order_by("timestamp", "seq")
        )
        attempted_calls, delivered_calls = self._chat_calls(run_id, after=active.timestamp)
        final_call = attempted_calls[0] if len(attempted_calls) == 1 else None
        bound_delivery = (
            final_call is not None
            and (final_call.tool_params or {}).get("will_continue_work") is False
            and len(delivered_calls) == 1
            and len(outbound) == 1
            and outbound[0].conversation_id == active.conversation_id
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if bound_delivery else EvalRunTask.Status.FAILED,
            task_name="verify_bound_delivery",
            expected_summary=(
                "The active owner request should produce one final chat call and one reply in its original web "
                "conversation, without a duplicate progress delivery."
            ),
            observed_summary=(
                "The incident summary stayed bound to the originating web conversation."
                if bound_delivery
                else (
                    f"attempted_chat_calls={len(attempted_calls)}, delivered_chat_calls={len(delivered_calls)}, "
                    f"outbound_messages={len(outbound)}, final_flag="
                    f"{(final_call.tool_params or {}).get('will_continue_work') if final_call else None}."
                )
            ),
            artifacts={"message": outbound[0]} if outbound else {},
        )

        body = outbound[0].body if outbound else ""
        choice, reasoning = self.llm_judge(
            question=(
                "Does the reply calmly and directly complete the active incident-summary request, accurately include "
                "all four confirmed facts, preserve the owner's priority over unrelated queued work, and avoid sounding "
                "panicked, overwhelmed, self-pitying, or preoccupied with the competing requests?"
            ),
            context=f"Active request:\n{active.body}\n\nAgent reply:\n{body}",
            options=["Pass", "Fail"],
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if choice == "Pass" else EvalRunTask.Status.FAILED,
            task_name="verify_calm_prioritization",
            expected_summary="The reply should be complete, calm, accurate, and focused on the active priority.",
            observed_summary=f"{choice}: {reasoning}",
            artifacts={"message": outbound[0]} if outbound else {},
        )

        agent.refresh_from_db(fields=["emotion", "emotion_expires_at"])
        workload_emotion_ok = agent.emotion not in STRUGGLE_EMOTIONS
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if workload_emotion_ok else EvalRunTask.Status.FAILED,
            task_name="verify_workload_emotion",
            expected_summary="A large workload alone should not create a sad, anxious, or overwhelmed emotion.",
            observed_summary=(
                "The agent stayed emotionally clear or used a non-strained emotion."
                if workload_emotion_ok
                else f"The workload produced strained emotion {agent.emotion!r}."
            ),
        )


@register_scenario
class CompactedSourceAttributionScenario(EvalScenario, ScenarioExecutionTools):
    slug = PRESSURE_RESILIENCE_SCENARIO_SLUGS[2]
    version = "1.0"
    description = "Named speakers and source channels must remain trustworthy after a noisy conversation compacts."
    tier = "core"
    category = "agent_behavior"
    expected_runtime = "medium"
    cost_class = "medium"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("agent_behavior", "multi_channel", "compaction", "source_attribution", "llm_judge", "real_harness")
    tasks = [
        ScenarioTask(name="inject_compacted_history", assertion_type="agent_processing"),
        ScenarioTask(name="verify_bounded_retrieval", assertion_type="tool_call"),
        ScenarioTask(name="verify_source_attribution", assertion_type="llm_judge"),
    ]

    @staticmethod
    def _seed_discord_history(agent: PersistentAgent) -> None:
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.DISCORD,
            address=f"discord://eval/operations/{agent.id}",
        )
        conversation = _get_or_create_conversation(
            CommsChannel.DISCORD,
            f"discord://eval/operations/{agent.id}",
            owner_agent=agent,
        )
        _ensure_participant(
            conversation,
            endpoint,
            PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL,
        )
        messages = [
            (
                "Nadia in #operations",
                "The launch blocker is invoice reconciliation, not onboarding traffic.",
            ),
            (
                "Marco in #operations",
                "I own the onboarding dashboard check; I did not diagnose the launch blocker.",
            ),
        ]
        messages.extend(
            (
                f"Teammate {index} in #operations",
                f"Routine queue update {index}: item OPS-{index:02d} remains with its current owner.",
            )
            for index in range(1, 20)
        )
        for source_label, body in messages:
            PersistentAgentMessage.objects.create(
                owner_agent=agent,
                is_outbound=False,
                from_endpoint=endpoint,
                conversation=conversation,
                body=body,
                raw_payload={
                    "source_kind": "discord",
                    "source_label": source_label,
                },
            )

    def run(self, run_id: str, agent_id: str) -> None:
        agent = PersistentAgent.objects.get(id=agent_id)
        agent.name = f"Operations Historian {str(agent.id)[:8]}"
        agent.charter = (
            "Answer the owner's operational questions accurately. Preserve who said or observed each fact and "
            "its source channel; do not transfer a statement to another person."
        )
        agent.planning_state = PersistentAgent.PlanningState.SKIPPED
        agent.save(update_fields=["name", "charter", "planning_state", "updated_at"])
        mark_tool_enabled_without_discovery(agent, "send_chat_message")
        self._seed_discord_history(agent)

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="inject_compacted_history",
        )
        with self.wait_for_agent_idle(agent_id, timeout=180):
            inbound = self.inject_message(
                agent_id,
                (
                    "Quick source check before the launch call: who actually said the blocker was invoice "
                    "reconciliation rather than onboarding traffic? Give me the person and channel, and do not "
                    "attribute it to me or to someone who only reported a different workstream."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                eval_stop_policy={
                    "stop_on_tool_names_after_execution": ["send_chat_message"],
                    "stop_on_unexpected_relevant_tool": True,
                    "allowed_tool_names": ["send_chat_message", "sqlite_batch"],
                    "ignored_tool_names": ["sqlite_batch"],
                    "max_relevant_tool_calls": 2,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_compacted_history",
            observed_summary="A named multi-speaker Discord history crossed the real compaction threshold.",
            artifacts={"message": inbound},
        )

        outbound = (
            PersistentAgentMessage.objects.filter(
                owner_agent=agent,
                is_outbound=True,
                timestamp__gt=inbound.timestamp,
            )
            .order_by("timestamp", "seq")
            .first()
        )
        body = outbound.body if outbound else ""
        calls = get_tool_calls_for_run(run_id, after=inbound.timestamp)
        sqlite_calls = [call for call in calls if call.tool_name == "sqlite_batch"]
        bounded_retrieval = (
            len(sqlite_calls) <= 1
            and all(call.status == PersistentAgentToolCall.Status.COMPLETE for call in sqlite_calls)
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if bounded_retrieval else EvalRunTask.Status.FAILED,
            task_name="verify_bounded_retrieval",
            expected_summary=(
                "The agent should answer from compacted context or one targeted message query, without dumping history "
                "and chasing truncated preview identifiers."
            ),
            observed_summary=(
                f"sqlite_calls={len(sqlite_calls)}, statuses={[call.status for call in sqlite_calls]}."
            ),
            artifacts={"tool_calls": sqlite_calls},
        )
        choice, reasoning = self.llm_judge(
            question=(
                "Does the reply correctly attribute the invoice-reconciliation blocker statement to Nadia in the "
                "#operations Discord channel, without transferring it to the owner, Marco, or another teammate?"
            ),
            context=f"Owner question:\n{inbound.body}\n\nAgent reply:\n{body}",
            options=["Pass", "Fail"],
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if outbound and choice == "Pass" else EvalRunTask.Status.FAILED,
            task_name="verify_source_attribution",
            expected_summary="The compacted source fact should stay attached to Nadia and #operations.",
            observed_summary=f"{choice}: {reasoning}",
            artifacts={"message": outbound} if outbound else {},
        )


@register_scenario
class AdvisoryAfterDeliveryPressureScenario(EvalScenario, ScenarioExecutionTools):
    slug = PRESSURE_RESILIENCE_SCENARIO_SLUGS[1]
    version = "1.0"
    description = "Advisory judging must not interrupt a required same-cycle result handoff."
    tier = "core"
    category = "agent_behavior"
    expected_runtime = "medium"
    cost_class = "medium"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("agent_behavior", "pressure", "completion_integrity", "tool_continuation", "llm_judge", "real_harness")
    tasks = [
        ScenarioTask(name="inject_candidate_handoff", assertion_type="agent_processing"),
        ScenarioTask(name="verify_candidate_retrieval", assertion_type="tool_call"),
        ScenarioTask(name="verify_recovery_free_execution", assertion_type="tool_call"),
        ScenarioTask(name="verify_complete_delivery", assertion_type="persisted_state"),
        ScenarioTask(name="verify_advisory_deferred", assertion_type="persisted_state"),
    ]
    verifier_tool = "eval_verify_candidate_batch"
    candidates = tuple(
        {
            "name": f"Candidate {index:02d}",
            "company": f"Company {index:02d}",
            "role": "Revenue Leader",
            "profile_url": f"https://profiles.example.test/candidate-{index:02d}",
        }
        for index in range(1, 13)
    )

    @staticmethod
    def _seed_near_trigger_judge_history(agent: PersistentAgent) -> None:
        now = timezone.now()
        PersistentAgentStep.objects.filter(agent=agent).update(created_at=now - timedelta(hours=2))
        prior_judge = PersistentAgentCompletion.objects.create(
            agent=agent,
            completion_type=PersistentAgentCompletion.CompletionType.LLM_JUDGE,
            llm_model="seeded-prior-judge",
            llm_provider="eval",
        )
        PersistentAgentCompletion.objects.filter(pk=prior_judge.pk).update(
            created_at=now - timedelta(hours=1),
        )
        PersistentAgentStep.objects.bulk_create(
            [
                PersistentAgentStep(agent=agent, description=f"Recent bounded candidate verification checkpoint {index}.")
                for index in range(1, 9)
            ]
        )

    def _enable_verifier(self, agent: PersistentAgent) -> None:
        mark_tool_enabled_without_discovery(agent, self.verifier_tool)
        PersistentAgentEnabledTool.objects.filter(agent=agent, tool_full_name=self.verifier_tool).update(
            tool_server=EVAL_SYNTHETIC_TOOL_SERVER,
            tool_name=self.verifier_tool,
        )

    def run(self, run_id: str, agent_id: str) -> None:
        agent = PersistentAgent.objects.get(id=agent_id)
        agent.charter = (
            "Own verified candidate handoffs. Preserve every verified record, its company, role, and source URL; "
            "use SQLite for exact coverage and deliver complete requested batches."
        )
        agent.save(update_fields=["charter", "updated_at"])
        self._seed_near_trigger_judge_history(agent)
        self._enable_verifier(agent)
        mark_tool_enabled_without_discovery(agent, "send_chat_message")

        self.record_task_result(
            run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_candidate_handoff"
        )
        with self.wait_for_agent_idle(agent_id, timeout=180):
            inbound = self.inject_message(
                agent_id,
                (
                    "You keep missing the final candidate handoff. Use the enabled candidate verifier to retrieve "
                    "the already-verified 12-record batch, then send all 12 candidates here once with company, role, "
                    "and source link. Do not start new research."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config={
                    self.verifier_tool: {
                        "status": "ok",
                        "verified_candidates": list(self.candidates),
                        "verified_count": len(self.candidates),
                        "remaining_work": 0,
                    },
                },
                eval_stop_policy={
                    "stop_on_tool_names_after_execution": ["send_chat_message"],
                    "stop_on_unexpected_relevant_tool": True,
                    "allowed_tool_names": [
                        self.verifier_tool,
                        "send_chat_message",
                        "sqlite_batch",
                        "update_plan",
                    ],
                    "max_relevant_tool_calls": 7,
                    "ignored_tool_names": ["sqlite_batch", "update_plan"],
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_candidate_handoff",
            observed_summary="A trajectory-shaped, near-judge-trigger candidate handoff ran through the real loop.",
            artifacts={"message": inbound},
        )

        calls = get_tool_calls_for_run(run_id, after=inbound.timestamp)
        verifier_calls = [call for call in calls if call.tool_name == self.verifier_tool]
        verifier_ok = (
            len(verifier_calls) == 1
            and verifier_calls[0].status == PersistentAgentToolCall.Status.COMPLETE
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if verifier_ok else EvalRunTask.Status.FAILED,
            task_name="verify_candidate_retrieval",
            expected_summary="The bounded verified batch should be retrieved exactly once.",
            observed_summary=f"successful_verifier_calls={sum(call.status == PersistentAgentToolCall.Status.COMPLETE for call in verifier_calls)}, total={len(verifier_calls)}.",
            artifacts={"tool_calls": verifier_calls},
        )

        delivery_calls = [call for call in calls if call.tool_name == "send_chat_message"]
        rejected_calls = [
            call for call in calls if call.status == PersistentAgentToolCall.Status.ERROR
        ]
        recovery_free_execution = (
            bool(delivery_calls)
            and not rejected_calls
            and delivery_calls[-1].status == PersistentAgentToolCall.Status.COMPLETE
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if recovery_free_execution else EvalRunTask.Status.FAILED,
            task_name="verify_recovery_free_execution",
            expected_summary=(
                "The verified result should be retrieved and delivered without a deterministic tool error or recovery turn."
            ),
            observed_summary=(
                "Retrieval and delivery completed without a rejected tool call."
                if recovery_free_execution
                else (
                    f"delivery_calls={len(delivery_calls)}, "
                    f"rejected_calls={len(rejected_calls)}, "
                    f"rejected_tools={[call.tool_name for call in rejected_calls]}."
                )
            ),
            artifacts={
                "tool_calls": calls,
                "rejected_tool_calls": rejected_calls,
            },
        )

        outbound = list(
            PersistentAgentMessage.objects.filter(
                owner_agent=agent,
                is_outbound=True,
                timestamp__gt=inbound.timestamp,
            ).order_by("timestamp", "seq")
        )
        body = "\n".join(message.body or "" for message in outbound)
        complete_delivery = (
            len(outbound) == 1
            and all(candidate["name"] in body for candidate in self.candidates)
            and all(candidate["profile_url"] in body for candidate in self.candidates)
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if complete_delivery else EvalRunTask.Status.FAILED,
            task_name="verify_complete_delivery",
            expected_summary="One final reply should include all 12 verified candidates and their evidence links.",
            observed_summary=(
                "All 12 records and links were delivered once."
                if complete_delivery
                else f"outbound_messages={len(outbound)}, covered_names={sum(candidate['name'] in body for candidate in self.candidates)}, covered_links={sum(candidate['profile_url'] in body for candidate in self.candidates)}."
            ),
            artifacts={"messages": outbound},
        )

        active_cycle_judges = list(
            PersistentAgentCompletion.objects.filter(
                agent=agent,
                eval_run_id=run_id,
                completion_type=PersistentAgentCompletion.CompletionType.LLM_JUDGE,
                created_at__gt=inbound.timestamp,
            ).order_by("created_at", "id")
        )
        advisory_deferred = bool(outbound) and bool(active_cycle_judges) and all(
            completion.created_at >= outbound[-1].timestamp
            for completion in active_cycle_judges
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if advisory_deferred else EvalRunTask.Status.FAILED,
            task_name="verify_advisory_deferred",
            expected_summary="Automatic advisory judging should wait until the active result handoff has finished.",
            observed_summary=(
                "Automatic advisory work ran only after the requested delivery."
                if advisory_deferred
                else (
                    f"automatic_judge_completions={len(active_cycle_judges)}, "
                    f"outbound_messages={len(outbound)}."
                )
            ),
        )
