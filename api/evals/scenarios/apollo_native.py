from typing import Any

from api.agent.system_skills.defaults import APOLLO_NATIVE_SYSTEM_SKILL_KEY
from api.evals.base import ScenarioTask
from api.evals.scenarios.native_http import (
    HttpRequestExpectation,
    NativeHttpCase as ApolloNativeCase,
    NativeHttpScenarioBase,
    call_matches_expectation as _call_matches_expectation,
    register_native_http_scenarios,
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


class ApolloHttpRequestExpectation(HttpRequestExpectation):
    def __init__(
        self,
        name: str,
        url_terms: tuple[str, ...],
        method: str = "POST",
        body_terms: tuple[str, ...] = (),
        allowed_statuses: tuple[str, ...] = ("complete",),
    ):
        super().__init__(
            name=name,
            url_terms=url_terms,
            method=method,
            body_terms=body_terms,
            allowed_statuses=allowed_statuses,
        )


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
                allowed_statuses=("error",),
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


class ApolloNativeScenario(NativeHttpScenarioBase):
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
    system_skill_key = APOLLO_NATIVE_SYSTEM_SKILL_KEY
    system_skill_name = "Apollo"
    forbidden_tool_names = FORBIDDEN_APOLLO_DISCOVERY_TOOL_NAMES
    forbidden_tool_prefixes = ("apollo_io-",)
    allowed_tool_names = ("http_request", "send_chat_message", "sqlite_batch")
    max_relevant_tool_calls = 10
    expected_requests_summary = "Agent completed the expected Apollo REST request(s)."
    forbidden_pass_summary = "Agent avoided legacy Apollo tools, skill discovery, browser, and web-search paths."
    response_pass_summary = "Final response included the expected mocked Apollo result or setup guidance."


register_native_http_scenarios(APOLLO_NATIVE_CASES, ApolloNativeScenario)
