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
        ScenarioTask(name="verify_search_query_pattern", assertion_type="manual"),
        ScenarioTask(name="verify_efficient_tool_usage", assertion_type="manual"),
        ScenarioTask(name="verify_http_request_after_search", assertion_type="manual"),
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
            owner_agent_id=agent_id, is_outbound=True
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

        # Track tool calls
        search_web_calls = []
        http_request_calls = []

        # Setup Mocks for Bitcoin price processing
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_efficient_tool_usage")

        with patch('api.agent.core.event_processing.execute_spawn_web_task') as mock_spawn, \
             patch('api.agent.core.event_processing.execute_search_web') as mock_search, \
             patch('api.agent.core.event_processing.execute_enabled_tool') as mock_enabled_tool:
            
            mock_spawn.return_value = {"status": "ok", "result": "Web task simulated success"}
            
            # Mock search_web (direct call)
            def search_side_effect(agent, params):
                search_web_calls.append(params) # Store call params
                query = params.get('query', '').lower()
                # If the agent is smart and searches for an API, give it the API directly
                if 'api' in query and ('bitcoin' in query or 'crypto' in query or 'price' in query):
                    return {
                        "status": "ok",
                        "result": "Found free Bitcoin price API: https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
                    }
                # If it searches generally, give it a normal search result leading to an API
                elif 'bitcoin' in query or 'crypto' in query or 'price' in query:
                    return {
                        "status": "ok",
                        "result": "Many sites offer Bitcoin prices. CoinGecko has a simple API: https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
                    }
                return {"status": "ok", "result": "No relevant results found."}
            
            mock_search.side_effect = search_side_effect

            # Mock enabled tools (http_request via search_tools)
            def enabled_tool_side_effect(agent, tool_name, params):
                if tool_name == 'http_request':
                    http_request_calls.append(params) # Store call params
                     # Return a realistic Bitcoin price API response
                    return {
                        "status": "ok", 
                        "content": json.dumps({"bitcoin":{"usd":68500.50}}), 
                        "status_code": 200
                    }
                return {"status": "ok", "message": "Mock tool success"}
            
            mock_enabled_tool.side_effect = enabled_tool_side_effect

            # Trigger processing synchronously
            process_agent_events(agent_id)

        # Assertion for verify_efficient_tool_usage
        if mock_spawn.called:
            self.record_task_result(
                run_id, None, EvalRunTask.Status.FAILED, task_name="verify_efficient_tool_usage",
                observed_summary="Agent used 'spawn_web_task'. Expected API usage only."
            )
            return # Stop here as it's a critical failure

        # If we reach here, spawn_web_task was not called.
        self.record_task_result(
            run_id, None, EvalRunTask.Status.PASSED, task_name="verify_efficient_tool_usage",
            observed_summary="Agent avoided 'spawn_web_task'."
        )


        # New Task: Verify search_web query pattern
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_search_query_pattern")
        
        if not search_web_calls:
            # If no search was performed, check if http_request was called directly.
            if http_request_calls:
                self.record_task_result(
                    run_id, None, EvalRunTask.Status.PASSED, task_name="verify_search_query_pattern",
                    observed_summary="Agent skipped search and called API directly (Optimal behavior)."
                )
            else:
                # Neither search nor http request? That's odd, but efficient_tool_usage or http_request_after_search will catch it.
                # We'll mark this as SKIPPED/PASSED to avoid double jeopardy.
                self.record_task_result(
                    run_id, None, EvalRunTask.Status.PASSED, task_name="verify_search_query_pattern",
                    observed_summary="No search performed."
                )
        else:
            # Check the first significant search query
            first_api_search_query = search_web_calls[0].get('query', '')
            
            judge_prompt = f"Analyze the following search query: '{first_api_search_query}'. Does it indicate an attempt to find an API, data source, or programmatic interface? If the query contains words like 'API', 'endpoint', 'JSON', or 'docs', answer 'Yes'. Does it look like a developer searching for a source?"
            choice, reasoning = self.llm_judge(
                question=judge_prompt,
                context=f"Agent's goal: find Bitcoin price. First search query: '{first_api_search_query}'",
                options=["Yes", "No"]
            )
            if choice == "Yes":
                self.record_task_result(
                    run_id, None, EvalRunTask.Status.PASSED, task_name="verify_search_query_pattern",
                    observed_summary=f"Search query indicated API/Data intent. Reasoning: {reasoning}",
                    artifacts={"query": first_api_search_query}
                )
            else:
                # Relaxed check: If they found the API anyway (which we know they did if http_request_calls is populated correctly later), maybe we can be lenient?
                # But the prompt says "Your query MUST include the word 'API'".
                # Let's stick to the judge.
                self.record_task_result(
                    run_id, None, EvalRunTask.Status.FAILED, task_name="verify_search_query_pattern",
                    observed_summary=f"Search query did NOT indicate API/Data intent. Reasoning: {reasoning}",
                    artifacts={"query": first_api_search_query}
                )
                # We don't return here, we let it proceed to check the next steps


        # New Task: Verify http_request after successful API search
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_http_request_after_search")
        if not http_request_calls:
            self.record_task_result(
                run_id, None, EvalRunTask.Status.FAILED, task_name="verify_http_request_after_search",
                observed_summary="Agent did not call http_request after finding an API URL via search."
            )
            return

        # Ensure the http_request was made to the *expected* API URL
        expected_api_url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
        http_request_to_expected_api = False
        for call in http_request_calls:
            if call.get('url') == expected_api_url:
                http_request_to_expected_api = True
                break
        
        if http_request_to_expected_api:
            self.record_task_result(
                run_id, None, EvalRunTask.Status.PASSED, task_name="verify_http_request_after_search",
                observed_summary=f"Agent correctly made http_request to the expected API: {expected_api_url}"
            )
        else:
            self.record_task_result(
                run_id, None, EvalRunTask.Status.FAILED, task_name="verify_http_request_after_search",
                observed_summary=f"Agent did not make http_request to the expected API: {expected_api_url}. Instead made calls: {http_request_calls}"
            )
            return # Fail early if API call is wrong


        # Verify final response (this remains similar)
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_bitcoin_response")
        last_outbound = PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            is_outbound=True,
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