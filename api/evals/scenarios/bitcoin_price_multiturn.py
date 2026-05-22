import re
from urllib.parse import parse_qs, urlparse

from api.evals.base import EvalScenario, ScenarioTask
from api.evals.registry import register_scenario
from api.evals.execution import ScenarioExecutionTools
from api.models import EvalRunTask, PersistentAgentToolCall, PersistentAgentMessage


BITCOIN_PRICE_RE = re.compile(r"\b68,?500(?:\.50)?\b")
URL_RE = re.compile(r"https?://[^\s)>\]]+")


def bitcoin_response_has_followup_question(body: str) -> bool:
    return "?" in URL_RE.sub("", body or "")


def is_supported_bitcoin_price_api_url(url: str) -> bool:
    if not url:
        return False

    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    if parsed.netloc == "api.coingecko.com":
        if parsed.path == "/api/v3/simple/price":
            ids = ",".join(query.get("ids", []))
            vs = ",".join(query.get("vs_currencies", []))
            return "bitcoin" in ids and "usd" in vs

        if parsed.path == "/api/v3/coins/markets":
            ids = ",".join(query.get("ids", []))
            vs = ",".join(query.get("vs_currency", []))
            return "bitcoin" in ids and "usd" in vs

    if parsed.netloc == "api.coindesk.com":
        return parsed.path in {
            "/v1/bpi/currentprice.json",
            "/v1/bpi/currentprice/USD.json",
        }

    return False


def bitcoin_tool_calls_include_supported_price_api(calls) -> bool:
    return any(
        is_supported_bitcoin_price_api_url((getattr(call, "tool_params", None) or {}).get("url", ""))
        for call in calls
    )


@register_scenario
class BitcoinPriceMultiturnScenario(EvalScenario, ScenarioExecutionTools):
    slug = "bitcoin_price_multiturn"
    description = "Chatty intro followed by Bitcoin price request. Checks for efficient API usage over browser."
    tier = "core"
    category = "tool_choice"
    expected_runtime = "medium"
    cost_class = "medium"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("tool_choice", "multi_turn", "web_research", "http_request")
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

        bitcoin_price_response = {
            "status": "ok",
            "content": '{"bitcoin":{"usd":68500.50}}',
            "status_code": 200,
        }

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
                "rules": [
                    {
                        "url_contains": "api.coingecko.com/api/v3/simple/price",
                        "result": bitcoin_price_response,
                    },
                    {
                        "url_contains": "api.coingecko.com/api/v3/coins/markets",
                        "result": bitcoin_price_response,
                    },
                    {
                        "url_contains": "api.coindesk.com/v1/bpi/currentprice.json",
                        "result": bitcoin_price_response,
                    },
                    {
                        "url_contains": "api.coindesk.com/v1/bpi/currentprice/USD.json",
                        "result": bitcoin_price_response,
                    },
                ],
                "default": {
                    "status": "error",
                    "message": (
                        "Unsupported Bitcoin price API URL for this eval. "
                        "Request a Bitcoin USD quote endpoint such as CoinGecko simple price "
                        "or Coindesk currentprice/USD.json."
                    ),
                    "retryable": True,
                },
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
        http_request_to_expected_api = bitcoin_tool_calls_include_supported_price_api(http_calls)

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

            if http_request_to_expected_api:
                self.record_task_result(
                    run_id, None, EvalRunTask.Status.PASSED, task_name="verify_search_query_pattern",
                    observed_summary=(
                        "Search wording was generic, but the agent converted it into a supported API request."
                    ),
                    artifacts={"query": first_query}
                )
            else:
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

        if http_request_to_expected_api:
            self.record_task_result(
                run_id, None, EvalRunTask.Status.PASSED, task_name="verify_http_request_after_search",
                observed_summary="Agent correctly made http_request to a supported Bitcoin price API endpoint."
            )
        else:
            seen_urls = [
                (call.tool_params or {}).get("url", "")
                for call in http_calls
                if (call.tool_params or {}).get("url", "")
            ]
            self.record_task_result(
                run_id, None, EvalRunTask.Status.FAILED, task_name="verify_http_request_after_search",
                observed_summary=(
                    "Agent did not make http_request to a supported Bitcoin price API endpoint. "
                    f"Seen URLs: {seen_urls}"
                )
            )
            return

        # Verify final response
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_bitcoin_response")

        last_outbound = PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            is_outbound=True,
            timestamp__gt=btc_msg.timestamp
        ).order_by('-timestamp').first()

        body = last_outbound.body if last_outbound else ""
        lower_body = body.lower()
        has_asset = "bitcoin" in lower_body or "btc" in lower_body
        has_price = bool(BITCOIN_PRICE_RE.search(body))
        if last_outbound and has_asset and has_price and not bitcoin_response_has_followup_question(body):
            self.record_task_result(
                run_id, None, EvalRunTask.Status.PASSED, task_name="verify_bitcoin_response",
                observed_summary=f"Agent replied with Bitcoin price data: {last_outbound.body[:100]}..."
            )
        elif last_outbound and has_asset and has_price:
            self.record_task_result(
                run_id, None, EvalRunTask.Status.FAILED, task_name="verify_bitcoin_response",
                observed_summary=f"Agent added an unnecessary follow-up question after the price answer. Body: {body}"
            )
        else:
            self.record_task_result(
                run_id, None, EvalRunTask.Status.FAILED, task_name="verify_bitcoin_response",
                observed_summary=f"Agent reply missing Bitcoin price data. Body: {body if last_outbound else 'None'}"
            )
