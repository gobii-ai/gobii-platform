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
    tier = "core"
    category = "sqlite_tool_results"
    expected_runtime = "medium"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("agent_behavior", "sqlite_tool_results", "tool_results", "agent_processing")
    builtin_tools: tuple[str, ...] = (); eval_synthetic_tools: tuple[str, ...] = (); answer_source_urls: tuple[str, ...] = (); required_terms: tuple[str, ...] = ()
    prompt = ""; mock_kind = ""; verify_task_name = "verify_sqlite_usage"; require_working_table = False; max_relevant_tool_calls = 18; min_sources = 1
    max_single_result_filters = 1
    sourced_answer_task_name = "verify_sourced_answer"

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        for names, synthetic in ((self.builtin_tools, False), (self.eval_synthetic_tools, True)):
            if names:
                self._enable_tools(agent_id, names, synthetic=synthetic)
        inbound = self._inject_and_wait(run_id, agent_id, self.prompt, MOCK_BUILDERS[self.mock_kind](), allowed_tool_names={*self.builtin_tools, *self.eval_synthetic_tools, "sqlite_batch", "update_plan", *MESSAGE_TOOL_NAMES, "search_tools"}, max_relevant_tool_calls=self.max_relevant_tool_calls)
        self._record_sqlite_usage(run_id, after=inbound.timestamp, task_name=self.verify_task_name, require_working_table=self.require_working_table, max_direct_fetches=0, max_single_result_filters=self.max_single_result_filters)
        self._record_sourced_answer(run_id, agent_id=agent_id, after=inbound.timestamp, task_name=self.sourced_answer_task_name, source_urls=self.answer_source_urls, required_terms=self.required_terms, min_sources=self.min_sources)

    def _ready_agent(self, agent_id: str) -> None:
        PersistentAgent.objects.filter(id=agent_id).update(charter="Use tools for source data, SQLite for structured multi-result synthesis, and cite source URLs.", planning_state=PersistentAgent.PlanningState.SKIPPED)
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

    def _record_sqlite_usage(self, run_id: str, *, after, task_name: str, require_working_table: bool = False, max_direct_fetches: int = 0, max_single_result_filters: int | None = None) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        calls = _tool_calls_for_run(run_id, after=after, tool_names={"sqlite_batch"})
        summary = summarize_sqlite_tool_result_calls(calls)
        failures = [msg for bad, msg in (
            (not calls, "no sqlite_batch call observed"),
            (summary.aggregate_tool_result_queries < 1, "no aggregate __tool_results query observed"),
            (summary.smart_tool_result_queries < 1, "no smart __tool_results query observed"),
            (summary.direct_result_text_fetches > max_direct_fetches, f"direct result_text fetches {summary.direct_result_text_fetches} > {max_direct_fetches}"),
            (bool(summary.duplicate_direct_fetches), f"duplicate direct result_text fetches={summary.duplicate_direct_fetches}"),
            (bool(summary.manual_values_working_tables), f"manual VALUES working tables={summary.manual_values_working_tables}"),
            (max_single_result_filters is not None and summary.single_result_id_filters > max_single_result_filters, f"single-result filters {summary.single_result_id_filters} > {max_single_result_filters}"),
            (require_working_table and not (summary.creates_working_table and summary.reads_working_table), "no durable working table created from __tool_results and queried"),
        ) if bad]
        status = EvalRunTask.Status.FAILED if failures else EvalRunTask.Status.PASSED
        usage = summary.__dict__
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
        missing_terms = [term for term in required_terms if term.casefold() not in body.casefold()]
        if len(linked_sources) >= min_sources and not missing_terms:
            self.record_task_result(run_id, None, EvalRunTask.Status.PASSED, task_name=task_name, observed_summary=f"Answer cited {len(linked_sources)} source URL(s) and included required facts.", artifacts={"message": message})
            return True

        self.record_task_result(run_id, None, EvalRunTask.Status.FAILED, task_name=task_name, observed_summary=f"Expected at least {min_sources} source URL(s) and required terms; linked_sources={len(linked_sources)}, missing_terms={missing_terms}.", artifacts={"message": message})
        return False


@register_scenario
class SqliteMultiResultWebSynthesisScenario(SqliteToolResultScenario):
    slug = SQLITE_MULTI_RESULT_WEB_SYNTHESIS
    description = "Multi-result web research should synthesize prior tool outputs with one shaped SQLite query."
    tasks = [ScenarioTask(name="inject_prompt", assertion_type="agent_processing"), ScenarioTask(name="verify_smart_sqlite_synthesis", assertion_type="tool_call"), ScenarioTask(name="verify_sourced_answer", assertion_type="manual")]
    eval_synthetic_tools = ("mcp_brightdata_scrape_as_markdown",)
    prompt = "Open exactly these support-automation pages with mcp_brightdata_scrape_as_markdown, then use one sqlite_batch CTE/IN/json_extract query over all prior scrape rows in __tool_results; markdown is at result_json $.result. Do not hand-build comparison rows. Recommend for enterprise, SMB, and regulated healthcare, citing URLs.\n\n" + "\n".join(f"- {url}" for url in SOURCE_URLS)
    mock_kind = "web"
    verify_task_name = "verify_smart_sqlite_synthesis"
    answer_source_urls = SOURCE_URLS
    required_terms = ("enterprise", "SMB", "HIPAA")
    min_sources = 3


@register_scenario
class SqliteIntermediateWorkingTableScenario(SqliteToolResultScenario):
    slug = SQLITE_INTERMEDIATE_WORKING_TABLE
    description = "Nontrivial multi-step synthesis should create and query a durable intermediate working table."
    tasks = [ScenarioTask(name="inject_prompt", assertion_type="agent_processing"), ScenarioTask(name="verify_working_table_sqlite_usage", assertion_type="tool_call"), ScenarioTask(name="verify_sourced_answer", assertion_type="manual")]
    builtin_tools = ("http_request",)
    prompt = "Fetch these product JSON endpoints. Then create durable SQLite table plan_candidates from __tool_results using json_extract/json_each, not hand-entered INSERT VALUES; http content is under result_json $.content and plans under $.content.plans. Query it to recommend the best plan for a 40-seat regulated support team needing HIPAA or SOC 2 under $900/month. Send one final answer with full source URLs.\n\n" + "\n".join(f"- {url}" for url in PRODUCT_URLS)
    mock_kind = "product"
    verify_task_name = "verify_working_table_sqlite_usage"
    require_working_table = True
    max_single_result_filters = 3
    answer_source_urls = PRODUCT_URLS
    required_terms = ("CareMesh", "HIPAA", "$720")


@register_scenario
class SqliteDedupeRequeryScenario(SqliteToolResultScenario):
    slug = SQLITE_DEDUPE_REQUERY
    description = "Duplicate source synthesis should use aggregate SQLite/CTE queries, not repeated blob re-fetches."
    tasks = [ScenarioTask(name="inject_prompt", assertion_type="agent_processing"), ScenarioTask(name="verify_dedupe_sqlite_usage", assertion_type="tool_call"), ScenarioTask(name="verify_sourced_answer", assertion_type="manual")]
    builtin_tools = ("http_request",)
    prompt = "Fetch these four source URLs, dedupe overlapping claims, and return the two strongest unique claims with citations. Use one aggregate sqlite_batch CTE/group/ranking query over __tool_results; do not repeatedly fetch result_text for the same result. Send one final answer with full source URLs, no progress note.\n\n" + "\n".join(f"- {url}" for url in SOURCE_URLS)
    mock_kind = "dedupe"
    verify_task_name = "verify_dedupe_sqlite_usage"
    answer_source_urls = SOURCE_URLS
    required_terms = ()
    min_sources = 2
    max_single_result_filters = 2


@register_scenario
class SqliteItemLinkReportScenario(SqliteToolResultScenario):
    slug = SQLITE_ITEM_LINK_REPORT
    description = "Reports over item records should preserve item-level listing URLs, not just source feed URLs."
    tasks = [ScenarioTask(name="inject_prompt", assertion_type="agent_processing"), ScenarioTask(name="verify_item_link_sqlite_usage", assertion_type="tool_call"), ScenarioTask(name="verify_listing_links_in_report", assertion_type="manual")]
    builtin_tools = ("http_request",)
    prompt = "Fetch these vehicle inventory JSON feeds. Then use SQLite over __tool_results to compare 2023+ Tesla Model Y records within 50 miles and send one concise initial report with the best batch. Do not browse or create files.\n\n" + "\n".join(f"- {url}" for url in INVENTORY_URLS)
    mock_kind = "inventory"
    verify_task_name = "verify_item_link_sqlite_usage"
    answer_source_urls = LISTING_URLS
    required_terms = ("Model Y", "Harrisburg", "$27,455")
    min_sources = 2
    max_single_result_filters = 2
    sourced_answer_task_name = "verify_listing_links_in_report"
