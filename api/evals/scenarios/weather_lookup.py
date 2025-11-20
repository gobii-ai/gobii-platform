from unittest.mock import patch, MagicMock
import json

from api.evals.base import EvalScenario, ScenarioTask
from api.evals.registry import register_scenario
from api.evals.execution import ScenarioExecutionTools
from api.models import EvalRunTask, PersistentAgentMessage, PersistentAgentStep
from api.agent.core.event_processing import process_agent_events

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
            None, # sequence
            EvalRunTask.Status.RUNNING, 
            task_name="inject_prompt"
        )
        
        # Inject without triggering async processing so we can run synchronously with mocks
        msg = self.inject_message(
            agent_id, 
            "what's the weather in frederick md?", 
            trigger_processing=False
        )
            
        self.record_task_result(
            run_id, 
            None,
            EvalRunTask.Status.PASSED, 
            task_name="inject_prompt",
            observed_summary="Message injected (processing paused for mocking)", 
            artifacts={"message": msg}
        )

        # Setup Mocks
        # We patch execute_enabled_tool because http_request is dispatched through it
        with patch('api.agent.core.event_processing.execute_spawn_web_task') as mock_spawn, \
             patch('api.agent.core.event_processing.execute_search_web') as mock_search, \
             patch('api.agent.core.event_processing.execute_enabled_tool') as mock_enabled_tool:
            
            # Configure mocks
            mock_spawn.return_value = {
                "status": "ok",
                "result": "Web task simulated success"
            }
            mock_search.return_value = {
                "status": "ok",
                "result": "Search simulated success"
            }
            
            def enabled_tool_side_effect(agent, tool_name, params):
                if tool_name == 'http_request':
                     return {
                        "status": "ok", 
                        "content": '{"current_weather": "72F, Sunny"}', 
                        "status_code": 200
                    }
                return {"status": "ok", "message": "Mock tool success"}
            
            mock_enabled_tool.side_effect = enabled_tool_side_effect

            # Trigger synchronous processing
            process_agent_events(agent_id)

        # Task 2: Charter Update
        self.record_task_result(
            run_id, 
            None,
            EvalRunTask.Status.RUNNING, 
            task_name="verify_charter_update"
        )
        
        steps = PersistentAgentStep.objects.filter(
            agent_id=agent_id,
            created_at__gte=msg.timestamp
        ).select_related() # removed 'tool_call' as it's a reverse relation

        charter_updates = []
        for s in steps:
            if hasattr(s, 'tool_call'):
                # Safe access to reverse OneToOne
                if s.tool_call.tool_name in ('update_charter', 'charter_updater'):
                    charter_updates.append(s)

        if charter_updates:
             self.record_task_result(
                run_id, 
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_charter_update",
                observed_summary="Charter update tool called.",
                artifacts={"step": charter_updates[0]}
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

        # Find the http_request call in the mock calls
        http_call_args = None
        for call in mock_enabled_tool.call_args_list:
            # call.args is (agent, tool_name, params)
            args, _ = call
            if len(args) >= 2 and args[1] == 'http_request':
                http_call_args = args
                break

        if mock_spawn.called:
            self.record_task_result(
                run_id, 
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_http_request",
                observed_summary="Agent used 'spawn_web_task', which is forbidden for this test. We want the agent to use a direct API request.",
            )
        elif http_call_args:
            params = http_call_args[2] if len(http_call_args) > 2 else {}
            
            # Run LLM Judge
            judge_prompt = f"Analyze this HTTP request parameters: {json.dumps(params)}. " \
                           f"Is this a request to a free/open weather API (like wttr.in, open-meteo, weather.gov, etc.) " \
                           f"that retrieves weather for Frederick, MD?"
            
            choice, reasoning = self.llm_judge(
                question=judge_prompt,
                context=f"User asked: 'what's the weather in frederick md?'. Agent made this request.",
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
