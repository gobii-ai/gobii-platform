from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from waffle.models import Flag

from api.agent.system_skills.defaults import HUBSPOT_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.service import enable_system_skills
from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_SERVER
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.native_http import response_contains_term, tool_calls_for_run
from api.models import (
    EvalRunTask,
    MCPServerConfig,
    MCPServerOAuthCredential,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
)
from api.services.managed_mcp_integrations import HUBSPOT_MCP_PROVIDER
from api.services.native_integrations import (
    HUBSPOT_PROVIDER,
    resolve_global_secret_owner_for_agent,
    save_native_integration_credentials,
)


HUBSPOT_MANAGED_MCP_SUITE_SLUG = "hubspot_managed_mcp"

HUBSPOT_MCP_CONTACT_SEARCH = "hubspot_mcp_contact_search"
HUBSPOT_MCP_DEAL_UPDATE = "hubspot_mcp_deal_update"
HUBSPOT_MCP_MISSING_CONNECTION = "hubspot_mcp_missing_connection"
HUBSPOT_MCP_REAUTHORIZATION = "hubspot_mcp_reauthorization"
HUBSPOT_MCP_FORBIDS_LEGACY_PATHS = "hubspot_mcp_forbids_legacy_paths"

HUBSPOT_USER_DETAILS_TOOL = "mcp_hubspot_get_user_details"
HUBSPOT_SEARCH_CRM_TOOL = "mcp_hubspot_search_crm_objects"
HUBSPOT_MANAGE_CRM_TOOL = "mcp_hubspot_manage_crm_objects"
HUBSPOT_EVAL_TOOL_NAMES = (
    HUBSPOT_USER_DETAILS_TOOL,
    HUBSPOT_SEARCH_CRM_TOOL,
    HUBSPOT_MANAGE_CRM_TOOL,
)


@dataclass(frozen=True)
class HubSpotManagedMCPCase:
    slug: str
    description: str
    prompt: str
    connected: bool
    mock_config: dict[str, Any]
    expected_hubspot_tools: tuple[str, ...]
    response_term_groups: tuple[tuple[str, ...], ...]
    tags: tuple[str, ...] = field(default_factory=tuple)
    seed_legacy_connection: bool = False


USER_DETAILS_RESULT = {
    "status": "ok",
    "connection_status": "AUTHORIZED",
    "user": {"email": "owner@example.test"},
    "account": {"id": "portal_123", "name": "Example CRM"},
    "available_objects": ["contacts", "companies", "deals"],
    "available_tools": ["search_crm_objects", "manage_crm_objects"],
}


HUBSPOT_MANAGED_MCP_CASES = (
    HubSpotManagedMCPCase(
        slug=HUBSPOT_MCP_CONTACT_SEARCH,
        description="Preflight the connected account and run a bounded HubSpot MCP contact search.",
        prompt=(
            "Use the connected HubSpot integration to search the first 25 contacts whose email domain is "
            "example.test. Return the contacts HubSpot finds."
        ),
        connected=True,
        mock_config={
            HUBSPOT_USER_DETAILS_TOOL: USER_DETAILS_RESULT,
            HUBSPOT_SEARCH_CRM_TOOL: {
                "status": "ok",
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
        },
        expected_hubspot_tools=(HUBSPOT_USER_DETAILS_TOOL, HUBSPOT_SEARCH_CRM_TOOL),
        response_term_groups=(("Mina",), ("mina@example.test",)),
        tags=("read",),
    ),
    HubSpotManagedMCPCase(
        slug=HUBSPOT_MCP_DEAL_UPDATE,
        description="Preflight the connected account and perform an explicitly approved HubSpot MCP write.",
        prompt=(
            "Use the connected HubSpot integration to update deal deal_123 amount to 25000. "
            "This exact update is approved; report the updated amount."
        ),
        connected=True,
        mock_config={
            HUBSPOT_USER_DETAILS_TOOL: USER_DETAILS_RESULT,
            HUBSPOT_MANAGE_CRM_TOOL: {
                "status": "ok",
                "operation": "update",
                "results": [
                    {
                        "id": "deal_123",
                        "properties": {"dealname": "Expansion Deal", "amount": "25000"},
                    }
                ],
            },
        },
        expected_hubspot_tools=(HUBSPOT_USER_DETAILS_TOOL, HUBSPOT_MANAGE_CRM_TOOL),
        response_term_groups=(("deal_123",), ("25000",)),
        tags=("write",),
    ),
    HubSpotManagedMCPCase(
        slug=HUBSPOT_MCP_MISSING_CONNECTION,
        description="Give managed HubSpot setup guidance without attempting a doomed tool call.",
        prompt="Use the connected HubSpot integration to find customer contacts in Texas.",
        connected=False,
        mock_config={},
        expected_hubspot_tools=(),
        response_term_groups=(
            ("HubSpot",),
            ("/app/integrations",),
            ("not connected", "isn't connected", "connect HubSpot"),
        ),
        tags=("missing_connection",),
    ),
    HubSpotManagedMCPCase(
        slug=HUBSPOT_MCP_REAUTHORIZATION,
        description="Stop HubSpot work and request reconnection when account preflight requires reauthorization.",
        prompt="Use the connected HubSpot integration to list our first 10 open deals.",
        connected=True,
        mock_config={
            HUBSPOT_USER_DETAILS_TOOL: {
                "status": "ok",
                "connection_status": "REQUIRES_REAUTHORIZATION",
                "message": "New HubSpot tool scopes require the app to be reinstalled.",
            },
        },
        expected_hubspot_tools=(HUBSPOT_USER_DETAILS_TOOL,),
        response_term_groups=(
            ("reconnect", "reauthorize", "re-authorize"),
            ("/app/integrations", "integrations page"),
        ),
        tags=("reauthorization",),
    ),
    HubSpotManagedMCPCase(
        slug=HUBSPOT_MCP_FORBIDS_LEGACY_PATHS,
        description="Use managed HubSpot MCP tools instead of REST, Pipedream, browser, or web-search fallbacks.",
        prompt=(
            "Find the first 20 HubSpot companies in Austin with lifecycle stage customer. Use our connected "
            "HubSpot integration, and do not ask me for a private-app token."
        ),
        connected=True,
        mock_config={
            HUBSPOT_USER_DETAILS_TOOL: USER_DETAILS_RESULT,
            HUBSPOT_SEARCH_CRM_TOOL: {
                "status": "ok",
                "results": [
                    {
                        "id": "company_001",
                        "properties": {
                            "name": "CipherLake",
                            "city": "Austin",
                            "lifecyclestage": "customer",
                        },
                    }
                ],
            },
        },
        expected_hubspot_tools=(HUBSPOT_USER_DETAILS_TOOL, HUBSPOT_SEARCH_CRM_TOOL),
        response_term_groups=(("CipherLake",), ("Austin",)),
        tags=("legacy_guardrail",),
        seed_legacy_connection=True,
    ),
)

HUBSPOT_MANAGED_MCP_SCENARIO_SLUGS = tuple(case.slug for case in HUBSPOT_MANAGED_MCP_CASES)


class _EvalMCPManager:
    _initialized = True

    @staticmethod
    def initialize(force=False):
        return True

    @staticmethod
    def get_tools_for_agent(agent, **kwargs):
        return []

    @staticmethod
    def get_enabled_tools_definitions(agent):
        return []

    @staticmethod
    def is_tool_blacklisted(tool_name):
        return False


def hubspot_tools_appear_in_order(calls, expected_tools: tuple[str, ...]) -> bool:
    expected_index = 0
    for call in calls:
        if expected_index >= len(expected_tools):
            break
        if call.tool_name == expected_tools[expected_index]:
            expected_index += 1
    return expected_index == len(expected_tools)


class HubSpotManagedMCPScenario(EvalScenario, ScenarioExecutionTools):
    tier = "core"
    category = "hubspot_managed_mcp"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "system_skills"
    tags = ("hubspot", "managed_mcp", "system_skill", "real_harness")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_managed_mcp_calls", assertion_type="tool_call"),
        ScenarioTask(name="verify_no_legacy_paths", assertion_type="tool_call"),
        ScenarioTask(name="verify_response", assertion_type="exact_match"),
    ]
    requires_personal_agent = True
    case: HubSpotManagedMCPCase | None = None

    def _case(self) -> HubSpotManagedMCPCase:
        if self.case is None:
            raise ValueError(f"{type(self).__name__}.case must be set.")
        return self.case

    @staticmethod
    def _seed_prior_processing_run(agent_id: str) -> None:
        if PersistentAgentSystemStep.objects.filter(
            step__agent_id=agent_id,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        ).exists():
            return
        step = PersistentAgentStep.objects.create(agent_id=agent_id, description="Process events")
        PersistentAgentSystemStep.objects.create(
            step=step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        )

    @staticmethod
    def _enable_managed_feature(agent: PersistentAgent) -> None:
        flag, _created = Flag.objects.get_or_create(name="hubspot_mcp")
        flag.users.add(agent.user)

    @staticmethod
    def _enable_eval_tools(agent: PersistentAgent) -> None:
        for tool_name in HUBSPOT_EVAL_TOOL_NAMES:
            result = mark_tool_enabled_without_discovery(agent, tool_name)
            if result.get("status") != "success":
                raise ValueError(f"Could not enable HubSpot eval tool {tool_name}: {result}")
            PersistentAgentEnabledTool.objects.filter(
                agent=agent,
                tool_full_name=tool_name,
            ).update(tool_server=EVAL_SYNTHETIC_TOOL_SERVER, tool_name=tool_name)

    @staticmethod
    def _seed_mixed_credentials(agent: PersistentAgent) -> None:
        owner_user, owner_org = resolve_global_secret_owner_for_agent(agent)
        save_native_integration_credentials(
            HUBSPOT_PROVIDER,
            owner_user,
            owner_org,
            {
                "provider_key": HUBSPOT_PROVIDER.key,
                "auth_type": "oauth2",
                "access_token": "eval-legacy-hubspot-access-token",
            },
        )
        scope = (
            MCPServerConfig.Scope.ORGANIZATION
            if owner_org is not None
            else MCPServerConfig.Scope.USER
        )
        config = MCPServerConfig.objects.create(
            scope=scope,
            organization=owner_org,
            user=owner_user,
            name=HUBSPOT_MCP_PROVIDER.key,
            display_name=HUBSPOT_MCP_PROVIDER.display_name,
            url=HUBSPOT_MCP_PROVIDER.server_url,
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
            managed_integration_key=HUBSPOT_MCP_PROVIDER.key,
            metadata={"managed_oauth": True, "provider_key": HUBSPOT_MCP_PROVIDER.key},
            is_active=False,
        )
        MCPServerOAuthCredential.objects.create(
            server_config=config,
            organization=owner_org,
            user=owner_user,
            metadata={"managed_integration_key": HUBSPOT_MCP_PROVIDER.key},
        )
        MCPServerConfig.objects.filter(pk=config.pk).update(is_active=True)

    def _prepare_agent(self, agent_id: str) -> PersistentAgent:
        PersistentAgent.objects.filter(id=agent_id).update(planning_state=PersistentAgent.PlanningState.SKIPPED)
        self._seed_prior_processing_run(agent_id)
        agent = PersistentAgent.objects.select_related("user", "organization").get(id=agent_id)
        self._enable_managed_feature(agent)
        if self._case().seed_legacy_connection:
            self._seed_mixed_credentials(agent)
        result = enable_system_skills(agent, [HUBSPOT_NATIVE_SYSTEM_SKILL_KEY])
        if result.get("invalid"):
            raise ValueError(f"Could not enable HubSpot system skill: {result}")
        if self._case().connected:
            self._enable_eval_tools(agent)
        return agent

    @staticmethod
    def _eval_stop_policy() -> dict[str, Any]:
        return {
            "allowed_tool_names": [
                *HUBSPOT_EVAL_TOOL_NAMES,
                "search_tools",
                "send_chat_message",
                "sqlite_batch",
            ],
            "ignored_tool_names": ["sleep_until_next_trigger", "update_plan"],
            "stop_on_unexpected_relevant_tool": True,
            "stop_on_tool_names": [
                "http_request",
                "spawn_web_task",
                "mcp_brightdata_search_engine",
                "mcp_brightdata_scrape_as_markdown",
            ],
            "stop_on_tool_names_after_finish": ["send_chat_message"],
            "max_relevant_tool_calls": 10,
        }

    def _record_managed_calls(self, run_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_managed_mcp_calls")
        calls = tool_calls_for_run(run_id, after=inbound.timestamp)
        hubspot_calls = [call for call in calls if call.tool_name in HUBSPOT_EVAL_TOOL_NAMES]
        ordered = hubspot_tools_appear_in_order(hubspot_calls, case.expected_hubspot_tools)
        unexpected = [
            call.tool_name
            for call in hubspot_calls
            if call.tool_name not in case.expected_hubspot_tools
        ]
        if ordered and not unexpected:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_managed_mcp_calls",
                observed_summary=(
                    "Agent used HubSpot account preflight before the expected managed MCP operation."
                    if case.expected_hubspot_tools
                    else "Agent made no HubSpot tool call while the managed connection was unavailable."
                ),
                artifacts={"step": hubspot_calls[0].step} if hubspot_calls else {},
            )
            return
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="verify_managed_mcp_calls",
            observed_summary=(
                f"Expected ordered HubSpot calls {case.expected_hubspot_tools}; "
                f"saw {[call.tool_name for call in hubspot_calls]}."
            ),
            artifacts={"step": hubspot_calls[0].step} if hubspot_calls else {},
        )

    def _record_no_legacy_paths(self, run_id: str, inbound) -> None:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_no_legacy_paths")
        calls = tool_calls_for_run(run_id, after=inbound.timestamp)
        bad_calls = [
            call
            for call in calls
            if call.tool_name in {"http_request", "spawn_web_task"}
            or call.tool_name.startswith(("hubspot-", "hubspot_", "mcp_brightdata_"))
        ]
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED if bad_calls else EvalRunTask.Status.PASSED,
            task_name="verify_no_legacy_paths",
            observed_summary=(
                f"Agent used forbidden HubSpot fallback tools: {[call.tool_name for call in bad_calls]}."
                if bad_calls
                else "Agent avoided HubSpot REST, Pipedream, browser, and web-search fallback paths."
            ),
            artifacts={"step": bad_calls[0].step} if bad_calls else {},
        )

    def _record_response(self, run_id: str, agent_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_response")
        final_response = (
            PersistentAgentMessage.objects.filter(
                owner_agent_id=agent_id,
                is_outbound=True,
                timestamp__gt=inbound.timestamp,
                conversation_id=inbound.conversation_id,
                to_endpoint_id=inbound.from_endpoint_id,
            )
            .order_by("-timestamp")
            .first()
        )
        body = final_response.body if final_response else ""
        missing_groups = [
            terms
            for terms in case.response_term_groups
            if not any(response_contains_term(body, term) for term in terms)
        ]
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED if missing_groups else EvalRunTask.Status.PASSED,
            task_name="verify_response",
            observed_summary=(
                f"Final response missing expected term groups {missing_groups}; body={body[:800]!r}."
                if missing_groups
                else "Final response reflected the managed HubSpot result or required setup action."
            ),
            artifacts={"message": final_response} if final_response else {},
        )

    def run(self, run_id: str, agent_id: str) -> None:
        case = self._case()
        self._prepare_agent(agent_id)
        fake_manager = _EvalMCPManager()

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with ExitStack() as stack:
            stack.enter_context(patch("api.agent.tools.search_tools.get_mcp_manager", return_value=fake_manager))
            stack.enter_context(patch("api.agent.tools.tool_manager.get_mcp_manager", return_value=fake_manager))
            if case.connected:
                stack.enter_context(
                    patch("api.agent.system_skills.defaults._native_connection_gate", return_value="")
                )
            with self.wait_for_agent_idle(agent_id, timeout=120):
                inbound = self.inject_message(
                    agent_id,
                    case.prompt,
                    trigger_processing=True,
                    eval_run_id=run_id,
                    mock_config=case.mock_config,
                    eval_stop_policy=self._eval_stop_policy(),
                )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed through the real agent harness.",
            artifacts={"message": inbound},
        )

        self._record_managed_calls(run_id, inbound)
        self._record_no_legacy_paths(run_id, inbound)
        self._record_response(run_id, agent_id, inbound)


for managed_case in HUBSPOT_MANAGED_MCP_CASES:
    scenario_type = type(
        "".join(part.title() for part in managed_case.slug.split("_")) + "Scenario",
        (HubSpotManagedMCPScenario,),
        {
            "slug": managed_case.slug,
            "description": managed_case.description,
            "tags": HubSpotManagedMCPScenario.tags + managed_case.tags,
            "case": managed_case,
        },
    )
    ScenarioRegistry.register(scenario_type())
