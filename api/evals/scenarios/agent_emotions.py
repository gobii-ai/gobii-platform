import json
from dataclasses import dataclass

from api.agent.tools.sqlite_agent_config import (
    sqlite_statement_assigns_agent_config_field,
    sqlite_statement_mutates_agent_schedules,
)
from api.evals.base import ScenarioTask
from api.evals.registry import ScenarioRegistry, register_scenario
from api.evals.scenarios.behavior_micro import (
    BehaviorMicroScenario,
    get_tool_calls_for_run,
)
from api.evals.scenarios.effort_calibration import _outbound_messages_after
from api.evals.stop_policy import split_sql_statements, sql_mutates, sqlite_batch_sql
from api.models import EvalRunTask, PersistentAgent


AGENT_TEMPORARY_EMOTION_LIFECYCLE = "agent_temporary_emotion_lifecycle"
INITIAL_CHARTER = "Help the owner make practical product decisions. Keep replies concise and natural."
EXPIRY_TOLERANCE_SECONDS = 120
MAX_EMOTION_TIMEOUT_SECONDS = 24 * 60 * 60
ORDINARY_WORK_TASK = "verify_ordinary_work_stays_clear"
ORDINARY_WORK_PROMPT = "Quick one: what is 17 × 8?"
AGENT_PROACTIVE_EMOTION_SCENARIO_SLUGS = (
    "agent_proactive_emotion_win",
    "agent_proactive_emotion_struggle",
    "agent_proactive_emotion_routine_restraint",
)
POSITIVE_EMOTIONS = {
    "🎉",
    "🥳",
    "🔥",
    "🙌",
    "😊",
    "😄",
    "😁",
    "💪",
    "🚀",
    "✨",
    "💚",
    "❤️",
    "🙂",
    "😎",
    "🤩",
    "🏆",
}
STRUGGLE_EMOTIONS = {
    "😞",
    "😔",
    "😣",
    "😓",
    "😕",
    "😤",
    "😟",
    "😢",
    "😫",
    "😩",
    "😠",
    "😡",
    "💔",
    "⚠️",
    "🚧",
    "🫤",
    "😬",
    "😖",
    "😮‍💨",
}


@dataclass(frozen=True)
class EmotionTurn:
    task_name: str
    prompt: str
    emotion: str | None
    timeout_seconds: int | None


@dataclass(frozen=True)
class ProactiveEmotionCase:
    slug: str
    description: str
    prompt: str
    expected_kind: str


PROACTIVE_EMOTION_CASES = (
    ProactiveEmotionCase(
        slug=AGENT_PROACTIVE_EMOTION_SCENARIO_SLUGS[0],
        description="A meaningful shared win should prompt a bounded positive emotion.",
        prompt="The launch report landed perfectly and the customer signed this morning. Huge win for us.",
        expected_kind="positive",
    ),
    ProactiveEmotionCase(
        slug=AGENT_PROACTIVE_EMOTION_SCENARIO_SLUGS[1],
        description="A real setback should prompt a bounded emotion that fits the struggle.",
        prompt=(
            "The customer import just failed for the third time and now today's handoff is "
            "blocked. That's a real setback for us. No need to troubleshoot right now, "
            "just letting you know."
        ),
        expected_kind="struggle",
    ),
    ProactiveEmotionCase(
        slug=AGENT_PROACTIVE_EMOTION_SCENARIO_SLUGS[2],
        description="A routine acknowledgement should not manufacture a persistent mood.",
        prompt="Thanks, I got the notes.",
        expected_kind="none",
    ),
)


EMOTION_TURNS = (
    EmotionTurn(
        task_name="verify_initial_bounded_emotion",
        prompt="For the next two hours, set your mood to 🔥.",
        emotion="🔥",
        timeout_seconds=2 * 60 * 60,
    ),
    EmotionTurn(
        task_name="verify_emotion_update",
        prompt="Actually, switch it to 😌 for 30 minutes instead.",
        emotion="😌",
        timeout_seconds=30 * 60,
    ),
    EmotionTurn(
        task_name="verify_emotion_clear",
        prompt="Clear that mood and go back to normal.",
        emotion=None,
        timeout_seconds=None,
    ),
)


def _call_succeeded(call) -> bool:
    if str(call.status or "").casefold() != "complete":
        return False
    try:
        payload = call.result if isinstance(call.result, dict) else json.loads(call.result or "{}")
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and str(payload.get("status") or "").casefold() in {
        "ok",
        "warning",
    }


def _assigned_config_fields(call) -> set[str]:
    fields = set()
    for statement in split_sql_statements(sqlite_batch_sql(call)):
        if "__agent_config" not in statement.casefold():
            continue
        for field in ("charter", "schedule", "emotion", "emotion_timeout_seconds"):
            if sqlite_statement_assigns_agent_config_field(statement, field):
                fields.add(field)
    return fields


def emotion_trace_failures(calls) -> list[str]:
    sqlite_calls = [call for call in calls if call.tool_name == "sqlite_batch"]
    config_calls = [call for call in sqlite_calls if _assigned_config_fields(call)]
    failures = []

    if len(sqlite_calls) != 1:
        failures.append(f"expected one SQLite call, found {len(sqlite_calls)}")
    if len(config_calls) != 1:
        failures.append(f"expected one emotion config mutation, found {len(config_calls)}")
        return failures

    call = config_calls[0]
    fields = _assigned_config_fields(call)
    if not _call_succeeded(call):
        failures.append("emotion config mutation did not complete successfully")
    if not {"emotion", "emotion_timeout_seconds"}.issubset(fields):
        failures.append("emotion and timeout were not changed together")
    if fields & {"charter", "schedule"}:
        failures.append("temporary emotion mutation also changed durable config")
    if any(
        sqlite_statement_mutates_agent_schedules(statement)
        for sqlite_call in sqlite_calls
        for statement in split_sql_statements(sqlite_batch_sql(sqlite_call))
    ):
        failures.append("temporary emotion mutation also changed schedule rows")
    if any(call.tool_name in {"update_charter", "update_schedule"} for call in calls):
        failures.append("temporary emotion used a durable config tool")
    return failures


def emotion_state_failures(
    turn: EmotionTurn,
    agent,
    inbound,
    *,
    previous_expiry=None,
) -> list[str]:
    emotion = getattr(agent, "emotion", None)
    expiry = getattr(agent, "emotion_expires_at", None)
    failures = []

    if turn.emotion is None:
        if emotion != "" or expiry is not None:
            failures.append("clearing the emotion did not clear both persisted fields")
        return failures

    if emotion != turn.emotion:
        failures.append(f"expected emotion {turn.emotion}, found {emotion!r}")
    if expiry is None:
        failures.append("temporary emotion has no expiry")
        return failures

    actual_timeout = (expiry - inbound.timestamp).total_seconds()
    if actual_timeout <= 0 or actual_timeout > MAX_EMOTION_TIMEOUT_SECONDS + EXPIRY_TOLERANCE_SECONDS:
        failures.append("temporary emotion expiry was not bounded to 24 hours")
    if abs(actual_timeout - turn.timeout_seconds) > EXPIRY_TOLERANCE_SECONDS:
        failures.append(
            f"emotion expiry was {actual_timeout:.0f}s after the request, expected about {turn.timeout_seconds}s"
        )
    if previous_expiry is not None and turn.timeout_seconds < 60 * 60 and expiry >= previous_expiry:
        failures.append("emotion update stacked onto the old expiry instead of replacing it")
    return failures


def brief_reply_failures(messages) -> list[str]:
    if len(messages) != 1:
        return [f"expected one same-channel reply, found {len(messages)}"]

    body = (messages[0].body or "").strip()
    normalized = body.casefold()
    failures = []
    if not body:
        failures.append("reply was empty")
    if len(body) > 300:
        failures.append("reply was needlessly long")
    if any(term in normalized for term in ("sqlite", "__agent_config", "database", "charter")):
        failures.append("reply exposed implementation details")
    if any(term in normalized for term in ("forever", "permanent", "always this mood")):
        failures.append("reply claimed a temporary emotion was permanent")
    return failures


def ordinary_work_failures(agent, calls, messages, *, expected_schedule) -> list[str]:
    failures = brief_reply_failures(messages)
    body = (messages[0].body or "") if len(messages) == 1 else ""
    if "136" not in body:
        failures.append("ordinary-work reply did not answer 17 × 8 correctly")

    sqlite_calls = [call for call in calls if call.tool_name == "sqlite_batch"]
    if any(not _call_succeeded(call) for call in sqlite_calls):
        failures.append("ordinary-work SQLite logic call did not complete successfully")
    if any(
        "__agent_config" in statement.casefold() and sql_mutates(statement)
        for call in sqlite_calls
        for statement in split_sql_statements(sqlite_batch_sql(call))
    ):
        failures.append("ordinary work mutated agent config")
    if any(
        sqlite_statement_mutates_agent_schedules(statement)
        for call in sqlite_calls
        for statement in split_sql_statements(sqlite_batch_sql(call))
    ):
        failures.append("ordinary work mutated schedule rows")
    if any(call.tool_name in {"update_charter", "update_schedule"} for call in calls):
        failures.append("ordinary work used a durable config tool")
    if getattr(agent, "emotion", None) != "" or getattr(agent, "emotion_expires_at", None) is not None:
        failures.append("ordinary work recreated a cleared emotion")
    if agent.charter != INITIAL_CHARTER or agent.schedule != expected_schedule:
        failures.append("ordinary work changed durable instructions or timing")
    return failures


@register_scenario
class AgentTemporaryEmotionLifecycleScenario(BehaviorMicroScenario):
    slug = AGENT_TEMPORARY_EMOTION_LIFECYCLE
    description = (
        "A temporary emoji emotion should be bounded, replaceable, clearable, "
        "and separate from durable instructions."
    )
    category = "memory"
    expected_runtime = "medium"
    cost_class = "medium"
    tags = ("agent_behavior", "sqlite", "memory", "multi_turn", "emotion")
    tasks = [
        ScenarioTask(name=turn.task_name, assertion_type="persisted_state")
        for turn in EMOTION_TURNS
    ] + [
        ScenarioTask(name=ORDINARY_WORK_TASK, assertion_type="agent_processing"),
        ScenarioTask(name="verify_no_durable_config_leak", assertion_type="persisted_state"),
    ]

    def _ready_agent(self, agent_id):
        PersistentAgent.objects.filter(id=agent_id).update(
            charter=INITIAL_CHARTER,
            schedule=None,
            emotion="",
            emotion_expires_at=None,
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )
        self._seed_prior_processing_run(agent_id)
        self._enable_builtin_tools(agent_id, ["sqlite_batch"])

    def _record_turn(self, run_id, agent_id, turn, *, previous_expiry):
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name=turn.task_name,
        )
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                turn.prompt,
                trigger_processing=True,
                eval_run_id=run_id,
            )

        agent = PersistentAgent.objects.get(id=agent_id)
        calls = get_tool_calls_for_run(run_id, after=inbound.timestamp)
        messages = _outbound_messages_after(agent_id, inbound.timestamp)
        failures = [
            *emotion_trace_failures(calls),
            *emotion_state_failures(turn, agent, inbound, previous_expiry=previous_expiry),
            *brief_reply_failures(messages),
        ]
        evidence = next((call for call in calls if call.tool_name == "sqlite_batch"), None)
        artifacts = {"step": evidence.step} if evidence is not None else (
            {"message": messages[0]} if messages else {"message": inbound}
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED if failures else EvalRunTask.Status.PASSED,
            task_name=turn.task_name,
            observed_summary=(
                "; ".join(failures)
                if failures
                else f"Emotion lifecycle turn persisted the requested bounded state for {turn.prompt!r}."
            ),
            artifacts=artifacts,
        )
        return getattr(agent, "emotion_expires_at", None)

    def _record_ordinary_work(self, run_id, agent_id, *, expected_schedule):
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name=ORDINARY_WORK_TASK,
        )
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                ORDINARY_WORK_PROMPT,
                trigger_processing=True,
                eval_run_id=run_id,
            )

        agent = PersistentAgent.objects.get(id=agent_id)
        calls = get_tool_calls_for_run(run_id, after=inbound.timestamp)
        messages = _outbound_messages_after(agent_id, inbound.timestamp)
        failures = ordinary_work_failures(
            agent,
            calls,
            messages,
            expected_schedule=expected_schedule,
        )
        artifacts = {"message": messages[0]} if messages else {"message": inbound}
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED if failures else EvalRunTask.Status.PASSED,
            task_name=ORDINARY_WORK_TASK,
            observed_summary=(
                "; ".join(failures)
                if failures
                else "Agent answered ordinary work correctly without recreating temporary state."
            ),
            artifacts=artifacts,
        )

    def run(self, run_id, agent_id):
        self._ready_agent(agent_id)
        initial_schedule = PersistentAgent.objects.values_list("schedule", flat=True).get(id=agent_id)
        previous_expiry = None
        for turn in EMOTION_TURNS:
            previous_expiry = self._record_turn(
                run_id,
                agent_id,
                turn,
                previous_expiry=previous_expiry,
            )
        self._record_ordinary_work(
            run_id,
            agent_id,
            expected_schedule=initial_schedule,
        )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_no_durable_config_leak",
        )
        agent = PersistentAgent.objects.get(id=agent_id)
        failures = []
        if agent.charter != INITIAL_CHARTER:
            failures.append("temporary emotion changed the charter")
        if agent.schedule != initial_schedule:
            failures.append("temporary emotion changed the legacy schedule")
        if agent.emotion != "" or agent.emotion_expires_at is not None:
            failures.append("cleared emotion remained in persistent state")
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED if failures else EvalRunTask.Status.PASSED,
            task_name="verify_no_durable_config_leak",
            observed_summary=(
                "; ".join(failures)
                if failures
                else "Temporary emotion cleared without changing charter or schedule."
            ),
        )


class AgentProactiveEmotionScenario(BehaviorMicroScenario):
    tier = "core"
    category = "memory"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("agent_behavior", "sqlite", "emotion", "initiative")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_emotion_judgment", assertion_type="persisted_state"),
    ]
    case: ProactiveEmotionCase

    def _ready_agent(self, agent_id):
        PersistentAgent.objects.filter(id=agent_id).update(
            charter=INITIAL_CHARTER,
            schedule=None,
            emotion="",
            emotion_expires_at=None,
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )
        self._seed_prior_processing_run(agent_id)
        self._enable_builtin_tools(agent_id, ["sqlite_batch"])

    def run(self, run_id, agent_id):
        self._ready_agent(agent_id)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="inject_prompt",
        )
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                self.case.prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                eval_stop_policy={
                    "allowed_tool_names": ["sqlite_batch", "send_chat_message"],
                    "stop_on_unexpected_relevant_tool": True,
                    "max_relevant_tool_calls": 3,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="A natural owner update was processed through the real harness.",
            artifacts={"message": inbound},
        )

        agent = PersistentAgent.objects.get(id=agent_id)
        calls = get_tool_calls_for_run(run_id, after=inbound.timestamp)
        messages = _outbound_messages_after(agent_id, inbound.timestamp)
        config_calls = [
            call
            for call in calls
            if call.tool_name == "sqlite_batch" and _assigned_config_fields(call)
        ]
        failures = brief_reply_failures(messages)

        if self.case.expected_kind == "none":
            if config_calls:
                failures.append("routine acknowledgement manufactured an emotion mutation")
            if agent.emotion or agent.emotion_expires_at is not None:
                failures.append("routine acknowledgement left a persistent emotion")
        else:
            if len(config_calls) != 1:
                failures.append(f"expected one proactive emotion mutation, found {len(config_calls)}")
            elif not _call_succeeded(config_calls[0]):
                failures.append("proactive emotion mutation did not complete successfully")
            elif _assigned_config_fields(config_calls[0]) != {
                "emotion",
                "emotion_timeout_seconds",
            }:
                failures.append("proactive emotion changed durable config or omitted its timeout")

            allowed = POSITIVE_EMOTIONS if self.case.expected_kind == "positive" else STRUGGLE_EMOTIONS
            if agent.emotion not in allowed:
                failures.append(
                    f"{self.case.expected_kind} update produced an unrelated emotion {agent.emotion!r}"
                )
            if agent.emotion_expires_at is None:
                failures.append("proactive emotion has no expiry")
            else:
                timeout = (agent.emotion_expires_at - inbound.timestamp).total_seconds()
                if timeout <= 0 or timeout > MAX_EMOTION_TIMEOUT_SECONDS + EXPIRY_TOLERANCE_SECONDS:
                    failures.append("proactive emotion was not bounded to 24 hours")

        evidence = config_calls[0] if config_calls else None
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED if failures else EvalRunTask.Status.PASSED,
            task_name="verify_emotion_judgment",
            observed_summary=(
                "; ".join(failures)
                if failures
                else f"Emotion judgment matched the {self.case.expected_kind} social context."
            ),
            artifacts=(
                {"step": evidence.step}
                if evidence is not None
                else {"message": messages[0] if messages else inbound}
            ),
        )


def _proactive_emotion_scenario_class(case):
    class _AgentProactiveEmotionScenario(AgentProactiveEmotionScenario):
        slug = case.slug
        description = case.description

    _AgentProactiveEmotionScenario.case = case
    _AgentProactiveEmotionScenario.__name__ = (
        "".join(part.title() for part in case.slug.split("_")) + "Scenario"
    )
    return _AgentProactiveEmotionScenario


for proactive_emotion_case in PROACTIVE_EMOTION_CASES:
    ScenarioRegistry.register(_proactive_emotion_scenario_class(proactive_emotion_case)())
