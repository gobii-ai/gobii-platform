import json
import re
from typing import Iterable

import sqlparse
from django.utils import timezone

from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_SERVER
from api.agent.tools.sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from api.agent.tools.sqlite_query_quality import (
    CREATE_TABLE_AS_RE,
    _created_table_name,
    _inserted_table_name,
    _reads_table,
    _structural_sql,
    source_derived_model_mutation_tables,
    summarize_sqlite_tool_result_calls,
)
from api.agent.tools.sqlite_state import agent_sqlite_db
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.agent.tools.web_chat_sender import _looks_like_routine_progress_message
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import register_scenario
from api.evals.tool_params import resolved_tool_param
from api.evals.scenarios.effort_calibration import MESSAGE_TOOL_NAMES, STOP_TOOL_NAMES, _outbound_messages_after, _tool_calls_for_run
from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentEnabledTool,
    PersistentAgentStep,
    PersistentAgentSystemStep,
)


SQLITE_TOOL_RESULT_SUITE_SLUG = "sqlite_tool_results"
SQLITE_MULTI_RESULT_WEB_SYNTHESIS = "sqlite_tool_results_multi_result_web_synthesis"
SQLITE_INTERMEDIATE_WORKING_TABLE = "sqlite_tool_results_intermediate_working_table"
SQLITE_DEDUPE_REQUERY = "sqlite_tool_results_dedupe_requery"
SQLITE_ITEM_LINK_REPORT = "sqlite_tool_results_item_link_report"
SQLITE_NATURAL_RESULT_ACCESS = "sqlite_tool_results_natural_result_access"
SQLITE_BOUNDED_PORTFOLIO_REPORT = "sqlite_tool_results_bounded_portfolio_report"
SQLITE_DOMAIN_TRUTH_OVER_STALE_HISTORY = "sqlite_domain_truth_over_stale_history"
SQLITE_DOMAIN_MODEL_REFRESHES_AND_EVOLVES = "sqlite_domain_model_refreshes_and_evolves"
SQLITE_TOOL_RESULT_SCENARIO_SLUGS = [
    SQLITE_MULTI_RESULT_WEB_SYNTHESIS,
    SQLITE_INTERMEDIATE_WORKING_TABLE,
    SQLITE_DEDUPE_REQUERY,
    SQLITE_ITEM_LINK_REPORT,
    SQLITE_NATURAL_RESULT_ACCESS,
    SQLITE_BOUNDED_PORTFOLIO_REPORT,
    SQLITE_DOMAIN_TRUTH_OVER_STALE_HISTORY,
    SQLITE_DOMAIN_MODEL_REFRESHES_AND_EVOLVES,
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
PORTFOLIO_INDEX_URL = "https://portfolio.example.test/arbor-seed"
PORTFOLIO_COMPANIES = (
    ("aster-forge", "Aster Forge", "Mina Patel", "reliability", "Previously led reliability engineering for a global payments network."),
    ("bramble-health", "Bramble Health", "Jonah Reed", "clinical", "A physician-engineer whose prior work focused on clinical informatics."),
    ("cinderline", "Cinderline", "Priya Nwosu", "streaming", "Built streaming infrastructure for high-volume logistics systems."),
    ("driftwood-robotics", "Driftwood Robotics", "Leo Martin", "autonomy", "An autonomy researcher who worked on warehouse navigation."),
    ("lattice-harbor", "Lattice Harbor", "Naomi Brooks", "security", "Previously ran security operations for a regional bank."),
    ("quarry-labs", "Quarry Labs", "Evan Cho", "developer", "Created developer tooling for large distributed engineering teams."),
    ("ternary-field", "Ternary Field", "Sofia Alvarez", "geospatial", "A geospatial modeling specialist from the climate-risk sector."),
    ("umbra-works", "Umbra Works", "Not publicly disclosed", "private beta", "The company says its founding team will be announced after its private beta."),
)
PORTFOLIO_DETAIL_URLS = (
    "https://profiles.example.test/founders/aster-forge-2d1",
    "https://profiles.example.test/founders/bramble-health-91c",
    "https://profiles.example.test/founders/cinderline-4e7",
    "https://profiles.example.test/founders/driftwood-robotics-a52",
    "https://profiles.example.test/founders/lattice-harbor-83b",
    "https://profiles.example.test/founders/quarry-labs-6d4",
    "https://profiles.example.test/founders/ternary-field-b18",
    "https://profiles.example.test/founders/umbra-works-3a9",
)
PORTFOLIO_SOURCE_URLS = PORTFOLIO_DETAIL_URLS
PORTFOLIO_FETCH_URLS = (PORTFOLIO_INDEX_URL, *PORTFOLIO_DETAIL_URLS)

DOMAIN_ACCOUNT_ID, DOMAIN_ACCOUNT_NAME = "acct-aster-042", "Aster Labs"
DOMAIN_CURRENT = ("legal_review", "Maya Chen", "send the SOC 2 packet")
DOMAIN_REFRESH = ("contracting", "Maya Chen", "resolve contract redlines")
DOMAIN_SOURCE_URL = "https://crm.example.test/accounts/aster-labs"
DOMAIN_REFRESH_URL = "https://crm.example.test/snapshots/aster-labs.json"
DOMAIN_REFRESH_OBSERVED_AT = "2026-07-21T14:30:00Z"
DOMAIN_LEGACY_ACCOUNT = (
    "acct-aster-legacy", DOMAIN_ACCOUNT_NAME, "archived", "Devon Price", "no action",
    "https://crm.example.test/accounts/aster-legacy", "2026-06-10T12:00:00Z",
)
DOMAIN_WORKSTREAMS = (
    ("ws-security-17", "Security packet", "complete", "Maya Chen", "2026-07-22"),
    ("ws-legal-04", "Contract redlines", "open", "Noah Reed", "2026-07-24"),
)


_MUTATION_TARGET_RE = re.compile(
    r'\b(?:insert\s+(?:or\s+\w+\s+)?into|replace\s+into|update|delete\s+from)\s+["`\[]?'
    r'(?P<table>[a-z_]\w*)',
    re.I,
)


def _result_payload(call) -> dict | None:
    result = getattr(call, "result", None)
    try:
        payload = result if isinstance(result, dict) else json.loads(str(result or ""))
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _contains_auto_correction(value) -> bool:
    if isinstance(value, dict):
        return "auto_correction" in value or any(_contains_auto_correction(item) for item in value.values())
    return isinstance(value, list) and any(_contains_auto_correction(item) for item in value)


def _tool_attempt_failures(calls, label: str, *, reject_auto_correction: bool = False) -> list[str]:
    failures = []
    for index, call in enumerate(calls, start=1):
        call_status = str(getattr(call, "status", "") or "").casefold()
        payload = _result_payload(call)
        if call_status != "complete":
            failures.append(f"{label} attempt {index} had execution status {call_status or 'missing'}")
        elif not isinstance(payload, dict):
            failures.append(f"{label} attempt {index} returned an unreadable result")
        elif str(payload.get("status") or "").casefold() != "ok":
            failures.append(f"{label} attempt {index} returned result status {payload.get('status') or 'missing'}")
        elif reject_auto_correction and _contains_auto_correction(payload):
            failures.append(f"{label} attempt {index} depended on auto-correction")
    return failures


def _sqlite_attempt_failures(calls) -> list[str]:
    return _tool_attempt_failures(calls, "SQLite", reject_auto_correction=True)


def _first_shot_source_phase_failures(calls) -> list[str]:
    calls = tuple(calls)
    fetches = [call for call in calls if call.tool_name == "http_request"]
    sqlite_calls = [call for call in calls if call.tool_name == "sqlite_batch"]
    sends = [call for call in calls if call.tool_name == "send_chat_message"]
    failures = _tool_attempt_failures(fetches, "Source fetch")
    failures.extend(_tool_attempt_failures(sends, "Final send"))
    failures.extend(message for failed, message in (
        (
            [call.tool_name for call in calls] != ["http_request", "sqlite_batch", "send_chat_message"],
            f"expected fetch, one SQLite batch, then one send; found {[call.tool_name for call in calls]}",
        ),
        (
            len(fetches) != 1
            or str(resolved_tool_param(fetches[0], "url") or "").rstrip("/")
            != DOMAIN_REFRESH_URL.rstrip("/"),
            "expected one exact CRM snapshot fetch",
        ),
        (len(sqlite_calls) != 1, f"expected one SQLite batch, found {len(sqlite_calls)}"),
        (
            len(sends) != 1 or resolved_tool_param(sends[0], "will_continue_work") is not False,
            f"expected one successful terminal send, found {len(sends)} send attempt(s)",
        ),
    ) if failed)

    completion_ids = [getattr(getattr(call, "step", None), "completion_id", None) for call in calls]
    if any(completion_id is None for completion_id in completion_ids):
        failures.append("every source, SQLite, and send phase must link to an orchestrator completion")
    elif len(set(completion_ids)) != len(completion_ids):
        failures.append("fetch, SQLite, and send phases need separate completions")
    return failures


def _source_write_effect_failures(calls, expected_tables: Iterable[str]) -> list[str]:
    expected = {table.casefold() for table in expected_tables}
    if len(calls) != 1:
        return ["source-derived model write was not a single clean batch"]

    raw_sql = (calls[0].tool_params or {}).get("queries", (calls[0].tool_params or {}).get("sql") or "")
    statements = [
        statement
        for query in (raw_sql if isinstance(raw_sql, list) else [raw_sql])
        for statement in sqlparse.split(str(query))
        if statement.strip()
    ]
    derived = []
    for index, statement in enumerate(statements):
        tables = set(source_derived_model_mutation_tables([statement]))
        mutation = _MUTATION_TARGET_RE.search(statement)
        if mutation and mutation.group("table").casefold() in {"__agent_config", "__agent_skills"}:
            return ["ordinary source reconciliation changed durable agent configuration"]
        if mutation and mutation.group("table").casefold() in expected and not tables:
            return ["source-derived model write began with literal or non-source facts"]
        if tables:
            derived.append((index, tables))

    payload = _result_payload(calls[0]) or {}
    results = payload.get("results", [])
    derived_tables = set().union(*(tables for _index, tables in derived)) if derived else set()
    effective = expected.issubset(derived_tables) and all(
        index < len(results)
        and not _contains_auto_correction(results[index])
        and re.search(r"\baffected\s+[1-9]\d*\s+rows?\b", json.dumps(results[index]), re.I)
        for index, _tables in derived
    )
    return [] if effective else [
        "source-derived model write was wrong, recovered within the batch, or affected no rows"
    ]


_DECISION_FIELD_RE = re.compile(r"\b(?:stage|status|owner|next_action|due_on)\b", re.I)
_AGGREGATE_CALL_RE = re.compile(
    r"\b(?:avg|count|group_concat|json_group_array|json_group_object|max|min|sum|total)\s*\([^)]*\)",
    re.I,
)


def _source_relationship_read_failures(
    sql_values: Iterable[str], parent_table: str, child_table: str,
) -> list[str]:
    expected = {parent_table.casefold(), child_table.casefold()}
    mutated = set()
    row_reads = {table: [] for table in expected}
    for sql in sql_values:
        for statement in sqlparse.split(str(sql or "")):
            derived = set(source_derived_model_mutation_tables((statement,)))
            if derived:
                mutated.update(derived)
                continue
            structural = _structural_sql(statement)
            select_matches = tuple(re.finditer(r"\bselect\b(?P<projection>.*?)\bfrom\b", structural, re.I | re.S))
            if not select_matches:
                continue
            projection = _AGGREGATE_CALL_RE.sub("", select_matches[-1].group("projection"))
            if "*" not in projection and not _DECISION_FIELD_RE.search(projection):
                continue
            for table in expected.intersection(mutated):
                if _reads_table(structural, table):
                    row_reads[table].append((structural, projection))

    missing = sorted(table for table, reads in row_reads.items() if not reads)
    if missing:
        return [f"updated model did not return decision rows from: {missing}"]
    if not all(
        any("*" in projection or re.search(r"\baccount_id\b", statement, re.I) for statement, projection in reads)
        for reads in row_reads.values()
    ):
        return ["model reads did not expose or filter the stable account relationship"]
    return []


def _orphan_completion_failures(run_id, after) -> list[str]:
    if after is None:
        return []
    completions = PersistentAgentCompletion.objects.filter(
        eval_run_id=run_id,
        completion_type=PersistentAgentCompletion.CompletionType.ORCHESTRATOR,
        created_at__gte=after,
    )
    completion_ids = set(completions.values_list("id", flat=True))
    linked_ids = set(
        PersistentAgentStep.objects.filter(
            eval_run_id=run_id,
            created_at__gte=after,
            completion_id__in=completion_ids,
            tool_call__isnull=False,
        ).values_list("completion_id", flat=True)
    )
    count = len(completion_ids - linked_ids)
    return [f"found {count} orphan reasoning completion(s) without an action"] if count else []


UNIQUE_MODEL_INDEX_RE = re.compile(r'\bcreate\s+unique\s+index\b[^;]*?\bon\s+"?(?P<table>[a-z_]\w*)"?', re.I | re.S)
STABLE_IDENTITY_RE = re.compile(r'\bprimary\s+key\b|(?<!["\'`\[])\bunique\b(?!["\'`\]])', re.I)

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


def _large_page(title: str, facts: Iterable[str], *, facts_last: bool = False) -> str:
    body = "\n".join(f"- {fact}" for fact in facts)
    if facts_last:
        filler = "\n".join(
            f"Appendix background {idx}. Routine implementation context covers support workflows and governance."
            for idx in range(520)
        )
        return f"# {title}\n\n## Appendix\n{filler}\n\n## Current details\n{body}"
    filler = "\n".join(f"Appendix note {idx}: implementation details, onboarding checklist, controls, and support workflow context." for idx in range(520))
    return f"# {title}\n\n{body}\n\n## Appendix\n{filler}"


def _web_mock(*, facts_last: bool = False) -> dict:
    pages = {url: _large_page(title, facts, facts_last=facts_last) for url, (title, facts) in zip(SOURCE_URLS, WEB_SOURCE_FACTS)}
    return {
        "mcp_brightdata_search_engine": {"status": "ok", "results": [{"title": title, "url": url, "snippet": facts[1]} for url, (title, facts) in zip(SOURCE_URLS, WEB_SOURCE_FACTS)]},
        "mcp_brightdata_scrape_as_markdown": {"rules": [{"url_contains": url, "result": {"status": "ok", "url": url, "result": page}} for url, page in pages.items()], "default": {"status": "error", "message": "Unknown eval URL."}},
        "search_tools": {"status": "ok", "tools": [{"name": "mcp_brightdata_search_engine", "description": "Search deterministic eval web results."}, {"name": "mcp_brightdata_scrape_as_markdown", "description": "Scrape deterministic eval web pages."}]},
    }


def _portfolio_mock() -> dict:
    pages = {}
    for (_slug, company, founder, _background_term, background), url in zip(
        PORTFOLIO_COMPANIES,
        PORTFOLIO_DETAIL_URLS,
    ):
        pages[url] = _large_page(f"{company} company profile", (
                f"Company: {company}",
                f"Founder: {founder}",
                f"Background: {background}",
                f"Source URL: {url}",
        ), facts_last=True)

    pages[PORTFOLIO_INDEX_URL] = "# Arbor Seed Ventures portfolio\n\n" + "\n".join(
        f"- [{company}]({url})"
        for (_slug, company, *_rest), url in zip(PORTFOLIO_COMPANIES, PORTFOLIO_DETAIL_URLS)
    )

    def search_result(company: str, founder: str, url: str) -> dict:
        founder_snippet = (
            "The founding team has not been publicly disclosed."
            if founder == "Not publicly disclosed"
            else f"The company profile identifies {founder} as founder."
        )
        return {
            "status": "ok",
            "results": [
                {
                    "title": f"{company} founder profile",
                    "url": url,
                    "snippet": founder_snippet,
                },
                {
                    "title": f"Companies with names similar to {company}",
                    "url": "https://directory.example.test/similar-company-names",
                    "snippet": "A noisy directory result about unrelated businesses with similar names.",
                },
            ],
        }

    search_rules = [
        {
            "param_contains": {"query": company},
            "result": search_result(company, founder, url),
        }
        for (_slug, company, founder, _term, _background), url in zip(
            PORTFOLIO_COMPANIES[1:],
            PORTFOLIO_DETAIL_URLS[1:],
        )
    ]
    broad_result = search_result(
        PORTFOLIO_COMPANIES[1][1],
        PORTFOLIO_COMPANIES[1][2],
        PORTFOLIO_DETAIL_URLS[1],
    )
    return {
        "http_request": {
            "rules": [
                {"url_contains": url, "result": {"status": "ok", "status_code": 200, "url": url, "content": page}}
                for url, page in pages.items()
            ],
            "default": {"status": "error", "message": "Unknown eval URL."},
        },
        "mcp_brightdata_search_engine": {
            "rules": search_rules,
            "default": broad_result,
        },
        "mcp_brightdata_scrape_as_markdown": {
            "rules": [
                {"url_contains": url, "result": {"status": "ok", "url": url, "result": page}}
                for url, page in pages.items()
            ],
            "default": {"status": "error", "message": "Unknown eval URL."},
        },
    }


def _product_mock() -> dict:
    def expanded_plans(plans: Iterable[tuple]) -> list[dict]:
        catalog = [
            {"plan": plan, "monthly_price_usd": price, "included_seats": seats, "compliance": list(compliance), "fit_score": score}
            for plan, price, seats, compliance, score in plans
        ]
        regional = [
            {
                "plan": f"Regional {index + 1}",
                "monthly_price_usd": 240 + index * 25,
                "included_seats": 8 + index * 2,
                "compliance": [],
                "fit_score": 30 + index,
            }
            for index in range(16)
        ]
        return [*regional, *catalog]

    payloads = {
        url: {"vendor": vendor, "source_url": url, "plans": expanded_plans(plans)}
        for url, (vendor, plans) in zip(PRODUCT_URLS, PRODUCT_PLAN_ROWS)
    }
    return {"http_request": {"rules": [{"url_contains": url, "result": {"status": "ok", "status_code": 200, "url": url, "content": payload}} for url, payload in payloads.items()], "default": {"status": "error", "message": "Unknown eval URL."}}}


def _inventory_mock() -> dict:
    def expanded_rows(source_index: int, vehicles: Iterable[dict]) -> list[dict]:
        dealer_names = ("Blue Ridge Auto", "Capital EV Center", "Piedmont Electric", "Potomac Motors")
        filler = [
            {
                "vin": f"5YJYGDEE{source_index}{index:08d}",
                "year": 2023 + index % 3,
                "trim": "Model Y Long Range" if index % 2 else "Model Y",
                "mileage": 41000 + index * 113,
                "price_usd": 42000 + index * 97,
                "distance_mi": 20 + index % 29,
                "dealer": dealer_names[(source_index + index) % len(dealer_names)],
                "listing_url": (
                    f"https://listings.example.test/tesla/model-y/5yjygdee{source_index}{index:08d}"
                ),
            }
            for index in range(40)
        ]
        return [*filler, *vehicles]

    rules = [
        {
            "url_contains": url,
            "result": {
                "status": "ok",
                "status_code": 200,
                "url": url,
                "content": {
                    "source_url": url,
                    "vehicles": expanded_rows(source_index, vehicles),
                },
            },
        }
        for source_index, (url, vehicles) in enumerate(INVENTORY_ROWS, start=1)
    ]
    return {"http_request": {"rules": rules, "default": {"status": "error", "message": "Unknown eval URL."}}}


def _dedupe_mock() -> dict:
    claims = (
        "Claim: AxonFlow is strongest for enterprise teams because it combines SOC 2 controls, analytics, Salesforce integration, and a 99.95% SLA.",
        "Claim: BrightSupport is strongest for SMB teams because it offers low-admin shared inbox automation and transparent monthly pricing.",
        "Claim: CareMesh is strongest for HIPAA-regulated healthcare support because it includes a BAA, PHI redaction, escalation routing, and audit exports.",
        "Claim: BrightSupport is strongest for SMB teams because it offers quick setup, low administration, and transparent monthly pricing.",
    )
    pages = {
        url: _large_page(f"Source {i}", (claim,))
        for i, (url, claim) in enumerate(zip(SOURCE_URLS, claims), start=1)
    }
    return {
        "http_request": {
            "rules": [
                {
                    "url_contains": url,
                    "result": {"status": "ok", "status_code": 200, "url": url, "content": {"url": url, "text": page}},
                }
                for url, page in pages.items()
            ],
            "default": {"status": "error", "message": "Unknown eval URL."},
        },
        "mcp_brightdata_scrape_as_markdown": {
            "rules": [
                {"url_contains": url, "result": {"status": "ok", "url": url, "result": page}}
                for url, page in pages.items()
            ],
            "default": {"status": "error", "message": "Unknown eval URL."},
        },
    }


def _domain_refresh_mock() -> dict:
    stage, owner, next_action = DOMAIN_REFRESH
    payload = {
        "observed_at": DOMAIN_REFRESH_OBSERVED_AT,
        "source_url": DOMAIN_REFRESH_URL,
        "accounts": [{
            "account_id": DOMAIN_ACCOUNT_ID, "name": DOMAIN_ACCOUNT_NAME,
            "stage": stage, "owner": owner, "next_action": next_action,
            "source_url": DOMAIN_REFRESH_URL, "observed_at": DOMAIN_REFRESH_OBSERVED_AT,
        }],
        "workstreams": [
            dict(zip(
                ("workstream_id", "name", "status", "owner", "due_on"), row,
            ), account_id=DOMAIN_ACCOUNT_ID, source_url=DOMAIN_REFRESH_URL,
                observed_at=DOMAIN_REFRESH_OBSERVED_AT)
            for row in DOMAIN_WORKSTREAMS
        ],
    }
    return {
        "http_request": {
            "rules": [{
                "url_contains": DOMAIN_REFRESH_URL,
                "result": {"status": "ok", "status_code": 200, "url": DOMAIN_REFRESH_URL, "content": payload},
            }],
            "default": {"status": "error", "message": "Unknown eval URL."},
        }
    }


MOCK_BUILDERS = {"web": _web_mock, "aged_web": lambda: _web_mock(facts_last=True), "product": _product_mock, "dedupe": _dedupe_mock, "inventory": _inventory_mock}


def _source_fetch_counts(calls, *, tool_names: Iterable[str], source_urls: Iterable[str]) -> dict[str, int]:
    expected = {url.rstrip("/"): 0 for url in source_urls}
    allowed_tools = set(tool_names)
    for call in calls:
        if call.tool_name not in allowed_tools or str(getattr(call, "status", "complete")).lower() != "complete":
            continue
        actual_url = str(resolved_tool_param(call, "url") or "").rstrip("/")
        if actual_url in expected:
            expected[actual_url] += 1
    return expected


def _sqlite_calls_with_persisted_effects(calls):
    successful_calls = [
        call for call in calls
        if str(getattr(call, "status", "complete")).lower() == "complete"
    ]
    successful_sql = "\n".join(
        str((call.tool_params or {}).get("sql") or "") for call in successful_calls
    )
    successful_statements = [
        _structural_sql(statement) for statement in sqlparse.split(successful_sql) if statement.strip()
    ]
    strategy_calls = []
    for call in calls:
        if call in successful_calls:
            strategy_calls.append(call)
            continue
        if "Query not executed:" in str(getattr(call, "result", "")):
            continue
        failed_summary = summarize_sqlite_tool_result_calls([call])
        if any(
            _reads_table(statement, table)
            for table in failed_summary.working_table_names
            for statement in successful_statements
        ):
            strategy_calls.append(call)
    return successful_calls, strategy_calls


def _domain_model_lineage(
    sql: str,
    *,
    direct_tables: Iterable[str],
    row_direct_tables: Iterable[str],
    candidate_tables: Iterable[str],
) -> tuple[tuple[str, ...], tuple[str, ...], set[str]]:
    candidates = tuple(dict.fromkeys(table.casefold() for table in candidate_tables))
    modeled = {table.casefold() for table in direct_tables if table.casefold() in candidates}
    row_modeled = {table.casefold() for table in row_direct_tables if table.casefold() in modeled}
    statements = [_structural_sql(statement) for statement in sqlparse.split(sql or "") if statement.strip()]

    changed = True
    while changed:
        changed = False
        for statement in statements:
            target = _created_table_name(statement) or _inserted_table_name(statement) or ""
            if target not in candidates:
                continue
            source_tables = {source for source in modeled if _reads_table(statement, source)}
            if source_tables:
                if target not in modeled:
                    modeled.add(target)
                    changed = True
                if source_tables.intersection(row_modeled) and target not in row_modeled:
                    row_modeled.add(target)
                    changed = True

    identity_tables = set()
    for statement in statements:
        created_table = _created_table_name(statement)
        if created_table and not CREATE_TABLE_AS_RE.search(statement) and STABLE_IDENTITY_RE.search(statement):
            identity_tables.add(created_table)
        if index_match := UNIQUE_MODEL_INDEX_RE.search(statement):
            identity_tables.add(index_match.group("table").casefold())
    return (
        tuple(table for table in candidates if table in modeled),
        tuple(table for table in candidates if table in row_modeled),
        identity_tables,
    )


def _decision_model_tables(sql: str, model_tables: Iterable[str]) -> tuple[str, ...]:
    tables = tuple(model_tables)
    decisions = set()
    for statement in (_structural_sql(part) for part in sqlparse.split(sql or "") if part.strip()):
        parsed = sqlparse.parse(statement)
        if not parsed or parsed[0].get_type() != "SELECT":
            continue
        if re.search(r"\bwhere\b", statement, re.I) and re.search(r"\border\s+by\b", statement, re.I):
            decisions.update(table for table in tables if _reads_table(statement, table))
    return tuple(table for table in tables if table in decisions)


def _seed_domain_account(
    agent_id: str, state: tuple[str, str, str], observed_at: str, *, duplicate_name: bool = False,
) -> None:
    stage, owner, next_action = state
    stages = ("qualification", "security_review", "legal_review", "contracting", "on_hold")
    decoys = [
        (
            f"acct-decoy-{index:03d}",
            f"Example Account {index:03d}",
            stages[index % len(stages)],
            f"Owner {index:03d}",
            f"Review account note {index:03d}",
            f"https://crm.example.test/accounts/example-{index:03d}",
            f"2026-07-{(index % 9) + 10:02d}T12:00:00Z",
        )
        for index in range(32)
    ]
    target = (DOMAIN_ACCOUNT_ID, DOMAIN_ACCOUNT_NAME, stage, owner, next_action, DOMAIN_SOURCE_URL, observed_at)

    with agent_sqlite_db(str(agent_id)) as db_path:
        conn = open_guarded_sqlite_connection(db_path)
        try:
            conn.execute("DROP TABLE IF EXISTS accounts;")
            conn.execute(
                "CREATE TABLE accounts (account_id TEXT PRIMARY KEY, name TEXT NOT NULL, stage TEXT NOT NULL, "
                "owner TEXT NOT NULL, next_action TEXT NOT NULL, source_url TEXT NOT NULL, observed_at TEXT NOT NULL);"
            )
            conn.executemany(
                "INSERT INTO accounts (account_id, name, stage, owner, next_action, source_url, observed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?);",
                [*decoys[:16], target, *decoys[16:], *([DOMAIN_LEGACY_ACCOUNT] if duplicate_name else [])],
            )
            conn.commit()
        finally:
            clear_guarded_connection(conn)
            conn.close()


def _inspect_domain_refresh_state(agent_id: str) -> tuple[list[str], str | None]:
    expected_ids = {row[0] for row in DOMAIN_WORKSTREAMS}
    failures = []
    child_table = None
    with agent_sqlite_db(str(agent_id)) as db_path:
        conn = open_guarded_sqlite_connection(db_path)
        try:
            account = conn.execute(
                """
                SELECT account_id, name, stage, owner, next_action, source_url, observed_at
                FROM accounts WHERE account_id = ?;
                """,
                (DOMAIN_ACCOUNT_ID,),
            ).fetchall()
            current = [(DOMAIN_ACCOUNT_ID, DOMAIN_ACCOUNT_NAME, *DOMAIN_REFRESH, DOMAIN_REFRESH_URL, DOMAIN_REFRESH_OBSERVED_AT)]
            if account != current:
                failures.append("existing account was duplicated or retained stale values")
            legacy = conn.execute(
                "SELECT account_id, name, stage, owner, next_action, source_url, observed_at FROM accounts WHERE account_id=?;",
                (DOMAIN_LEGACY_ACCOUNT[0],),
            ).fetchall()
            if legacy != [DOMAIN_LEGACY_ACCOUNT]:
                failures.append("refresh changed a different account sharing the display name")

            child = None
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND substr(name, 1, 2) != '__';"
            ).fetchall()
            for (table_name,) in tables:
                if table_name == "accounts":
                    continue
                quoted = '"' + table_name.replace('"', '""') + '"'
                columns = conn.execute(f"PRAGMA table_info({quoted});").fetchall()
                names = {column[1] for column in columns}
                required = {
                    "workstream_id", "account_id", "name", "status", "owner", "due_on",
                    "source_url", "observed_at",
                }
                if not required.issubset(names):
                    continue
                rows = conn.execute(
                    f"SELECT workstream_id, account_id, name, status, owner, due_on, source_url, observed_at "
                    f"FROM {quoted} WHERE account_id = ?;",
                    (DOMAIN_ACCOUNT_ID,),
                ).fetchall()
                if len(rows) != len(expected_ids) or {row[0] for row in rows} != expected_ids:
                    continue
                unique_indexes = [row[1] for row in conn.execute(f"PRAGMA index_list({quoted});") if row[2]]
                keyed = any(row[1] == "workstream_id" and row[5] for row in columns) or any(
                    "workstream_id" in {row[2] for row in conn.execute(f'PRAGMA index_info("{name}");')}
                    for name in unique_indexes
                )
                expected_rows = {
                    (workstream_id, DOMAIN_ACCOUNT_ID, name, status, owner, due_on,
                     DOMAIN_REFRESH_URL, DOMAIN_REFRESH_OBSERVED_AT)
                    for workstream_id, name, status, owner, due_on in DOMAIN_WORKSTREAMS
                }
                sourced = set(rows) == expected_rows
                child = (table_name, keyed, sourced)
                break
            if child is None:
                failures.append("new workstreams were not modeled as a related table")
            else:
                child_table = child[0]
                if not child[1]:
                    failures.append("workstream model lacked stable identity")
                if not child[2]:
                    failures.append("workstream model lacked complete facts, relationship, or provenance")
        finally:
            clear_guarded_connection(conn)
            conn.close()
    return failures, child_table


def _domain_refresh_state_failures(agent_id: str) -> list[str]:
    return _inspect_domain_refresh_state(agent_id)[0]


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
    max_sqlite_usage_calls: int | None = None
    max_sqlite_attempts: int | None = None
    reject_result_id_case_rows = False
    sourced_answer_task_name = "verify_sourced_answer"
    result_access_source_urls: tuple[str, ...] = ()
    reject_duplicate_fetches = False
    max_result_access_sqlite_calls = 4
    max_result_access_response_bytes = 32_000
    result_access_fetch_tools = ("mcp_brightdata_scrape_as_markdown",)
    require_result_access_sqlite = True

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        for names, synthetic in ((self.builtin_tools, False), (self.eval_synthetic_tools, True)):
            if names:
                self._enable_tools(agent_id, names, synthetic=synthetic)
        inbound = self._inject_and_wait(run_id, agent_id, self.prompt, MOCK_BUILDERS[self.mock_kind](), allowed_tool_names={*self.builtin_tools, *self.eval_synthetic_tools, "sqlite_batch", "update_plan", *MESSAGE_TOOL_NAMES, "search_tools"}, max_relevant_tool_calls=self.max_relevant_tool_calls)
        if self.result_access_source_urls:
            self._record_result_access(run_id, after=inbound.timestamp, task_name=self.verify_task_name, source_urls=self.result_access_source_urls, reject_duplicate_fetches=self.reject_duplicate_fetches)
        else:
            self._record_sqlite_usage(run_id, after=inbound.timestamp, task_name=self.verify_task_name, require_working_table=self.require_working_table, max_direct_fetches=0, max_single_result_filters=self.max_single_result_filters)
        self._record_sourced_answer(run_id, agent_id=agent_id, after=inbound.timestamp, task_name=self.sourced_answer_task_name, source_urls=self.answer_source_urls, required_terms=self.required_terms, min_sources=self.min_sources)

    def _ready_agent(self, agent_id: str) -> None:
        PersistentAgent.objects.filter(id=agent_id).update(
            charter="Research requested sources efficiently, synthesize the evidence, and cite source URLs.",
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

    def _inject_and_wait(self, run_id: str, agent_id: str, prompt: str, mock_config: dict, *, allowed_tool_names: Iterable[str], max_relevant_tool_calls: int = 14, task_name: str = "inject_prompt"):
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        with self.wait_for_agent_idle(agent_id, timeout=240):
            inbound = self.inject_message(agent_id, prompt, trigger_processing=True, eval_run_id=run_id, mock_config=mock_config, eval_stop_policy={"max_relevant_tool_calls": max_relevant_tool_calls, "stop_on_unexpected_relevant_tool": True, "allowed_tool_names": list(allowed_tool_names), "ignored_tool_names": list(STOP_TOOL_NAMES)})
        self.record_task_result(run_id, None, EvalRunTask.Status.PASSED, task_name=task_name, observed_summary="Prompt injected and processing completed.", artifacts={"message": inbound})
        return inbound

    def _record_sqlite_usage(self, run_id: str, *, after, task_name: str, require_working_table: bool = False, max_direct_fetches: int = 0, max_single_result_filters: int | None = None) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        calls = _tool_calls_for_run(run_id, after=after, tool_names={"sqlite_batch"})
        successful_calls, strategy_calls = _sqlite_calls_with_persisted_effects(calls)
        summary = summarize_sqlite_tool_result_calls(strategy_calls)
        result_id_case_calls = [
            call for call in successful_calls
            if any(
                re.search(
                    r'\bcase\s+(?:(?:\(\s*)?(?:\w+\.)?"?\bresult_id\b"?(?:\s*\))?\s+when\b|when\b(?:(?!\bend\b).)*(?:\w+\.)?"?\bresult_id\b"?)',
                    _structural_sql(statement),
                    re.I | re.S,
                )
                for statement in sqlparse.split(str((call.tool_params or {}).get("sql") or ""))
            )
        ]
        failures = [msg for bad, msg in (
            (not successful_calls, "no successful sqlite_batch call observed"),
            (self.max_sqlite_attempts is not None and len(calls) > self.max_sqlite_attempts, f"sqlite_batch attempts {len(calls)} > {self.max_sqlite_attempts}"),
            (self.max_sqlite_usage_calls is not None and len(successful_calls) > self.max_sqlite_usage_calls, f"sqlite_batch calls {len(successful_calls)} > {self.max_sqlite_usage_calls}"),
            (summary.aggregate_tool_result_queries < 1, "no aggregate __tool_results query observed"),
            (summary.smart_tool_result_queries < 1, "no smart __tool_results query observed"),
            (summary.direct_result_text_fetches > max_direct_fetches, f"direct result_text fetches {summary.direct_result_text_fetches} > {max_direct_fetches}"),
            (bool(summary.duplicate_direct_fetches), f"duplicate direct result_text fetches={summary.duplicate_direct_fetches}"),
            (bool(summary.manual_values_working_tables), f"manual VALUES working tables={summary.manual_values_working_tables}"),
            (self.reject_result_id_case_rows and bool(result_id_case_calls), "comparison rows were hand-built with CASE result_id"),
            (max_single_result_filters is not None and summary.single_result_id_filters > max_single_result_filters, f"single-result filters {summary.single_result_id_filters} > {max_single_result_filters}"),
            (require_working_table and not (summary.creates_working_table and summary.reads_working_table), "no durable working table created from __tool_results and queried"),
        ) if bad]
        status = EvalRunTask.Status.FAILED if failures else EvalRunTask.Status.PASSED
        usage = summary.__dict__
        observed = "; ".join(failures) if failures else f"Observed smart sqlite/tool-result usage: {usage}"
        self.record_task_result(run_id, None, status, task_name=task_name, observed_summary=observed, artifacts={"step": strategy_calls[0].step, "usage": usage} if strategy_calls else {})
        return not failures

    def _record_result_access(self, run_id: str, *, after, task_name: str, source_urls: Iterable[str], reject_duplicate_fetches: bool = False) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        calls = _tool_calls_for_run(run_id, after=after)
        fetch_calls = [call for call in calls if call.tool_name in self.result_access_fetch_tools]
        sqlite_calls = [call for call in calls if call.tool_name == "sqlite_batch"]
        fetch_counts = _source_fetch_counts(calls, tool_names=self.result_access_fetch_tools, source_urls=source_urls)
        attempt_urls = [str(resolved_tool_param(call, "url") or "").rstrip("/") for call in fetch_calls]
        duplicate_attempts = [url for url in fetch_counts if attempt_urls.count(url) > 1]
        successful_sqlite, strategy_calls = _sqlite_calls_with_persisted_effects(sqlite_calls)
        summary = summarize_sqlite_tool_result_calls(strategy_calls)
        missing = [url for url, count in fetch_counts.items() if count == 0]
        read_file_calls = [call for call in calls if call.tool_name == "read_file"]
        oversized_sqlite = [
            len(str(call.result or "").encode("utf-8"))
            for call in successful_sqlite
            if len(str(call.result or "").encode("utf-8")) > self.max_result_access_response_bytes
        ]
        failures = _tool_attempt_failures(fetch_calls, "Source fetch")
        failures.extend(_sqlite_attempt_failures(sqlite_calls))
        failures.extend(message for failed, message in (
            (bool(missing), f"missing source fetches={missing}"),
            (reject_duplicate_fetches and bool(duplicate_attempts), f"duplicate source fetches={duplicate_attempts}"),
            (bool(read_file_calls), f"read_file used for web results {len(read_file_calls)} time(s)"),
            (self.require_result_access_sqlite and not successful_sqlite, "no successful sqlite_batch call observed"),
            (bool(successful_sqlite) and summary.aggregate_tool_result_queries < 1, "no aggregate __tool_results query observed"),
            (
                len(successful_sqlite) > self.max_result_access_sqlite_calls,
                f"sqlite result-access probes {len(successful_sqlite)} > {self.max_result_access_sqlite_calls}",
            ),
            (bool(oversized_sqlite), f"oversized SQLite result bytes={oversized_sqlite}"),
        ) if failed)
        evidence = read_file_calls or successful_sqlite[-1:] or strategy_calls or calls
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED if failures else EvalRunTask.Status.PASSED,
            task_name=task_name,
            observed_summary=(
                "; ".join(failures)
                if failures
                else "Fetched every source without file access or wasteful result probing."
            ),
            artifacts={"step": evidence[0].step, "fetch_counts": fetch_counts} if evidence else {},
        )
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
        normalized_body = body.casefold().replace(",", "")
        missing_terms = [term for term in required_terms if term.casefold().replace(",", "") not in normalized_body]
        if len(linked_sources) >= min_sources and not missing_terms:
            self.record_task_result(run_id, None, EvalRunTask.Status.PASSED, task_name=task_name, observed_summary=f"Answer cited {len(linked_sources)} source URL(s) and included required facts.", artifacts={"message": message})
            return True

        self.record_task_result(run_id, None, EvalRunTask.Status.FAILED, task_name=task_name, observed_summary=f"Expected at least {min_sources} source URL(s) and required terms; linked_sources={len(linked_sources)}, missing_terms={missing_terms}.", artifacts={"message": message})
        return False


@register_scenario
class SqliteMultiResultWebSynthesisScenario(SqliteToolResultScenario):
    slug = SQLITE_MULTI_RESULT_WEB_SYNTHESIS
    description = "Multi-result web research should synthesize prior tool outputs with efficient shaped SQLite queries."
    tasks = [ScenarioTask(name="inject_prompt", assertion_type="agent_processing"), ScenarioTask(name="verify_smart_sqlite_synthesis", assertion_type="tool_call"), ScenarioTask(name="verify_sourced_answer", assertion_type="manual")]
    eval_synthetic_tools = ("mcp_brightdata_scrape_as_markdown",)
    prompt = (
        "Compare these support automation products for an enterprise team, a small team, and a regulated healthcare "
        "team. Include the evidence and source links you relied on.\n\n"
        + "\n".join(f"- {url}" for url in SOURCE_URLS)
    )
    mock_kind = "web"
    verify_task_name = "verify_smart_sqlite_synthesis"
    answer_source_urls = SOURCE_URLS
    required_terms = ("enterprise", "SMB", "HIPAA")
    min_sources = 3
    max_sqlite_usage_calls = 2
    max_sqlite_attempts = 3
    reject_result_id_case_rows = True


@register_scenario
class SqliteNaturalResultAccessScenario(SqliteToolResultScenario):
    slug = SQLITE_NATURAL_RESULT_ACCESS
    description = "Large web results should be fetched naturally and synthesized from SQLite without invented filespace paths."
    tasks = [ScenarioTask(name="inject_prompt", assertion_type="agent_processing"), ScenarioTask(name="verify_natural_result_access", assertion_type="tool_call"), ScenarioTask(name="verify_sourced_answer", assertion_type="manual")]
    builtin_tools = ("read_file",)
    eval_synthetic_tools = ("mcp_brightdata_scrape_as_markdown",)
    prompt = (
        "Compare these support automation products. Which one best fits an enterprise team, a small team, and a "
        "regulated healthcare team? Include the evidence and source links you relied on.\n\n"
        + "\n".join(f"- {url}" for url in SOURCE_URLS)
    )

    mock_kind = "aged_web"
    verify_task_name = "verify_natural_result_access"
    result_access_source_urls = SOURCE_URLS
    reject_duplicate_fetches = True
    answer_source_urls = SOURCE_URLS
    required_terms = ("99.95", "shared inbox", "PHI", "Shopify")
    min_sources = 4


@register_scenario
class SqliteIntermediateWorkingTableScenario(SqliteToolResultScenario):
    slug = SQLITE_INTERMEDIATE_WORKING_TABLE
    description = "Multi-turn catalog reasoning should model related domain entities once and reuse them."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_domain_model", assertion_type="tool_call"),
        ScenarioTask(name="verify_initial_answer", assertion_type="manual"),
        ScenarioTask(name="inject_followup", assertion_type="agent_processing"),
        ScenarioTask(name="verify_model_reuse", assertion_type="tool_call"),
        ScenarioTask(name="verify_followup_answer", assertion_type="manual"),
    ]
    builtin_tools = ("http_request",)
    prompt = (
        "Fetch these product catalog JSON endpoints and recommend the best plan for a 40-person regulated support "
        "team that needs HIPAA or SOC 2 and must stay under $900/month. Include the plan, price, seat capacity, "
        "compliance reason, and source URL. We'll have follow-up questions across vendors, plans, and compliance, so "
        "keep the analysis reusable.\n\n"
        + "\n".join(f"- {url}" for url in PRODUCT_URLS)
    )
    followup_prompt = (
        "Using the same catalog, the team is now 70 people, SAML is mandatory, and the budget is $1,600/month. "
        "Which plan is best? Reply with the plan, price, seat capacity, and source URL."
    )
    mock_kind = "product"

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        self._enable_tools(agent_id, self.builtin_tools)
        allowed_tools = {*self.builtin_tools, "sqlite_batch", "update_plan", *MESSAGE_TOOL_NAMES}
        initial = self._inject_and_wait(
            run_id,
            agent_id,
            self.prompt,
            _product_mock(),
            allowed_tool_names=allowed_tools,
            max_relevant_tool_calls=18,
        )
        model_tables = self._record_domain_model(run_id, after=initial.timestamp)
        self._record_sourced_answer(
            run_id,
            agent_id=agent_id,
            after=initial.timestamp,
            task_name="verify_initial_answer",
            source_urls=(PRODUCT_URLS[2],),
            required_terms=("CareMesh", "Clinic", "720", "50", "HIPAA"),
            min_sources=1,
        )

        followup = self._inject_and_wait(
            run_id,
            agent_id,
            self.followup_prompt,
            _product_mock(),
            allowed_tool_names=allowed_tools,
            max_relevant_tool_calls=24,
            task_name="inject_followup",
        )
        self._record_model_reuse(run_id, after=followup.timestamp, model_tables=model_tables)
        self._record_sourced_answer(
            run_id,
            agent_id=agent_id,
            after=followup.timestamp,
            task_name="verify_followup_answer",
            source_urls=(PRODUCT_URLS[0],),
            required_terms=("AxonFlow", "Enterprise", "1500", "80", "SAML"),
            min_sources=1,
        )

    def _record_domain_model(self, run_id: str, *, after) -> tuple[str, ...]:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_domain_model")
        calls = _tool_calls_for_run(run_id, after=after, tool_names={"sqlite_batch"})
        successful_calls, strategy_calls = _sqlite_calls_with_persisted_effects(calls)
        summary = summarize_sqlite_tool_result_calls(strategy_calls)
        successful_sql = "\n".join(str((call.tool_params or {}).get("sql") or "") for call in successful_calls)
        strategy_sql = "\n".join(str((call.tool_params or {}).get("sql") or "") for call in strategy_calls)
        direct_tables = summary.derived_working_table_names
        model_tables, row_model_tables, identity_tables = _domain_model_lineage(
            strategy_sql,
            direct_tables=direct_tables,
            row_direct_tables=summary.row_derived_working_table_names,
            candidate_tables=summary.working_table_names,
        )
        read_tables = _decision_model_tables(successful_sql, model_tables)
        has_stable_identity = bool(read_tables) and set(read_tables).issubset(identity_tables)
        reusable_tables = tuple(table for table in model_tables if table in identity_tables)
        row_derived_model_tables = set(read_tables).intersection(row_model_tables)
        manually_populated_model_tables = set(summary.manual_values_table_names).intersection(model_tables)
        failures = [message for failed, message in (
            (not successful_calls, "no successful sqlite_batch call observed"),
            (summary.tool_result_statement_count < 1 or summary.uses_json_functions < 1, "domain model was not derived from tool-result JSON"),
            (summary.aggregate_tool_result_queries < 1, "domain model did not import tool results in aggregate"),
            (
                summary.single_result_id_filters > self.max_single_result_filters,
                f"domain model imported tool results one result at a time "
                f"({summary.single_result_id_filters} > {self.max_single_result_filters})",
            ),
            (not model_tables, "no reusable domain table was created"),
            (not has_stable_identity, "domain model lacked stable identity constraints"),
            (not row_derived_model_tables, "repeating child rows were not extracted into the domain model"),
            (not re.search(r"\b(?:source_url|source_id|provenance)\b", strategy_sql, re.I), "domain model lacked source provenance"),
            (not read_tables, "initial decision did not query the reusable domain model"),
            (not re.search(r"\bwhere\b", successful_sql, re.I), "initial decision did not filter in SQL"),
            (not re.search(r"\border\s+by\b", successful_sql, re.I), "initial decision did not rank in SQL"),
            (bool(manually_populated_model_tables), "domain rows were hand-entered with VALUES"),
        ) if failed]
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED if failures else EvalRunTask.Status.PASSED,
            task_name="verify_domain_model",
            observed_summary="; ".join(failures) if failures else f"Modeled and queried reusable domain tables: {reusable_tables}.",
            artifacts={"step": successful_calls[0].step, "model_tables": model_tables, "decision_tables": read_tables} if successful_calls else {},
        )
        return reusable_tables

    def _record_model_reuse(self, run_id: str, *, after, model_tables: Iterable[str]) -> None:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_model_reuse")
        calls = _tool_calls_for_run(run_id, after=after)
        http_calls = [call for call in calls if call.tool_name == "http_request"]
        sqlite_calls = [call for call in calls if call.tool_name == "sqlite_batch" and call.status == "complete"]
        sql = "\n".join(str((call.tool_params or {}).get("sql") or "") for call in sqlite_calls)
        structural_sql = "\n".join(
            _structural_sql(statement) for statement in sqlparse.split(sql) if statement.strip()
        )
        read_tables = _decision_model_tables(structural_sql, model_tables)
        failures = [message for failed, message in (
            (bool(http_calls), f"follow-up refetched {len(http_calls)} source(s)"),
            (not sqlite_calls, "follow-up did not query SQLite"),
            ("__tool_results" in structural_sql.casefold(), "follow-up reread raw tool results instead of the domain model"),
            (not read_tables, f"follow-up did not read an identity-qualified domain model: {read_tables}"),
            (not re.search(r"\bwhere\b", structural_sql, re.I), "follow-up did not apply decision filters in SQL"),
            (not re.search(r"\border\s+by\b", structural_sql, re.I), "follow-up did not rank candidates in SQL"),
        ) if failed]
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED if failures else EvalRunTask.Status.PASSED,
            task_name="verify_model_reuse",
            observed_summary="; ".join(failures) if failures else f"Reused shaped domain model: {read_tables}.",
            artifacts={"step": sqlite_calls[0].step, "read_tables": read_tables} if sqlite_calls else {},
        )


class SqliteDomainModelScenario(SqliteToolResultScenario):
    domain_charter = "Own current account and relationship operations for the sales team. Keep incoming evidence coherent and report precise, source-backed next actions."

    def _prepare_domain_agent(
        self, agent_id: str, state: tuple[str, str, str], observed_at: str, *, duplicate_name: bool = False,
    ) -> None:
        self._ready_agent(agent_id)
        PersistentAgent.objects.filter(id=agent_id).update(charter=self.domain_charter)
        _seed_domain_account(agent_id, state, observed_at, duplicate_name=duplicate_name)

    def _record_check(self, run_id: str, task_name: str, failures: list[str], success: str) -> None:
        self.record_task_result(
            run_id, None, EvalRunTask.Status.FAILED if failures else EvalRunTask.Status.PASSED,
            task_name=task_name,
            observed_summary="; ".join(failures) if failures else success,
            artifacts={},
        )


@register_scenario
class SqliteDomainTruthOverStaleHistoryScenario(SqliteDomainModelScenario):
    slug = SQLITE_DOMAIN_TRUTH_OVER_STALE_HISTORY
    description = "An established domain model should beat stale conversation history."
    expected_runtime = "short"
    tasks = [ScenarioTask(name="inject_prompt", assertion_type="agent_processing"), ScenarioTask(name="verify_modeled_truth_read", assertion_type="tool_call"), ScenarioTask(name="verify_current_truth_answer", assertion_type="manual")]
    prompt = "I'm picking up Aster Labs. Where does it actually stand now, who owns it, and what should happen next?"

    def run(self, run_id: str, agent_id: str) -> None:
        self._prepare_domain_agent(agent_id, DOMAIN_CURRENT, timezone.now().isoformat())
        self.inject_message(
            agent_id,
            "Last week's note put Aster Labs in discovery with Devon Price, waiting until Friday.",
            trigger_processing=False,
            eval_run_id=run_id,
        )
        inbound = self._inject_and_wait(
            run_id, agent_id, self.prompt, {},
            allowed_tool_names={"sqlite_batch", "send_chat_message"}, max_relevant_tool_calls=4,
        )
        calls = _tool_calls_for_run(run_id, after=inbound.timestamp, tool_names={"sqlite_batch"})
        successful = [call for call in calls if str(call.status).lower() == "complete"]
        sql = "\n".join(str((call.tool_params or {}).get("sql") or "") for call in successful)
        targeted = any(
            _reads_table(statement, "accounts") and re.search(r"\bwhere\b", statement, re.I)
            for call in successful
            for statement in sqlparse.split(str((call.tool_params or {}).get("sql") or ""))
        )
        failures = [message for failed, message in (
            (not successful, "existing account model was not queried"),
            (not (re.search(r"\bselect\b", sql, re.I) and _reads_table(sql, "accounts")), "account truth was not selected from the model"),
            (not targeted, "account model was not queried with a bounded filter"),
            (len(successful) > 2, f"SQLite reads were not bounded: {len(successful)}"),
        ) if failed]
        self._record_check(
            run_id, "verify_modeled_truth_read", failures,
            "Read the existing account model before one terminal reply.",
        )
        self._record_sourced_answer(
            run_id, agent_id=agent_id, after=inbound.timestamp, task_name="verify_current_truth_answer",
            source_urls=(), required_terms=("Aster Labs", "legal", "Maya Chen", "SOC 2 packet"), min_sources=0,
        )


@register_scenario
class SqliteDomainModelRefreshesAndEvolvesScenario(SqliteDomainModelScenario):
    slug = SQLITE_DOMAIN_MODEL_REFRESHES_AND_EVOLVES
    description = "Newer source evidence should refresh an entity and add a keyed, sourced child relation."
    tasks = [ScenarioTask(name="inject_prompt", assertion_type="agent_processing"), ScenarioTask(name="verify_source_to_model_refresh", assertion_type="tool_call"), ScenarioTask(name="verify_persisted_domain_evolution", assertion_type="manual"), ScenarioTask(name="verify_refreshed_answer", assertion_type="manual")]
    prompt = f"Review this latest CRM snapshot and give me the current Aster Labs picture, including anything still open: {DOMAIN_REFRESH_URL}"

    def run(self, run_id: str, agent_id: str) -> None:
        self._prepare_domain_agent(
            agent_id, ("security_review", "Maya Chen", "wait for the security questionnaire"),
            "2026-07-18T13:00:00Z", duplicate_name=True,
        )
        self._enable_tools(agent_id, ("http_request",))
        inbound = self._inject_and_wait(
            run_id, agent_id, self.prompt, _domain_refresh_mock(),
            allowed_tool_names={"http_request", "sqlite_batch", "send_chat_message"}, max_relevant_tool_calls=8,
        )
        calls = _tool_calls_for_run(run_id, after=inbound.timestamp)
        sqlite_attempts = [call for call in calls if call.tool_name == "sqlite_batch"]
        sqlite_calls = [
            call for call in sqlite_attempts if str(call.status).lower() == "complete"
        ]
        sql_values = [str((call.tool_params or {}).get("sql") or "") for call in sqlite_calls]
        source_mutations = set(source_derived_model_mutation_tables(sql_values))
        state_failures, child_table = _inspect_domain_refresh_state(agent_id)
        if PersistentAgent.objects.values_list("charter", flat=True).get(id=agent_id) != self.domain_charter:
            state_failures.append("ordinary CRM review changed the agent charter")
        expected_tables = {"accounts"}
        if child_table:
            expected_tables.add(child_table.casefold())
        failures = _first_shot_source_phase_failures(calls)
        failures.extend(_sqlite_attempt_failures(sqlite_attempts))
        failures.extend(_source_write_effect_failures(sqlite_attempts, expected_tables))
        if child_table:
            failures.extend(_source_relationship_read_failures(sql_values, "accounts", child_table))
        failures.extend(_orphan_completion_failures(run_id, inbound.timestamp))
        failures.extend(message for failed, message in (
            (
                not expected_tables.issubset(source_mutations),
                f"expected source-derived writes to {sorted(expected_tables)}, found {sorted(source_mutations)}",
            ),
        ) if failed)
        self._record_check(
            run_id, "verify_source_to_model_refresh", failures,
            "Fetched the newer snapshot once, ingested it, and then replied.",
        )
        self._record_check(
            run_id, "verify_persisted_domain_evolution", state_failures,
            "Refreshed one stable account and modeled keyed, sourced child rows.",
        )
        self._record_sourced_answer(
            run_id, agent_id=agent_id, after=inbound.timestamp, task_name="verify_refreshed_answer",
            source_urls=(), required_terms=("Aster Labs", "contracting", "redlines", "Noah Reed"), min_sources=0,
        )


@register_scenario
class SqliteDedupeRequeryScenario(SqliteToolResultScenario):
    slug = SQLITE_DEDUPE_REQUERY
    description = "Duplicate source synthesis should use aggregate SQLite/CTE queries, not repeated blob re-fetches."
    tasks = [ScenarioTask(name="inject_prompt", assertion_type="agent_processing"), ScenarioTask(name="verify_dedupe_sqlite_usage", assertion_type="tool_call"), ScenarioTask(name="verify_sourced_answer", assertion_type="manual")]
    builtin_tools = ("http_request", "mcp_brightdata_scrape_as_markdown")
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
    prompt = "Fetch these vehicle inventory JSON feeds, compare 2023+ Tesla Model Y records within 50 miles, and send one concise initial report with the best batch, the cheapest qualifying option, and listing links for recommended vehicles. Do not browse or create files.\n\n" + "\n".join(f"- {url}" for url in INVENTORY_URLS)
    mock_kind = "inventory"
    verify_task_name = "verify_item_link_sqlite_usage"
    answer_source_urls = LISTING_URLS
    required_terms = ("Model Y", "Harrisburg", "$27,455")
    min_sources = 2
    max_single_result_filters = 2
    sourced_answer_task_name = "verify_listing_links_in_report"


@register_scenario
class SqliteBoundedPortfolioReportScenario(SqliteToolResultScenario):
    slug = SQLITE_BOUNDED_PORTFOLIO_REPORT
    description = "A bounded multi-entity research request should reconcile full source coverage and deliver a useful owner report."
    cost_class = "medium"
    tags = (*SqliteToolResultScenario.tags, "coverage", "message_quality")
    tasks = [ScenarioTask(name="inject_prompt", assertion_type="agent_processing"), ScenarioTask(name="verify_result_access", assertion_type="tool_call"), ScenarioTask(name="verify_complete_terminal_report", assertion_type="manual"), ScenarioTask(name="verify_report_hierarchy", assertion_type="manual")]
    prompt = f"Tell me about the founders of Arbor Seed Ventures' current portfolio companies, with a source link for each profile: {PORTFOLIO_INDEX_URL}"
    result_access_fetch_tools = ("http_request", "mcp_brightdata_scrape_as_markdown")
    require_result_access_sqlite = False

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        self._enable_tools(agent_id, ("http_request", "read_file"))
        self._enable_tools(
            agent_id,
            ("mcp_brightdata_search_engine", "mcp_brightdata_scrape_as_markdown"),
            synthetic=True,
        )
        inbound = self._inject_and_wait(
            run_id, agent_id, self.prompt, _portfolio_mock(),
            allowed_tool_names={"http_request", "mcp_brightdata_search_engine", "mcp_brightdata_scrape_as_markdown", "read_file", "search_tools", "sqlite_batch", "update_plan", *MESSAGE_TOOL_NAMES},
            max_relevant_tool_calls=22,
        )
        self._record_result_access(run_id, after=inbound.timestamp, task_name="verify_result_access", source_urls=PORTFOLIO_FETCH_URLS, reject_duplicate_fetches=True)
        final_body = self._record_complete_terminal_report(run_id, after=inbound.timestamp)
        self._record_report_hierarchy(run_id, final_body)

    def _record_complete_terminal_report(self, run_id: str, *, after) -> str:
        task_name = "verify_complete_terminal_report"
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        calls = _tool_calls_for_run(run_id, after=after)
        send_calls = [call for call in calls if call.tool_name == "send_chat_message"]
        terminal_calls = [
            (index, call)
            for index, call in enumerate(calls)
            if call.tool_name == "send_chat_message"
            and (call.tool_params or {}).get("will_continue_work") is False
            and str(getattr(call, "status", "complete")).lower() == "complete"
        ]
        if len(terminal_calls) != 1:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name=task_name,
                observed_summary=f"Expected one honest terminal report; found {len(terminal_calls)}.",
                artifacts={"step": terminal_calls[0][1].step} if terminal_calls else {},
            )
            return ""

        final_position, final_call = terminal_calls[0]
        body = str(resolved_tool_param(final_call, "body") or "")
        missing_associations = self._missing_portfolio_associations(body)

        detail_positions = {
            str(resolved_tool_param(call, "url") or "").rstrip("/"): index
            for index, call in enumerate(calls)
            if call.tool_name in self.result_access_fetch_tools
            and str(getattr(call, "status", "complete")).lower() == "complete"
        }
        fetched_before_final = all(detail_positions.get(url, final_position + 1) < final_position for url in PORTFOLIO_FETCH_URLS)
        failures = _tool_attempt_failures(send_calls, "Final send")
        failures.extend(message for failed, message in (
            (bool(missing_associations), f"final report missing/mismatched={missing_associations}"),
            (not fetched_before_final, "terminal report was sent before all available item evidence was fetched"),
        ) if failed)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED if failures else EvalRunTask.Status.PASSED,
            task_name=task_name,
            observed_summary=(
                "; ".join(failures)
                if failures
                else "Terminal report covered all 8 companies, every discoverable founder, the sourced disclosure blocker, and item-level sources."
            ),
            artifacts={"step": final_call.step, "body_preview": body[:1600]},
        )
        return body

    @staticmethod
    def _portfolio_entity_blocks(body: str) -> list[str]:
        lines = body.splitlines()
        blocks = [line for line in lines if line.strip()]
        blocks.extend(block for block in re.split(r"\n\s*\n", body) if block.strip())

        heading_starts = [
            index
            for index, line in enumerate(lines)
            if re.match(r"^\s{0,3}#{1,6}\s+", line)
        ]
        list_starts = []
        for index, line in enumerate(lines):
            match = re.match(r"^(?P<indent>\s*)(?:[-*+]|\d+[.)])\s+", line)
            if match:
                list_starts.append((index, len(match.group("indent"))))

        for position, start in enumerate(heading_starts):
            end = heading_starts[position + 1] if position + 1 < len(heading_starts) else len(lines)
            blocks.append("\n".join(lines[start:end]))
        for position, (start, indent) in enumerate(list_starts):
            end = len(lines)
            for next_start, next_indent in list_starts[position + 1:]:
                if next_indent <= indent:
                    end = next_start
                    break
            blocks.append("\n".join(lines[start:end]))

        distinct_blocks = dict.fromkeys(block.strip() for block in blocks if block.strip())
        return [
            block
            for block in distinct_blocks
            if sum(company.casefold() in block.casefold() for _slug, company, *_rest in PORTFOLIO_COMPANIES) == 1
        ]

    @classmethod
    def _missing_portfolio_associations(cls, body: str) -> list[str]:
        folded = body.casefold()
        blocks = cls._portfolio_entity_blocks(body)
        missing = []
        for (_slug, company, founder, background_term, _background), url in zip(
            PORTFOLIO_COMPANIES,
            PORTFOLIO_SOURCE_URLS,
        ):
            expected_fields = (
                ("company", company),
                ("founder", founder),
                ("background", background_term),
            )
            has_fields = any(
                all(value.casefold() in block.casefold() for _label, value in expected_fields)
                for block in blocks
            )
            has_source = any(
                company.casefold() in block.casefold() and url.casefold() in block.casefold()
                for block in blocks
            )
            if has_fields and has_source:
                continue
            absent = [label for label, value in expected_fields if value.casefold() not in folded]
            if not has_fields and not absent:
                absent.append("field association")
            if not has_source:
                absent.append("source")
            missing.append(f"{company}:{','.join(absent) if absent else 'association'}")
        return missing

    def _record_report_hierarchy(self, run_id: str, body: str) -> None:
        task_name = "verify_report_hierarchy"
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        passed = self._has_complete_structured_report(body)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if passed else EvalRunTask.Status.FAILED,
            task_name=task_name,
            expected_summary=(
                "Report should state meaningful coverage and compare all eight peers in one structured report."
            ),
            observed_summary=(
                "One complete structured comparison covers the full bounded set with a meaningful coverage summary."
                if passed
                else "Missing meaningful coverage or one complete structured comparison."
            ),
        )

    @classmethod
    def _has_complete_structured_report(cls, body: str) -> bool:
        return not cls._missing_portfolio_associations(body) and cls._has_complete_comparison_table(body)

    @staticmethod
    def _has_complete_comparison_table(body: str) -> bool:
        lines = body.splitlines()
        for separator_index, line in enumerate(lines):
            if not re.fullmatch(r"\s*\|(?:\s*:?-{3,}:?\s*\|){2,}\s*", line):
                continue
            if separator_index == 0 or not lines[separator_index - 1].strip().startswith("|"):
                continue
            data_rows = []
            for row in lines[separator_index + 1:]:
                stripped = row.strip()
                if not stripped.startswith("|") or not stripped.endswith("|"):
                    break
                data_rows.append(stripped)
            entity_rows = tuple(
                tuple(
                    (index, row)
                    for index, row in enumerate(data_rows)
                    if company.casefold() in row.casefold()
                )
                for _slug, company, _founder, _background_term, _background in PORTFOLIO_COMPANIES
            )
            complete_rows = all(
                len(rows) == 1
                and all(term.casefold() in rows[0][1].casefold() for term in (company, founder, background_term))
                for rows, (_slug, company, founder, background_term, _background) in zip(
                    entity_rows,
                    PORTFOLIO_COMPANIES,
                )
            )
            distinct_rows = len({rows[0][0] for rows in entity_rows if rows}) == len(PORTFOLIO_COMPANIES)
            summary = "\n".join((
                *lines[:separator_index - 1],
                *lines[separator_index + 1 + len(data_rows):],
            ))
            if complete_rows and distinct_rows and SqliteBoundedPortfolioReportScenario._has_coverage_summary(summary):
                return True
        return False

    @staticmethod
    def _has_coverage_summary(body: str) -> bool:
        seven = r"(?:7|seven)"
        eight = r"(?:8|eight)"
        company = r"compan(?:y|ies)"
        state = r"(?:resolved|accounted\s+for|covered)"
        explicit_total = (
            re.search(
                rf"\b{state}\b[^\n]{{0,40}}\b(?:8\s*/\s*8|all\s+{eight})\b[^\n]{{0,30}}\b{company}\b",
                body,
                re.I,
            )
            or re.search(
                rf"\b(?:8\s*/\s*8|all\s+{eight})\b[^\n]{{0,30}}\b{company}\b[^\n]{{0,40}}\b{state}\b",
                body,
                re.I,
            )
        )
        founder_coverage = any(re.search(pattern, body, re.I) for pattern in (
            rf"\b{seven}\s+(?:named\s+)?founders?\s+(?:were\s+)?(?:identified|known|named)\b",
            rf"\b{seven}\s+of\s+(?:the\s+)?{eight}\s+{company}\b[^\n]{{0,50}}"
            r"\b(?:named\s+founders?|founders?\s+(?:identified|known|named))\b",
            rf"\b{seven}\s*(?:of\s+(?:the\s+)?{eight}|/\s*8)\s+(?:named\s+)?founders?\b"
            r"[^\n]{0,35}\b(?:identified|known|named|found|confirmed|sourced)\b",
            rf"\b(?:identified|named|found|confirmed|sourced)\b[^\n]{{0,25}}\bfounders?\b"
            rf"[^\n]{{0,25}}\b(?:for|at)\s+{seven}\s+of\s+(?:the\s+)?{eight}\s+(?:portfolio\s+)?{company}\b",
            rf"\bfounders?\b[^\n]{{0,20}}\b(?:identified|named|known|confirmed|sourced)\b"
            rf"[^\n]{{0,25}}\b(?:for|at)\s+{seven}\s+of\s+(?:the\s+)?{eight}\s+(?:portfolio\s+)?{company}\b",
        ))
        blocker = re.search(
            r"\b(?:1|one)\b[^\n]{0,80}\b(?:nondisclos|undisclos|not\s+publicly\s+disclosed|unavailable|unresolved|block)",
            body,
            re.I,
        )
        nondisclosure = re.search(r"\b(?:nondisclos|undisclos|not\s+publicly\s+disclosed)\b", body, re.I)
        return bool(explicit_total or (founder_coverage and (blocker or nondisclosure)))
