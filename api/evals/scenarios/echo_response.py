
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.registry import register_scenario
from api.evals.execution import ScenarioExecutionTools
from api.models import EvalRunTask, PersistentAgentMessage

@register_scenario
class EchoResponseScenario(EvalScenario, ScenarioExecutionTools):
    slug = "echo_response"
    description = "Send a message and verify the agent replies with the requested keyword."
    tasks = [
        ScenarioTask(name="send_message", assertion_type="manual"),
        ScenarioTask(name="verify_response", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        # Task 1: Send message
        self.record_task_result(run_id, 1, EvalRunTask.Status.RUNNING)
        
        # Use context manager to avoid race conditions
        with self.wait_for_agent_idle(agent_id, timeout=30):
            msg = self.inject_message(agent_id, "Please reply with the word ORANGE.")
            
        self.record_task_result(
            run_id, 1, EvalRunTask.Status.PASSED, 
            observed_summary="Message injected", 
            artifacts={"message": msg}
        )

        # Task 2: Verify response
        self.record_task_result(run_id, 2, EvalRunTask.Status.RUNNING)
        
        # We already waited for idle above, so we can proceed to assertion immediately.

        # Check the last outbound message
        last_message = PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            is_outbound=True
        ).order_by('timestamp').last()

        if not last_message:
            self.record_task_result(
                run_id, 2, EvalRunTask.Status.FAILED,
                observed_summary="Agent did not send any reply."
            )
            return

        if "ORANGE" in (last_message.body or "").upper():
            self.record_task_result(
                run_id, 2, EvalRunTask.Status.PASSED, 
                observed_summary=f"Agent replied: {last_message.body}",
                artifacts={"message": last_message}
            )
        else:
            self.record_task_result(
                run_id, 2, EvalRunTask.Status.FAILED, 
                observed_summary=f"Agent replied but missed keyword. Body: {last_message.body}",
                artifacts={"message": last_message}
            )
