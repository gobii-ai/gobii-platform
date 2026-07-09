from dataclasses import dataclass, field
from typing import Any, Iterable

from api.agent.system_skills.defaults import RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY
from api.agent.system_skills.service import enable_system_skills
from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_SERVER, is_eval_synthetic_tool_name
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.native_http import response_contains_term
from api.models import EvalRunTask, PersistentAgent, PersistentAgentEnabledTool, PersistentAgentHumanInputRequest, PersistentAgentMessage, PersistentAgentStep, PersistentAgentSystemStep, PersistentAgentToolCall


RECRUITMENT_SOURCING_SUITE_SLUG = "recruitment_sourcing"

RECRUITMENT_SOURCING_INTAKE_GATES_SOURCING = "recruitment_sourcing_intake_gates_sourcing"
RECRUITMENT_SOURCING_CRITERIA_FIDELITY = "recruitment_sourcing_criteria_fidelity"
RECRUITMENT_SOURCING_SOURCE_FALLBACK = "recruitment_sourcing_source_fallback"
RECRUITMENT_SOURCING_DEDUPE_LEDGER = "recruitment_sourcing_dedupe_ledger"
RECRUITMENT_SOURCING_PARTIAL_VERIFICATION = "recruitment_sourcing_partial_verification"

RECRUITMENT_SOURCING_SCENARIO_SLUGS = (
    RECRUITMENT_SOURCING_INTAKE_GATES_SOURCING,
    RECRUITMENT_SOURCING_CRITERIA_FIDELITY,
    RECRUITMENT_SOURCING_SOURCE_FALLBACK,
    RECRUITMENT_SOURCING_DEDUPE_LEDGER,
    RECRUITMENT_SOURCING_PARTIAL_VERIFICATION,
)

MESSAGE_TOOL_NAMES = ("send_chat_message", "send_email", "send_sms")
NON_SOURCING_TOOL_NAMES = ("request_human_input", "update_plan", *MESSAGE_TOOL_NAMES)
SOURCING_TOOL_NAMES = (
    "search_tools",
    "apollo_io-search-contacts",
    "mcp_brightdata_search_engine",
    "mcp_brightdata_web_data_linkedin_people_search",
    "mcp_brightdata_web_data_linkedin_person_profile",
    "eval_verify_candidate_batch",
    "create_csv",
)


@dataclass(frozen=True)
class RecruitmentSourcingCase:
    slug: str
    description: str
    prompt: str
    mock_config: dict[str, Any]
    expected_tool_names: tuple[str, ...] = ()
    accepted_tool_alternatives: dict[str, tuple[str, ...]] = field(default_factory=dict)
    allowed_extra_tool_names: tuple[str, ...] = ()
    forbidden_tool_names: tuple[str, ...] = ()
    response_term_groups: tuple[tuple[str, ...], ...] = ()
    forbidden_response_terms: tuple[str, ...] = ()
    required_proximate_response_terms: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = ()
    tags: tuple[str, ...] = field(default_factory=tuple)
    max_relevant_tool_calls: int = 10
    stop_on_human_input_request: bool = False

    def tool_names_to_enable(self) -> tuple[str, ...]:
        alternative_tool_names = [
            alternative
            for alternatives in self.accepted_tool_alternatives.values()
            for alternative in alternatives
        ]
        return tuple(
            dict.fromkeys(
                [
                    "search_tools",
                    *self.expected_tool_names,
                    *alternative_tool_names,
                    *self.allowed_extra_tool_names,
                    *self.forbidden_tool_names,
                ]
            )
        )

    def eval_stop_policy(self) -> dict[str, Any]:
        allowed = set(NON_SOURCING_TOOL_NAMES)
        allowed.update(self.expected_tool_names)
        allowed.update(self.allowed_extra_tool_names)
        for alternatives in self.accepted_tool_alternatives.values():
            allowed.update(alternatives)
        allowed.add("search_tools")
        if self.expected_tool_names:
            allowed.add("sqlite_batch")
        if not self.expected_tool_names:
            allowed.discard("search_tools")

        return {
            "allowed_tool_names": sorted(allowed),
            "ignored_tool_names": ["sleep_until_next_trigger", "update_plan"],
            "stop_on_tool_names": list(self.forbidden_tool_names),
            "stop_on_unexpected_relevant_tool": True,
            "max_relevant_tool_calls": self.max_relevant_tool_calls,
            "accepted_tool_alternatives": {
                tool_name: list(alternatives)
                for tool_name, alternatives in self.accepted_tool_alternatives.items()
            },
            "stop_on_human_input_request": self.stop_on_human_input_request,
        }


def _linkedin_people_result(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "success",
        "result": items,
        "content": {"items": items, "match_count": len(items)},
    }


def _apollo_people_result(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "success",
        "contacts": items,
        "people": items,
        "content": {"contacts": items, "people": items, "match_count": len(items)},
    }


def _search_result(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "success",
        "result": {"kind": "serp", "items": items},
        "content": {"results": items, "match_count": len(items)},
    }


def _linkedin_profile_result(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "success",
        "result": profile,
        "content": profile,
    }


RECRUITMENT_SOURCING_CASES = (
    RecruitmentSourcingCase(
        slug=RECRUITMENT_SOURCING_INTAKE_GATES_SOURCING,
        description="Ask for missing role-specific screening material instead of sourcing from a thin brief.",
        prompt=(
            "Please start sourcing today for a Litigation Paralegal at Northlake Legal Group in Denver. "
            "It is onsite and Sara owns the role. I do not have the job posting, intake notes, required skills, "
            "or dealbreakers ready yet."
        ),
        mock_config={},
        forbidden_tool_names=SOURCING_TOOL_NAMES,
        response_term_groups=(
            ("job posting", "requirements", "required skills", "screening criteria", "dealbreakers"),
            ("provide", "share", "wait", "missing", "more details", "additional details", "questions", "screen"),
        ),
        tags=("intake",),
        max_relevant_tool_calls=3,
        stop_on_human_input_request=True,
    ),
    RecruitmentSourcingCase(
        slug=RECRUITMENT_SOURCING_CRITERIA_FIDELITY,
        description="Source a bounded batch and preserve hard requirements over tempting near matches.",
        prompt=(
            "Source a first pass of Assistant Project Manager or Project Manager candidates for a commercial "
            "retail construction GC role. Hard requirements: currently at BuildRight GC or Summit Retail Builders, "
            "located in OH, IN, KY, western PA, or Nashville TN, and exclude Estimators, Project Engineers, "
            "Superintendents, Directors, and VPs. Return fewer high-quality matches if needed."
        ),
        expected_tool_names=("mcp_brightdata_web_data_linkedin_people_search",),
        accepted_tool_alternatives={
            "mcp_brightdata_web_data_linkedin_people_search": ("apollo_io-search-contacts",),
        },
        mock_config={
            "mcp_brightdata_web_data_linkedin_people_search": _linkedin_people_result(
                [
                    {
                        "name": "Mina Patel",
                        "title": "Assistant Project Manager",
                        "company": "BuildRight GC",
                        "location": "Columbus, OH",
                        "url": "https://www.linkedin.com/in/mina-patel-eval",
                        "evidence": "Commercial retail construction project coordination.",
                    },
                    {
                        "name": "Priya Shah",
                        "title": "Project Manager",
                        "company": "Summit Retail Builders",
                        "location": "Nashville, TN",
                        "url": "https://www.linkedin.com/in/priya-shah-eval",
                        "evidence": "Retail buildout GC-side project delivery.",
                    },
                    {
                        "name": "Evan Brooks",
                        "title": "Estimator",
                        "company": "BuildRight GC",
                        "location": "Indianapolis, IN",
                        "url": "https://www.linkedin.com/in/evan-brooks-eval",
                        "evidence": "Estimator title is excluded.",
                    },
                    {
                        "name": "Dana Lee",
                        "title": "Project Manager",
                        "company": "Summit Retail Builders",
                        "location": "Charlotte, NC",
                        "url": "https://www.linkedin.com/in/dana-lee-eval",
                        "evidence": "Outside approved geography.",
                    },
                ]
            ),
            "apollo_io-search-contacts": _apollo_people_result(
                [
                    {
                        "name": "Mina Patel",
                        "title": "Assistant Project Manager",
                        "company": "BuildRight GC",
                        "location": "Columbus, OH",
                        "linkedin_url": "https://www.linkedin.com/in/mina-patel-eval",
                        "evidence": "Commercial retail construction project coordination.",
                    },
                    {
                        "name": "Priya Shah",
                        "title": "Project Manager",
                        "company": "Summit Retail Builders",
                        "location": "Nashville, TN",
                        "linkedin_url": "https://www.linkedin.com/in/priya-shah-eval",
                        "evidence": "Retail buildout GC-side project delivery.",
                    },
                    {
                        "name": "Evan Brooks",
                        "title": "Estimator",
                        "company": "BuildRight GC",
                        "location": "Indianapolis, IN",
                        "linkedin_url": "https://www.linkedin.com/in/evan-brooks-eval",
                        "evidence": "Estimator title is excluded.",
                    },
                    {
                        "name": "Dana Lee",
                        "title": "Project Manager",
                        "company": "Summit Retail Builders",
                        "location": "Charlotte, NC",
                        "linkedin_url": "https://www.linkedin.com/in/dana-lee-eval",
                        "evidence": "Outside approved geography.",
                    },
                ]
            ),
        },
        response_term_groups=(
            ("Mina Patel",),
            ("Priya Shah",),
            ("Estimator", "excluded", "exclude"),
        ),
        required_proximate_response_terms=(
            (
                ("Dana Lee", "dana-lee-eval"),
                ("outside approved geography", "outside geography", "not approved geography", "outside approved"),
            ),
        ),
        tags=("criteria",),
    ),
    RecruitmentSourcingCase(
        slug=RECRUITMENT_SOURCING_SOURCE_FALLBACK,
        description="Use available web/LinkedIn-like sources when Apollo is unavailable instead of blocking.",
        prompt=(
            "Apollo is not connected for this agent. Use available web or LinkedIn-style sources to find attorney "
            "recruiters similar to NALSC boutiques and legal staffing firms. The example companies are archetypes, "
            "not the full target list. Return a small first batch with source links."
        ),
        expected_tool_names=("mcp_brightdata_search_engine",),
        accepted_tool_alternatives={
            "mcp_brightdata_search_engine": ("mcp_brightdata_web_data_linkedin_people_search",),
        },
        allowed_extra_tool_names=(
            "mcp_brightdata_web_data_linkedin_company_profile",
            "mcp_brightdata_web_data_linkedin_person_profile",
            "mcp_brightdata_web_data_linkedin_job_listings",
            "http_request",
        ),
        forbidden_tool_names=("apollo_io-search-contacts",),
        mock_config={
            "mcp_brightdata_search_engine": _search_result(
                [
                    {
                        "t": "Carolina Vega - Legal Recruiter",
                        "u": "https://www.linkedin.com/in/carolina-vega-eval",
                        "p": 1,
                    },
                    {
                        "t": "NorthStar Legal Search - Attorney Recruiting",
                        "u": "https://northstarlegal.example.test/team",
                        "p": 2,
                    },
                    {
                        "t": "Jordan Kim - Partner, Attorney Search",
                        "u": "https://www.linkedin.com/in/jordan-kim-eval",
                        "p": 3,
                    },
                ]
            ),
            "mcp_brightdata_web_data_linkedin_people_search": _linkedin_people_result(
                [
                    {
                        "name": "Carolina Vega",
                        "title": "Legal Recruiter",
                        "company": "NALSC-style boutique legal search firm",
                        "location": "Charlotte, NC",
                        "url": "https://www.linkedin.com/in/carolina-vega-eval",
                        "evidence": "Attorney recruiting profile similar to boutique legal staffing archetypes.",
                    },
                    {
                        "name": "Jordan Kim",
                        "title": "Partner, Attorney Search",
                        "company": "NorthStar Legal Search",
                        "location": "Atlanta, GA",
                        "url": "https://www.linkedin.com/in/jordan-kim-eval",
                        "evidence": "Partner-level attorney search profile at a legal recruiting firm.",
                    },
                ]
            ),
            "mcp_brightdata_web_data_linkedin_company_profile": {
                "status": "success",
                "result": [
                    {
                        "name": "NorthStar Legal Search",
                        "url": "https://northstarlegal.example.test/team",
                        "description": "Attorney search and boutique legal recruiting team.",
                    },
                    {
                        "name": "NALSC-style boutique legal search firms",
                        "url": "https://www.nalsc.org/eval-directory",
                        "description": "Archetype source for legal recruiting boutiques.",
                    },
                ],
                "content": {
                    "items": [
                        {
                            "name": "NorthStar Legal Search",
                            "url": "https://northstarlegal.example.test/team",
                            "description": "Attorney search and boutique legal recruiting team.",
                        },
                        {
                            "name": "NALSC-style boutique legal search firms",
                            "url": "https://www.nalsc.org/eval-directory",
                            "description": "Archetype source for legal recruiting boutiques.",
                        },
                    ],
                    "match_count": 2,
                },
            },
            "mcp_brightdata_web_data_linkedin_person_profile": _linkedin_profile_result(
                {
                    "name": "Carolina Vega",
                    "title": "Legal Recruiter",
                    "company": "NALSC-style boutique legal search firm",
                    "location": "Charlotte, NC",
                    "url": "https://www.linkedin.com/in/carolina-vega-eval",
                    "evidence": "Attorney recruiting profile similar to boutique legal staffing archetypes.",
                }
            ),
            "mcp_brightdata_web_data_linkedin_job_listings": {
                "status": "success",
                "result": [
                    {
                        "title": "Attorney Recruiter",
                        "company": "NorthStar Legal Search",
                        "url": "https://northstarlegal.example.test/jobs/attorney-recruiter",
                        "description": "Legal recruiting role at a boutique attorney search firm.",
                    },
                    {
                        "title": "Legal Search Consultant",
                        "company": "NALSC-style boutique legal search firm",
                        "url": "https://www.nalsc.org/eval-directory",
                        "description": "Archetype posting for attorney search and legal staffing.",
                    },
                ],
                "content": {
                    "items": [
                        {
                            "title": "Attorney Recruiter",
                            "company": "NorthStar Legal Search",
                            "url": "https://northstarlegal.example.test/jobs/attorney-recruiter",
                            "description": "Legal recruiting role at a boutique attorney search firm.",
                        },
                        {
                            "title": "Legal Search Consultant",
                            "company": "NALSC-style boutique legal search firm",
                            "url": "https://www.nalsc.org/eval-directory",
                            "description": "Archetype posting for attorney search and legal staffing.",
                        },
                    ],
                    "match_count": 2,
                },
            },
            "apollo_io-search-contacts": {
                "status": "error",
                "message": "Apollo must not be used in this fallback eval.",
            },
            "http_request": {
                "rules": [
                    {
                        "url_contains": "nalsc.org/eval-directory",
                        "url_not_contains": ("apollo", "api.apollo.io"),
                        "result": {
                            "status": "success",
                            "content": (
                                "NALSC-style directory archetype for boutique legal search firms. "
                                "NorthStar Legal Search focuses on attorney recruiting and legal staffing."
                            ),
                            "body": (
                                "NALSC-style directory archetype for boutique legal search firms. "
                                "NorthStar Legal Search focuses on attorney recruiting and legal staffing."
                            ),
                            "url": "https://www.nalsc.org/eval-directory",
                        },
                    },
                    {
                        "url_contains": "northstarlegal.example.test",
                        "url_not_contains": ("apollo", "api.apollo.io"),
                        "result": {
                            "status": "success",
                            "content": "NorthStar Legal Search is an attorney search and boutique legal recruiting team.",
                            "body": "NorthStar Legal Search is an attorney search and boutique legal recruiting team.",
                            "url": "https://northstarlegal.example.test/team",
                        },
                    },
                ],
                "default": {
                    "status": "error",
                    "message": "HTTP fallback mock only permits non-Apollo legal-search directory pages.",
                },
            },
        },
        response_term_groups=(
            ("Carolina Vega", "Jordan Kim", "NorthStar"),
            ("archetype", "similar", "legal"),
        ),
        tags=("fallback",),
        max_relevant_tool_calls=14,
    ),
    RecruitmentSourcingCase(
        slug=RECRUITMENT_SOURCING_DEDUPE_LEDGER,
        description="Consult the candidate ledger and distinguish new candidates from duplicates or prior rejects.",
        prompt=(
            "Continue the Customer Success Manager sourcing pipeline. Before delivery, check the candidate ledger "
            "and only report which queued profiles are new versus duplicates or previously rejected. Do not source "
            "new people until the ledger is checked."
        ),
        expected_tool_names=("sqlite_batch",),
        forbidden_tool_names=(
            "mcp_brightdata_search_engine",
            "mcp_brightdata_web_data_linkedin_people_search",
            "apollo_io-search-contacts",
        ),
        mock_config={
            "sqlite_batch": {
                "status": "ok",
                "results": [
                    {
                        "result": [
                            {
                                "candidate_name": "Harper Nguyen",
                                "profile_url": "https://www.linkedin.com/in/harper-nguyen-eval",
                                "status": "NEW",
                                "reason": "No prior delivery or rejection in ledger.",
                            },
                            {
                                "candidate_name": "Jordan Blake",
                                "profile_url": "https://www.linkedin.com/in/jordan-blake-eval",
                                "status": "DUPLICATE",
                                "reason": "Delivered in prior batch.",
                            },
                            {
                                "candidate_name": "Riley Chen",
                                "profile_url": "https://www.linkedin.com/in/riley-chen-eval",
                                "status": "REJECTED",
                                "reason": "Recruiter rejected for wrong segment.",
                            },
                        ],
                        "message": "Query returned 3 queued candidate statuses.",
                    }
                ],
            }
        },
        response_term_groups=(
            ("Harper Nguyen",),
            ("Jordan Blake", "duplicate"),
            ("Riley Chen", "rejected"),
        ),
        tags=("dedupe", "ledger"),
    ),
    RecruitmentSourcingCase(
        slug=RECRUITMENT_SOURCING_PARTIAL_VERIFICATION,
        description="Report a verified partial candidate set and the remaining-work limitation.",
        prompt=(
            "Verify the next queued batch of captive insurance candidates from State Farm, Allstate, "
            "American Family, and Farmers in South/Southeast Wisconsin with less than 5 years tenure. "
            "I asked for 15; if source access only verifies a partial set, report the verified candidates "
            "and keep the remaining verification from getting lost."
        ),
        expected_tool_names=("eval_verify_candidate_batch",),
        mock_config={
            "eval_verify_candidate_batch": {
                "status": "partial",
                "verified_candidates": [
                    {
                        "name": "Kathleen Clatworthy",
                        "company": "American Family",
                        "location": "Janesville, WI",
                        "tenure": "2 years",
                        "linkedin_url": "https://www.linkedin.com/in/kathleen-clatworthy-eval",
                    },
                    {
                        "name": "Rogelio Perez",
                        "company": "Farmers Insurance",
                        "location": "Kenosha, WI",
                        "tenure": "1 year",
                        "linkedin_url": "https://www.linkedin.com/in/rogelio-perez-eval",
                    },
                ],
                "blocked_reason": "Most public LinkedIn pages did not expose start dates or tenure.",
                "remaining_work": 13,
                "next_cursor": "candidate-offset-2",
                "next_action": (
                    "Report the verified partial set with the source limitation, then continue bounded verification "
                    "or preserve the next cursor."
                ),
            }
        },
        response_term_groups=(
            ("Kathleen Clatworthy",),
            ("Rogelio Perez",),
            ("partial", "remaining", "source limitation", "could not verify"),
        ),
        tags=("partial", "verification"),
    ),
)


def _tool_calls_for_run(run_id: str, *, after=None, tool_names: Iterable[str] | None = None):
    queryset = PersistentAgentToolCall.objects.filter(step__eval_run_id=run_id)
    if after is not None:
        queryset = queryset.filter(step__created_at__gte=after)
    if tool_names is not None:
        queryset = queryset.filter(tool_name__in=list(tool_names))
    return list(queryset.select_related("step").order_by("step__created_at", "step__id"))


def _tool_name_satisfies_expected(call: PersistentAgentToolCall, expected_tool_name: str, case: RecruitmentSourcingCase) -> bool:
    accepted_names = {expected_tool_name, *case.accepted_tool_alternatives.get(expected_tool_name, ())}
    return call.tool_name in accepted_names and call.status == "complete"


def _message_tool_body(call: PersistentAgentToolCall) -> str:
    params = call.tool_params or {}
    return str(params.get("body") or "")


def _candidate_response_bodies(run_id: str, agent_id: str, inbound) -> list[tuple[str, object]]:
    bodies: list[tuple[str, object]] = []
    for message in (
        PersistentAgentMessage.objects
        .filter(owner_agent_id=agent_id, is_outbound=True, timestamp__gt=inbound.timestamp)
        .order_by("seq")
    ):
        bodies.append((message.body or "", message))

    for call in _tool_calls_for_run(run_id, after=inbound.timestamp, tool_names=MESSAGE_TOOL_NAMES):
        body = _message_tool_body(call)
        if body:
            bodies.append((body, call))

    for request in (
        PersistentAgentHumanInputRequest.objects
        .filter(agent_id=agent_id, originating_step__eval_run_id=run_id, created_at__gt=inbound.timestamp)
        .order_by("created_at", "id")
    ):
        bodies.append((request.question or "", request))

    return bodies


def _contains_proximate_terms(body: str, anchor_terms: tuple[str, ...], context_terms: tuple[str, ...]) -> bool:
    normalized = body.lower()
    context_window_chars = 320
    for anchor in anchor_terms:
        anchor_text = anchor.lower()
        start = normalized.find(anchor_text)
        while start != -1:
            window_start = max(0, start - context_window_chars)
            window_end = min(len(normalized), start + len(anchor_text) + context_window_chars)
            window = normalized[window_start:window_end]
            if any(context.lower() in window for context in context_terms):
                return True
            start = normalized.find(anchor_text, start + len(anchor_text))
    return False


class RecruitmentSourcingScenario(EvalScenario, ScenarioExecutionTools):
    tier = "core"
    category = "recruitment_sourcing"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "system_skills"
    tags = ("recruitment_sourcing", "system_skill", "micro")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_expected_tools", assertion_type="tool_call"),
        ScenarioTask(name="verify_forbidden_tools", assertion_type="tool_call"),
        ScenarioTask(name="verify_response", assertion_type="exact_match"),
    ]
    case: RecruitmentSourcingCase | None = None

    def _case(self) -> RecruitmentSourcingCase:
        if self.case is None:
            raise ValueError(f"{type(self).__name__}.case must be set.")
        return self.case

    def _seed_prior_processing_run(self, agent_id: str) -> None:
        if PersistentAgentSystemStep.objects.filter(
            step__agent_id=agent_id,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        ).exists():
            return
        step = PersistentAgentStep.objects.create(agent_id=agent_id, description="Process events")
        PersistentAgentSystemStep.objects.create(step=step, code=PersistentAgentSystemStep.Code.PROCESS_EVENTS)

    def _enable_tools(self, agent_id: str, tool_names: Iterable[str]) -> None:
        agent = PersistentAgent.objects.get(id=agent_id)
        for tool_name in dict.fromkeys(tool_names):
            mark_tool_enabled_without_discovery(agent, tool_name)
            if is_eval_synthetic_tool_name(tool_name):
                PersistentAgentEnabledTool.objects.filter(
                    agent=agent,
                    tool_full_name=tool_name,
                ).update(
                    tool_server=EVAL_SYNTHETIC_TOOL_SERVER,
                    tool_name=tool_name,
                )

    def _prepare_agent(self, agent_id: str) -> None:
        PersistentAgent.objects.filter(id=agent_id).update(planning_state=PersistentAgent.PlanningState.SKIPPED)
        self._seed_prior_processing_run(agent_id)
        case = self._case()
        self._enable_tools(agent_id, case.tool_names_to_enable())
        agent = PersistentAgent.objects.get(id=agent_id)
        result = enable_system_skills(agent, [RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY])
        if result.get("invalid"):
            raise ValueError(f"Could not enable Recruitment Sourcing system skill: {result}")

    def _record_expected_tools(self, run_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_expected_tools")
        if not case.expected_tool_names:
            calls = _tool_calls_for_run(run_id, after=inbound.timestamp, tool_names=SOURCING_TOOL_NAMES)
            if calls:
                self.record_task_result(
                    run_id,
                    None,
                    EvalRunTask.Status.FAILED,
                    task_name="verify_expected_tools",
                    observed_summary=f"Agent sourced before intake was complete: {[call.tool_name for call in calls]}.",
                    artifacts={"step": calls[0].step},
                )
                return
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_expected_tools",
                observed_summary="Agent did not call sourcing tools before intake was complete.",
            )
            return

        calls = _tool_calls_for_run(run_id, after=inbound.timestamp)
        missing = [
            tool_name
            for tool_name in case.expected_tool_names
            if not any(_tool_name_satisfies_expected(call, tool_name, case) for call in calls)
        ]
        if missing:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_expected_tools",
                observed_summary=f"Missing expected sourcing tool(s): {missing}.",
                artifacts={"step": calls[0].step} if calls else {},
            )
            return

        expected_summary = ", ".join(
            (
                f"{tool_name} or {case.accepted_tool_alternatives[tool_name]}"
                if case.accepted_tool_alternatives.get(tool_name)
                else tool_name
            )
            for tool_name in case.expected_tool_names
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_expected_tools",
            observed_summary=f"Agent used the expected sourcing or ledger tool(s): {expected_summary}.",
            artifacts={"step": calls[0].step} if calls else {},
        )

    def _record_forbidden_tools(self, run_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_forbidden_tools")
        calls = _tool_calls_for_run(run_id, after=inbound.timestamp, tool_names=case.forbidden_tool_names)
        if calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_forbidden_tools",
                observed_summary=f"Agent used forbidden tool(s): {[call.tool_name for call in calls]}.",
                artifacts={"step": calls[0].step},
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_forbidden_tools",
            observed_summary="Agent avoided forbidden sourcing tools for this case.",
        )

    def _record_response(self, run_id: str, agent_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_response")
        response_bodies = _candidate_response_bodies(run_id, agent_id, inbound)
        matched_body = ""
        matched_artifact = None
        final_missing_groups = list(case.response_term_groups)
        final_missing_proximate_groups = list(case.required_proximate_response_terms)
        final_forbidden_terms: list[str] = []
        for body, artifact in response_bodies:
            missing_groups = [
                terms
                for terms in case.response_term_groups
                if not any(response_contains_term(body, term) for term in terms)
            ]
            missing_proximate_groups = [
                terms
                for terms in case.required_proximate_response_terms
                if not _contains_proximate_terms(body, terms[0], terms[1])
            ]
            forbidden_terms = [term for term in case.forbidden_response_terms if response_contains_term(body, term)]
            final_missing_groups = missing_groups
            final_missing_proximate_groups = missing_proximate_groups
            final_forbidden_terms = forbidden_terms
            if not missing_groups and not missing_proximate_groups and not forbidden_terms:
                matched_body = body
                matched_artifact = artifact
                break

        if final_missing_groups or final_missing_proximate_groups or final_forbidden_terms:
            latest_body = response_bodies[-1][0] if response_bodies else ""
            latest_artifact = response_bodies[-1][1] if response_bodies else None
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_response",
                observed_summary=(
                    f"Missing expected term group(s) {final_missing_groups}; "
                    f"missing proximate term group(s) {final_missing_proximate_groups}; "
                    f"forbidden response terms present {final_forbidden_terms}; body={latest_body[:800]!r}."
                ),
                artifacts={"response_artifact": latest_artifact} if latest_artifact else {},
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_response",
            observed_summary="Agent response reflected the sourcing guardrail under test.",
            artifacts={"response_artifact": matched_artifact, "response_preview": matched_body[:800]} if matched_artifact else {},
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

        self._record_expected_tools(run_id, inbound)
        self._record_forbidden_tools(run_id, inbound)
        self._record_response(run_id, agent_id, inbound)


for recruitment_case in RECRUITMENT_SOURCING_CASES:
    scenario_type = type(
        "".join(part.title() for part in recruitment_case.slug.split("_")) + "Scenario",
        (RecruitmentSourcingScenario,),
        {
            "slug": recruitment_case.slug,
            "description": recruitment_case.description,
            "tags": RecruitmentSourcingScenario.tags + recruitment_case.tags,
            "case": recruitment_case,
        },
    )
    ScenarioRegistry.register(scenario_type())
