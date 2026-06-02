import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote_plus

from api.agent.system_skills.defaults import APOLLO_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.service import enable_system_skills
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentSystemStep,
    PersistentAgentStep,
    PersistentAgentToolCall,
)


APOLLO_NATIVE_SUITE_SLUG = "apollo_native"

APOLLO_NATIVE_PEOPLE_SEARCH = "apollo_native_people_search"
APOLLO_NATIVE_ORGANIZATION_SEARCH = "apollo_native_organization_search"
APOLLO_NATIVE_PERSON_ENRICHMENT = "apollo_native_person_enrichment"
APOLLO_NATIVE_MISSING_CONNECTION = "apollo_native_missing_connection"
APOLLO_NATIVE_CREATE_CONTACT = "apollo_native_create_contact"

FORBIDDEN_APOLLO_DISCOVERY_TOOL_NAMES = (
    "search_tools",
    "enable_system_skills",
    "mcp_brightdata_search_engine",
    "spawn_web_task",
)


@dataclass(frozen=True)
class ApolloHttpRequestExpectation:
    name: str
    url_terms: tuple[str, ...]
    method: str = "POST"
    body_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApolloNativeCase:
    slug: str
    prompt: str
    description: str
    http_rules: tuple[dict[str, Any], ...]
    expected_http_requests: tuple[ApolloHttpRequestExpectation, ...]
    response_term_groups: tuple[tuple[str, ...], ...] = ()
    tags: tuple[str, ...] = field(default_factory=tuple)

    def mock_config(self) -> dict[str, dict[str, Any]]:
        return {
            "http_request": {
                "rules": list(self.http_rules),
                "default": {
                    "status": "error",
                    "status_code": 404,
                    "message": "Unexpected Apollo native eval URL.",
                    "content": {"ok": False},
                },
            }
        }


def _json_body(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


def _http_result(url: str, content: Any, *, status_code: int = 200, status: str = "ok") -> dict[str, Any]:
    return {
        "status": status,
        "status_code": status_code,
        "url": url,
        "content": content,
    }


APOLLO_API_BASE = "https://api.apollo.io/api/v1"


def _apollo_rule(path: str, content: Any, *extra_terms: str, status_code: int = 200, status: str = "ok") -> dict[str, Any]:
    return {
        "url_contains": ("api.apollo.io/api/v1", path, *extra_terms),
        "result": _http_result(f"{APOLLO_API_BASE}/{path}", content, status_code=status_code, status=status),
    }


APOLLO_NATIVE_CASES = (
    ApolloNativeCase(
        slug=APOLLO_NATIVE_PEOPLE_SEARCH,
        description="Search Apollo people through the native Apollo REST API.",
        prompt=(
            "Use the native Apollo integration to search for VP Sales contacts at healthcare SaaS companies "
            "in Boston. Return the top matches."
        ),
        http_rules=(
            _apollo_rule(
                "mixed_people/api_search",
                {
                    "people": [
                        {
                            "id": "person_001",
                            "name": "Mina Patel",
                            "title": "VP Sales",
                            "organization": {"name": "CareFlow"},
                        }
                    ],
                    "pagination": {"page": 1, "per_page": 10, "total_entries": 1},
                },
            ),
        ),
        expected_http_requests=(
            ApolloHttpRequestExpectation(
                name="people_search",
                url_terms=("api.apollo.io/api/v1/mixed_people/api_search",),
            ),
        ),
        response_term_groups=(("Mina", "CareFlow"), ("VP Sales",)),
        tags=("people_search",),
    ),
    ApolloNativeCase(
        slug=APOLLO_NATIVE_ORGANIZATION_SEARCH,
        description="Search Apollo organizations through mixed company search.",
        prompt=(
            "Use native Apollo to find cybersecurity companies in Austin with 50-200 employees. "
            "Summarize the first page."
        ),
        http_rules=(
            _apollo_rule(
                "mixed_companies/search",
                {
                    "organizations": [
                        {
                            "id": "org_001",
                            "name": "CipherLake",
                            "city": "Austin",
                            "estimated_num_employees": 120,
                        }
                    ],
                    "pagination": {"page": 1, "per_page": 10, "total_entries": 1},
                },
            ),
        ),
        expected_http_requests=(
            ApolloHttpRequestExpectation(
                name="organization_search",
                url_terms=("api.apollo.io/api/v1/mixed_companies/search",),
            ),
        ),
        response_term_groups=(("CipherLake",), ("Austin",)),
        tags=("organization_search",),
    ),
    ApolloNativeCase(
        slug=APOLLO_NATIVE_PERSON_ENRICHMENT,
        description="Enrich one person through Apollo people match.",
        prompt="Use native Apollo to enrich pat@example.test and return their title and company.",
        http_rules=(
            _apollo_rule(
                "people/match",
                {
                    "person": {
                        "id": "person_pat",
                        "email": "pat@example.test",
                        "name": "Pat Rowan",
                        "title": "Director of Revenue Operations",
                        "organization": {"name": "Northstar Logistics"},
                    }
                },
            ),
        ),
        expected_http_requests=(
            ApolloHttpRequestExpectation(
                name="person_enrichment",
                url_terms=("api.apollo.io/api/v1/people/match",),
            ),
        ),
        response_term_groups=(("Pat", "Revenue"), ("Northstar Logistics",)),
        tags=("person_enrichment",),
    ),
    ApolloNativeCase(
        slug=APOLLO_NATIVE_MISSING_CONNECTION,
        description="Report native integration setup guidance when Apollo is not connected.",
        prompt=(
            "Use the native Apollo integration to search for RevOps directors in Texas. "
            "If Apollo is not connected, tell me what to do."
        ),
        http_rules=(
            {
                "url_contains": ("api.apollo.io/api/v1", "mixed_people/api_search"),
                "result": {
                    "status": "error",
                    "status_code": 401,
                    "url": f"{APOLLO_API_BASE}/mixed_people/api_search",
                    "message": (
                        "native_integration_not_connected: Apollo is not connected. "
                        "Ask the user to open /app/integrations and connect Apollo."
                    ),
                    "content": {"ok": False},
                }
            },
        ),
        expected_http_requests=(
            ApolloHttpRequestExpectation(
                name="apollo_search_attempt",
                url_terms=("api.apollo.io/api/v1/mixed_people/api_search",),
            ),
        ),
        response_term_groups=(("Apollo",), ("/app/integrations",), ("connect", "connected")),
        tags=("missing_connection",),
    ),
    ApolloNativeCase(
        slug=APOLLO_NATIVE_CREATE_CONTACT,
        description="Create a contact through the native Apollo REST API after a clear write request.",
        prompt=(
            "Use native Apollo to create a contact for Alex Morgan, alex@example.test, "
            "VP Sales at ExampleCo. Keep the request bounded and report the created contact id."
        ),
        http_rules=(
            _apollo_rule(
                "contacts",
                {
                    "contact": {
                        "id": "contact_001",
                        "first_name": "Alex",
                        "last_name": "Morgan",
                        "email": "alex@example.test",
                    }
                },
            ),
        ),
        expected_http_requests=(
            ApolloHttpRequestExpectation(
                name="create_contact",
                url_terms=("api.apollo.io/api/v1/contacts",),
                body_terms=("alex", "morgan", "alex@example.test"),
            ),
        ),
        response_term_groups=(("contact_001",), ("Alex", "created")),
        tags=("contact_write",),
    ),
)

APOLLO_NATIVE_SCENARIO_SLUGS = tuple(case.slug for case in APOLLO_NATIVE_CASES)


def _tool_calls_for_run(run_id: str, *, after=None, tool_names=None):
    queryset = PersistentAgentToolCall.objects.filter(step__eval_run_id=run_id)
    if after is not None:
        queryset = queryset.filter(step__created_at__gte=after)
    if tool_names is not None:
        queryset = queryset.filter(tool_name__in=list(tool_names))
    return list(queryset.select_related("step").order_by("step__created_at", "step__id"))


def _decoded_url(call: PersistentAgentToolCall) -> str:
    params = call.tool_params or {}
    return unquote_plus(str(params.get("url") or "")).lower()


def _request_body(call: PersistentAgentToolCall) -> str:
    body = (call.tool_params or {}).get("body")
    if isinstance(body, str):
        return body.lower()
    if body is None:
        return ""
    return _json_body(body).lower()


def _request_method(call: PersistentAgentToolCall) -> str:
    return str((call.tool_params or {}).get("method") or "GET").strip().upper()


def _call_matches_expectation(call: PersistentAgentToolCall, expectation: ApolloHttpRequestExpectation) -> bool:
    if str(getattr(call, "status", "") or "").lower() != "complete":
        return False
    url = _decoded_url(call)
    body = _request_body(call)
    if _request_method(call) != expectation.method.upper():
        return False
    if not all(term.lower() in url for term in expectation.url_terms):
        return False
    if not all(term.lower() in body for term in expectation.body_terms):
        return False
    return True


class ApolloNativeScenario(EvalScenario, ScenarioExecutionTools):
    tier = "core"
    category = "apollo_native"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "system_skills"
    tags = ("apollo_native", "system_skill", "micro", "http_request")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_expected_http_requests", assertion_type="tool_call"),
        ScenarioTask(name="verify_no_forbidden_tools", assertion_type="tool_call"),
        ScenarioTask(name="verify_response", assertion_type="exact_match"),
    ]
    case: ApolloNativeCase | None = None

    def _seed_prior_processing_run(self, agent_id: str) -> None:
        if PersistentAgentSystemStep.objects.filter(
            step__agent_id=agent_id,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        ).exists():
            return

        prior_step = PersistentAgentStep.objects.create(
            agent_id=agent_id,
            description="Process events",
        )
        PersistentAgentSystemStep.objects.create(
            step=prior_step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        )

    def _prepare_agent(self, agent_id: str) -> None:
        PersistentAgent.objects.filter(id=agent_id).update(planning_state=PersistentAgent.PlanningState.SKIPPED)
        self._seed_prior_processing_run(agent_id)
        agent = PersistentAgent.objects.get(id=agent_id)
        result = enable_system_skills(agent, [APOLLO_NATIVE_SYSTEM_SKILL_KEY])
        if result.get("invalid"):
            raise ValueError(f"Could not enable Apollo native system skill: {result}")

    def _eval_stop_policy(self) -> dict[str, Any]:
        return {
            "allowed_tool_names": ["http_request", "send_chat_message", "sqlite_batch"],
            "ignored_tool_names": ["sleep_until_next_trigger"],
            "stop_on_unexpected_relevant_tool": True,
            "stop_on_tool_names": list(FORBIDDEN_APOLLO_DISCOVERY_TOOL_NAMES),
            "stop_on_tool_names_after_finish": ["send_chat_message"],
            "max_relevant_tool_calls": 10,
        }

    def _record_expected_http_requests(self, run_id: str, inbound) -> None:
        case = self.case
        if case is None:
            raise ValueError("ApolloNativeScenario.case must be set.")

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_expected_http_requests",
        )
        http_calls = _tool_calls_for_run(run_id, after=inbound.timestamp, tool_names=["http_request"])
        missing = [
            expectation.name
            for expectation in case.expected_http_requests
            if not any(_call_matches_expectation(call, expectation) for call in http_calls)
        ]
        if not missing:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_expected_http_requests",
                observed_summary="Agent completed the expected Apollo REST request(s).",
                artifacts={"step": http_calls[0].step} if http_calls else {},
            )
            return

        seen = [
            {
                "method": _request_method(call),
                "url": (call.tool_params or {}).get("url"),
                "body": _request_body(call)[:500],
            }
            for call in http_calls
        ]
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="verify_expected_http_requests",
            observed_summary=f"Missing expected HTTP request(s): {missing}; saw {seen}.",
            artifacts={"step": http_calls[0].step} if http_calls else {},
        )

    def _record_forbidden_absence(self, run_id: str, inbound) -> None:
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_no_forbidden_tools",
        )
        calls = _tool_calls_for_run(run_id, after=inbound.timestamp)
        bad_calls = [
            call
            for call in calls
            if call.tool_name.startswith("apollo_io-") or call.tool_name in FORBIDDEN_APOLLO_DISCOVERY_TOOL_NAMES
        ]
        if bad_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_no_forbidden_tools",
                observed_summary=f"Agent used forbidden Apollo tool or discovery path: {[call.tool_name for call in bad_calls]}.",
                artifacts={"step": bad_calls[0].step},
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_no_forbidden_tools",
            observed_summary="Agent avoided legacy Apollo tools, skill discovery, browser, and web-search paths.",
        )

    def _record_response(self, run_id: str, agent_id: str, inbound) -> None:
        case = self.case
        if case is None:
            raise ValueError("ApolloNativeScenario.case must be set.")

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_response")
        final_response = (
            PersistentAgentMessage.objects
            .filter(owner_agent_id=agent_id, is_outbound=True, timestamp__gt=inbound.timestamp)
            .order_by("-timestamp")
            .first()
        )
        body = final_response.body if final_response else ""
        missing_groups = [
            terms
            for terms in case.response_term_groups
            if not any(term.lower() in body.lower() for term in terms)
        ]
        if not missing_groups:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_response",
                observed_summary="Final response included the expected mocked Apollo result or setup guidance.",
                artifacts={"message": final_response} if final_response else {},
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="verify_response",
            observed_summary=f"Final response missing expected term group(s) {missing_groups}; body={body[:800]!r}.",
            artifacts={"message": final_response} if final_response else {},
        )

    def run(self, run_id: str, agent_id: str) -> None:
        case = self.case
        if case is None:
            raise ValueError("ApolloNativeScenario.case must be set.")

        self._prepare_agent(agent_id)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                case.prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=case.mock_config(),
                eval_stop_policy=self._eval_stop_policy(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self._record_expected_http_requests(run_id, inbound)
        self._record_forbidden_absence(run_id, inbound)
        self._record_response(run_id, agent_id, inbound)


def _apollo_native_scenario_class(case: ApolloNativeCase):
    class _ApolloNativeCaseScenario(ApolloNativeScenario):
        slug = case.slug
        description = case.description
        tags = ApolloNativeScenario.tags + case.tags

    _ApolloNativeCaseScenario.case = case
    _ApolloNativeCaseScenario.__name__ = "".join(part.title() for part in case.slug.split("_")) + "Scenario"
    return _ApolloNativeCaseScenario


for apollo_native_case in APOLLO_NATIVE_CASES:
    ScenarioRegistry.register(_apollo_native_scenario_class(apollo_native_case)())
