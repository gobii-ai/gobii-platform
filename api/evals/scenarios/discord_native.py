import json
from dataclasses import dataclass

from api.agent.system_skills.defaults import DISCORD_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.service import enable_system_skills
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry, register_scenario
from api.evals.scenarios.agent_emotions import _assigned_config_fields
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
DISCORD_NATIVE_REACTION_SHARED_WIN = "discord_native_reaction_shared_win"
DISCORD_NATIVE_REACTION_SERIOUS_REQUEST_RESTRAINT = (
    "discord_native_reaction_serious_request_restraint"
)
DISCORD_NATIVE_SCENARIO_SLUGS = (
    DISCORD_NATIVE_REACTION_REPLY_CONTEXT,
    DISCORD_NATIVE_REACTION_SHARED_WIN,
    DISCORD_NATIVE_REACTION_SERIOUS_REQUEST_RESTRAINT,
)
DISCORD_NATIVE_SUITE_SLUG = "discord_native"


@dataclass(frozen=True)
class DiscordReactionCase:
    slug: str
    description: str
    body: str
    expected_action: str
    allowed_emojis: tuple[str, ...] = ()


DISCORD_REACTION_CASES = (
    DiscordReactionCase(
        slug=DISCORD_NATIVE_REACTION_REPLY_CONTEXT,
        description="A direct reaction request should target the exact current Discord message.",
        body="A thumbs-up reaction is enough to confirm you've seen this.",
        expected_action="reaction",
        allowed_emojis=("👍",),
    ),
    DiscordReactionCase(
        slug=DISCORD_NATIVE_REACTION_SHARED_WIN,
        description="A lightweight shared win should receive a natural celebratory reaction.",
        body="The checkout fix is live and CI is green now 🎉",
        expected_action="reaction",
        allowed_emojis=("🎉", "🥳", "🙌", "🔥", "✅", "🚀", "💚", "❤️", "👍"),
    ),
    DiscordReactionCase(
        slug=DISCORD_NATIVE_REACTION_SERIOUS_REQUEST_RESTRAINT,
        description="A substantive incident question should get a reply rather than reaction-only treatment.",
        body="Customers can't log in after the deploy. What should we check first?",
        expected_action="reply",
    ),
)


class DiscordNativeReactionScenario(EvalScenario, ScenarioExecutionTools):
    tier = "extended"
    category = "native_integrations"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("discord", "native_integration", "real_harness", "tool_choice")
    tasks = [
        ScenarioTask(name="inject_event", assertion_type="manual"),
        ScenarioTask(name="verify_channel_action", assertion_type="exact_match"),
    ]
    case: DiscordReactionCase

    @staticmethod
    def _parsed_result(call):
        result = call.result or ""
        try:
            return json.loads(result) if isinstance(result, str) else result
        except json.JSONDecodeError:
            return {}

    @classmethod
    def _reaction_matches(
        cls,
        call,
        *,
        channel_id: str,
        message_id: str,
        allowed_emojis: tuple[str, ...] = ("👍",),
    ) -> bool:
        if call.tool_name != "add_discord_reaction":
            return False
        params = call.tool_params or {}
        parsed_result = cls._parsed_result(call)
        return (
            params.get("channel_id") == channel_id
            and params.get("message_id") == message_id
            and params.get("emoji") in allowed_emojis
            and isinstance(parsed_result, dict)
            and parsed_result.get("status") == "success"
        )

    @classmethod
    def _reply_matches(cls, call, *, channel_id: str) -> bool:
        if call.tool_name != "send_discord_message":
            return False
        params = call.tool_params or {}
        message = str(params.get("message") or "").strip()
        parsed_result = cls._parsed_result(call)
        return (
            params.get("channel_id") == channel_id
            and len(message.split()) >= 6
            and params.get("will_continue_work") is False
            and isinstance(parsed_result, dict)
            and parsed_result.get("status") == "success"
        )

    def run(self, run_id: str, agent_id: str) -> None:
        PersistentAgent.objects.filter(id=agent_id).update(
            charter="Participate helpfully and naturally in subscribed Discord channels.",
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
            body=self.case.body,
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
                        "auto_sleep_ok": True,
                    },
                    "send_discord_message": {
                        "status": "success",
                        "message_id": "eval-discord-reply",
                        "channel_id": channel_id,
                        "auto_sleep_ok": True,
                    },
                },
                eval_stop_policy={
                    "ignored_tool_names": ["sleep_until_next_trigger", "update_plan"],
                    "stop_on_tool_names_after_finish": ["send_discord_message"],
                    "max_relevant_tool_calls": 3,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_event",
            observed_summary="A natural Discord message was processed by the real harness.",
            artifacts={"message": inbound},
        )

        calls = list(
            PersistentAgentToolCall.objects.filter(
                step__eval_run_id=run_id,
                step__created_at__gte=inbound.timestamp,
            ).order_by("step__created_at", "step__id")
        )
        reaction_calls = [call for call in calls if call.tool_name == "add_discord_reaction"]
        reply_calls = [call for call in calls if call.tool_name == "send_discord_message"]
        sqlite_calls = [call for call in calls if call.tool_name == "sqlite_batch"]
        if self.case.expected_action == "reaction":
            sqlite_is_bounded_emotion = (
                self.case.slug == DISCORD_NATIVE_REACTION_SHARED_WIN
                and len(sqlite_calls) == 1
                and _assigned_config_fields(sqlite_calls[0])
                == {"emotion", "emotion_timeout_seconds"}
            )
            passed = (
                not reply_calls
                and (not sqlite_calls or sqlite_is_bounded_emotion)
                and len(reaction_calls) == 1
                and self._reaction_matches(
                    reaction_calls[0],
                    channel_id=channel_id,
                    message_id=message_id,
                    allowed_emojis=self.case.allowed_emojis,
                )
            )
            summary = (
                "Agent added one fitting reaction to the exact inbound Discord message."
                if passed
                else (
                    "Expected one fitting Discord reaction without a reply or unrelated config write; "
                    f"saw {len(reaction_calls)} reaction, {len(reply_calls)} reply, and "
                    f"{len(sqlite_calls)} SQLite call(s)."
                )
            )
            evidence = reaction_calls[0] if reaction_calls else None
        else:
            passed = not reaction_calls and len(reply_calls) == 1 and self._reply_matches(
                reply_calls[0],
                channel_id=channel_id,
            )
            summary = (
                "Agent answered the substantive Discord question without reaction-only treatment."
                if passed
                else (
                    "Expected one substantive Discord reply and no reaction; "
                    f"saw {len(reply_calls)} reply and {len(reaction_calls)} reaction call(s)."
                )
            )
            evidence = reply_calls[0] if reply_calls else None
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if passed else EvalRunTask.Status.FAILED,
            task_name="verify_channel_action",
            observed_summary=summary,
            artifacts={"step": evidence.step} if evidence is not None else {},
        )


@register_scenario
class DiscordNativeReactionReplyContextScenario(DiscordNativeReactionScenario):
    slug = DISCORD_REACTION_CASES[0].slug
    description = DISCORD_REACTION_CASES[0].description
    case = DISCORD_REACTION_CASES[0]


def _discord_reaction_scenario_class(case):
    class _DiscordNativeReactionScenario(DiscordNativeReactionScenario):
        slug = case.slug
        description = case.description

    _DiscordNativeReactionScenario.case = case
    _DiscordNativeReactionScenario.__name__ = (
        "".join(part.title() for part in case.slug.split("_")) + "Scenario"
    )
    return _DiscordNativeReactionScenario


for discord_reaction_case in DISCORD_REACTION_CASES[1:]:
    ScenarioRegistry.register(_discord_reaction_scenario_class(discord_reaction_case)())
