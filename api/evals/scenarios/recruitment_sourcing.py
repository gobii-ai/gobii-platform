import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable

from api.agent.system_skills.defaults import RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY
from api.agent.system_skills.service import enable_system_skills
from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_SERVER, is_eval_synthetic_tool_name
from api.agent.tools.sqlite_state import agent_sqlite_db
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.native_http import response_contains_term
from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentHumanInputRequest,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
)


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
RECRUITMENT_RETRIEVAL_TOOL_NAMES = (
    "apollo_io-search-contacts",
    "eval_verify_candidate_batch",
    "http_request",
    "mcp_brightdata_search_engine",
    "mcp_brightdata_web_data_linkedin_company_profile",
    "mcp_brightdata_web_data_linkedin_job_listings",
    "mcp_brightdata_web_data_linkedin_people_search",
    "mcp_brightdata_web_data_linkedin_person_profile",
)

DEDUPE_QUEUE_ROWS = (
    ("Harper Nguyen", "https://www.linkedin.com/in/harper-nguyen-eval"),
    ("Jordan Blake", "https://www.linkedin.com/in/jordan-blake-eval"),
    ("Riley Chen", "https://www.linkedin.com/in/riley-chen-eval"),
)
DEDUPE_LEDGER_ROWS = (
    ("https://www.linkedin.com/in/jordan-blake-eval", "DUPLICATE", "Delivered in prior batch."),
    ("https://www.linkedin.com/in/riley-chen-eval", "REJECTED", "Recruiter rejected for wrong segment."),
)
SOURCE_FALLBACK_PEOPLE = (
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
    forbidden_without_proximate_response_terms: tuple[
        tuple[tuple[str, ...], tuple[str, ...]], ...
    ] = ()
    required_candidate_link_pairs: tuple[tuple[str, str], ...] = ()
    min_candidate_link_pairs: int = 0
    tags: tuple[str, ...] = field(default_factory=tuple)
    max_relevant_tool_calls: int = 10
    max_retrieval_tool_calls: int = 4
    stop_on_human_input_request: bool = False
    require_bounded_missing_material_request: bool = False
    require_partial_resume_state: bool = False

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
        ),
        tags=("intake",),
        max_relevant_tool_calls=3,
        max_retrieval_tool_calls=0,
        stop_on_human_input_request=True,
        require_bounded_missing_material_request=True,
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
        },
        response_term_groups=(
            ("Mina Patel",),
            ("Priya Shah",),
        ),
        forbidden_response_terms=("evan-brooks-eval", "dana-lee-eval"),
        forbidden_without_proximate_response_terms=(
            (
                ("Evan Brooks",),
                ("excluded", "exclude", "not qualified", "does not qualify", "ineligible"),
            ),
            (
                ("Dana Lee",),
                ("excluded", "outside", "not qualified", "does not qualify", "ineligible"),
            ),
        ),
        tags=("criteria",),
        max_retrieval_tool_calls=4,
    ),
    RecruitmentSourcingCase(
        slug=RECRUITMENT_SOURCING_SOURCE_FALLBACK,
        description="Use available web/LinkedIn-like sources when Apollo is unavailable instead of blocking.",
        prompt=(
            "Apollo is not connected for this agent. Use available web or LinkedIn-style sources to find attorney "
            "recruiters similar to NALSC boutiques and legal staffing firms. The example companies are archetypes, "
            "not the full target list. Return up to two strong profiles in the first batch with source links."
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
            "mcp_brightdata_web_data_linkedin_people_search": _linkedin_people_result(list(SOURCE_FALLBACK_PEOPLE)),
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
            "mcp_brightdata_web_data_linkedin_person_profile": {
                "rules": [
                    {
                        "param_contains": {"url": "carolina-vega-eval"},
                        "result": _linkedin_profile_result(SOURCE_FALLBACK_PEOPLE[0]),
                    },
                    {
                        "param_contains": {"url": "jordan-kim-eval"},
                        "result": _linkedin_profile_result(SOURCE_FALLBACK_PEOPLE[1]),
                    },
                ],
                "default": {"status": "error", "message": "No matching mocked LinkedIn profile."},
            },
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
            ("Carolina Vega", "Jordan Kim"),
            ("archetype", "similar", "legal"),
        ),
        required_candidate_link_pairs=tuple(
            (str(person["name"]), str(person["url"]))
            for person in SOURCE_FALLBACK_PEOPLE
        ),
        min_candidate_link_pairs=1,
        tags=("fallback",),
        max_relevant_tool_calls=14,
        max_retrieval_tool_calls=6,
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
        mock_config={},
        response_term_groups=(
            ("Harper Nguyen",),
            ("Jordan Blake",),
            ("Riley Chen",),
        ),
        required_proximate_response_terms=(
            (("Harper Nguyen",), ("new",)),
            (("Jordan Blake",), ("duplicate",)),
            (("Riley Chen",), ("rejected",)),
        ),
        tags=("dedupe", "ledger"),
        max_retrieval_tool_calls=0,
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
                    "Report the verified partial set with the source limitation and preserve the next cursor for a later "
                    "batch; do not rerun the current batch."
                ),
            }
        },
        response_term_groups=(
            ("Kathleen Clatworthy",),
            ("Rogelio Perez",),
            ("partial", "remaining", "source limitation", "could not verify"),
        ),
        tags=("partial", "verification"),
        max_retrieval_tool_calls=1,
        require_partial_resume_state=True,
    ),
)


def _tool_calls_for_run(run_id: str, *, after=None, tool_names: Iterable[str] | None = None):
    queryset = PersistentAgentToolCall.objects.filter(step__eval_run_id=run_id)
    if after is not None:
        queryset = queryset.filter(step__created_at__gte=after)
    if tool_names is not None:
        queryset = queryset.filter(tool_name__in=list(tool_names))
    return list(queryset.select_related("step").order_by("step__created_at", "step__id"))


def _normalize_retrieval_signature_value(value):
    if isinstance(value, dict):
        return {
            key: _normalize_retrieval_signature_value(item)
            for key, item in sorted(value.items())
            if key != "will_continue_work"
        }
    if isinstance(value, list):
        normalized_items = [_normalize_retrieval_signature_value(item) for item in value]
        return sorted(
            normalized_items,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), default=str),
        )
    return value


def _retrieval_call_signature(call: PersistentAgentToolCall) -> str:
    normalized_params = _normalize_retrieval_signature_value(call.tool_params or {})
    params_json = json.dumps(normalized_params, sort_keys=True, separators=(",", ":"), default=str)
    return f"{call.tool_name}:{params_json}"


def _duplicate_retrieval_signatures(calls: Iterable[PersistentAgentToolCall]) -> tuple[str, ...]:
    seen = set()
    duplicates = []
    for call in calls:
        signature = _retrieval_call_signature(call)
        if signature in seen and signature not in duplicates:
            duplicates.append(signature)
        seen.add(signature)
    return tuple(duplicates)


def _tool_call_has_usable_result(call: PersistentAgentToolCall) -> bool:
    if str(call.status or "").casefold() != "complete":
        return False
    result = call.result
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return bool(result.strip())
    if not isinstance(result, dict) or result.get("error"):
        return False
    failure_statuses = {"error", "failed", "failure", "warning", "pending", "cancelled", "canceled"}
    payloads = [result]
    if isinstance(result.get("result"), dict):
        payloads.append(result["result"])
    return all(
        str(payload.get("status") or "").casefold() not in failure_statuses
        and not payload.get("error")
        for payload in payloads
    )


def _tool_name_satisfies_expected(call: PersistentAgentToolCall, expected_tool_name: str, case: RecruitmentSourcingCase) -> bool:
    accepted_names = {expected_tool_name, *case.accepted_tool_alternatives.get(expected_tool_name, ())}
    return call.tool_name in accepted_names and _tool_call_has_usable_result(call)


def _ledger_query_is_substantive(call: PersistentAgentToolCall) -> bool:
    if call.tool_name != "sqlite_batch" or call.status != "complete":
        return False
    params = call.tool_params if isinstance(call.tool_params, dict) else {}
    sql_value = params.get("sql") or params.get("query") or params.get("queries") or ""
    if isinstance(sql_value, (list, tuple)):
        sql = "\n".join(str(value) for value in sql_value)
    else:
        sql = str(sql_value)
    lowered = sql.lower()
    if not (
        "candidate_queue" in lowered
        and "candidate_ledger" in lowered
        and (" join " in lowered or " exists" in lowered)
    ):
        return False
    result = (call.result or "").lower()
    return all(
        term.lower() in result
        for term in ("Harper Nguyen", "Jordan Blake", "Riley Chen", "NEW", "DUPLICATE", "REJECTED")
    )


def _message_tool_body(call: PersistentAgentToolCall) -> str:
    params = call.tool_params or {}
    return str(params.get("body") or "")


def _human_input_request_body(request: PersistentAgentHumanInputRequest) -> str:
    """Return all user-visible request text, not only the top-level prompt."""
    parts = [request.question or ""]
    for option in request.options_json or ():
        if not isinstance(option, dict):
            continue
        parts.extend((str(option.get("title") or ""), str(option.get("description") or "")))
    return "\n".join(part for part in parts if part)


def _group_human_input_requests(
    requests: Iterable[PersistentAgentHumanInputRequest],
) -> list[tuple[object, str, PersistentAgentHumanInputRequest]]:
    """Combine child questions emitted by one request_human_input tool step."""
    grouped: dict[tuple[str, object], list[PersistentAgentHumanInputRequest]] = {}
    for request in requests:
        originating_step_id = getattr(request, "originating_step_id", None)
        key = (
            ("step", originating_step_id)
            if originating_step_id is not None
            else ("request", getattr(request, "id", id(request)))
        )
        grouped.setdefault(key, []).append(request)

    interactions = []
    for child_requests in grouped.values():
        child_requests.sort(key=lambda request: (request.created_at, str(getattr(request, "id", ""))))
        visible_bodies = list(dict.fromkeys(_human_input_request_body(request) for request in child_requests))
        interactions.append(
            (
                child_requests[0].created_at,
                "\n".join(body for body in visible_bodies if body),
                child_requests[0],
            )
        )
    interactions.sort(key=lambda interaction: interaction[0])
    return interactions


def _is_bounded_missing_material_request(
    body: str,
    *,
    response_count: int,
    tracked_request_count: int,
) -> bool:
    normalized = " ".join(str(body or "").casefold().split())
    mentions_missing_material = any(
        term in normalized
        for term in (
            "job posting",
            "requirements",
            "required skills",
            "screening criteria",
            "dealbreakers",
            "intake notes",
        )
    )
    requests_material = bool(
        "?" in normalized
        or re.search(
            r"\b(?:please\s+)?(?:share|send|provide|upload|paste)\b|"
            r"\b(?:could|can|would|will)\s+you\b",
            normalized,
        )
    )
    bounded_delivery = (
        1 <= tracked_request_count <= 3
        if tracked_request_count
        else response_count == 1
    )
    return mentions_missing_material and requests_material and bounded_delivery


def _candidate_response_bodies(run_id: str, agent_id: str, inbound) -> list[tuple[str, object]]:
    candidates: list[tuple[object, int, str, object]] = []
    for message in (
        PersistentAgentMessage.objects
        .filter(owner_agent_id=agent_id, is_outbound=True, timestamp__gt=inbound.timestamp)
        .order_by("seq")
    ):
        candidates.append((message.timestamp, 0, message.body or "", message))

    for call in _tool_calls_for_run(run_id, after=inbound.timestamp, tool_names=MESSAGE_TOOL_NAMES):
        body = _message_tool_body(call)
        if body:
            candidates.append((call.step.created_at, 1, body, call))

    human_input_requests = list(
        PersistentAgentHumanInputRequest.objects
        .filter(agent_id=agent_id, originating_step__eval_run_id=run_id, created_at__gt=inbound.timestamp)
        .order_by("created_at", "id")
    )
    for created_at, body, request in _group_human_input_requests(human_input_requests):
        candidates.append((created_at, 2, body, request))

    candidates.sort(key=lambda item: (item[0], item[1]))
    bodies: list[tuple[str, object]] = []
    seen = set()
    for _timestamp, _rank, body, artifact in candidates:
        normalized = " ".join(body.split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        bodies.append((body, artifact))
    return bodies


def _anchor_context_units(body: str, anchor: str) -> list[str]:
    segments = [
        segment.strip()
        for segment in re.split(r"\n+|(?<=[.!?])\s+", body or "")
        if segment.strip()
    ]
    units = []
    for index, segment in enumerate(segments):
        if anchor.lower() not in segment.lower():
            continue
        unit = segment
        if len(segment) <= len(anchor) + 20 and index + 1 < len(segments):
            unit += " " + segments[index + 1]
        units.append(unit)
    return units


def _contains_proximate_terms(body: str, anchor_terms: tuple[str, ...], context_terms: tuple[str, ...]) -> bool:
    for anchor in anchor_terms:
        for unit in _anchor_context_units(body, anchor):
            if any(context.lower() in unit.lower() for context in context_terms):
                return True
    return False


def _has_anchor_without_proximate_context(
    body: str,
    anchor_terms: tuple[str, ...],
    context_terms: tuple[str, ...],
) -> bool:
    """Return true when a disqualified candidate is presented without exclusion context."""
    positive_recommendation = re.compile(
        r"\b(?:strong(?:est)?(?:\s+match)?|best\s+match|qualified|recommend(?:ed)?|shortlist(?:ed)?|include[ds]?)\b",
        re.IGNORECASE,
    )
    negated_recommendation = re.compile(
        r"\b(?:not|isn't|wasn't|do not|don't)\s+(?:qualified|recommend(?:ed)?|shortlist(?:ed)?|include[ds]?)\b",
        re.IGNORECASE,
    )
    for anchor in anchor_terms:
        for unit in _anchor_context_units(body, anchor):
            if not any(context.lower() in unit.lower() for context in context_terms) and not any(
                marker in unit for marker in ("❌", "✗", "✕", "❎")
            ):
                return True
            if positive_recommendation.search(unit) and not negated_recommendation.search(unit):
                return True
    return False


def _candidate_link_pairs_in_body(
    body: str,
    pairs: tuple[tuple[str, str], ...],
) -> set[str]:
    matched = set()
    paragraphs = [part for part in re.split(r"\n\s*\n", body or "") if part.strip()]
    lines = [line for line in (body or "").splitlines() if line.strip()]
    candidate_names = tuple(name for name, _url in pairs)

    for name, url in pairs:
        if any(name.casefold() in line.casefold() and url in line for line in lines):
            matched.add(name)
            continue

        for paragraph in paragraphs:
            lowered = paragraph.casefold()
            name_start = lowered.find(name.casefold())
            if name_start < 0:
                continue
            later_name_starts = [
                lowered.find(other_name.casefold(), name_start + len(name))
                for other_name in candidate_names
                if other_name != name
            ]
            later_name_starts = [index for index in later_name_starts if index >= 0]
            name_end = min(later_name_starts, default=len(paragraph))
            if url in paragraph[name_start:name_end]:
                matched.add(name)
                break
    return matched


def _partial_response_has_correct_polarity(body: str, *, remaining_work: int) -> bool:
    normalized = " ".join(str(body or "").casefold().replace("_", " ").split())
    contradiction_patterns = (
        r"\bnot (?:a )?partial\b",
        r"\bno (?:work )?(?:is )?remaining\b",
        r"\bnothing remains\b",
        r"\b(?:verification|work) (?:is )?(?:fully )?complete\b",
        r"\ball 15 (?:candidates? )?(?:were )?verified\b",
    )
    if any(re.search(pattern, normalized) for pattern in contradiction_patterns):
        return False
    remaining_patterns = (
        rf"\b{remaining_work}\s+(?:candidates?|profiles?|verifications?|items?|records?|checks?)\s+(?:remain|remaining)\b",
        rf"\bremaining (?:work|verification|candidates?|profiles?)\D{{0,12}}{remaining_work}\b",
        rf"\bremaining[\s:*_-]{{1,12}}{remaining_work}\b",
        rf"\b{remaining_work}\s+(?:candidates?|profiles?|items?)\s+(?:still\s+)?(?:queued|remain(?:ing)?|left|unverified)\b",
        rf"\b{remaining_work}\D{{0,12}}(?:remaining|left|unverified)\b",
        r"\b2\s+(?:of|/)\s*15\b",
    )
    limitation = bool(
        re.search(r"\b(?:partial|limitation|block(?:ed|ing))\b", normalized)
        or any(term in normalized for term in ("could not verify", "unable to verify"))
    )
    return limitation and any(re.search(pattern, normalized) for pattern in remaining_patterns)


def _partial_resume_state_tables(agent_id: str, *, cursor: str, remaining_work: int) -> list[str]:
    matched_tables = []
    with agent_sqlite_db(str(agent_id)) as db_path:
        connection = sqlite3.connect(db_path)
        try:
            table_names = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT GLOB '__*' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            for table_name in table_names:
                quoted_name = str(table_name).replace('"', '""')
                try:
                    rows = connection.execute(f'SELECT * FROM "{quoted_name}" LIMIT 100').fetchall()
                except sqlite3.Error:
                    continue
                if any(
                    any(str(value) == cursor for value in row)
                    and any(str(value) == str(remaining_work) for value in row)
                    for row in rows
                ):
                    matched_tables.append(str(table_name))
        finally:
            connection.close()
    return matched_tables


class RecruitmentSourcingScenario(EvalScenario, ScenarioExecutionTools):
    fingerprint_data = {
        "dedupe_queue_rows": DEDUPE_QUEUE_ROWS,
        "dedupe_ledger_rows": DEDUPE_LEDGER_ROWS,
    }
    fingerprint_dependencies = (
        _tool_calls_for_run,
        _retrieval_call_signature,
        _duplicate_retrieval_signatures,
        _tool_call_has_usable_result,
        _tool_name_satisfies_expected,
        _ledger_query_is_substantive,
        _message_tool_body,
        _human_input_request_body,
        _group_human_input_requests,
        _is_bounded_missing_material_request,
        _candidate_response_bodies,
        _anchor_context_units,
        _contains_proximate_terms,
        _has_anchor_without_proximate_context,
        _candidate_link_pairs_in_body,
        _partial_response_has_correct_polarity,
        _partial_resume_state_tables,
        response_contains_term,
    )
    tier = "core"
    category = "recruitment_sourcing"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "system_skills"
    tags = ("recruitment_sourcing", "system_skill", "micro")
    tasks = [
        ScenarioTask.setup(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_expected_tools", assertion_type="tool_call"),
        ScenarioTask(name="verify_forbidden_tools", assertion_type="tool_call"),
        ScenarioTask(name="verify_efficient_retrieval", assertion_type="tool_call"),
        ScenarioTask(name="verify_partial_resume_state", assertion_type="tool_call"),
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
        if case.slug == RECRUITMENT_SOURCING_DEDUPE_LEDGER:
            self._seed_candidate_ledger(agent_id)

    @staticmethod
    def _seed_candidate_ledger(agent_id: str) -> None:
        with agent_sqlite_db(str(agent_id)) as db_path:
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    "DROP TABLE IF EXISTS candidate_queue;"
                    "DROP TABLE IF EXISTS candidate_ledger;"
                    "CREATE TABLE candidate_queue(candidate_name TEXT NOT NULL, profile_url TEXT PRIMARY KEY);"
                    "CREATE TABLE candidate_ledger(profile_url TEXT PRIMARY KEY, status TEXT NOT NULL, reason TEXT NOT NULL);"
                )
                connection.executemany(
                    "INSERT INTO candidate_queue(candidate_name, profile_url) VALUES (?, ?)",
                    DEDUPE_QUEUE_ROWS,
                )
                connection.executemany(
                    "INSERT INTO candidate_ledger(profile_url, status, reason) VALUES (?, ?, ?)",
                    DEDUPE_LEDGER_ROWS,
                )
                connection.commit()
            finally:
                connection.close()

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
        if (
            case.slug == RECRUITMENT_SOURCING_DEDUPE_LEDGER
            and not any(_ledger_query_is_substantive(call) for call in calls)
        ):
            missing.append("substantive candidate_queue/candidate_ledger classification query")
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

    def _record_efficient_retrieval(self, run_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_efficient_retrieval")
        calls = _tool_calls_for_run(
            run_id,
            after=inbound.timestamp,
            tool_names=RECRUITMENT_RETRIEVAL_TOOL_NAMES,
        )
        duplicates = _duplicate_retrieval_signatures(calls)
        within_cap = len(calls) <= case.max_retrieval_tool_calls
        if not duplicates and within_cap:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_efficient_retrieval",
                observed_summary=(
                    f"Retrieval work was bounded ({len(calls)}/{case.max_retrieval_tool_calls}) "
                    "with no duplicate call signatures."
                ),
                artifacts={"step": calls[0].step} if calls else {},
            )
            return

        duplicate_tools = sorted({signature.split(":", 1)[0] for signature in duplicates})
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="verify_efficient_retrieval",
            observed_summary=(
                f"Retrieval work was {len(calls)}/{case.max_retrieval_tool_calls}; "
                f"duplicate call signatures used for tools {duplicate_tools}."
            ),
            artifacts={"step": calls[-1].step} if calls else {},
        )

    def _record_response(self, run_id: str, agent_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_response")
        response_bodies = _candidate_response_bodies(run_id, agent_id, inbound)
        final_body, final_artifact = response_bodies[-1] if response_bodies else ("", None)
        final_missing_groups = [
            terms
            for terms in case.response_term_groups
            if not any(response_contains_term(final_body, term) for term in terms)
        ]
        final_missing_proximate_groups = [
            terms
            for terms in case.required_proximate_response_terms
            if not _contains_proximate_terms(final_body, terms[0], terms[1])
        ]
        final_forbidden_terms = [
            term
            for term in case.forbidden_response_terms
            if any(response_contains_term(body, term) for body, _artifact in response_bodies)
        ]
        final_misrepresented_groups = [
            terms
            for terms in case.forbidden_without_proximate_response_terms
            if any(
                _has_anchor_without_proximate_context(body, terms[0], terms[1])
                for body, _artifact in response_bodies
            )
        ]
        tracked_request_count = sum(
            isinstance(artifact, PersistentAgentHumanInputRequest)
            for _body, artifact in response_bodies
        )
        missing_bounded_request = bool(
            case.require_bounded_missing_material_request
            and not _is_bounded_missing_material_request(
                final_body,
                response_count=len(response_bodies),
                tracked_request_count=tracked_request_count,
            )
        )
        linked_candidate_names = _candidate_link_pairs_in_body(
            final_body,
            case.required_candidate_link_pairs,
        )
        missing_candidate_links = max(
            0,
            case.min_candidate_link_pairs - len(linked_candidate_names),
        )
        invalid_partial_polarity = bool(
            case.require_partial_resume_state
            and not _partial_response_has_correct_polarity(final_body, remaining_work=13)
        )

        if (
            final_missing_groups
            or final_missing_proximate_groups
            or final_forbidden_terms
            or final_misrepresented_groups
            or missing_bounded_request
            or missing_candidate_links
            or invalid_partial_polarity
        ):
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_response",
                observed_summary=(
                    f"Missing expected term group(s) {final_missing_groups}; "
                    f"missing proximate term group(s) {final_missing_proximate_groups}; "
                    f"forbidden response terms present {final_forbidden_terms}; body={final_body[:800]!r}."
                    f" disqualified candidates lacking exclusion context {final_misrepresented_groups}; "
                    f"missing_bounded_material_request={missing_bounded_request}."
                    f" linked_candidate_names={sorted(linked_candidate_names)}; "
                    f"missing_candidate_links={missing_candidate_links}."
                    f" invalid_partial_polarity={invalid_partial_polarity}."
                ),
                artifacts={"response_artifact": final_artifact} if final_artifact else {},
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_response",
            observed_summary="Agent response reflected the sourcing guardrail under test.",
            artifacts={"response_artifact": final_artifact, "response_preview": final_body[:800]} if final_artifact else {},
        )

    def _record_partial_resume_state(self, run_id: str, agent_id: str) -> None:
        case = self._case()
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_partial_resume_state")
        if not case.require_partial_resume_state:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_partial_resume_state",
                observed_summary="No durable partial-resume assertion applies to this case.",
            )
            return

        matched_tables = _partial_resume_state_tables(
            agent_id,
            cursor="candidate-offset-2",
            remaining_work=13,
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if matched_tables else EvalRunTask.Status.FAILED,
            task_name="verify_partial_resume_state",
            observed_summary=(
                f"Persisted the exact cursor and remaining-work count in user SQLite table(s): {matched_tables}."
                if matched_tables
                else "Partial verification did not persist its exact cursor and remaining-work count in user SQLite."
            ),
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
        self._record_efficient_retrieval(run_id, inbound)
        self._record_partial_resume_state(run_id, agent_id)
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
