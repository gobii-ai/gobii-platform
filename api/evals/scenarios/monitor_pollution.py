import time

from api.evals.base import EvalScenario, ScenarioTask
from api.evals.registry import register_scenario
from api.evals.execution import ScenarioExecutionTools
from api.models import EvalRunTask, PersistentAgentMessage
from api.evals.sim_config import get_sim_weather_url
from api.agent.events import AgentEventType

@register_scenario
class MonitorPollutionScenario(EvalScenario, ScenarioExecutionTools):
    slug = "monitor_pollution"
    description = "Instruct agent to monitor pollution in DC. Verifies charter update, schedule setting, web browsing, and correct reporting."
    tasks = [
        ScenarioTask(name="instruct_agent", assertion_type="manual"),
        ScenarioTask(name="verify_charter_update", assertion_type="llm_judge"),
        ScenarioTask(name="verify_schedule_setting", assertion_type="llm_judge"),
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

            first_event = events.wait_for(AgentEventType.PROCESSING_COMPLETE, timeout=120)
            if not first_event:
                self.record_task_result(
                    run_id,
                    1,
                    EvalRunTask.Status.FAILED,
                    observed_summary="Timed out waiting for initial agent processing to complete.",
                )
                return

            # If the agent spawned background work, wait for it to drain to idle
            outstanding = int((first_event.get("payload") or {}).get("outstanding_tasks", 0) or 0)
            completion_event = first_event
            if outstanding:
                idle_wait_start = time.time()
                remaining = 180
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
                    remaining = max(0, 180 - int(time.time() - idle_wait_start))

            final_outstanding = int((completion_event.get("payload") or {}).get("outstanding_tasks", 0) or 0) if completion_event else None
            if not completion_event or final_outstanding != 0:
                self.record_task_result(
                    run_id,
                    1,
                    EvalRunTask.Status.FAILED,
                    observed_summary="Timed out waiting for agent to finish background web task.",
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

        charter_judge_q = "Does the agent's charter mention monitoring pollution or air quality in Washington DC?"
        charter_choice, charter_reason = self.llm_judge(
            question=charter_judge_q,
            context=f"Agent Charter:\n{agent.charter}",
        )
        
        if charter_choice == "Yes":
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

        schedule_judge_q = (
            "Is the agent's schedule set to something reasonable for monitoring daily weather/pollution? "
            "(e.g. daily, twice daily). It should NOT be extremely frequent (every minute) or missing."
        )
        schedule_choice, schedule_reason = self.llm_judge(
            question=schedule_judge_q,
            context=f"Agent Schedule: {agent.schedule}",
        )

        if schedule_choice == "Yes":
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
        
        last_message = PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            is_outbound=True
        ).order_by('timestamp').last()

        if not last_message:
            self.record_task_result(
                run_id, 5, EvalRunTask.Status.FAILED,
                observed_summary="Agent did not send any reply."
            )
            return

        body = (last_message.body or "").lower()
        if "55" in body:
             self.record_task_result(
                run_id, 5, EvalRunTask.Status.PASSED,
                observed_summary=f"Agent reported correct index: {last_message.body}",
                artifacts={"message": last_message}
            )
        else:
            self.record_task_result(
                run_id, 5, EvalRunTask.Status.FAILED,
                observed_summary=f"Agent failed to report '55'. Body: {last_message.body}",
                artifacts={"message": last_message}
            )
