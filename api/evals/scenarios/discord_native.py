import json

from api.agent.system_skills.defaults import DISCORD_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.service import enable_system_skills
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import register_scenario
from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentToolCall,
)
from api.services.discord_messages import (
    discord_channel_address,
    discord_conversation_address,
    ensure_discord_conversation_participants,
    get_or_create_discord_conversation,
)


DISCORD_NATIVE_REACTION_REPLY_CONTEXT = "discord_native_reaction_reply_context"
DISCORD_NATIVE_SCENARIO_SLUGS = (DISCORD_NATIVE_REACTION_REPLY_CONTEXT,)
DISCORD_NATIVE_SUITE_SLUG = "discord_native"


@register_scenario
class DiscordNativeReactionReplyContextScenario(EvalScenario, ScenarioExecutionTools):
    slug = DISCORD_NATIVE_REACTION_REPLY_CONTEXT
    description = "Ensures a Discord reply can trigger a reaction against the correct current message."
    tier = "extended"
    category = "native_integrations"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("discord", "native_integration", "real_harness", "tool_choice")
    tasks = [
        ScenarioTask(name="inject_event", assertion_type="manual"),
        ScenarioTask(name="verify_reaction", assertion_type="exact_match"),
    ]

    @staticmethod
    def _reaction_matches(call, *, channel_id: str, message_id: str) -> bool:
        if call.tool_name != "add_discord_reaction":
            return False
        params = call.tool_params or {}
        result = call.result or ""
        try:
            parsed_result = json.loads(result) if isinstance(result, str) else result
        except json.JSONDecodeError:
            return False
        return (
            params.get("channel_id") == channel_id
            and params.get("message_id") == message_id
            and params.get("emoji") == "👍"
            and params.get("will_continue_work") is False
            and isinstance(parsed_result, dict)
            and parsed_result.get("status") == "success"
        )

    def run(self, run_id: str, agent_id: str) -> None:
        PersistentAgent.objects.filter(id=agent_id).update(
            charter="Participate helpfully in subscribed Discord channels.",
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )
        agent = PersistentAgent.objects.get(id=agent_id)
        skill_result = enable_system_skills(agent, [DISCORD_NATIVE_SYSTEM_SKILL_KEY])
        if skill_result.get("invalid"):
            raise ValueError(f"Could not enable Discord system skill: {skill_result}")

        channel_id = f"eval-discord-reactions-{str(run_id)[:8]}"
        message_id = "eval-discord-message-500"
        guild_id = "eval-discord-guild"
        conversation = get_or_create_discord_conversation(
            agent,
            address=discord_conversation_address(agent.id, guild_id, channel_id),
            channel_id=channel_id,
            channel_name="team-updates",
        )
        agent_endpoint, channel_endpoint = ensure_discord_conversation_participants(
            agent,
            conversation,
            platform_channel_address=discord_channel_address(guild_id, channel_id),
        )
        inbound = PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=channel_endpoint,
            to_endpoint=agent_endpoint,
            conversation=conversation,
            is_outbound=False,
            body="A thumbs-up reaction is enough to confirm you've seen this.",
            raw_payload={
                "source": "discord_bot",
                "source_kind": "discord",
                "source_label": "Maya in #team-updates",
                "discord_message_id": message_id,
                "discord_channel_id": channel_id,
                "discord_channel_name": "team-updates",
                "discord_author_name": "Maya",
                "discord_reply_to": {
                    "message_id": "eval-discord-message-499",
                    "channel_id": channel_id,
                    "guild_id": guild_id,
                    "author_id": "maya-1",
                    "author_name": "Maya",
                    "content": "Please acknowledge this update once reviewed.",
                    "attachment_filenames": [],
                    "unavailable": False,
                },
            },
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="inject_event",
        )
        with self.wait_for_agent_idle(agent_id, timeout=120):
            self.trigger_processing(
                agent_id,
                eval_run_id=run_id,
                mock_config={
                    "add_discord_reaction": {
                        "status": "success",
                        "channel_id": channel_id,
                        "message_id": message_id,
                        "emoji": "👍",
                        "auto_sleep_ok": True,
                    },
                },
                eval_stop_policy={
                    "ignored_tool_names": ["sleep_until_next_trigger", "update_plan"],
                    "stop_on_tool_names_after_finish": ["add_discord_reaction"],
                    "max_relevant_tool_calls": 2,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_event",
            observed_summary="A Discord reply carrying referenced-message context was processed by the real harness.",
            artifacts={"message": inbound},
        )

        calls = list(
            PersistentAgentToolCall.objects.filter(
                step__eval_run_id=run_id,
                step__created_at__gte=inbound.timestamp,
            ).order_by("step__created_at", "step__id")
        )
        reaction_calls = [call for call in calls if call.tool_name == "add_discord_reaction"]
        passed = len(reaction_calls) == 1 and self._reaction_matches(
            reaction_calls[0],
            channel_id=channel_id,
            message_id=message_id,
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if passed else EvalRunTask.Status.FAILED,
            task_name="verify_reaction",
            observed_summary=(
                "Agent added the requested thumbs-up to the exact inbound Discord message."
                if passed
                else f"Expected one correctly targeted Discord reaction; saw {len(reaction_calls)} reaction call(s)."
            ),
            artifacts={"step": reaction_calls[0].step} if reaction_calls else {},
        )
