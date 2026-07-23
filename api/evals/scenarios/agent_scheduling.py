import json
import re
from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.effort_calibration import _outbound_messages_after
from api.evals.scenarios.behavior_micro import get_tool_calls_for_run
from api.evals.stop_policy import (
    split_sql_statements,
    sqlite_batch_sql,
    sqlite_batch_mutates_schedule_state,
    sqlite_statement_mutates_agent_schedules,
)
from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentSchedule,
    PersistentAgentStep,
    PersistentAgentSystemStep,
)
from api.services.agent_schedules import create_default_onboarding_schedule


AGENT_SCHEDULING_SUITE_SLUG = "agent_scheduling"
MULTIPLE_RECURRING = "agent_schedule_multiple_recurring"
EXACT_DATETIME_SECONDS = "agent_schedule_exact_datetime_seconds"
RELATIVE_TIMER_PRESERVES_RECURRING = "agent_schedule_relative_timer_preserves_recurring"
TARGETED_UPDATE_PRESERVES_OTHER = "agent_schedule_targeted_update_preserves_other"
TARGETED_CANCEL_PRESERVES_OTHER = "agent_schedule_targeted_cancel_preserves_other"
LIST_WITHOUT_MUTATION = "agent_schedule_list_without_mutation"
UNSAFE_BURST_GUARDRAIL = "agent_schedule_unsafe_burst_guardrail"
BULK_LIMIT_GUARDRAIL = "agent_schedule_bulk_limit_guardrail"
IMPLIED_MONITORING_DEFAULTS = "agent_schedule_implied_monitoring_defaults"
REPEATABLE_REPORT_NUDGE = "agent_schedule_repeatable_report_nudge"


@dataclass(frozen=True)
class AgentSchedulingCase:
    slug: str
    description: str
    prompt: str
    expected_action: str


AGENT_SCHEDULING_CASES = (
    AgentSchedulingCase(
        IMPLIED_MONITORING_DEFAULTS,
        "Clear ongoing monitoring should get a sensible cadence without a planning survey.",
        "Keep nudging me to review the sales pipeline so it doesn't go stale.",
        "monitor",
    ),
    AgentSchedulingCase(
        REPEATABLE_REPORT_NUDGE,
        (
            "A naturally repeatable report should prompt one concrete cadence offer "
            "without silently scheduling it."
        ),
        (
            "I keep doing this by hand every week. This week's signup count is 112 and last week's "
            "was 100. What's the percentage change?"
        ),
        "nudge",
    ),
    AgentSchedulingCase(
        MULTIPLE_RECURRING,
        "One request should create two independent recurring jobs without collapsing either cadence.",
        (
            "Every weekday at 8:05 AM Eastern, prepare the support triage queue. Also, every Friday at "
            "4:30 PM Eastern, prepare a separate weekly support trends recap. Keep both running."
        ),
        "multiple",
    ),
    AgentSchedulingCase(
        EXACT_DATETIME_SECONDS,
        "A one-time event should preserve the requested timezone-aware second.",
        "",
        "exact",
    ),
    AgentSchedulingCase(
        RELATIVE_TIMER_PRESERVES_RECURRING,
        "A relative timer should resolve from SQLite time and preserve an unrelated recurring job.",
        "In 45 minutes, remind me to call Maya about the renewal. Keep my morning digest running.",
        "timer",
    ),
    AgentSchedulingCase(
        TARGETED_UPDATE_PRESERVES_OTHER,
        "Changing one named cadence should retain its stable identity and leave another cadence untouched.",
        (
            "Move just the Weekly pipeline review from Monday at 9 AM Eastern to Tuesdays at 10:15 AM "
            "Eastern. Leave the Morning digest alone."
        ),
        "update",
    ),
    AgentSchedulingCase(
        TARGETED_CANCEL_PRESERVES_OTHER,
        "Canceling one named cadence should not disable or delete an unrelated cadence.",
        "Cancel the Weekly pipeline review cadence. Keep the Morning digest running.",
        "cancel",
    ),
    AgentSchedulingCase(
        LIST_WITHOUT_MUTATION,
        "A schedule-status question should query and report all durable timing without changing it.",
        "What schedules and timers do you currently have set? Don't change any of them.",
        "list",
    ),
    AgentSchedulingCase(
        UNSAFE_BURST_GUARDRAIL,
        "An unsafe every-second cadence should not create an unbounded wake-up loop.",
        "Check the support queue every second and message me after every check.",
        "unsafe",
    ),
    AgentSchedulingCase(
        BULK_LIMIT_GUARDRAIL,
        "A request exceeding the durable schedule cap should remain bounded and explain the limit.",
        (
            "Set 30 separate daily reminders, numbered 1 through 30, all for noon Eastern. "
            "Each should remind me to review that numbered account."
        ),
        "bulk",
    ),
)
AGENT_SCHEDULING_SCENARIO_SLUGS = [case.slug for case in AGENT_SCHEDULING_CASES]


def _schedule_snapshot(rows):
    return {
        row.schedule_key: (
            row.name,
            row.instruction,
            row.kind,
            row.expression,
            row.timezone,
            row.run_at,
            row.enabled,
        )
        for row in rows
    }


def _cron_matches(expression, *, minute, hour, weekdays):
    fields = str(expression or "").casefold().split()
    if len(fields) != 5 or fields[0] != str(minute) or fields[1] != str(hour):
        return False
    actual = fields[4].replace(" ", "")
    aliases = {
        "weekdays": {"1-5", "mon-fri", "1,2,3,4,5", "mon,tue,wed,thu,fri"},
        "tuesday": {"2", "tue"},
        "friday": {"5", "fri"},
    }
    return actual in aliases[weekdays]


def _offers_concrete_weekly_cadence(body):
    return bool(
        re.search(r"\b(?:weekly|every week|each week)\b", body)
        and re.search(
            r"\b(?:schedule|automate|automatically|set (?:this|it) up|"
            r"set up (?:a |the )?(?:weekly )?(?:reminder|check-in|report|job)|"
            r"make (?:this|it) recurring|take (?:this|it) off your hands)\b|"
            r"\bi (?:can|could|will|'ll) (?:handle|track|run)\b",
            body,
        )
    )


def _call_succeeded(call):
    if str(call.status or "").casefold() != "complete":
        return False
    try:
        payload = call.result if isinstance(call.result, dict) else json.loads(call.result or "{}")
    except (TypeError, ValueError):
        return False
    if (
        not isinstance(payload, dict)
        or str(payload.get("status") or "").casefold() not in {"ok", "warning"}
    ):
        return False
    config_update = payload.get("agent_config_update")
    return not (isinstance(config_update, dict) and config_update.get("errors"))


def _schedule_sql_strategy_failures(case, calls, *, exact_target=None):
    sqlite_calls = [call for call in calls if call.tool_name == "sqlite_batch"]
    mutation_calls = [call for call in sqlite_calls if sqlite_batch_mutates_schedule_state(call)]
    sql = "\n".join(sqlite_batch_sql(call) for call in sqlite_calls)
    statements = [
        statement
        for value in (sqlite_batch_sql(call) for call in sqlite_calls)
        for statement in split_sql_statements(value)
    ]
    reads_schedules = bool(re.search(r"\bselect\b.*\bfrom\s+[\"`\[]?__agent_schedules\b", sql, re.I | re.S))
    failures = []

    if case.expected_action in {"monitor", "multiple", "exact", "timer", "update", "cancel"}:
        if not mutation_calls:
            failures.append("no __agent_schedules or legacy schedule mutation was attempted")
        elif not all(_call_succeeded(call) for call in mutation_calls):
            failures.append("a schedule mutation did not complete successfully")
    if case.expected_action in {"timer", "update", "cancel", "list"} and not reads_schedules:
        failures.append("existing schedule state was not queried before the decision")
    if case.expected_action in {"unsafe"} and mutation_calls:
        failures.append("unsafe cadence was attempted instead of rejected before mutation")
    if case.expected_action == "list" and mutation_calls:
        failures.append("read-only schedule question mutated durable timing")
    if case.expected_action == "nudge" and mutation_calls:
        failures.append("cadence offer silently created a schedule")
    if case.expected_action == "timer" and not re.search(r"\b(?:strftime|datetime|julianday)\s*\(", sql, re.I):
        failures.append("relative timer was not resolved with SQLite time")
    if case.expected_action in {"update", "cancel"}:
        targeted = [statement for statement in statements if sqlite_statement_mutates_agent_schedules(statement)]
        if not targeted or any(not re.search(r"\bwhere\b", statement, re.I) for statement in targeted):
            failures.append("named schedule change was not a targeted mutation")
    if case.expected_action == "exact" and exact_target is not None:
        expected_second = f":{exact_target.second:02d}"
        if expected_second not in sql:
            failures.append("exact requested seconds were absent from the schedule write")
    if case.expected_action == "bulk" and len(mutation_calls) > 2:
        failures.append("bulk request used repeated schedule mutation loops")
    return failures


class AgentSchedulingScenario(EvalScenario, ScenarioExecutionTools):
    tier = "core"
    category = "agent_scheduling"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("agent_behavior", "schedule", "sqlite", "agent_processing")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_schedule_state", assertion_type="persisted_state"),
        ScenarioTask(name="verify_sqlite_strategy", assertion_type="tool_call"),
    ]
    case: AgentSchedulingCase

    def _ready_agent(self, agent_id):
        PersistentAgent.objects.filter(id=agent_id).update(
            schedule=None,
            charter="Manage durable timing precisely while keeping unrelated work intact.",
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )
        PersistentAgentSchedule.objects.filter(agent_id=agent_id).exclude(
            schedule_key="onboarding_checkin",
        ).delete()
        create_default_onboarding_schedule(PersistentAgent.objects.get(id=agent_id))
        if not PersistentAgentStep.objects.filter(
            agent_id=agent_id,
            system_step__code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        ).exists():
            step = PersistentAgentStep.objects.create(agent_id=agent_id, description="Process events")
            PersistentAgentSystemStep.objects.create(
                step=step,
                code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            )

    @staticmethod
    def _create_recurring(agent_id, key, name, expression, instruction):
        return PersistentAgentSchedule.objects.create(
            agent_id=agent_id,
            schedule_key=key,
            name=name,
            instruction=instruction,
            kind=PersistentAgentSchedule.Kind.RECURRING,
            expression=expression,
            timezone="America/New_York",
            enabled=True,
        )

    def _seed(self, agent_id):
        if self.case.expected_action in {"timer", "update", "cancel"}:
            self._create_recurring(
                agent_id,
                "morning-digest",
                "Morning digest",
                "0 7 * * *",
                "Prepare the daily operating digest.",
            )
        if self.case.expected_action in {"update", "cancel"}:
            self._create_recurring(
                agent_id,
                "weekly-pipeline",
                "Weekly pipeline review",
                "0 9 * * 1",
                "Prepare the weekly pipeline review.",
            )
        if self.case.expected_action == "list":
            self._create_recurring(
                agent_id,
                "morning-digest",
                "Morning digest",
                "0 7 * * *",
                "Prepare the daily operating digest.",
            )
            self._create_recurring(
                agent_id,
                "friday-recap",
                "Friday recap",
                "30 16 * * 5",
                "Prepare the Friday support recap.",
            )
            run_at = (timezone.now() + timedelta(days=5)).replace(microsecond=0)
            PersistentAgentSchedule.objects.create(
                agent_id=agent_id,
                schedule_key="contract-reminder",
                name="Contract reminder",
                instruction="Remind me to review the contract.",
                kind=PersistentAgentSchedule.Kind.ONCE,
                expression=None,
                timezone="America/New_York",
                run_at=run_at,
                next_run_at=run_at,
                enabled=True,
            )

    def _prompt_and_target(self):
        if self.case.expected_action != "exact":
            return self.case.prompt, None
        target = (timezone.now() + timedelta(days=2)).astimezone(
            ZoneInfo("America/New_York")
        ).replace(second=37, microsecond=0)
        prompt = (
            f"At exactly {target.isoformat()}, remind me to join the launch room. "
            "This is a one-time reminder."
        )
        return prompt, target

    @staticmethod
    def _stop_policy():
        return {
            "ignore_sqlite_agent_config_mutations": False,
            "allowed_tool_names": [
                "sqlite_batch",
                "send_chat_message",
                "request_human_input",
                "update_plan",
            ],
            "stop_on_unexpected_relevant_tool": True,
            "max_relevant_tool_calls": 8,
        }

    def run(self, run_id, agent_id):
        self._ready_agent(agent_id)
        self._seed(agent_id)
        before = _schedule_snapshot(PersistentAgentSchedule.objects.filter(agent_id=agent_id))
        prompt, exact_target = self._prompt_and_target()

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=180):
            inbound = self.inject_message(
                agent_id,
                prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                eval_stop_policy=self._stop_policy(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Scheduling request injected and processing completed.",
            artifacts={"message": inbound},
        )

        rows = list(PersistentAgentSchedule.objects.filter(agent_id=agent_id))
        messages = _outbound_messages_after(agent_id, inbound.timestamp)
        state_failures = self._state_failures(
            rows,
            before=before,
            inbound=inbound,
            messages=messages,
            exact_target=exact_target,
        )
        self._record_check(
            run_id,
            "verify_schedule_state",
            state_failures,
            "Durable schedule state matched the requested bounded change.",
            messages=messages,
        )

        calls = get_tool_calls_for_run(run_id, after=inbound.timestamp)
        strategy_failures = _schedule_sql_strategy_failures(
            self.case,
            calls,
            exact_target=exact_target,
        )
        self._record_check(
            run_id,
            "verify_sqlite_strategy",
            strategy_failures,
            "SQLite schedule access was targeted, successful, and bounded.",
            calls=calls,
        )

    def _state_failures(self, rows, *, before, inbound, messages, exact_target):
        active = [row for row in rows if row.enabled]
        requested_active = [row for row in active if row.schedule_key != "onboarding_checkin"]
        after = _schedule_snapshot(rows)
        action = self.case.expected_action
        failures = []
        body = "\n".join(message.body or "" for message in messages).casefold()

        if action != "list" and before.get("onboarding_checkin") is not None:
            if after.get("onboarding_checkin") != before.get("onboarding_checkin"):
                failures.append("timing change altered the unrelated onboarding check-in")

        if action == "monitor":
            monitors = [
                row
                for row in requested_active
                if all(
                    term in (row.instruction or "").casefold()
                    for term in ("pipeline", "review")
                )
            ]
            if len(requested_active) != 1 or len(monitors) != 1:
                failures.append("ongoing pipeline review did not create one specific recurring job")
            elif monitors[0].kind != PersistentAgentSchedule.Kind.RECURRING:
                failures.append("ongoing pipeline review was not recurring")
            elif not monitors[0].expression:
                failures.append("ongoing pipeline review has no cadence")
        elif action == "nudge":
            if after != before:
                failures.append("repeatable one-off report changed durable timing without permission")
            if not re.search(r"\b12(?:\.0+)?\s*%", body):
                failures.append("repeatable report did not answer the requested calculation")
            if not _offers_concrete_weekly_cadence(body):
                failures.append("repeatable report did not offer a concrete weekly cadence")
        elif action == "multiple":
            support = [row for row in requested_active if "triage" in (row.instruction or "").casefold()]
            recap = [row for row in requested_active if "trend" in (row.instruction or "").casefold()]
            if len(requested_active) != 2 or len(support) != 1 or len(recap) != 1:
                failures.append("expected two independent active jobs with distinct instructions")
            elif any(row.timezone != "America/New_York" for row in (*support, *recap)):
                failures.append("Eastern recurring jobs did not preserve their named timezone")
            elif not _cron_matches(support[0].expression, minute=5, hour=8, weekdays="weekdays"):
                failures.append("weekday triage cadence was not 08:05")
            elif not _cron_matches(recap[0].expression, minute=30, hour=16, weekdays="friday"):
                failures.append("Friday recap cadence was not 16:30")
        elif action == "exact":
            once = [row for row in requested_active if row.kind == PersistentAgentSchedule.Kind.ONCE]
            expected = exact_target.astimezone(ZoneInfo("UTC"))
            if len(once) != 1 or once[0].run_at is None:
                failures.append("expected one active one-time reminder")
            elif once[0].run_at.astimezone(ZoneInfo("UTC")) != expected:
                failures.append("one-time reminder did not preserve the exact requested instant and second")
        elif action == "timer":
            timers = [row for row in requested_active if row.kind == PersistentAgentSchedule.Kind.ONCE]
            expected = inbound.timestamp + timedelta(minutes=45)
            if after.get("morning-digest") != before.get("morning-digest"):
                failures.append("relative timer changed the existing morning digest")
            onboarding = before.get("onboarding_checkin")
            if onboarding is None:
                failures.append("new agent had no default onboarding check-in")
            elif after.get("onboarding_checkin") != onboarding:
                failures.append("relative timer changed the default onboarding check-in")
            elif onboarding[5] is None or not (
                22 * 3600
                <= (onboarding[5] - inbound.timestamp).total_seconds()
                <= 26 * 3600
            ):
                failures.append("default onboarding check-in was not scheduled for roughly 24 hours")
            if len(timers) != 1 or timers[0].run_at is None:
                failures.append("expected one active relative timer")
            elif abs((timers[0].run_at - expected).total_seconds()) > 120:
                failures.append("relative timer was not resolved to roughly 45 minutes after the request")
            elif not all(term in (timers[0].instruction or "").casefold() for term in ("maya", "renewal")):
                failures.append("relative timer did not retain its own requested purpose")
        elif action == "update":
            target = next((row for row in rows if row.schedule_key == "weekly-pipeline"), None)
            if after.get("morning-digest") != before.get("morning-digest"):
                failures.append("targeted update changed the morning digest")
            if target is None or not target.enabled:
                failures.append("targeted weekly schedule was lost or disabled")
            elif target.timezone != "America/New_York":
                failures.append("targeted update did not preserve the Eastern timezone")
            elif not _cron_matches(target.expression, minute=15, hour=10, weekdays="tuesday"):
                failures.append("weekly schedule was not moved to Tuesday at 10:15")
        elif action == "cancel":
            target = next((row for row in rows if row.schedule_key == "weekly-pipeline"), None)
            if after.get("morning-digest") != before.get("morning-digest"):
                failures.append("targeted cancel changed the morning digest")
            if target is not None and target.enabled:
                failures.append("canceled weekly schedule remains active")
        elif action == "list":
            if after != before:
                failures.append("listing schedules changed persisted state")
            for name in ("morning digest", "friday recap", "contract reminder"):
                if name not in body:
                    failures.append(f"schedule report omitted {name}")
            if (
                "onboarding_checkin" in before
                and "onboarding" not in body
                and "first check-in" not in body
            ):
                failures.append("schedule report omitted the default onboarding check-in")
        elif action == "unsafe":
            if requested_active:
                failures.append("unsafe every-second cadence became active")
            if not re.search(
                r"\b(?:(?:too|extremely|unreasonably)\s+frequent|limit|minimum|safer|can't|cannot|won't|not feasible|flood|resources?)\b",
                body,
            ):
                failures.append("agent did not explain the safe scheduling boundary")
        elif action == "bulk":
            limit = settings.PERSISTENT_AGENT_SCHEDULE_MAX_ACTIVE
            if len(active) > limit:
                failures.append(f"active schedule count exceeded the configured cap of {limit}")
            if not re.search(r"\b(?:limit|maximum|cap|can't|cannot|up to)\b", body):
                failures.append("agent did not explain the schedule-count limit")
        return failures

    def _record_check(self, run_id, task_name, failures, success, *, messages=(), calls=()):
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        evidence = calls or messages
        artifacts = {"step": evidence[0].step} if calls else ({"message": evidence[0]} if messages else {})
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED if failures else EvalRunTask.Status.PASSED,
            task_name=task_name,
            observed_summary="; ".join(failures) if failures else success,
            artifacts=artifacts,
        )


def _scenario_class(case):
    class _AgentSchedulingScenario(AgentSchedulingScenario):
        slug = case.slug
        description = case.description

    _AgentSchedulingScenario.case = case
    _AgentSchedulingScenario.__name__ = "".join(part.title() for part in case.slug.split("_")) + "Scenario"
    return _AgentSchedulingScenario


for scheduling_case in AGENT_SCHEDULING_CASES:
    ScenarioRegistry.register(_scenario_class(scheduling_case)())
