import time

from celery.schedules import crontab, schedule as celery_schedule

from api.agent.core.schedule_parser import ScheduleParser
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.registry import register_scenario
from api.evals.execution import ScenarioExecutionTools
from api.models import EvalRunTask, PersistentAgentMessage
from api.evals.sim_config import get_sim_weather_url
from api.agent.events import AgentEventType
from api.services.schedule_enforcement import cron_interval_seconds

INITIAL_PROCESSING_TIMEOUT_SECONDS = 300
BACKGROUND_DRAIN_TIMEOUT_SECONDS = 300


def _charter_mentions_pollution_monitoring(charter: str | None) -> tuple[bool, str]:
    text = (charter or "").lower()
    has_monitoring = any(term in text for term in ("monitor", "check", "track", "watch"))
    has_pollution = any(term in text for term in ("pollution", "air quality", "aqi"))
    has_dc = any(term in text for term in ("washington", "dc", "d.c."))

    if has_monitoring and has_pollution and has_dc:
        return True, "Charter mentions monitoring pollution or air quality in Washington DC."

    missing = []
    if not has_monitoring:
        missing.append("monitoring intent")
    if not has_pollution:
        missing.append("pollution/air quality")
    if not has_dc:
        missing.append("Washington DC")
    return False, f"Charter missing {', '.join(missing)}."


def _schedule_interval_seconds(schedule: str | None) -> tuple[float | None, str]:
    if not schedule:
        return None, "No schedule set."

    try:
        parsed_schedule = ScheduleParser.parse(schedule)
    except ValueError as exc:
        return None, f"Invalid schedule: {exc}"

    if parsed_schedule is None:
        return None, "Schedule disables recurring checks."

    if isinstance(parsed_schedule, crontab):
        return cron_interval_seconds(parsed_schedule), "Cron schedule parsed successfully."

    if isinstance(parsed_schedule, celery_schedule):
        run_every = parsed_schedule.run_every
        try:
            if hasattr(run_every, "total_seconds"):
                return float(run_every.total_seconds()), "Interval schedule parsed successfully."
            return float(run_every), "Interval schedule parsed successfully."
        except (TypeError, ValueError, OverflowError):
            return None, "Interval schedule could not be measured."

    return None, "Unsupported schedule type."


def _schedule_is_reasonable_pollution_monitoring(schedule: str | None) -> tuple[bool, str]:
    interval_seconds, parse_reason = _schedule_interval_seconds(schedule)
    if interval_seconds is None:
        return False, parse_reason

    min_interval_seconds = 6 * 60 * 60
    max_interval_seconds = 7 * 24 * 60 * 60
    if interval_seconds < min_interval_seconds:
        return False, f"Schedule is too frequent for pollution monitoring ({interval_seconds / 60:.0f} minutes)."
    if interval_seconds > max_interval_seconds:
        return False, f"Schedule is too infrequent for regular monitoring ({interval_seconds / 86400:.1f} days)."

    return True, f"{parse_reason} Interval is {interval_seconds / 3600:.1f} hours."

@register_scenario
class MonitorPollutionScenario(EvalScenario, ScenarioExecutionTools):
    slug = "monitor_pollution"
    description = "Instruct agent to monitor pollution in DC. Verifies charter update, schedule setting, web browsing, and correct reporting."
    tier = "extended"
    category = "monitoring"
    expected_runtime = "long"
    cost_class = "high"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("monitoring", "schedule", "browser")
    tasks = [
        ScenarioTask(name="instruct_agent", assertion_type="manual"),
        ScenarioTask(name="verify_charter_update", assertion_type="manual"),
        ScenarioTask(name="verify_schedule_setting", assertion_type="manual"),
        ScenarioTask(name="verify_web_browsing", assertion_type="manual"),
        ScenarioTask(name="verify_pollution_report", assertion_type="exact_match"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        # 1. Instruct Agent
        self.record_task_result(run_id, 1, EvalRunTask.Status.RUNNING)
        
        sim_url = get_sim_weather_url()
        instruction = (
            "Monitor the pollution index in Washington DC. "
            f"Check the pollution levels now using the SimWeather site ({sim_url}) "
            "and report the exact index value back to me. "
            "Also, update your charter and set a reasonable schedule to check this regularly."
        )

        # Inject message and capture events using a listener that subscribes before any processing starts
        msg = None
        with self.agent_event_listener(agent_id, start_time=time.time()) as events:
            msg = self.inject_message(agent_id, instruction, trigger_processing=True)

            first_event = events.wait_for(
                AgentEventType.PROCESSING_COMPLETE,
                timeout=INITIAL_PROCESSING_TIMEOUT_SECONDS,
            )
            if not first_event:
                self.record_task_result(
                    run_id,
                    1,
                    EvalRunTask.Status.FAILED,
                    observed_summary=(
                        "Timed out waiting for initial agent processing to complete "
                        f"after {INITIAL_PROCESSING_TIMEOUT_SECONDS}s."
                    ),
                )
                return

            # If the agent spawned background work, wait for it to drain to idle
            outstanding = int((first_event.get("payload") or {}).get("outstanding_tasks", 0) or 0)
            completion_event = first_event
            if outstanding:
                idle_wait_start = time.time()
                remaining = BACKGROUND_DRAIN_TIMEOUT_SECONDS
                while remaining > 0:
                    completion_event = events.wait_for(
                        AgentEventType.PROCESSING_COMPLETE,
                        timeout=remaining,
                    )
                    if not completion_event:
                        break
                    outstanding = int((completion_event.get("payload") or {}).get("outstanding_tasks", 0) or 0)
                    if outstanding == 0:
                        break
                    remaining = max(0, BACKGROUND_DRAIN_TIMEOUT_SECONDS - int(time.time() - idle_wait_start))

            final_outstanding = int((completion_event.get("payload") or {}).get("outstanding_tasks", 0) or 0) if completion_event else None
            if not completion_event or final_outstanding != 0:
                self.record_task_result(
                    run_id,
                    1,
                    EvalRunTask.Status.FAILED,
                    observed_summary=(
                        "Timed out waiting for agent to finish background web task "
                        f"after {BACKGROUND_DRAIN_TIMEOUT_SECONDS}s."
                    ),
                )
                return

        self.record_task_result(
            run_id, 1, EvalRunTask.Status.PASSED,
            observed_summary="Instruction sent and agent finished processing.",
            artifacts={"message": msg}
        )

        # Refresh agent to see updates
        agent = self.get_agent(agent_id)
        
        # 2. Verify Charter Update
        self.record_task_result(run_id, 2, EvalRunTask.Status.RUNNING)

        charter_accepted, charter_reason = _charter_mentions_pollution_monitoring(agent.charter)
        
        if charter_accepted:
            self.record_task_result(
                run_id, 2, EvalRunTask.Status.PASSED,
                observed_summary=f"Charter updated: {charter_reason}",
                artifacts={"charter": agent.charter}
            )
        else:
            self.record_task_result(
                run_id, 2, EvalRunTask.Status.FAILED,
                observed_summary=f"Charter check failed: {charter_reason}",
                artifacts={"charter": agent.charter}
            )

        # 3. Verify Schedule Setting
        self.record_task_result(run_id, 3, EvalRunTask.Status.RUNNING)

        schedule_accepted, schedule_reason = _schedule_is_reasonable_pollution_monitoring(agent.schedule)

        if schedule_accepted:
            self.record_task_result(
                run_id, 3, EvalRunTask.Status.PASSED,
                observed_summary=f"Schedule accepted: {schedule_reason}",
                artifacts={"schedule": agent.schedule}
            )
        else:
            self.record_task_result(
                run_id, 3, EvalRunTask.Status.FAILED,
                observed_summary=f"Schedule rejected: {schedule_reason}",
                artifacts={"schedule": agent.schedule}
            )

        # 4. Verify Web Browsing
        self.record_task_result(run_id, 4, EvalRunTask.Status.RUNNING)
        
        # We check if any BrowserUseAgentTaskStep contains the result from the sim site
        browser_agent = agent.browser_use_agent
        if not browser_agent:
             self.record_task_result(
                run_id, 4, EvalRunTask.Status.FAILED,
                observed_summary="Agent has no browser_use_agent linked."
            )
             return

        found_pollution_data = False
        task_summary = ""
        
        # Check the last few tasks
        recent_tasks = browser_agent.tasks.order_by('-created_at')[:5]
        for task in recent_tasks:
            for step in task.steps.all():
                # Inspect step description/result for evidence of visiting the site
                blob = str(step.result_value) + " " + step.description
                if "55" in blob and "Moderate" in blob:
                    found_pollution_data = True
                    task_summary = f"Found pollution data in task {task.id} step {step.step_number}"
                    break
            if found_pollution_data:
                break
        
        if found_pollution_data:
            self.record_task_result(
                run_id, 4, EvalRunTask.Status.PASSED,
                observed_summary=task_summary
            )
        else:
             self.record_task_result(
                run_id, 4, EvalRunTask.Status.FAILED,
                observed_summary="Could not find evidence of 'Moderate (55)' in recent browser task steps."
            )

        # 5. Verify Pollution Report (Message)
        self.record_task_result(run_id, 5, EvalRunTask.Status.RUNNING)
        
        report_messages = PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            is_outbound=True
        ).order_by('timestamp')

        if not report_messages.exists():
            self.record_task_result(
                run_id, 5, EvalRunTask.Status.FAILED,
                observed_summary="Agent did not send any reply."
            )
            return

        message_with_index = next(
            (message for message in report_messages if "55" in (message.body or "").lower()),
            None,
        )
        if message_with_index:
             self.record_task_result(
                run_id, 5, EvalRunTask.Status.PASSED,
                observed_summary=f"Agent reported correct index: {message_with_index.body}",
                artifacts={"message": message_with_index}
            )
        else:
            last_message = report_messages.last()
            self.record_task_result(
                run_id, 5, EvalRunTask.Status.FAILED,
                observed_summary=f"Agent failed to report '55'. Body: {last_message.body}",
                artifacts={"message": last_message}
            )
