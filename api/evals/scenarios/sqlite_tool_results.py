import json
import re
from typing import Iterable

from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_SERVER
from api.agent.tools.sqlite_query_quality import summarize_sqlite_tool_result_calls
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.agent.tools.web_chat_sender import _looks_like_routine_progress_message
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import register_scenario
from api.evals.scenarios.effort_calibration import MESSAGE_TOOL_NAMES, STOP_TOOL_NAMES, _outbound_messages_after, _tool_calls_for_run
from api.models import EvalRunTask, PersistentAgent, PersistentAgentEnabledTool, PersistentAgentStep, PersistentAgentSystemStep


SQLITE_TOOL_RESULT_SUITE_SLUG = "sqlite_tool_results"
SQLITE_MULTI_RESULT_WEB_SYNTHESIS = "sqlite_tool_results_multi_result_web_synthesis"
SQLITE_INTERMEDIATE_WORKING_TABLE = "sqlite_tool_results_intermediate_working_table"
SQLITE_DEDUPE_REQUERY = "sqlite_tool_results_dedupe_requery"
SQLITE_ITEM_LINK_REPORT = "sqlite_tool_results_item_link_report"
SQLITE_TOOL_RESULT_SCENARIO_SLUGS = [
    SQLITE_MULTI_RESULT_WEB_SYNTHESIS,
    SQLITE_INTERMEDIATE_WORKING_TABLE,
    SQLITE_DEDUPE_REQUERY,
    SQLITE_ITEM_LINK_REPORT,
]


SOURCE_URLS = ("https://sources.example.test/helpdesk/axonflow", "https://sources.example.test/helpdesk/brightsupport", "https://sources.example.test/helpdesk/caremesh", "https://sources.example.test/helpdesk/dockwise")
PRODUCT_URLS = ("https://api.example.test/products/axonflow.json", "https://api.example.test/products/brightsupport.json", "https://api.example.test/products/caremesh.json", "https://api.example.test/products/dockwise.json")
INVENTORY_URLS = ("https://inventory.example.test/tesla/model-y/local.json", "https://inventory.example.test/tesla/model-y/dealer.json")
LISTING_URLS = (
    "https://listings.example.test/tesla/model-y/vin-7say-001",
    "https://listings.example.test/tesla/model-y/vin-7say-002",
    "https://listings.example.test/tesla/model-y/vin-7say-003",
    "https://listings.example.test/tesla/model-y/vin-7say-004",
    "https://listings.example.test/tesla/model-y/vin-7say-005",
)

_DEDUPE_CLAIM_SPECS = (
    (
        "AxonFlow",
        (SOURCE_URLS[0],),
        ("enterprise",),
        ("soc 2", "analytics", "salesforce", "99.95%", "sla"),
    ),
    (
        "BrightSupport",
        (SOURCE_URLS[1], SOURCE_URLS[3]),
        ("smb", "small business", "small teams"),
        ("low-admin", "low admin", "low administration", "transparent", "monthly pricing", "quick setup", "shared inbox"),
    ),
    (
        "CareMesh",
        (SOURCE_URLS[2],),
        ("hipaa", "healthcare", "regulated"),
        ("baa", "phi", "redaction", "escalation", "audit export"),
    ),
)

_PENDING_SOC2_RE = re.compile(
    r"(?:\bsoc\s*2\b.{0,24}\bpending\b|\bpending\b.{0,24}\bsoc\s*2\b)",
    re.IGNORECASE,
)
_DISQUALIFIED_TERMS = (
    "disqualif",
    "does not qualify",
    "didn't qualify",
    "didn’t qualify",
    "did not qualify",
    "doesn't count",
    "doesn’t count",
    "does not count",
    "not qualified",
    "not compliant",
    "not currently compliant",
    "not certified",
    "not a certified",
    "not yet certified",
    "non-qualifying",
    "nonqualifying",
    "failed requirements",
    "failed criteria",
    "ineligible",
    "excluded",
    "fails the requirement",
)
_NEGATIVE_QUALIFICATION_MARKERS = ("❌", "✗", "✕", "❎")
_SQLITE_SHAPE_REPAIR_ERRORS = (
    ("schema", ("no such column", "no such table", "ambiguous column", "has no column named")),
    ("json", ("malformed json", "json path error", "bad json path")),
    ("aggregate", ("distinct aggregates must have exactly one argument",)),
)
_QUALIFYING_DOCKWISE_TERMS = ("qualif", "meets", "eligible", "passes", "valid alternative", "runner-up", "✅", "✔")
_FALSE_DOCKWISE_EXCLUSIONS = ("partial", "poor fit", "does not qualify", "doesn't qualify", "not qualified", "disqualif", "unqualif", "fails", "ineligible", "excluded", "wrong compliance")


def _tool_call_result(call) -> dict:
    raw_result = getattr(call, "result", None)
    if isinstance(raw_result, dict):
        return raw_result
    if raw_result in (None, ""):
        return {}
    try:
        result = json.loads(str(raw_result))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return result if isinstance(result, dict) else {}


def _tool_call_completed_successfully(call) -> bool:
    call_status = str(getattr(call, "status", "") or "").casefold()
    if call_status in {"pending", "error", "failed"}:
        return False
    result = _tool_call_result(call)
    if str(result.get("status") or "").casefold() in {"error", "failed", "failure"}:
        return False
    status_code = result.get("status_code")
    return not isinstance(status_code, int) or status_code < 400


def _tool_call_failure_text(call) -> str:
    result = _tool_call_result(call)
    fields = (
        getattr(call, "status", ""),
        result.get("status"),
        result.get("error_type"),
        result.get("code"),
        result.get("error"),
        result.get("message"),
    )
    return " ".join(str(value) for value in fields if value).casefold()


def _sqlite_shape_error_kind(call) -> str | None:
    text = _tool_call_failure_text(call)
    return next((kind for kind, fragments in _SQLITE_SHAPE_REPAIR_ERRORS if any(fragment in text for fragment in fragments)), None)


def _pending_soc2_is_explicitly_disqualified(body: str) -> bool:
    section = "neutral"
    unresolved_pending = False
    for raw_line in (body or "").splitlines():
        stripped = raw_line.strip()
        line = stripped.casefold()
        explicitly_disqualified = (
            any(term in line for term in _DISQUALIFIED_TERMS)
            or any(marker in raw_line for marker in _NEGATIVE_QUALIFICATION_MARKERS)
        )
        heading_text = re.sub(r"<[^>]+>|[#*_]", "", stripped).strip().rstrip(":").casefold()
        marked_heading = bool(
            re.match(r"^#{1,6}\s+", stripped)
            or re.fullmatch(r"(?:\*\*|__).+(?:\*\*|__)", stripped)
            or re.fullmatch(r"<(?:h[1-6]|strong|b)\b[^>]*>.*</(?:h[1-6]|strong|b)>", stripped, re.I)
        )
        section_label = bool(re.fullmatch(
            r"(?:(?:non[- ]?qualifying|disqualified|ineligible|excluded|qualifying|qualified|recommended)\s+"
            r"(?:plans?|options?|candidates?|vendors?)|(?:plans?|options?|candidates?|vendors?)\s+(?:that\s+)?"
            r"(?:qualify|qualified|(?:did|do|does)\s+not\s+qualify|didn['’]t\s+qualify)|"
            r"(?:failed|failing)\s+(?:requirements|criteria)|recommendation)",
            heading_text,
        ))
        is_heading = marked_heading or (len(stripped) <= 80 and (stripped.endswith(":") or section_label))
        if is_heading:
            if explicitly_disqualified:
                section = "disqualified"
            elif any(term in line for term in (
                "qualifying", "qualified plans", "plans that qualify", "plans qualify", "recommendation", "recommended",
            )):
                section = "qualified"
            else:
                section = "neutral"

        positive_pending_line = re.sub(r"\bnot\s+pending\b", "", line)
        if not _PENDING_SOC2_RE.search(positive_pending_line):
            continue
        if explicitly_disqualified or section == "disqualified":
            continue
        unresolved_pending = True
    return not unresolved_pending


def _dockwise_is_explicitly_qualified(body: str) -> bool:
    section_is_qualified = False
    units = []
    for raw_line in (body or "").splitlines():
        line = raw_line.strip()
        lowered = line.casefold()
        if "qualifying" in lowered or "plans meeting all criteria" in lowered:
            section_is_qualified = True
        elif "disqualified" in lowered or "does not qualify" in lowered:
            section_is_qualified = False
        if line:
            units.append((line, section_is_qualified))
    units.extend(
        (part.strip(), False)
        for part in re.split(r"\n\s*\n", body or "")
        if part.strip()
    )
    for unit, qualified_section in units:
        lowered = unit.casefold()
        if "dockwise" not in lowered or "commerce plus" not in lowered:
            continue
        if not all(term in lowered for term in ("890", "65", "soc 2")):
            continue
        if any(term in lowered for term in _FALSE_DOCKWISE_EXCLUSIONS):
            continue
        if qualified_section or any(term in lowered for term in _QUALIFYING_DOCKWISE_TERMS):
            return True
    return False


def _caremesh_is_claimed_as_only_qualifier(body: str) -> bool:
    compact = " ".join((body or "").casefold().split())
    global_qualification = (
        r"(?:qualifies\b(?!\s+(?:for|under|with|on|in|among)\b)|"
        r"(?:meets|satisfies)\s+(?:all|every|the)\s+(?:criteria|criterion|requirements?)\b)"
    )
    patterns = (
        r"\bcaremesh(?:\s+clinic)?\s+(?:is|remains)\s+the\s+only\s+qualifying\s+"
        r"(?:plan|option|one)\b(?!\s+(?:with|for|under|among|in)\b)",
        rf"\bcaremesh(?:\s+clinic)?\s+(?:is|remains)\s+the\s+only\s+(?:plan|option|one)\s+"
        rf"(?:that\s+)?{global_qualification}",
        r"\bthe\s+only\s+qualifying\s+(?:plan|option)\s+(?:is|was|remains)\s+caremesh\b",
        rf"\bthe\s+only\s+(?:plan|option|one)\s+(?:that\s+)?{global_qualification}"
        rf".{{0,60}}\b(?:is|was|remains)\s+caremesh\b",
        r"\b(?:recommendation(?:\s+is)?|recommend|recommended(?:\s+plan)?(?:\s+is)?)\s*:?\s*"
        r"caremesh(?:\s+clinic)?\b.{0,60}\b(?:this|it)\s+(?:is|was|remains)\s+the\s+only\s+"
        rf"(?:(?:qualifying|eligible|compliant)\s+(?:plan|option|one)\b"
        rf"(?!\s+(?:with|for|under|among|in)\b)|(?:plan|option|one)\s+(?:that\s+)?{global_qualification})",
        rf"\bonly\s+caremesh(?:\s+clinic)?\s+{global_qualification}",
        rf"\bcaremesh(?:\s+clinic)?\s+alone\s+{global_qualification}",
        rf"\bcaremesh(?:\s+clinic)?\s+(?:is|remains)\s+(?:the\s+)?sole\s+"
        rf"(?:(?:qualifying|eligible|compliant)\s+(?:plan|option|one)\b"
        rf"(?!\s+(?:with|for|under|among|in)\b)|(?:plan|option|one)\s+(?:that\s+)?{global_qualification})",
    )
    return any(re.search(pattern, compact) for pattern in patterns)


def _dedupe_claim_units(body: str) -> list[str]:
    source_urls = {
        url
        for _, urls, _, _ in _DEDUPE_CLAIM_SPECS
        for url in urls
    }
    units: list[str] = []
    pending: list[str] = []
    for paragraph in re.split(r"\n\s*\n", body or ""):
        paragraph = paragraph.strip()
        if not paragraph or re.fullmatch(r"-{3,}", paragraph):
            continue

        source_lines = [
            line.strip()
            for line in paragraph.splitlines()
            if any(url in line for url in source_urls)
        ]
        if len(source_lines) > 1:
            # Tables and compact lists often put one claim and citation on each
            # line. Keeping those rows separate prevents a sources footer from
            # making unrelated claims appear locally supported.
            units.extend(source_lines)
            pending.clear()
            continue

        pending.append(paragraph)
        if source_lines:
            units.append("\n\n".join(pending))
            pending.clear()
    return units


def _dedupe_claim_coverage(body: str) -> tuple[set[str], set[str]]:
    claim_counts: dict[str, int] = {}
    for unit in _dedupe_claim_units(body):
        lowered = unit.casefold()
        supported_families = []
        for family, source_urls, fit_terms, evidence_terms in _DEDUPE_CLAIM_SPECS:
            if (
                any(url in unit for url in source_urls)
                and any(term in lowered for term in fit_terms)
                and any(term in lowered for term in evidence_terms)
            ):
                supported_families.append(family)
        if len(supported_families) != 1:
            continue
        family = supported_families[0]
        claim_counts[family] = claim_counts.get(family, 0) + 1
    mapped = set(claim_counts)
    repeated = {family for family, count in claim_counts.items() if count > 1}
    return mapped, repeated


WEB_SOURCE_FACTS = (
    ("AxonFlow support automation", ("Vendor: AxonFlow", "Best fit: enterprise support teams with strict audit needs.", "Strengths: SOC 2 controls, workflow analytics, Salesforce integration, and 99.95% SLA.", "Tradeoff: higher implementation effort and annual pricing.")),
    ("BrightSupport", ("Vendor: BrightSupport", "Best fit: SMB teams that need fast deployment and low administration overhead.", "Strengths: shared inbox automation, simple knowledge-base answers, and transparent monthly pricing.", "Tradeoff: fewer governance controls than enterprise suites.")),
    ("CareMesh Assist", ("Vendor: CareMesh Assist", "Best fit: regulated healthcare support where HIPAA workflows matter.", "Strengths: HIPAA BAA, PHI redaction, clinical escalation routing, and audit exports.", "Tradeoff: narrower integrations outside healthcare.")),
    ("Dockwise Support", ("Vendor: Dockwise Support", "Best fit: mid-market ecommerce teams with high ticket seasonality.", "Strengths: Shopify macros, refund workflow automation, and seasonal staffing forecasts.", "Tradeoff: limited native healthcare compliance features.")),
)
PRODUCT_PLAN_ROWS = (
    ("AxonFlow", (("Growth", 980, 35, ("SOC 2",), 78), ("Enterprise", 1500, 80, ("SOC 2", "SAML"), 84))),
    ("BrightSupport", (("Team", 420, 25, (), 61), ("Business", 760, 45, ("SOC 2 pending",), 69))),
    ("CareMesh", (("Clinic", 720, 50, ("HIPAA", "SOC 2"), 92), ("Network", 1100, 100, ("HIPAA", "SOC 2"), 88))),
    ("Dockwise", (("Commerce", 640, 40, ("PCI",), 70), ("Commerce Plus", 890, 65, ("PCI", "SOC 2"), 76))),
)
INVENTORY_ROWS = (
    (
        INVENTORY_URLS[0],
        (
            {
                "vin": "7SAY-001",
                "year": 2023,
                "trim": "Model Y Long Range",
                "mileage": 26298,
                "price_usd": 32985,
                "distance_mi": 45,
                "dealer": "Harrisburg Mitsubishi",
                "listing_url": LISTING_URLS[0],
            },
            {
                "vin": "7SAY-002",
                "year": 2023,
                "trim": "Model Y Long Range",
                "mileage": 72189,
                "price_usd": 27455,
                "distance_mi": 45,
                "dealer": "Harrisburg Mitsubishi",
                "listing_url": LISTING_URLS[1],
            },
            {
                "vin": "7SAY-003",
                "year": 2024,
                "trim": "Model Y",
                "mileage": 37279,
                "price_usd": 34800,
                "distance_mi": 47,
                "dealer": "Ourisman Chevrolet",
                "listing_url": LISTING_URLS[2],
            },
        ),
    ),
    (
        INVENTORY_URLS[1],
        (
            {
                "vin": "7SAY-001",
                "year": 2023,
                "trim": "Model Y Long Range",
                "mileage": 26298,
                "price_usd": 32985,
                "distance_mi": 45,
                "dealer": "Harrisburg Mitsubishi",
                "listing_url": LISTING_URLS[0],
            },
            {
                "vin": "7SAY-004",
                "year": 2023,
                "trim": "Model Y Performance",
                "mileage": 32000,
                "price_usd": 32920,
                "distance_mi": 43,
                "dealer": "Private Seller Exchange",
                "listing_url": LISTING_URLS[3],
            },
            {
                "vin": "7SAY-005",
                "year": 2025,
                "trim": "Model Y",
                "mileage": 13896,
                "price_usd": 39129,
                "distance_mi": 26,
                "dealer": "Renn Kirby Frederick",
                "listing_url": LISTING_URLS[4],
            },
        ),
    ),
)


def _large_page(title: str, facts: Iterable[str]) -> str:
    body = "\n".join(f"- {fact}" for fact in facts)
    filler = "\n".join(f"Appendix note {idx}: implementation details, onboarding checklist, controls, and support workflow context." for idx in range(520))
    return f"# {title}\n\n{body}\n\n## Appendix\n{filler}"


def _web_mock() -> dict:
    pages = {url: _large_page(title, facts) for url, (title, facts) in zip(SOURCE_URLS, WEB_SOURCE_FACTS)}
    return {
        "mcp_brightdata_search_engine": {"status": "ok", "results": [{"title": title, "url": url, "snippet": facts[1]} for url, (title, facts) in zip(SOURCE_URLS, WEB_SOURCE_FACTS)]},
        "mcp_brightdata_scrape_as_markdown": {"rules": [{"url_contains": url, "result": {"status": "ok", "url": url, "result": page}} for url, page in pages.items()], "default": {"status": "error", "message": "Unknown eval URL."}},
        "search_tools": {"status": "ok", "tools": [{"name": "mcp_brightdata_search_engine", "description": "Search deterministic eval web results."}, {"name": "mcp_brightdata_scrape_as_markdown", "description": "Scrape deterministic eval web pages."}]},
    }


def _product_mock() -> dict:
    payloads = {
        url: {"vendor": vendor, "source_url": url, "plans": [{"plan": p, "monthly_price_usd": price, "included_seats": seats, "compliance": list(comp), "fit_score": score} for p, price, seats, comp, score in plans]}
        for url, (vendor, plans) in zip(PRODUCT_URLS, PRODUCT_PLAN_ROWS)
    }
    return {"http_request": {"rules": [{"url_contains": url, "result": {"status": "ok", "status_code": 200, "url": url, "content": payload}} for url, payload in payloads.items()], "default": {"status": "error", "message": "Unknown eval URL."}}}


def _inventory_mock() -> dict:
    rules = [
        {
            "url_contains": url,
            "result": {
                "status": "ok",
                "status_code": 200,
                "url": url,
                "content": {
                    "source_url": url,
                    "vehicles": list(vehicles),
                },
            },
        }
        for url, vehicles in INVENTORY_ROWS
    ]
    return {"http_request": {"rules": rules, "default": {"status": "error", "message": "Unknown eval URL."}}}


def _dedupe_mock() -> dict:
    claims = (
        "Claim: AxonFlow is strongest for enterprise teams because it combines SOC 2 controls, analytics, Salesforce integration, and a 99.95% SLA.",
        "Claim: BrightSupport is strongest for SMB teams because it offers low-admin shared inbox automation and transparent monthly pricing.",
        "Claim: CareMesh is strongest for HIPAA-regulated healthcare support because it includes a BAA, PHI redaction, escalation routing, and audit exports.",
        "Claim: BrightSupport is strongest for SMB teams because it offers quick setup, low administration, and transparent monthly pricing.",
    )
    rules = [{"url_contains": url, "result": {"status": "ok", "status_code": 200, "url": url, "content": {"url": url, "text": _large_page(f"Source {i}", (claim,))}}} for i, (url, claim) in enumerate(zip(SOURCE_URLS, claims), start=1)]
    return {"http_request": {"rules": rules, "default": {"status": "error", "message": "Unknown eval URL."}}}


MOCK_BUILDERS = {"web": _web_mock, "product": _product_mock, "dedupe": _dedupe_mock, "inventory": _inventory_mock}


class SqliteToolResultScenario(EvalScenario, ScenarioExecutionTools):
    fingerprint_dependencies = (
        summarize_sqlite_tool_result_calls,
        _looks_like_routine_progress_message,
        _outbound_messages_after,
        _tool_call_result,
        _tool_call_completed_successfully,
        _tool_call_failure_text,
        _sqlite_shape_error_kind,
        _pending_soc2_is_explicitly_disqualified,
        _dockwise_is_explicitly_qualified,
        _caremesh_is_claimed_as_only_qualifier,
        _dedupe_claim_units,
        _dedupe_claim_coverage,
        _large_page,
        _web_mock,
        _product_mock,
        _dedupe_mock,
        _inventory_mock,
    )
    fingerprint_data = {
        "source_urls": SOURCE_URLS,
        "product_urls": PRODUCT_URLS,
        "inventory_urls": INVENTORY_URLS,
        "listing_urls": LISTING_URLS,
        "dedupe_claim_specs": _DEDUPE_CLAIM_SPECS,
        "web_source_facts": WEB_SOURCE_FACTS,
        "product_plan_rows": PRODUCT_PLAN_ROWS,
        "inventory_rows": INVENTORY_ROWS,
        "mock_builders": {
            key: f"{builder.__module__}.{builder.__qualname__}"
            for key, builder in MOCK_BUILDERS.items()
        },
        "pending_soc2_pattern": _PENDING_SOC2_RE.pattern,
        "disqualified_terms": _DISQUALIFIED_TERMS,
        "negative_qualification_markers": _NEGATIVE_QUALIFICATION_MARKERS,
        "sqlite_shape_repair_errors": _SQLITE_SHAPE_REPAIR_ERRORS,
        "qualifying_dockwise_terms": _QUALIFYING_DOCKWISE_TERMS,
        "false_dockwise_exclusions": _FALSE_DOCKWISE_EXCLUSIONS,
    }
    tier = "contract"
    category = "sqlite_tool_results"
    expected_runtime = "medium"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("agent_behavior", "sqlite_tool_results", "tool_results", "agent_processing")
    builtin_tools: tuple[str, ...] = (); eval_synthetic_tools: tuple[str, ...] = (); answer_source_urls: tuple[str, ...] = (); required_terms: tuple[str, ...] = ()
    prompt = ""; mock_kind = ""; verify_task_name = "verify_sqlite_usage"; require_working_table = False; max_relevant_tool_calls = 18; min_sources = 1
    max_single_result_filters = 1
    max_direct_fetches = 0
    max_sqlite_calls = 4
    require_unique_source_urls = False
    sourced_answer_task_name = "verify_sourced_answer"

    def _answer_quality_issues(self, body: str) -> list[str]:
        return []

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        for names, synthetic in ((self.builtin_tools, False), (self.eval_synthetic_tools, True)):
            if names:
                self._enable_tools(agent_id, names, synthetic=synthetic)
        inbound = self._inject_and_wait(run_id, agent_id, self.prompt, MOCK_BUILDERS[self.mock_kind](), allowed_tool_names={*self.builtin_tools, *self.eval_synthetic_tools, "sqlite_batch", "update_plan", *MESSAGE_TOOL_NAMES, "search_tools"}, max_relevant_tool_calls=self.max_relevant_tool_calls)
        self._record_sqlite_usage(run_id, after=inbound.timestamp, task_name=self.verify_task_name, require_working_table=self.require_working_table, max_direct_fetches=self.max_direct_fetches, max_single_result_filters=self.max_single_result_filters)
        self._record_sourced_answer(run_id, agent_id=agent_id, after=inbound.timestamp, task_name=self.sourced_answer_task_name, source_urls=self.answer_source_urls, required_terms=self.required_terms, min_sources=self.min_sources)

    def _ready_agent(self, agent_id: str) -> None:
        PersistentAgent.objects.filter(id=agent_id).update(
            charter="Provide accurate, concise reports grounded in source data with direct source URLs.",
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )
        exists = PersistentAgentStep.objects.filter(agent_id=agent_id, system_step__code="PROCESS_EVENTS").exists()
        if not exists:
            step = PersistentAgentStep.objects.create(agent_id=agent_id, description="Process events")
            PersistentAgentSystemStep.objects.create(step=step, code=PersistentAgentSystemStep.Code.PROCESS_EVENTS)

    def _enable_tools(self, agent_id: str, tool_names: Iterable[str], *, synthetic: bool = False) -> None:
        agent = PersistentAgent.objects.get(id=agent_id)
        for tool_name in tool_names:
            mark_tool_enabled_without_discovery(agent, tool_name)
            if synthetic:
                PersistentAgentEnabledTool.objects.filter(agent=agent, tool_full_name=tool_name).update(tool_server=EVAL_SYNTHETIC_TOOL_SERVER, tool_name=tool_name)

    def _inject_and_wait(self, run_id: str, agent_id: str, prompt: str, mock_config: dict, *, allowed_tool_names: Iterable[str], max_relevant_tool_calls: int = 14):
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=240):
            inbound = self.inject_message(agent_id, prompt, trigger_processing=True, eval_run_id=run_id, mock_config=mock_config, eval_stop_policy={"max_relevant_tool_calls": max_relevant_tool_calls, "stop_on_unexpected_relevant_tool": True, "allowed_tool_names": list(allowed_tool_names), "ignored_tool_names": list(STOP_TOOL_NAMES)})
        self.record_task_result(run_id, None, EvalRunTask.Status.PASSED, task_name="inject_prompt", observed_summary="Prompt injected and processing completed.", artifacts={"message": inbound})
        return inbound

    def _record_sqlite_usage(self, run_id: str, *, after, task_name: str, require_working_table: bool = False, max_direct_fetches: int | None = None, max_single_result_filters: int | None = None) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        if max_direct_fetches is None:
            max_direct_fetches = self.max_direct_fetches
        observed_calls = _tool_calls_for_run(run_id, after=after, tool_names={"sqlite_batch"})
        calls = [call for call in observed_calls if _tool_call_completed_successfully(call)]
        failed_calls = [call for call in observed_calls if not _tool_call_completed_successfully(call)]
        summary = summarize_sqlite_tool_result_calls(calls)
        all_attempts_summary = summarize_sqlite_tool_result_calls(observed_calls)
        has_durable_working_set = summary.creates_working_table and summary.reads_working_table
        has_smart_aggregate_working_set = (
            summary.aggregate_tool_result_queries >= 1
            and (
                summary.smart_tool_result_queries >= 1
                or has_durable_working_set
            )
        )
        has_substantive_transform = bool(
            has_durable_working_set
            or summary.smart_tool_result_queries
            or summary.uses_json_functions
            or summary.uses_join
            or summary.uses_group_by
            or summary.uses_window
        )
        failed_call_texts = [_tool_call_failure_text(call) for call in failed_calls]
        hit_efficiency_budget = any("budget_exhausted" in text for text in failed_call_texts)
        manual_guard_failures = [
            call
            for call in failed_calls
            if "manual_tool_result_copy" in _tool_call_failure_text(call)
        ]
        allowed_manual_repair = False
        if len(manual_guard_failures) == 1 and len(failed_calls) == 1:
            failed_index = observed_calls.index(manual_guard_failures[0])
            allowed_manual_repair = any(
                _tool_call_completed_successfully(call)
                for call in observed_calls[failed_index + 1:]
            )
        successful_manual_copy = bool(
            summary.manual_values_working_tables or summary.manual_literal_rowsets
        )
        attempted_manual_copy = bool(
            successful_manual_copy
            or (
                (
                    all_attempts_summary.manual_values_working_tables
                    or all_attempts_summary.manual_literal_rowsets
                    or any("manual_tool_result_copy" in text or "manual_working_table" in text for text in failed_call_texts)
                )
                and not allowed_manual_repair
            )
        )
        repair_kinds = [_sqlite_shape_error_kind(call) for call in failed_calls]
        allowed_shape_repair = bool(
            0 < len(failed_calls) <= 2
            and all(repair_kinds)
            and len(set(repair_kinds)) == len(repair_kinds)
        )
        if allowed_shape_repair:
            failed_index = max(observed_calls.index(call) for call in failed_calls)
            allowed_shape_repair = any(
                _tool_call_completed_successfully(call)
                for call in observed_calls[failed_index + 1:]
            )
        failures = [msg for bad, msg in (
            (not calls, "no sqlite_batch call observed"),
            (summary.aggregate_tool_result_queries < 1, "no aggregate __tool_results query observed"),
            (not has_smart_aggregate_working_set, "no smart aggregate query or reusable table derived from __tool_results"),
            (not has_substantive_transform, "no substantive SQLite transform beyond ordering/filtering"),
            (summary.sqlite_call_count > self.max_sqlite_calls, f"successful sqlite batches {summary.sqlite_call_count} > {self.max_sqlite_calls}"),
            (
                summary.unshaped_multi_result_payload_queries > 1,
                f"repeated unshaped multi-result payload projections={summary.unshaped_multi_result_payload_queries}",
            ),
            (summary.direct_result_text_fetches > max_direct_fetches, f"direct result_text fetches {summary.direct_result_text_fetches} > {max_direct_fetches}"),
            (bool(summary.duplicate_direct_fetches), f"duplicate direct result_text fetches={summary.duplicate_direct_fetches}"),
            (
                summary.single_row_offset_queries > 1,
                f"one-row OFFSET tool-result query loop={summary.single_row_offset_queries}",
            ),
            (
                attempted_manual_copy,
                "manual-copy attempt observed; "
                f"VALUES tables={all_attempts_summary.manual_values_working_tables}, "
                f"literal rowsets={all_attempts_summary.manual_literal_rowsets}",
            ),
            (hit_efficiency_budget, "SQLite efficiency budget was exhausted"),
            (
                bool(failed_calls) and not (allowed_shape_repair or allowed_manual_repair),
                f"unrepaired or repeated failed sqlite attempts={len(failed_calls)}",
            ),
            (max_single_result_filters is not None and summary.single_result_id_filters > max_single_result_filters, f"single-result filters {summary.single_result_id_filters} > {max_single_result_filters}"),
            (
                require_working_table
                and not (has_durable_working_set or has_smart_aggregate_working_set),
                "no smart aggregate working-set query or durable table created from __tool_results and queried",
            ),
        ) if bad]
        status = EvalRunTask.Status.FAILED if failures else EvalRunTask.Status.PASSED
        usage = {
            **summary.__dict__,
            "rejected_or_failed_calls": len(failed_calls),
            "allowed_shape_repair": allowed_shape_repair,
            "allowed_manual_repair": allowed_manual_repair,
        }
        observed = "; ".join(failures) if failures else f"Observed smart sqlite/tool-result usage: {usage}"
        self.record_task_result(run_id, None, status, task_name=task_name, observed_summary=observed, artifacts={"step": calls[0].step, "usage": usage} if calls else {})
        return not failures

    def _record_sourced_answer(self, run_id: str, *, agent_id: str, after, task_name: str, source_urls: Iterable[str], required_terms: Iterable[str], min_sources: int) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        outbound = _outbound_messages_after(agent_id, after)
        if not outbound:
            self.record_task_result(run_id, None, EvalRunTask.Status.FAILED, task_name=task_name, observed_summary="Expected a sourced outbound answer; found none.", artifacts={})
            return False
        progress_messages = [message for message in outbound if _looks_like_routine_progress_message(message.body or "")]
        if len(outbound) != 1 or progress_messages:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name=task_name,
                observed_summary=(
                    f"Expected exactly one final sourced answer and no progress-only message; "
                    f"outbound={len(outbound)}, progress_messages={len(progress_messages)}."
                ),
                artifacts={"message": progress_messages[0] if progress_messages else outbound[0]},
            )
            return False

        message = outbound[0]
        body = message.body or ""
        linked_sources = [url for url in source_urls if url in body]
        duplicate_source_urls = [url for url in source_urls if body.count(url) > 1]
        missing_terms = [term for term in required_terms if term.casefold() not in body.casefold()]
        quality_issues = self._answer_quality_issues(body)
        if (
            len(linked_sources) >= min_sources
            and not missing_terms
            and not quality_issues
            and not (self.require_unique_source_urls and duplicate_source_urls)
        ):
            self.record_task_result(run_id, None, EvalRunTask.Status.PASSED, task_name=task_name, observed_summary=f"Answer cited {len(linked_sources)} source URL(s) and included required facts.", artifacts={"message": message})
            return True

        self.record_task_result(run_id, None, EvalRunTask.Status.FAILED, task_name=task_name, observed_summary=f"Expected at least {min_sources} source URL(s), required terms, accurate qualification, and unique item URLs when configured; linked_sources={len(linked_sources)}, missing_terms={missing_terms}, quality_issues={quality_issues}, duplicate_source_urls={duplicate_source_urls}.", artifacts={"message": message})
        return False


@register_scenario
class SqliteMultiResultWebSynthesisScenario(SqliteToolResultScenario):
    slug = SQLITE_MULTI_RESULT_WEB_SYNTHESIS
    description = "Multi-result web research should synthesize prior tool outputs with one shaped SQLite query."
    tasks = [ScenarioTask.setup(name="inject_prompt", assertion_type="agent_processing"), ScenarioTask(name="verify_smart_sqlite_synthesis", assertion_type="tool_call"), ScenarioTask(name="verify_sourced_answer", assertion_type="manual")]
    eval_synthetic_tools = ("mcp_brightdata_scrape_as_markdown",)
    prompt = "Review exactly these support-automation pages and recommend the best option for enterprise, SMB, and regulated healthcare teams. Compare the evidence and cite the source URLs in one final answer.\n\n" + "\n".join(f"- {url}" for url in SOURCE_URLS)
    mock_kind = "web"
    verify_task_name = "verify_smart_sqlite_synthesis"
    answer_source_urls = SOURCE_URLS
    required_terms = ("enterprise", "SMB", "HIPAA")
    min_sources = 3


@register_scenario
class SqliteIntermediateWorkingTableScenario(SqliteToolResultScenario):
    slug = SQLITE_INTERMEDIATE_WORKING_TABLE
    description = "Nontrivial multi-result synthesis should use one smart aggregate query or a durable working table."
    tasks = [ScenarioTask.setup(name="inject_prompt", assertion_type="agent_processing"), ScenarioTask(name="verify_working_table_sqlite_usage", assertion_type="tool_call"), ScenarioTask(name="verify_sourced_answer", assertion_type="manual")]
    builtin_tools = ("http_request",)
    prompt = "Fetch these product JSON endpoints, identify every plan for a 40-seat regulated support team that has HIPAA or SOC 2 and costs under $900/month, then recommend the best. Include each qualifying plan's full source URL in the final answer.\n\n" + "\n".join(f"- {url}" for url in PRODUCT_URLS)
    mock_kind = "product"
    verify_task_name = "verify_working_table_sqlite_usage"
    require_working_table = True
    max_direct_fetches = 1
    max_single_result_filters = 3
    answer_source_urls = PRODUCT_URLS
    required_terms = ("CareMesh", "Clinic", "HIPAA", "$720", "Dockwise", "Commerce Plus", "$890")
    min_sources = 2

    def _answer_quality_issues(self, body: str) -> list[str]:
        issues = []
        if not _pending_soc2_is_explicitly_disqualified(body):
            issues.append("SOC 2 pending was presented without explicitly disqualifying it")
        if not _dockwise_is_explicitly_qualified(body):
            issues.append("Dockwise Commerce Plus was not accurately identified as qualifying at $890 for 65 seats with SOC 2")
        if _caremesh_is_claimed_as_only_qualifier(body):
            issues.append("CareMesh was incorrectly described as the only qualifying plan")
        return issues


@register_scenario
class SqliteDedupeRequeryScenario(SqliteToolResultScenario):
    slug = SQLITE_DEDUPE_REQUERY
    description = "Duplicate source synthesis should use aggregate SQLite/CTE queries, not repeated blob re-fetches."
    tasks = [ScenarioTask.setup(name="inject_prompt", assertion_type="agent_processing"), ScenarioTask(name="verify_dedupe_sqlite_usage", assertion_type="tool_call"), ScenarioTask(name="verify_sourced_answer", assertion_type="manual")]
    builtin_tools = ("http_request",)
    prompt = "Fetch these four source URLs and return the two strongest genuinely distinct claims. Keep each claim tied to its supporting full source URL and send one final answer with no progress note.\n\n" + "\n".join(f"- {url}" for url in SOURCE_URLS)
    mock_kind = "dedupe"
    verify_task_name = "verify_dedupe_sqlite_usage"
    answer_source_urls = SOURCE_URLS
    required_terms = ()
    min_sources = 2
    max_single_result_filters = 2

    def _answer_quality_issues(self, body: str) -> list[str]:
        mapped_claims, repeated_claims = _dedupe_claim_coverage(body)
        issues = []
        if len(mapped_claims) < 2:
            issues.append(f"fewer than two distinct claims had claim-specific supporting citations: {sorted(mapped_claims)}")
        if repeated_claims:
            issues.append(f"duplicate claim families were not collapsed: {sorted(repeated_claims)}")
        return issues


@register_scenario
class SqliteItemLinkReportScenario(SqliteToolResultScenario):
    slug = SQLITE_ITEM_LINK_REPORT
    description = "Reports over item records should preserve item-level listing URLs, not just source feed URLs."
    tasks = [ScenarioTask.setup(name="inject_prompt", assertion_type="agent_processing"), ScenarioTask(name="verify_item_link_sqlite_usage", assertion_type="tool_call"), ScenarioTask(name="verify_listing_links_in_report", assertion_type="manual")]
    builtin_tools = ("http_request",)
    prompt = "Fetch these vehicle inventory JSON feeds, deduplicate repeated vehicles by VIN, compare 2023+ Tesla Model Y records within 50 miles, and send one concise initial report with the best batch and each item's listing URL. Do not browse or create files.\n\n" + "\n".join(f"- {url}" for url in INVENTORY_URLS)
    mock_kind = "inventory"
    verify_task_name = "verify_item_link_sqlite_usage"
    answer_source_urls = LISTING_URLS
    required_terms = ("Model Y", "Harrisburg", "$27,455")
    min_sources = 2
    max_single_result_filters = 2
    sourced_answer_task_name = "verify_listing_links_in_report"

    def _answer_quality_issues(self, body: str) -> list[str]:
        mapped_items = []
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body or "") if part.strip()]
        lines = [line.strip() for line in (body or "").splitlines() if line.strip()]

        def _has_item_details(unit: str, url: str, vin: str) -> bool:
            without_url = unit.replace(url, "")
            if vin in without_url.upper():
                return True
            return bool(
                "model y" in without_url.casefold()
                and re.search(r"\b20(?:2[3-9]|[3-9]\d)\b", without_url)
                and re.search(r"\$[\d,]+", without_url)
            )

        for url in LISTING_URLS:
            vin = url.rsplit("/vin-", 1)[-1].upper()
            if any(
                url in block
                and sum(candidate_url in block for candidate_url in LISTING_URLS) == 1
                and _has_item_details(block, url, vin)
                for block in [*lines, *paragraphs]
            ):
                mapped_items.append(vin)
        issues = []
        if len(set(mapped_items)) < self.min_sources:
            issues.append(f"fewer than {self.min_sources} listing URLs were mapped to item details")
        return issues
