from urllib.parse import parse_qs, urlparse

from api.evals.base import EvalScenario, ScenarioTask
from api.evals.registry import register_scenario
from api.evals.execution import ScenarioExecutionTools
from api.models import EvalRunTask, PersistentAgentMessage, PersistentAgentToolCall

FREDERICK_MD_LATITUDE = 39.4143
FREDERICK_MD_LONGITUDE = -77.4105
COORDINATE_TOLERANCE_DEGREES = 0.5
MOCK_WEATHER_RESULT = {
    "current_weather": "72F, Sunny",
    "temperature_f": 72,
    "condition": "Sunny",
    "location": "Frederick, MD",
    "forecast": [
        {"day": "today", "high_f": 74, "low_f": 58, "condition": "Sunny"},
        {"day": "tomorrow", "high_f": 76, "low_f": 60, "condition": "Mostly sunny"},
    ],
}


def _query_float(query, key):
    values = query.get(key) or []
    if not values:
        return None
    try:
        return float(values[0])
    except (TypeError, ValueError):
        return None


def _coordinates_match_frederick(query) -> bool:
    latitude = _query_float(query, "latitude")
    longitude = _query_float(query, "longitude")
    if latitude is None or longitude is None:
        point = " ".join(query.get("point", []))
        if "," not in point:
            return False
        raw_latitude, raw_longitude = point.split(",", 1)
        try:
            latitude = float(raw_latitude)
            longitude = float(raw_longitude)
        except ValueError:
            return False

    return (
        abs(latitude - FREDERICK_MD_LATITUDE) <= COORDINATE_TOLERANCE_DEGREES
        and abs(longitude - FREDERICK_MD_LONGITUDE) <= COORDINATE_TOLERANCE_DEGREES
    )


def _is_free_weather_request(params):
    method = str(params.get("method") or "GET").upper()
    if method != "GET":
        return False, f"Expected GET request, saw {method}."

    raw_url = str(params.get("url") or "")
    parsed = urlparse(raw_url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    query = parse_qs(parsed.query.lower())
    url_text = raw_url.lower()

    if "wttr.in" in host:
        if "frederick" in url_text and ("md" in url_text or "maryland" in url_text):
            return True, "wttr.in request includes Frederick, MD."
        return False, "wttr.in request did not include Frederick, MD."

    if host == "api.weather.gov" and path.startswith("/points/"):
        point_text = path.removeprefix("/points/").split("/", 1)[0]
        point_query = {"point": [point_text]}
        if _coordinates_match_frederick(point_query):
            return True, "weather.gov points request targets Frederick, MD coordinates."
        return False, "weather.gov points request does not target Frederick, MD coordinates."

    if host == "api.weather.gov" and path.startswith("/gridpoints/lwx/"):
        return True, "weather.gov gridpoints request uses the Frederick-area LWX office."

    if host == "api.weather.gov" and path.startswith("/alerts/"):
        if _coordinates_match_frederick(query):
            return True, "weather.gov alerts request targets Frederick, MD coordinates."
        return False, "weather.gov alerts request does not target Frederick, MD coordinates."

    if "api.open-meteo.com" in host and {"latitude", "longitude"}.issubset(query):
        if _coordinates_match_frederick(query):
            return True, "open-meteo request targets Frederick, MD coordinates."
        return False, "open-meteo request does not target Frederick, MD coordinates."

    if "geocoding-api.open-meteo.com" in host:
        return False, "Open-Meteo geocoding only resolves coordinates; it is not a weather request."

    if "api.openweathermap.org" in host:
        location = " ".join(query.get("q", []))
        if "frederick" in location and ("md" in location or "us" in location):
            return True, "OpenWeather request includes Frederick."
        return False, "OpenWeather request did not include Frederick."

    return False, f"URL does not look like a supported free weather API: {raw_url}"


@register_scenario
class WeatherLookupScenario(EvalScenario, ScenarioExecutionTools):
    slug = "weather_lookup"
    description = "Ask for weather and expect a direct HTTP API request to a free weather service plus a user-facing answer."
    tier = "smoke"
    category = "tool_choice"
    expected_runtime = "medium"
    cost_class = "medium"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("smoke", "tool_choice", "weather", "http_request", "llm_judge")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
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
            "mcp_brightdata_search_engine": {
                "status": "ok",
                "result": (
                    "Found free weather API: https://api.weather.gov/gridpoints/LWX/96,70/forecast "
                    "provides forecast for Frederick, MD. Also available: "
                    "https://api.openweathermap.org/data/2.5/weather?q=Frederick,MD,US&appid=demo"
                )
            },
            "search_tools": {
                "status": "success",
                "message": (
                    "Use http_request with a direct weather forecast/current-conditions endpoint. "
                    "Good examples: https://wttr.in/Frederick,MD?format=j1 or "
                    "https://api.weather.gov/gridpoints/LWX/96,70/forecast. "
                    "Open-Meteo geocoding endpoints only resolve coordinates; they are not weather results."
                ),
            },
            "http_request": {
                "rules": [
                    {
                        "url_contains": "geocoding-api.open-meteo.com",
                        "result": {
                            "status": "ok",
                            "content": {
                                "results": [
                                    {
                                        "name": "Frederick",
                                        "admin1": "Maryland",
                                        "latitude": FREDERICK_MD_LATITUDE,
                                        "longitude": FREDERICK_MD_LONGITUDE,
                                    }
                                ]
                            },
                            "status_code": 200,
                        },
                    }
                ],
                "default": {
                    "status": "ok",
                    "content": MOCK_WEATHER_RESULT,
                    "status_code": 200,
                },
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

        # Task 2: Verify HTTP Request (Judge)
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

        if http_calls.exists():
            checked_requests = []
            valid_call = None
            valid_reason = ""
            for http_call in http_calls.order_by("step__created_at", "step__id"):
                params = http_call.tool_params or {}
                valid_request, reason = _is_free_weather_request(params)
                checked_requests.append({"params": params, "reason": reason})
                if valid_request:
                    valid_call = http_call
                    valid_reason = reason
                    break

            if valid_call is not None:
                first_spawn_at = (
                    spawn_calls.order_by("step__created_at")
                    .values_list("step__created_at", flat=True)
                    .first()
                )
                if first_spawn_at and first_spawn_at < valid_call.step.created_at:
                    self.record_task_result(
                        run_id,
                        None,
                        EvalRunTask.Status.FAILED,
                        task_name="verify_http_request",
                        observed_summary=(
                            "Agent used spawn_web_task before making a valid direct weather API request."
                        ),
                        artifacts={"params": valid_call.tool_params or {}, "checked_requests": checked_requests},
                    )
                    return

                redundant_browser_note = " A later redundant browser task was ignored." if spawn_calls.exists() else ""
                self.record_task_result(
                    run_id,
                    None,
                    EvalRunTask.Status.PASSED,
                    task_name="verify_http_request",
                    observed_summary=f"Valid HTTP request detected. {valid_reason}{redundant_browser_note}",
                    artifacts={"params": valid_call.tool_params or {}, "checked_requests": checked_requests}
                )
            else:
                self.record_task_result(
                    run_id,
                    None,
                    EvalRunTask.Status.FAILED,
                    task_name="verify_http_request",
                    observed_summary=(
                        "HTTP requests invalid/irrelevant. "
                        f"Checked {len(checked_requests)} request(s); last reason: {checked_requests[-1]['reason']}"
                    ),
                    artifacts={"checked_requests": checked_requests}
                )
        elif spawn_calls.exists():
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_http_request",
                observed_summary="Agent used 'spawn_web_task' without a valid direct weather API request.",
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_http_request",
                observed_summary="Agent did not make an HTTP request or spawn a web task.",
            )

        # Task 3: Verify Response
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

        reply = (last_outbound.body or "") if last_outbound else ""
        normalized_reply = reply.lower()
        includes_mock_weather = "72" in normalized_reply and "sun" in normalized_reply

        if last_outbound and includes_mock_weather:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_response",
                observed_summary=f"Agent replied with the mocked weather result: {reply[:100]}...",
                artifacts={"message": last_outbound}
            )
        elif last_outbound:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_response",
                observed_summary=f"Agent replied without the mocked weather result. Body: {reply[:200]}",
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
