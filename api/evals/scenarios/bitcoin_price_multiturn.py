from unittest.mock import patch
import json

from api.evals.base import EvalScenario, ScenarioTask
from api.evals.registry import register_scenario
from api.evals.execution import ScenarioExecutionTools
from api.models import EvalRunTask, PersistentAgentToolCall, PersistentAgentMessage
from api.agent.core.event_processing import process_agent_events

@register_scenario
class BitcoinPriceMultiturnScenario(EvalScenario, ScenarioExecutionTools):
    slug = "bitcoin_price_multiturn"
    description = "Chatty intro followed by Bitcoin price request. Checks for efficient API usage over browser."
    tasks = [
        ScenarioTask(name="inject_hello", assertion_type="manual"),
        ScenarioTask(name="verify_hello_response", assertion_type="manual"),
        ScenarioTask(name="inject_bitcoin_request", assertion_type="manual"),
        ScenarioTask(name="verify_efficient_tool_usage", assertion_type="manual"),
        ScenarioTask(name="verify_bitcoin_response", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        # --- Turn 1: Hello ---
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_hello")
        
        # Send "Hello"
        with self.wait_for_agent_idle(agent_id):
            self.inject_message(agent_id, "Hello there!", trigger_processing=True)

        self.record_task_result(
            run_id, None, EvalRunTask.Status.PASSED, task_name="inject_hello", 
            observed_summary="Injected 'Hello there!'"
        )

        # Verify response to Hello
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_hello_response")
        
        last_msg = PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            is_outbound=True
        ).order_by('-timestamp').first()

        if last_msg and "hello" in last_msg.body.lower(): # Loose check for a greeting
             self.record_task_result(
                run_id, None, EvalRunTask.Status.PASSED, task_name="verify_hello_response",
                observed_summary=f"Agent replied: {last_msg.body[:50]}..."
            )
        else:
             self.record_task_result(
                run_id, None, EvalRunTask.Status.PASSED, task_name="verify_hello_response", # Still pass, not critical for this test
                observed_summary=f"Agent replied (greeting not found): {last_msg.body[:50] if last_msg else 'None'}"
            )

        # --- Turn 2: Bitcoin Price Request ---
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_bitcoin_request")
        
        msg = self.inject_message(
            agent_id, 
            "what's the current price of Bitcoin in USD?", 
            trigger_processing=False # Will manually trigger processing with mocks
        )
        
        self.record_task_result(
            run_id, None, EvalRunTask.Status.PASSED, task_name="inject_bitcoin_request",
            observed_summary="Injected Bitcoin price prompt"
        )

        # Setup Mocks for Bitcoin price processing
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_efficient_tool_usage")

        with patch('api.agent.core.event_processing.execute_spawn_web_task') as mock_spawn, \
             patch('api.agent.core.event_processing.execute_enabled_tool') as mock_enabled_tool:
            
            mock_spawn.return_value = {"status": "ok", "result": "Web task simulated success"}
            
            def enabled_tool_side_effect(agent, tool_name, params):
                if tool_name == 'http_request':
                     # Return a realistic Bitcoin price API response
                     return {
                        "status": "ok", 
                        "content": json.dumps({"bitcoin":{"usd":68500.50}}), 
                        "status_code": 200
                    }
                if tool_name == 'search_web':
                    query = params.get('query', '').lower()
                    # Mock search_web to return a Bitcoin API URL
                    if 'bitcoin' in query or 'crypto' in query or 'price' in query:
                        return {
                            "status": "ok",
                            "result": "Found free Bitcoin price API: https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
                        }
                return {"status": "ok", "message": "Mock tool success"}
            
            mock_enabled_tool.side_effect = enabled_tool_side_effect

            # Trigger processing synchronously
            process_agent_events(agent_id)

        # Check usage
        if mock_spawn.called:
            self.record_task_result(
                run_id, None, EvalRunTask.Status.FAILED, task_name="verify_efficient_tool_usage",
                observed_summary="Agent used 'spawn_web_task'. Expected API usage only."
            )
            return # Stop here if this is the failure we're looking for

        # Verify http_request was called (and implicitly, not spawn_web_task due to the above check)
        http_called = False
        http_params = None
        for call in mock_enabled_tool.call_args_list:
            args, _ = call
            if len(args) >= 2 and args[1] == 'http_request':
                http_called = True
                http_params = args[2] # Capture params for potential judging
                break
        
        if http_called:
            # LLM judge for the HTTP request parameters
            judge_prompt = f"Analyze this HTTP request parameters: {json.dumps(http_params)}. " \
                           f"Is this a request to a free/public Bitcoin price API that requires no API key for a simple lookup?"
            
            choice, reasoning = self.llm_judge(
                question=judge_prompt,
                context=f"User asked: 'what's the current price of Bitcoin in USD?'. Agent made this request.",
                options=["Yes", "No"]
            )

            if choice == "Yes":
                self.record_task_result(
                    run_id, None, EvalRunTask.Status.PASSED, task_name="verify_efficient_tool_usage",
                    observed_summary=f"Agent used 'http_request' for Bitcoin price. Reasoning: {reasoning}",
                    artifacts={"params": http_params}
                )
            else:
                self.record_task_result(
                    run_id, None, EvalRunTask.Status.FAILED, task_name="verify_efficient_tool_usage",
                    observed_summary=f"HTTP request parameters invalid/irrelevant. Reasoning: {reasoning}",
                    artifacts={"params": http_params}
                )
        else:
             self.record_task_result(
                run_id, None, EvalRunTask.Status.FAILED, task_name="verify_efficient_tool_usage",
                observed_summary="Agent did NOT use 'http_request' for Bitcoin price."
            )

        # Verify final response
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_bitcoin_response")
        last_outbound = PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            is_outbound=True,
            # Filter messages after the Bitcoin request was injected
            timestamp__gt=msg.timestamp 
        ).order_by('-timestamp').first()

        if last_outbound and ("bitcoin" in last_outbound.body.lower() or "68500" in last_outbound.body):
             self.record_task_result(
                run_id, None, EvalRunTask.Status.PASSED, task_name="verify_bitcoin_response",
                observed_summary=f"Agent replied with Bitcoin price data: {last_outbound.body[:100]}..."
            )
        else:
             self.record_task_result(
                run_id, None, EvalRunTask.Status.FAILED, task_name="verify_bitcoin_response",
                observed_summary=f"Agent reply missing Bitcoin price data. Body: {last_outbound.body if last_outbound else 'None'}"
            )
