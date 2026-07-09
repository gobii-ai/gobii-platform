from typing import Any

from api.agent.system_skills.defaults import HUBSPOT_NATIVE_SYSTEM_SKILL_KEY
from api.evals.base import ScenarioTask
from api.evals.scenarios.native_http import HttpRequestExpectation, NativeHttpCase as HubSpotNativeCase, NativeHttpScenarioBase, call_matches_expectation as _call_matches_expectation, register_native_http_scenarios


HUBSPOT_NATIVE_SUITE_SLUG = "hubspot_native"

HUBSPOT_NATIVE_CONTACT_SEARCH = "hubspot_native_contact_search"
HUBSPOT_NATIVE_COMPANY_SEARCH = "hubspot_native_company_search"
HUBSPOT_NATIVE_DEAL_UPDATE = "hubspot_native_deal_update"
HUBSPOT_NATIVE_MISSING_CONNECTION = "hubspot_native_missing_connection"
HUBSPOT_NATIVE_CREATE_CONTACT = "hubspot_native_create_contact"

FORBIDDEN_HUBSPOT_DISCOVERY_TOOL_NAMES = (
    "search_tools",
    "enable_system_skills",
    "mcp_brightdata_search_engine",
    "spawn_web_task",
)


class HubSpotHttpRequestExpectation(HttpRequestExpectation):
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


HUBSPOT_API_BASE = "https://api.hubapi.com/crm/v3"


def _hubspot_rule(path: str, content: Any, *extra_terms: str, status_code: int = 200, status: str = "ok") -> dict[str, Any]:
    return {
        "url_contains": ("api.hubapi.com", path, *extra_terms),
        "result": _http_result(f"{HUBSPOT_API_BASE}/{path}", content, status_code=status_code, status=status),
    }


HUBSPOT_NATIVE_CASES = (
    HubSpotNativeCase(
        slug=HUBSPOT_NATIVE_CONTACT_SEARCH,
        description="Search HubSpot contacts through the native HubSpot REST API.",
        prompt=(
            "Use the native HubSpot integration to search the first page of contacts whose email domain is "
            "example.test. Return the contacts HubSpot returns."
        ),
        http_rules=(
            _hubspot_rule(
                "objects/contacts/search",
                {
                    "results": [
                        {
                            "id": "contact_001",
                            "properties": {
                                "email": "mina@example.test",
                                "firstname": "Mina",
                                "lastname": "Patel",
                            },
                        }
                    ],
                    "paging": {"next": {"after": "contact_001"}},
                },
            ),
        ),
        expected_http_requests=(
            HubSpotHttpRequestExpectation(
                name="contact_search",
                url_terms=("api.hubapi.com/crm/v3/objects/contacts/search",),
                body_terms=("example.test", "limit"),
            ),
        ),
        response_term_groups=(("Mina", "mina@example.test"),),
        tags=("contact_search",),
    ),
    HubSpotNativeCase(
        slug=HUBSPOT_NATIVE_COMPANY_SEARCH,
        description="Search HubSpot companies through the native HubSpot REST API.",
        prompt=(
            "Use native HubSpot to find companies in Austin with lifecycle stage customer. "
            "Summarize the first page HubSpot returns."
        ),
        http_rules=(
            _hubspot_rule(
                "objects/companies/search",
                {
                    "results": [
                        {
                            "id": "company_001",
                            "properties": {
                                "name": "CipherLake",
                                "city": "Austin",
                                "lifecyclestage": "customer",
                            },
                        }
                    ]
                },
            ),
        ),
        expected_http_requests=(
            HubSpotHttpRequestExpectation(
                name="company_search",
                url_terms=("api.hubapi.com/crm/v3/objects/companies/search",),
                body_terms=("austin", "customer", "limit"),
            ),
        ),
        response_term_groups=(("CipherLake",), ("Austin",)),
        tags=("company_search",),
    ),
    HubSpotNativeCase(
        slug=HUBSPOT_NATIVE_DEAL_UPDATE,
        description="Update one HubSpot deal through the native HubSpot REST API after a clear write request.",
        prompt=(
            "Use native HubSpot to update deal deal_123 amount to 25000. This exact update is approved; "
            "report the updated amount."
        ),
        http_rules=(
            _hubspot_rule(
                "objects/deals/deal_123",
                {
                    "id": "deal_123",
                    "properties": {
                        "dealname": "Expansion Deal",
                        "amount": "25000",
                    },
                },
            ),
        ),
        expected_http_requests=(
            HubSpotHttpRequestExpectation(
                name="deal_update",
                url_terms=("api.hubapi.com/crm/v3/objects/deals/deal_123",),
                method="PATCH",
                body_terms=("amount", "25000"),
            ),
        ),
        response_term_groups=(("deal_123",), ("25000",)),
        tags=("deal_write",),
    ),
    HubSpotNativeCase(
        slug=HUBSPOT_NATIVE_MISSING_CONNECTION,
        description="Report native integration setup guidance when HubSpot is not connected.",
        prompt=(
            "Use the native HubSpot integration to search for customer contacts in Texas. "
            "If HubSpot is not connected, tell me what to do."
        ),
        http_rules=(
            {
                "url_contains": ("api.hubapi.com", "objects/contacts/search"),
                "result": {
                    "status": "error",
                    "status_code": 401,
                    "url": f"{HUBSPOT_API_BASE}/objects/contacts/search",
                    "message": (
                        "native_integration_not_connected: HubSpot is not connected. "
                        "Ask the user to open /app/integrations and connect HubSpot."
                    ),
                    "content": {"ok": False},
                }
            },
        ),
        expected_http_requests=(
            HubSpotHttpRequestExpectation(
                name="hubspot_search_attempt",
                url_terms=("api.hubapi.com/crm/v3/objects/contacts/search",),
                allowed_statuses=("error",),
            ),
        ),
        response_term_groups=(
            ("HubSpot",),
            ("/app/integrations", "Integrations page", "Integrations section", "Agent Settings"),
            ("connect", "connected"),
        ),
        tags=("missing_connection",),
    ),
    HubSpotNativeCase(
        slug=HUBSPOT_NATIVE_CREATE_CONTACT,
        description="Create a HubSpot contact through the native HubSpot REST API after a clear write request.",
        prompt=(
            "Use native HubSpot to create a contact for Alex Morgan, alex@example.test. "
            "This contact creation is approved; report the created contact id."
        ),
        http_rules=(
            _hubspot_rule(
                "objects/contacts",
                {
                    "id": "contact_002",
                    "properties": {
                        "email": "alex@example.test",
                        "firstname": "Alex",
                        "lastname": "Morgan",
                    },
                },
                status_code=201,
            ),
        ),
        expected_http_requests=(
            HubSpotHttpRequestExpectation(
                name="create_contact",
                url_terms=("api.hubapi.com/crm/v3/objects/contacts",),
                body_terms=("alex", "morgan", "alex@example.test"),
            ),
        ),
        response_term_groups=(("contact_002",), ("Alex", "created")),
        tags=("contact_write",),
    ),
)

HUBSPOT_NATIVE_SCENARIO_SLUGS = tuple(case.slug for case in HUBSPOT_NATIVE_CASES)


class HubSpotNativeScenario(NativeHttpScenarioBase):
    tier = "core"
    category = "hubspot_native"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "system_skills"
    tags = ("hubspot_native", "system_skill", "micro", "http_request")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_expected_http_requests", assertion_type="tool_call"),
        ScenarioTask(name="verify_no_forbidden_tools", assertion_type="tool_call"),
        ScenarioTask(name="verify_response", assertion_type="exact_match"),
    ]
    system_skill_key = HUBSPOT_NATIVE_SYSTEM_SKILL_KEY
    system_skill_name = "HubSpot"
    native_provider_key = "hubspot"
    forbidden_tool_names = FORBIDDEN_HUBSPOT_DISCOVERY_TOOL_NAMES
    forbidden_tool_prefixes = ("hubspot-", "hubspot_")
    allowed_tool_names = ("http_request", "send_chat_message", "sqlite_batch")
    max_relevant_tool_calls = 10
    expected_requests_summary = "Agent completed the expected HubSpot REST request(s)."
    forbidden_pass_summary = "Agent avoided HubSpot discovery, browser, and web-search paths."
    response_pass_summary = "Final response included the expected mocked HubSpot result or setup guidance."


register_native_http_scenarios(HUBSPOT_NATIVE_CASES, HubSpotNativeScenario)
