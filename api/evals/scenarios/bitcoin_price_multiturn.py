import json

from api.evals.base import EvalScenario, ScenarioTask
from api.evals.registry import register_scenario
from api.evals.execution import ScenarioExecutionTools
from api.models import EvalRunTask, PersistentAgentToolCall, PersistentAgentMessage


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
        # --- Turn 1: Hello (no mocks needed) ---
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_hello")

        with self.wait_for_agent_idle(agent_id, timeout=120):
            hello_msg = self.inject_message(
                agent_id, "Hello there!",
                trigger_processing=True,
                eval_run_id=run_id,
            )

        self.record_task_result(
            run_id, None, EvalRunTask.Status.PASSED, task_name="inject_hello",
            observed_summary="Injected 'Hello there!'"
        )

        # Verify response to Hello
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_hello_response")

        last_msg = PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id, is_outbound=True,
            timestamp__gt=hello_msg.timestamp
        ).order_by('-timestamp').first()

        if last_msg and "hello" in last_msg.body.lower():
            self.record_task_result(
                run_id, None, EvalRunTask.Status.PASSED, task_name="verify_hello_response",
                observed_summary=f"Agent replied: {last_msg.body[:50]}..."
            )
        else:
            self.record_task_result(
                run_id, None, EvalRunTask.Status.PASSED, task_name="verify_hello_response",
                observed_summary=f"Agent replied (greeting not found): {last_msg.body[:50] if last_msg else 'None'}"
            )

        # --- Turn 2: Bitcoin Price Request (with mocks) ---
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_bitcoin_request")

        # Mock config - passed directly to Celery worker
        mock_config = {
            "spawn_web_task": {
                "status": "error",
                "message": "spawn_web_task disabled - use http_request for API calls"
            },
            "mcp_brightdata_search_engine": {
                "status": "ok",
                "result": (
                    "Found free Bitcoin price API: "
                    "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
                )
            },
            "http_request": {
                "status": "ok",
                "content": '{"bitcoin":{"usd":68500.50}}',
                "status_code": 200
            },
        }

        with self.wait_for_agent_idle(agent_id, timeout=120):
            btc_msg = self.inject_message(
                agent_id,
                "what's the current price of Bitcoin in USD?",
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
            )

        self.record_task_result(
            run_id, None, EvalRunTask.Status.PASSED, task_name="inject_bitcoin_request",
            observed_summary="Injected Bitcoin price prompt"
        )

        # Verify efficient tool usage (no spawn_web_task)
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_efficient_tool_usage")

        spawn_calls = PersistentAgentToolCall.objects.filter(
            step__agent_id=agent_id,
            step__created_at__gte=btc_msg.timestamp,
            tool_name='spawn_web_task'
        )

        if spawn_calls.exists():
            self.record_task_result(
                run_id, None, EvalRunTask.Status.FAILED, task_name="verify_efficient_tool_usage",
                observed_summary="Agent used 'spawn_web_task'. Expected API usage only."
            )
            return

        self.record_task_result(
            run_id, None, EvalRunTask.Status.PASSED, task_name="verify_efficient_tool_usage",
            observed_summary="Agent avoided 'spawn_web_task'."
        )

        # Verify mcp_brightdata_search_engine query pattern
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_search_query_pattern")

        search_calls = PersistentAgentToolCall.objects.filter(
            step__agent_id=agent_id,
            step__created_at__gte=btc_msg.timestamp,
            tool_name='mcp_brightdata_search_engine'
        ).order_by('step__created_at')

        http_calls = PersistentAgentToolCall.objects.filter(
            step__agent_id=agent_id,
            step__created_at__gte=btc_msg.timestamp,
            tool_name='http_request'
        )

        if not search_calls.exists():
            if http_calls.exists():
                self.record_task_result(
                    run_id, None, EvalRunTask.Status.PASSED, task_name="verify_search_query_pattern",
                    observed_summary="Agent skipped search and called API directly (Optimal behavior)."
                )
            else:
                self.record_task_result(
                    run_id, None, EvalRunTask.Status.PASSED, task_name="verify_search_query_pattern",
                    observed_summary="No search performed."
                )
        else:
            first_search = search_calls.first()
            first_query = (first_search.tool_params or {}).get('query', '')

            judge_prompt = (
                f"Analyze the following search query: '{first_query}'. "
                "Does it indicate an attempt to find an API, data source, or programmatic interface? "
                "If the query contains words like 'API', 'endpoint', 'JSON', or 'docs', answer 'Yes'."
            )
            choice, reasoning = self.llm_judge(
                question=judge_prompt,
                context=f"Agent's goal: find Bitcoin price. First search query: '{first_query}'",
                options=["Yes", "No"]
            )
            if choice == "Yes":
                self.record_task_result(
                    run_id, None, EvalRunTask.Status.PASSED, task_name="verify_search_query_pattern",
                    observed_summary=f"Search query indicated API/Data intent. Reasoning: {reasoning}",
                    artifacts={"query": first_query}
                )
            else:
                self.record_task_result(
                    run_id, None, EvalRunTask.Status.FAILED, task_name="verify_search_query_pattern",
                    observed_summary=f"Search query did NOT indicate API/Data intent. Reasoning: {reasoning}",
                    artifacts={"query": first_query}
                )

        # Verify http_request after search
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_http_request_after_search")

        if not http_calls.exists():
            self.record_task_result(
                run_id, None, EvalRunTask.Status.FAILED, task_name="verify_http_request_after_search",
                observed_summary="Agent did not call http_request after finding an API URL via search."
            )
            return

        expected_api_url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
        http_request_to_expected_api = any(
            (call.tool_params or {}).get('url', '') == expected_api_url
            for call in http_calls
        )

        if http_request_to_expected_api:
            self.record_task_result(
                run_id, None, EvalRunTask.Status.PASSED, task_name="verify_http_request_after_search",
                observed_summary=f"Agent correctly made http_request to the expected API: {expected_api_url}"
            )
        else:
            self.record_task_result(
                run_id, None, EvalRunTask.Status.FAILED, task_name="verify_http_request_after_search",
                observed_summary=f"Agent did not make http_request to the expected API: {expected_api_url}"
            )
            return

        # Verify final response
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_bitcoin_response")

        last_outbound = PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            is_outbound=True,
            timestamp__gt=btc_msg.timestamp
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
