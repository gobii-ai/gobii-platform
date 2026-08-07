from dataclasses import dataclass, field
from typing import Any

from waffle import get_waffle_flag_model

from api.agent.tools.brightdata import BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME
from api.agent.tools.contactout import (
    CONTACTOUT_SYSTEM_SKILL_KEY,
    CONTACTOUT_TOOL_NAME,
    ENRICH_LINKEDIN_PROFILE,
    SEARCH_COMPANIES,
    SEARCH_PEOPLE,
)
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemSkillState,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
)
from constants.feature_flags import CONTACTOUT_PILOT


CONTACTOUT_PILOT_SUITE_SLUG = "contactout_pilot"

CONTACTOUT_PEOPLE_SEARCH_PROFILE_ONLY = "contactout_people_search_profile_only"
CONTACTOUT_LINKEDIN_PROFILE_ONLY = "contactout_linkedin_profile_only"
CONTACTOUT_EXPLICIT_CONTACT_REVEAL = "contactout_explicit_contact_reveal"
CONTACTOUT_COMPANY_SEARCH = "contactout_company_search"
CONTACTOUT_BRIGHTDATA_FALLBACK = "contactout_brightdata_fallback"

CONTACTOUT_PILOT_SCENARIO_SLUGS = (
    CONTACTOUT_PEOPLE_SEARCH_PROFILE_ONLY,
    CONTACTOUT_LINKEDIN_PROFILE_ONLY,
    CONTACTOUT_EXPLICIT_CONTACT_REVEAL,
    CONTACTOUT_COMPANY_SEARCH,
    CONTACTOUT_BRIGHTDATA_FALLBACK,
)

MESSAGE_TOOL_NAMES = ("send_chat_message", "send_email", "send_sms")


def _contactout_result(operation: str, content: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "success",
        "provider": "contactout",
        "operation": operation,
        "content": content,
    }


@dataclass(frozen=True)
class ContactOutPilotCase:
    slug: str
    description: str
    prompt: str
    mock_config: dict[str, Any]
    expected_operations: tuple[str, ...]
    required_response_terms: tuple[str, ...]
    explicit_contact_reveal: bool = False
    expected_contact_types: tuple[str, ...] = ()
    expect_brightdata_fallback: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)

    def eval_stop_policy(self) -> dict[str, Any]:
        allowed = {CONTACTOUT_TOOL_NAME, *MESSAGE_TOOL_NAMES}
        if self.expect_brightdata_fallback:
            allowed.add(BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME)
        return {
            "allowed_tool_names": sorted(allowed),
            "ignored_tool_names": ["sleep_until_next_trigger", "update_plan"],
            "stop_on_unexpected_relevant_tool": True,
            "max_relevant_tool_calls": 5,
            "stop_on_human_input_request": False,
        }


ADA_PROFILE = "https://www.linkedin.com/in/ada-lovelace-eval"

CONTACTOUT_PILOT_CASES = (
    ContactOutPilotCase(
        slug=CONTACTOUT_PEOPLE_SEARCH_PROFILE_ONLY,
        description="Prefer ContactOut for a covered people search without revealing paid contact data.",
        prompt=(
            "Find up to 10 current VP Engineering or CTO candidates in New York with Python and Django experience. "
            "I only need names, current titles, companies, locations, and LinkedIn profile URLs; do not fetch "
            "contact details."
        ),
        mock_config={
            CONTACTOUT_TOOL_NAME: _contactout_result(
                SEARCH_PEOPLE,
                {
                    "status_code": 200,
                    "profiles": {
                        ADA_PROFILE: {
                            "full_name": "Ada Lovelace",
                            "title": "VP Engineering",
                            "company": {"name": "Analytical Engines"},
                            "location": "New York, New York",
                        }
                    },
                },
            ),
            BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME: {
                "status": "success",
                "result": "unexpected duplicate BrightData lookup",
            },
        },
        expected_operations=(SEARCH_PEOPLE,),
        required_response_terms=("Ada Lovelace", "Analytical Engines"),
        tags=("people_search", "profile_only"),
    ),
    ContactOutPilotCase(
        slug=CONTACTOUT_LINKEDIN_PROFILE_ONLY,
        description="Use ContactOut profile-only enrichment for a known regular LinkedIn URL.",
        prompt=(
            f"Summarize this person's current title, company, location, and headline from {ADA_PROFILE}. "
            "Do not retrieve email addresses or phone numbers."
        ),
        mock_config={
            CONTACTOUT_TOOL_NAME: _contactout_result(
                ENRICH_LINKEDIN_PROFILE,
                {
                    "status_code": 200,
                    "profile": {
                        "full_name": "Ada Lovelace",
                        "title": "VP Engineering",
                        "company": "Analytical Engines",
                        "location": "New York, New York",
                        "headline": "Computing pioneer",
                    },
                },
            )
        },
        expected_operations=(ENRICH_LINKEDIN_PROFILE,),
        required_response_terms=("Ada Lovelace", "Computing pioneer"),
        tags=("linkedin", "profile_only"),
    ),
    ContactOutPilotCase(
        slug=CONTACTOUT_EXPLICIT_CONTACT_REVEAL,
        description="Reveal only explicitly requested work-email and phone data in a people search.",
        prompt=(
            "Find a current CTO at Analytical Engines in New York and retrieve their work email and phone number. "
            "I explicitly need both contact fields for this sourcing task."
        ),
        mock_config={
            CONTACTOUT_TOOL_NAME: _contactout_result(
                SEARCH_PEOPLE,
                {
                    "status_code": 200,
                    "profiles": {
                        ADA_PROFILE: {
                            "full_name": "Ada Lovelace",
                            "title": "CTO",
                            "contact_info": {
                                "work_email": ["ada@example.com"],
                                "phone": ["+1-212-555-0100"],
                            },
                        }
                    },
                },
            )
        },
        expected_operations=(SEARCH_PEOPLE,),
        explicit_contact_reveal=True,
        expected_contact_types=("work_email", "phone"),
        required_response_terms=("ada@example.com", "212-555-0100"),
        tags=("people_search", "contact_reveal"),
    ),
    ContactOutPilotCase(
        slug=CONTACTOUT_COMPANY_SEARCH,
        description="Prefer ContactOut for a covered filtered company search.",
        prompt=(
            "Find cybersecurity software companies in the United States with 51-200 employees that use AWS. "
            "Return the company name, domain, location, and LinkedIn URL."
        ),
        mock_config={
            CONTACTOUT_TOOL_NAME: _contactout_result(
                SEARCH_COMPANIES,
                {
                    "status_code": 200,
                    "companies": [
                        {
                            "name": "Cipher Harbor",
                            "domain": "cipherharbor.example",
                            "location": "Boston, Massachusetts",
                            "linkedin_url": "https://linkedin.com/company/cipher-harbor-eval",
                        }
                    ],
                },
            )
        },
        expected_operations=(SEARCH_COMPANIES,),
        required_response_terms=("Cipher Harbor", "cipherharbor.example"),
        tags=("company_search",),
    ),
    ContactOutPilotCase(
        slug=CONTACTOUT_BRIGHTDATA_FALLBACK,
        description="Fall back to BrightData only after ContactOut fails for the same known profile.",
        prompt=(
            f"Pull the current title, company, and location for {ADA_PROFILE}. Use the available structured data "
            "sources and finish the lookup even if the preferred provider is temporarily unavailable."
        ),
        mock_config={
            CONTACTOUT_TOOL_NAME: {
                "status": "error",
                "provider": "contactout",
                "operation": ENRICH_LINKEDIN_PROFILE,
                "message": "ContactOut API request timed out.",
                "retryable": True,
            },
            BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME: {
                "status": "success",
                "result": (
                    '[{"name":"Ada Lovelace","current_company":"Analytical Engines",'
                    '"position":"VP Engineering","city":"New York"}]'
                ),
            },
        },
        expected_operations=(ENRICH_LINKEDIN_PROFILE,),
        expect_brightdata_fallback=True,
        required_response_terms=("Ada Lovelace", "Analytical Engines"),
        tags=("fallback", "brightdata"),
    ),
)


def _relevant_calls(run_id: str, inbound) -> list[PersistentAgentToolCall]:
    return list(
        PersistentAgentToolCall.objects.filter(
            step__eval_run_id=run_id,
            step__created_at__gte=inbound.timestamp,
            tool_name__in=(CONTACTOUT_TOOL_NAME, BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME),
        )
        .select_related("step")
        .order_by("step__created_at", "step__id")
    )


def _response_bodies(run_id: str, agent_id: str, inbound) -> list[tuple[str, object]]:
    bodies: list[tuple[str, object]] = []
    for message in (
        PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            is_outbound=True,
            timestamp__gt=inbound.timestamp,
        ).order_by("seq")
    ):
        bodies.append((message.body or "", message))
    for call in (
        PersistentAgentToolCall.objects.filter(
            step__eval_run_id=run_id,
            step__created_at__gte=inbound.timestamp,
            tool_name__in=MESSAGE_TOOL_NAMES,
        )
        .select_related("step")
        .order_by("step__created_at", "step__id")
    ):
        params = call.tool_params or {}
        body = str(params.get("body") or params.get("message") or "")
        if body:
            bodies.append((body, call))
    return bodies


class ContactOutPilotScenario(EvalScenario, ScenarioExecutionTools):
    tier = "core"
    category = "contactout_pilot"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "system_skills"
    tags = ("contactout", "system_skill", "real_harness", "pilot")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_provider_routing", assertion_type="tool_call"),
        ScenarioTask(name="verify_contact_safety", assertion_type="tool_params"),
        ScenarioTask(name="verify_response", assertion_type="exact_match"),
    ]
    case: ContactOutPilotCase | None = None

    def _case(self) -> ContactOutPilotCase:
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
        PersistentAgentSystemStep.objects.create(step=step, code=PersistentAgentSystemStep.Code.PROCESS_EVENTS)

    def _prepare_agent(self, agent_id: str) -> None:
        PersistentAgent.objects.filter(id=agent_id).update(planning_state=PersistentAgent.PlanningState.SKIPPED)
        self._seed_prior_processing_run(agent_id)
        agent = PersistentAgent.objects.select_related("user").get(id=agent_id)
        flag, _ = get_waffle_flag_model().objects.get_or_create(
            name=CONTACTOUT_PILOT,
            defaults={"everyone": None},
        )
        flag.users.add(agent.user)
        result = mark_tool_enabled_without_discovery(agent, CONTACTOUT_TOOL_NAME)
        if result.get("status") != "success":
            raise ValueError(f"Could not enable eval ContactOut tool: {result}")
        if not PersistentAgentSystemSkillState.objects.filter(
            agent=agent,
            skill_key=CONTACTOUT_SYSTEM_SKILL_KEY,
            is_enabled=True,
        ).exists():
            raise ValueError("ContactOut system skill was not enabled with its native tool.")
        fallback_result = mark_tool_enabled_without_discovery(
            agent,
            BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME,
        )
        if fallback_result.get("status") != "success":
            raise ValueError(f"Could not enable BrightData comparison/fallback tool: {fallback_result}")

    def _record_provider_routing(self, run_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_provider_routing",
        )
        calls = _relevant_calls(run_id, inbound)
        names = [call.tool_name for call in calls]
        expected_names = [CONTACTOUT_TOOL_NAME]
        if case.expect_brightdata_fallback:
            expected_names.append(BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME)
        errors = []
        if names != expected_names:
            errors.append(f"expected provider sequence {expected_names}, saw {names}")
        contactout_calls = [call for call in calls if call.tool_name == CONTACTOUT_TOOL_NAME]
        operations = tuple(str((call.tool_params or {}).get("operation") or "") for call in contactout_calls)
        if operations != case.expected_operations:
            errors.append(f"expected ContactOut operations {case.expected_operations}, saw {operations}")
        if errors:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_provider_routing",
                observed_summary="; ".join(errors),
                artifacts={"step": calls[0].step} if calls else {},
            )
            return
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_provider_routing",
            observed_summary=f"Observed provider sequence {names}.",
            artifacts={"step": calls[0].step},
        )

    def _record_contact_safety(self, run_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_contact_safety",
        )
        contactout_calls = [
            call for call in _relevant_calls(run_id, inbound) if call.tool_name == CONTACTOUT_TOOL_NAME
        ]
        errors = []
        for call in contactout_calls:
            params = call.tool_params or {}
            reveal = params.get("include_contact_info") is True
            if reveal != case.explicit_contact_reveal:
                errors.append(
                    f"expected include_contact_info={case.explicit_contact_reveal}, "
                    f"saw {params.get('include_contact_info')!r}"
                )
            if case.expected_contact_types:
                actual_types = set(params.get("contact_data_types") or [])
                if actual_types != set(case.expected_contact_types):
                    errors.append(
                        f"expected contact_data_types {case.expected_contact_types}, saw {sorted(actual_types)}"
                    )
        if not contactout_calls:
            errors.append("no ContactOut call was recorded")
        status = EvalRunTask.Status.FAILED if errors else EvalRunTask.Status.PASSED
        self.record_task_result(
            run_id,
            None,
            status,
            task_name="verify_contact_safety",
            observed_summary="; ".join(errors) if errors else "Contact reveal matched the user's explicit request.",
            artifacts={"step": contactout_calls[0].step} if contactout_calls else {},
        )

    def _record_response(self, run_id: str, agent_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_response")
        responses = _response_bodies(run_id, agent_id, inbound)
        match = next(
            (
                (body, artifact)
                for body, artifact in reversed(responses)
                if all(term.lower() in body.lower() for term in case.required_response_terms)
            ),
            None,
        )
        if match is None:
            latest = responses[-1][0] if responses else ""
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_response",
                observed_summary=(
                    f"No response contained {case.required_response_terms}; latest response={latest[:800]!r}."
                ),
                artifacts={"response_artifact": responses[-1][1]} if responses else {},
            )
            return
        body, artifact = match
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_response",
            observed_summary="Agent returned the mocked provider facts.",
            artifacts={"response_artifact": artifact, "response_preview": body[:800]},
        )

    def run(self, run_id: str, agent_id: str) -> None:
        case = self._case()
        self._prepare_agent(agent_id)
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=180):
            inbound = self.inject_message(
                agent_id,
                case.prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=case.mock_config,
                eval_stop_policy=case.eval_stop_policy(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )
        self._record_provider_routing(run_id, inbound)
        self._record_contact_safety(run_id, inbound)
        self._record_response(run_id, agent_id, inbound)


for contactout_case in CONTACTOUT_PILOT_CASES:
    scenario_type = type(
        "".join(part.title() for part in contactout_case.slug.split("_")) + "Scenario",
        (ContactOutPilotScenario,),
        {
            "slug": contactout_case.slug,
            "description": contactout_case.description,
            "tags": ContactOutPilotScenario.tags + contactout_case.tags,
            "case": contactout_case,
        },
    )
    ScenarioRegistry.register(scenario_type())
