import json

from api.evals.base import EvalScenario, ScenarioTask
from api.evals.registry import register_scenario
from api.evals.execution import ScenarioExecutionTools
from api.models import EvalRunTask, PersistentAgentMessage, PersistentAgentStep


@register_scenario
class WeatherLookupScenario(EvalScenario, ScenarioExecutionTools):
    slug = "weather_lookup"
    description = "Ask for weather and expect a charter update and a direct HTTP API request to a free weather service."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_charter_update", assertion_type="manual"),
        ScenarioTask(name="verify_http_request", assertion_type="llm_judge"),
        ScenarioTask(name="verify_response", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        # Task 1: Inject Prompt
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="inject_prompt"
        )

        # Mock config - passed directly to Celery worker via task args
        mock_config = {
            "spawn_web_task": {
                "status": "error",
                "message": "spawn_web_task disabled for this eval - use http_request"
            },
            "search_web": {
                "status": "ok",
                "result": (
                    "Found free weather API: https://api.weather.gov/gridpoints/LWX/96,70/forecast "
                    "provides forecast for Frederick, MD. Also available: "
                    "https://api.openweathermap.org/data/2.5/weather?q=Frederick,MD,US&appid=demo"
                )
            },
            "http_request": {
                "status": "ok",
                "content": '{"current_weather": "72F, Sunny"}',
                "status_code": 200
            },
        }

        # Inject message with async processing via Celery
        with self.wait_for_agent_idle(agent_id, timeout=120):
            msg = self.inject_message(
                agent_id,
                "what's the weather in frederick md?",
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
            )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Message injected and processed via Celery",
            artifacts={"message": msg}
        )

        # Task 2: Charter Update
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_charter_update"
        )

        from api.models import PersistentAgentToolCall
        charter_updates = PersistentAgentToolCall.objects.filter(
            step__agent_id=agent_id,
            step__created_at__gte=msg.timestamp,
            tool_name__in=['update_charter', 'charter_updater']
        )

        if charter_updates.exists():
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_charter_update",
                observed_summary="Charter update tool called.",
                artifacts={"tool_call": charter_updates.first()}
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_charter_update",
                observed_summary="No charter update tool called."
            )

        # Task 3: Verify HTTP Request (Judge)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_http_request"
        )

        http_calls = PersistentAgentToolCall.objects.filter(
            step__agent_id=agent_id,
            step__created_at__gte=msg.timestamp,
            tool_name='http_request'
        )

        spawn_calls = PersistentAgentToolCall.objects.filter(
            step__agent_id=agent_id,
            step__created_at__gte=msg.timestamp,
            tool_name='spawn_web_task'
        )

        if spawn_calls.exists():
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_http_request",
                observed_summary="Agent used 'spawn_web_task', which is forbidden. We want direct API request.",
            )
        elif http_calls.exists():
            http_call = http_calls.first()
            params = http_call.tool_params or {}

            judge_prompt = (
                f"Analyze this HTTP request parameters: {json.dumps(params)}. "
                f"Is this a request to a free/open weather API (like wttr.in, open-meteo, weather.gov, etc.) "
                f"that retrieves weather for Frederick, MD?"
            )

            choice, reasoning = self.llm_judge(
                question=judge_prompt,
                context="User asked: 'what's the weather in frederick md?'. Agent made this request.",
                options=["Yes", "No"]
            )

            if choice == "Yes":
                self.record_task_result(
                    run_id,
                    None,
                    EvalRunTask.Status.PASSED,
                    task_name="verify_http_request",
                    observed_summary=f"Valid HTTP request detected. Reasoning: {reasoning}",
                    artifacts={"params": params}
                )
            else:
                self.record_task_result(
                    run_id,
                    None,
                    EvalRunTask.Status.FAILED,
                    task_name="verify_http_request",
                    observed_summary=f"HTTP request invalid/irrelevant. Reasoning: {reasoning}",
                    artifacts={"params": params}
                )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_http_request",
                observed_summary="Agent did not make an HTTP request or spawn a web task.",
            )

        # Task 4: Verify Response
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_response"
        )

        last_outbound = PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            is_outbound=True,
            timestamp__gt=msg.timestamp
        ).order_by('timestamp').last()

        if last_outbound:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_response",
                observed_summary=f"Agent replied: {last_outbound.body[:100]}...",
                artifacts={"message": last_outbound}
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_response",
                observed_summary="Agent did not send a reply."
            )
