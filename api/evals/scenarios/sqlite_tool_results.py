import json
import re
from datetime import timedelta
from decimal import Decimal
from typing import Iterable

import sqlparse
from django.utils import timezone

from api.agent.core.tool_results import build_short_result_id_map
from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_SERVER
from api.agent.tools.sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from api.agent.tools.sqlite_query_quality import (
    CREATE_TABLE_AS_RE,
    _created_table_name,
    _inserted_table_name,
    _reads_table,
    _structural_sql,
    source_derived_model_mutation_tables,
    source_derived_model_reconciled_tables,
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
    CommsChannel,
    EvalRunTask,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentCompletion,
    PersistentAgentConversation,
    PersistentAgentEnabledTool,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
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
SQLITE_SCHEMA_GROUNDED_EXISTING_TABLE = "sqlite_schema_grounded_existing_table"
SQLITE_SCHEMA_GROUNDED_EXISTING_TABLE_WRITE = "sqlite_schema_grounded_existing_table_write"
SQLITE_SOURCE_ARRAY_FIRST_WRITE = "sqlite_source_array_first_write"
SQLITE_SIBLING_RESULT_SET_FIRST_WRITE = "sqlite_sibling_result_set_first_write"
SQLITE_UNSTRUCTURED_BINDINGS_FIRST_WRITE = "sqlite_unstructured_bindings_first_write"
SQLITE_INCREMENTAL_DOMAIN_MODEL = "sqlite_incremental_domain_model"
SQLITE_PROSPECT_PIPELINE_COMPLETES = "sqlite_prospect_pipeline_completes"
SQLITE_ENRICHMENT_REFRESH_UNDER_PRESSURE = "sqlite_enrichment_refresh_under_pressure"
SQLITE_SOURCE_CARDINALITY_AND_IDENTITY = "sqlite_source_cardinality_and_identity"
SQLITE_FRESH_PEER_FACT_OVER_EMPTY_MODEL = "sqlite_fresh_peer_fact_over_empty_model"
SQLITE_STRUCTURED_PEER_EVENT_PERSISTENCE = "sqlite_structured_peer_event_persistence"
SQLITE_PEER_OUTCOME_RECONCILES_CANONICAL_MODEL = "sqlite_peer_outcome_reconciles_canonical_model"
SQLITE_TOOL_RESULT_SCENARIO_SLUGS = [
    SQLITE_MULTI_RESULT_WEB_SYNTHESIS,
    SQLITE_INTERMEDIATE_WORKING_TABLE,
    SQLITE_DEDUPE_REQUERY,
    SQLITE_ITEM_LINK_REPORT,
    SQLITE_NATURAL_RESULT_ACCESS,
    SQLITE_BOUNDED_PORTFOLIO_REPORT,
    SQLITE_DOMAIN_TRUTH_OVER_STALE_HISTORY,
    SQLITE_DOMAIN_MODEL_REFRESHES_AND_EVOLVES,
    SQLITE_SCHEMA_GROUNDED_EXISTING_TABLE,
    SQLITE_SCHEMA_GROUNDED_EXISTING_TABLE_WRITE,
    SQLITE_SOURCE_ARRAY_FIRST_WRITE,
    SQLITE_SIBLING_RESULT_SET_FIRST_WRITE,
    SQLITE_UNSTRUCTURED_BINDINGS_FIRST_WRITE,
    SQLITE_INCREMENTAL_DOMAIN_MODEL,
    SQLITE_PROSPECT_PIPELINE_COMPLETES,
    SQLITE_ENRICHMENT_REFRESH_UNDER_PRESSURE,
    SQLITE_SOURCE_CARDINALITY_AND_IDENTITY,
    SQLITE_FRESH_PEER_FACT_OVER_EMPTY_MODEL,
    SQLITE_STRUCTURED_PEER_EVENT_PERSISTENCE,
    SQLITE_PEER_OUTCOME_RECONCILES_CANONICAL_MODEL,
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
SIBLING_ACCOUNT_BATCHES = (
    (
        "https://exports.example.test/accounts/northeast",
        (
            ("acct-101", "Harbor O'Brien Supply", "procurement", 2),
            ("acct-102", "Aster Freight", "operations", 1),
        ),
    ),
    (
        "https://exports.example.test/accounts/central",
        (
            ("acct-201", "Cinder Works", "procurement", 3),
            ("acct-202", "Bramble Health", "security", 2),
        ),
    ),
    (
        "https://exports.example.test/accounts/west",
        (
            ("acct-301", "Lattice Foods", "operations", 2),
            ("acct-302", "Quarry Labs", "security", 1),
        ),
    ),
)
HISTORICAL_ACCOUNT_BATCH = (
    "https://exports.example.test/accounts/archived",
    (("acct-old", "Legacy Imports", "legacy", 40),),
)
UNSTRUCTURED_INTERVIEW_NOTES = (
    (
        "https://research.example.test/interviews/northstar-growth",
        "# Customer interview\nInterview ID: int-101\nCompany: Northstar Growth\n"
        "Primary pain point: manual research handoffs\nDecision stage: evaluating\nPriority: high\n",
    ),
    (
        "https://research.example.test/interviews/obrien-advisory",
        "# Customer interview\nInterview ID: int-102\nCompany: O'Brien Advisory\n"
        "Primary pain point: manual research handoffs\nDecision stage: piloting\nPriority: high\n",
    ),
    (
        "https://research.example.test/interviews/harbor-creative",
        "# Customer interview\nInterview ID: int-103\nCompany: Harbor Creative\n"
        "Primary pain point: manual research handoffs\nDecision stage: exploring\nPriority: medium\n",
    ),
)
HANDOFF_LEDGER_TABLE = "z_handoff_ledger"
HANDOFF_ROWS = (
    ("handoff-01", "agent-red", "open"),
    ("handoff-02", "agent-red", "blocked"),
    ("handoff-03", "agent-blue", "open"),
    ("handoff-04", "agent-green", "resolved"),
)
RELEASE_CALENDAR_URL = "https://ops.example.test/releases/calendar.json"
RELEASE_CALENDAR_OBSERVED_AT = "2026-07-22T14:15:00Z"
RELEASE_EVENTS = (
    ("rel-checkout-41", "Checkout API", "2026-07-23T15:30:17Z", "Priya Shah", "approved"),
    ("rel-search-18", "Search index", "2026-07-23T17:00:43Z", "Mateo Ruiz", "blocked"),
    ("rel-billing-09", "Billing worker", "2026-07-24T13:05:29Z", "Hana Lee", "approved"),
    ("rel-mobile-27", "Mobile client", "2026-07-24T19:45:11Z", "Avery Cole", "canceled"),
)

INITIATIVE_FEED_URL = "https://ops.example.test/initiatives.json"
OWNERSHIP_FEED_URL = "https://ops.example.test/ownership.json"
RISK_FEED_URL = "https://ops.example.test/risks.json"
OPERATING_FEED_URLS = (INITIATIVE_FEED_URL, OWNERSHIP_FEED_URL, RISK_FEED_URL)

PROSPECT_PROFILE_URLS = (
    "https://profiles.example.test/people/maya-chen",
    "https://profiles.example.test/people/leo-martin",
    "https://profiles.example.test/people/priya-nwosu",
    "https://profiles.example.test/people/evan-cho",
)
PROSPECT_COMPANY_FEED_URL = "https://crm.example.test/staffing/companies.json"
PROSPECT_PEOPLE_FEED_URL = "https://crm.example.test/staffing/people.json"
PROSPECT_FEED_URLS = (PROSPECT_COMPANY_FEED_URL, PROSPECT_PEOPLE_FEED_URL)
ENRICHMENT_FEED_URLS = (
    "https://directory.example.test/enrichment/east.json",
    "https://directory.example.test/enrichment/central.json",
    "https://directory.example.test/enrichment/west.json",
)
ENRICHED_CONTACTS = (
    ("contact-101", "Mina Patel", "Aster Forge", "Priya Shah", "mina@aster.example.test"),
    ("contact-102", "Jonah Reed", "Bramble Health", "Priya Shah", None),
    ("contact-103", "Leo Martin", "Cinderline", "Priya Shah", "leo@cinderline.example.test"),
    ("contact-201", "Naomi Brooks", "Lattice Harbor", "Mateo Ruiz", "naomi@lattice.example.test"),
    ("contact-202", "Evan Cho", "Quarry Labs", "Mateo Ruiz", None),
    ("contact-203", "Sofia Alvarez", "Ternary Field", "Mateo Ruiz", "sofia@ternary.example.test"),
    ("contact-301", "Maya Chen", "Umbra Works", "Hana Lee", "maya@umbra.example.test"),
    ("contact-302", "Rowan Kim", "Driftwood Robotics", "Hana Lee", "rowan@driftwood.example.test"),
    ("contact-303", "Avery Cole", "Northstar Systems", "Hana Lee", None),
)
CLAIM_INTAKE_URL = "https://api.example.test/intake/claim-batch.json"
CLAIM_INTAKE_CONTACTS = (
    (
        "contact-17",
        "Mina Patel",
        "Aster Forge",
        "mina.o'brien@aster.example.test",
        "https://profiles.example.test/people/mina-patel",
    ),
    (
        "contact-29",
        "Jonah Reed",
        "Bramble Health",
        "jonah@bramble.example.test",
        "https://profiles.example.test/people/jonah-reed",
    ),
)
INITIATIVES = (
    ("init-checkout", "Checkout Recovery", "active", "revenue"),
    ("init-search", "Search Relevance", "active", "discovery"),
    ("init-migration", "Lumen Migration", "active", "platform"),
    ("init-mobile", "Mobile Refresh", "paused", "engagement"),
    ("init-billing", "Billing Reliability", "active", "revenue"),
    ("init-onboarding", "Onboarding Simplification", "active", "activation"),
)
OWNERSHIP_ASSIGNMENTS = (
    ("own-checkout", "init-checkout", "Priya Shah", "primary"),
    ("own-search", "init-search", "Mateo Ruiz", "primary"),
    ("own-mobile", "init-mobile", "Avery Cole", "primary"),
    ("own-billing", "init-billing", "Hana Lee", "primary"),
    ("own-onboarding", "init-onboarding", "Rowan Kim", "advisor"),
)
INITIATIVE_RISKS = (
    ("risk-checkout", "init-checkout", "critical", "payment data consistency"),
    ("risk-search", "init-search", "medium", "index freshness"),
    ("risk-migration", "init-migration", "high", "cutover dependency"),
    ("risk-billing", "init-billing", "low", "retry backlog"),
    ("risk-onboarding", "init-onboarding", "high", "incomplete analytics"),
)


_MUTATION_TARGET_RE = re.compile(
    r'\b(?:insert\s+(?:or\s+\w+\s+)?into|replace\s+into|update|delete\s+from)\s+["`\[]?'
    r'(?P<table>[a-z_]\w*)',
    re.I,
)


def _mutation_target_table(statement: str) -> str | None:
    statement_without_comments = sqlparse.format(statement, strip_comments=True)
    match = _MUTATION_TARGET_RE.search(statement_without_comments)
    return match.group("table").casefold() if match else None


def _result_payload(call) -> dict | None:
    result = getattr(call, "result", None)
    try:
        payload = result if isinstance(result, dict) else json.loads(str(result or ""))
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _seed_account_export(agent_id, completion, source_url, accounts, observed_at, description):
    step = PersistentAgentStep.objects.create(
        agent_id=agent_id,
        completion=completion,
        description=description,
    )
    PersistentAgentToolCall.objects.create(
        step=step,
        tool_name="http_request",
        tool_params={"url": source_url},
        result=json.dumps({
            "status": "ok",
            "content": {"accounts": [
                {
                    "account_id": account_id,
                    "company": company,
                    "buyer_segment": segment,
                    "verified_contacts": contacts,
                    "source_url": source_url,
                    "observed_at": observed_at,
                }
                for account_id, company, segment, contacts in accounts
            ]},
        }),
        status="complete",
    )
    return step


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
    calls = list(calls)
    failures = _tool_attempt_failures(calls, "SQLite", reject_auto_correction=True)
    for index, call in enumerate(calls, start=1):
        payload = _result_payload(call)
        raw_result = getattr(call, "result", None)
        if (isinstance(payload, dict) and payload.get("advisories")) or "SQLITE QUERY ADVICE" in str(raw_result or ""):
            failures.append(f"SQLite attempt {index} returned a query advisory")
    return failures


def _uses_bound_source_values(call, statement: str, expected_values: set[str]) -> bool:
    bindings = (getattr(call, "tool_params", None) or {}).get("bindings") or {}
    used_bound_values = {
        str(value)
        for key, value in bindings.items()
        if (
            isinstance(value, (str, int, float))
            and re.search(rf":{re.escape(str(key))}\b", statement)
        )
    }
    statement_without_comments = sqlparse.format(statement, strip_comments=True)
    sql_literals = {
        match.group(1).replace("''", "'")
        for match in re.finditer(r"'((?:''|[^'])*)'", statement_without_comments)
    }
    sql_literals.update(
        match.group(1).replace('""', '"')
        for match in re.finditer(r'"((?:""|[^"])*)"', statement_without_comments)
    )
    return expected_values.issubset(used_bound_values) and expected_values.isdisjoint(sql_literals)


def _derives_structured_message_fields(
    statement: str,
    expected_fields: set[str],
) -> bool:
    statement_without_comments = sqlparse.format(statement, strip_comments=True)
    lowered = statement_without_comments.casefold()
    return (
        _reads_table(statement, "__messages")
        and "structured_payload_json" in lowered
        and all(f"$.{field.casefold()}" in lowered for field in expected_fields)
        and _structured_outcome_assignments_use_extracted_fields(lowered)
    )


def _bound_json_payload_placeholder(
    call,
    statement: str,
    expected_payload: dict[str, str],
) -> str | None:
    bindings = (getattr(call, "tool_params", None) or {}).get("bindings") or {}
    statement_without_comments = sqlparse.format(statement, strip_comments=True)
    lowered = statement_without_comments.casefold()
    expected_values = set(expected_payload.values())
    sql_literals = {
        match.group(1).replace("''", "'")
        for match in re.finditer(r"'((?:''|[^'])*)'", statement_without_comments)
    }
    sql_literals.update(
        match.group(1).replace('""', '"')
        for match in re.finditer(r'"((?:""|[^"])*)"', statement_without_comments)
    )
    if expected_values.intersection(sql_literals):
        return None
    for key, raw_payload in bindings.items():
        placeholder = f":{str(key).casefold()}"
        if placeholder not in lowered:
            continue
        if isinstance(raw_payload, str):
            try:
                payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                continue
        else:
            payload = raw_payload
        if not isinstance(payload, dict):
            continue
        if any(str(payload.get(field)) != value for field, value in expected_payload.items()):
            continue
        if any(
            other_key != key
            and isinstance(other_value, (str, int, float))
            and str(other_value) in expected_values
            for other_key, other_value in bindings.items()
        ):
            continue
        if not all(
            re.search(
                rf"json_extract\s*\(\s*{re.escape(placeholder)}\s*,\s*['\"]\$\."
                rf"{re.escape(field.casefold())}['\"]\s*\)",
                lowered,
            )
            for field in expected_payload
        ):
            continue
        return placeholder
    return None


def _insert_values_derive_bound_payload_fields(
    statement: str,
    *,
    table_name: str,
    placeholder: str,
    expected_fields: set[str],
) -> bool:
    parsed = sqlparse.parse(statement)
    if len(parsed) != 1:
        return False
    tokens = [token for token in parsed[0].tokens if not token.is_whitespace]
    into_index = next(
        (
            index
            for index, token in enumerate(tokens)
            if str(getattr(token, "normalized", "")).casefold() == "into"
        ),
        -1,
    )
    target = tokens[into_index + 1] if 0 <= into_index < len(tokens) - 1 else None
    target_name = str(getattr(target, "get_name", lambda: "")() or "").casefold()
    if target_name != table_name.casefold():
        return False
    values = next(
        (
            token
            for token in tokens
            if isinstance(token, sqlparse.sql.Values)
        ),
        None,
    )
    if values is None:
        return False

    if isinstance(target, sqlparse.sql.Function):
        target_parenthesis = next(
            (
                token
                for token in target.tokens
                if isinstance(token, sqlparse.sql.Parenthesis)
            ),
            None,
        )
    else:
        target_parenthesis = (
            tokens[into_index + 2]
            if into_index < len(tokens) - 2
            and isinstance(tokens[into_index + 2], sqlparse.sql.Parenthesis)
            else None
        )
    values_parenthesis = next(
        (token for token in values.tokens if isinstance(token, sqlparse.sql.Parenthesis)),
        None,
    )
    if target_parenthesis is None or values_parenthesis is None:
        return False

    def _items(parenthesis):
        identifier_list = next(
            (
                token
                for token in parenthesis.tokens
                if isinstance(token, sqlparse.sql.IdentifierList)
            ),
            None,
        )
        return (
            [str(item).strip() for item in identifier_list.get_identifiers()]
            if identifier_list is not None
            else []
        )

    columns = [item.strip('"`[] ').casefold() for item in _items(target_parenthesis)]
    expressions = _items(values_parenthesis)
    if len(columns) != len(expressions):
        return False
    assignments = dict(zip(columns, expressions))
    return all(
        field.casefold() in assignments
        and re.fullmatch(
            rf"json_extract\s*\(\s*{re.escape(placeholder)}\s*,\s*['\"]\$\."
            rf"{re.escape(field.casefold())}['\"]\s*\)",
            assignments[field.casefold()].casefold(),
        )
        for field in expected_fields
    )


def _derives_bound_structured_message_fields(
    call,
    statement: str,
    expected_payload: dict[str, str],
) -> bool:
    return bool(
        _bound_json_payload_placeholder(call, statement, expected_payload)
        and _structured_outcome_assignments_use_extracted_fields(
            sqlparse.format(statement, strip_comments=True).casefold()
        )
    )


def _structured_outcome_assignments_use_extracted_fields(lowered_statement: str) -> bool:
    assignment_sources = {
        "state": "delivery_status",
        "provider_message_id": "provider_message_id",
        "sent_at": "sent_at",
    }
    for column, source_field in assignment_sources.items():
        match = re.search(
            rf"\b{column}\s*=\s*(.+?)(?=,\s*(?:state|provider_message_id|sent_at|"
            rf"source_message_id)\s*=|\bfrom\b|\bwhere\b)",
            lowered_statement,
            flags=re.DOTALL,
        )
        if match is None or not _direct_source_assignment(match.group(1), source_field):
            return False
    where_match = re.search(r"\bwhere\b(.+)", lowered_statement, flags=re.DOTALL)
    return where_match is not None and "recipient" in where_match.group(1)


def _direct_source_assignment(expression: str, source_field: str) -> bool:
    expression, field = expression.strip(), re.escape(source_field)
    if re.fullmatch(rf"(?:[a-z_][a-z0-9_]*\.)?{field}", expression):
        return True
    json_extract_pattern = rf"json_extract\s*\(\s*[:a-z_][a-z0-9_.:]*\s*,\s*['\"]\$.{field}['\"]\s*\)"
    return bool(re.fullmatch(json_extract_pattern, expression))


def _first_shot_source_phase_failures(calls, *, expected_url=DOMAIN_REFRESH_URL) -> list[str]:
    calls = tuple(calls)
    fetches = [call for call in calls if call.tool_name == "http_request"]
    sqlite_calls = [call for call in calls if call.tool_name == "sqlite_batch"]
    sends = [call for call in calls if call.tool_name == "send_chat_message"]
    terminal_sends = [
        call for call in sends
        if resolved_tool_param(call, "will_continue_work") is False
    ]
    core_calls = [
        call for call in calls
        if call.tool_name != "send_chat_message"
        or resolved_tool_param(call, "will_continue_work") is False
    ]
    failures = _tool_attempt_failures(fetches, "Source fetch")
    failures.extend(_tool_attempt_failures(sends, "Source workflow message"))
    extra_model_reads = sqlite_calls[1:]
    extra_read_failures = []
    for call in extra_model_reads:
        sql = str((call.tool_params or {}).get("sql") or "")
        if (
            "__tool_results" in _structural_sql(sql).casefold()
            or source_derived_model_mutation_tables((sql,))
            or not re.search(r"\bselect\b", sql, re.I)
        ):
            extra_read_failures.append(
                "a follow-up SQLite call reread source results or mutated the model instead of applying model-only logic"
            )

    core_names = [call.tool_name for call in core_calls]
    valid_core_order = (
        len(core_names) >= 3
        and core_names[0] == "http_request"
        and core_names[-1] == "send_chat_message"
        and all(name == "sqlite_batch" for name in core_names[1:-1])
    )
    failures.extend(message for failed, message in (
        (
            not valid_core_order,
            f"expected fetch, SQLite model work, then one terminal send; found {[call.tool_name for call in calls]}",
        ),
        (
            len(fetches) != 1
            or str(resolved_tool_param(fetches[0], "url") or "").rstrip("/")
            != expected_url.rstrip("/"),
            "expected one exact CRM snapshot fetch",
        ),
        (
            not 1 <= len(sqlite_calls) <= 2,
            f"expected one import/decision batch and at most one model-logic read, found {len(sqlite_calls)} SQLite calls",
        ),
        (
            len(terminal_sends) != 1,
            f"expected one successful terminal send, found {len(terminal_sends)} terminal send attempt(s)",
        ),
    ) if failed)
    failures.extend(extra_read_failures)

    completion_ids = [getattr(getattr(call, "step", None), "completion_id", None) for call in core_calls]
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


def _modeled_source_urls(agent_id: str, table_names: Iterable[str]) -> set[str]:
    urls = set()
    with agent_sqlite_db(str(agent_id)) as db_path:
        conn = open_guarded_sqlite_connection(db_path)
        try:
            for table_name in table_names:
                escaped_table = table_name.replace('"', '""')
                columns = {
                    str(row[1]).casefold()
                    for row in conn.execute(f'PRAGMA table_info("{escaped_table}")')
                }
                if "source_url" not in columns:
                    continue
                urls.update(
                    str(row[0])
                    for row in conn.execute(
                        f'SELECT DISTINCT source_url FROM "{escaped_table}" WHERE source_url IS NOT NULL'
                    )
                )
        finally:
            clear_guarded_connection(conn)
            conn.close()
    return urls


def _repeated_source_import_tables(sql_values: Iterable[str]) -> tuple[str, ...]:
    """Find model tables populated by multiple source-derived statements."""

    counts: dict[str, int] = {}
    for sql in sql_values:
        for statement in sqlparse.split(str(sql or "")):
            for table in source_derived_model_mutation_tables((statement,)):
                counts[table] = counts.get(table, 0) + 1
    return tuple(sorted(table for table, count in counts.items() if count > 1))


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
            # Source-key filters commonly contain a nested SELECT. The decision
            # fields are in the outer projection, not the final nested one.
            projection = _AGGREGATE_CALL_RE.sub("", select_matches[0].group("projection"))
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


def _claim_intake_mock() -> dict:
    contacts = [
        {
            "contact_id": contact_id,
            "full_name": full_name,
            "company": company,
            "email": email,
            "profile_url": profile_url,
        }
        for contact_id, full_name, company, email, profile_url in CLAIM_INTAKE_CONTACTS
    ]
    return {
        "http_request": {
            "rules": [
                {
                    "url_contains": CLAIM_INTAKE_URL,
                    "result": {
                        "status": "ok",
                        "status_code": 200,
                        "url": CLAIM_INTAKE_URL,
                        "content": {
                            "batch_id": "claim-2026-07-28-a",
                            "contacts": contacts,
                        },
                    },
                },
            ],
            "default": {"status": "error", "message": "Unknown eval URL."},
        },
    }


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


def _release_calendar_mock() -> dict:
    payload = {
        "observed_at": RELEASE_CALENDAR_OBSERVED_AT,
        "source_url": RELEASE_CALENDAR_URL,
        "events": [
            {
                "release_id": release_id,
                "service": service,
                "starts_at": starts_at,
                "owner": owner,
                "status": status,
                "source_url": RELEASE_CALENDAR_URL,
                "observed_at": RELEASE_CALENDAR_OBSERVED_AT,
            }
            for release_id, service, starts_at, owner, status in RELEASE_EVENTS
        ],
    }
    return {
        "http_request": {
            "rules": [{
                "url_contains": RELEASE_CALENDAR_URL,
                "result": {
                    "status": "ok",
                    "status_code": 200,
                    "url": RELEASE_CALENDAR_URL,
                    "content": payload,
                },
            }],
            "default": {"status": "error", "message": "Unknown eval URL."},
        }
    }


def _operating_feeds_mock() -> dict:
    payloads = {
        INITIATIVE_FEED_URL: {
            "source_url": INITIATIVE_FEED_URL,
            "initiatives": [
                {
                    "initiative_id": initiative_id,
                    "name": name,
                    "status": status,
                    "program": program,
                    "source_url": INITIATIVE_FEED_URL,
                }
                for initiative_id, name, status, program in INITIATIVES
            ],
        },
        OWNERSHIP_FEED_URL: {
            "source_url": OWNERSHIP_FEED_URL,
            "assignments": [
                {
                    "assignment_id": assignment_id,
                    "initiative_id": initiative_id,
                    "person": person,
                    "role": role,
                    "source_url": OWNERSHIP_FEED_URL,
                }
                for assignment_id, initiative_id, person, role in OWNERSHIP_ASSIGNMENTS
            ],
        },
        RISK_FEED_URL: {
            "source_url": RISK_FEED_URL,
            "risks": [
                {
                    "risk_id": risk_id,
                    "initiative_id": initiative_id,
                    "risk_level": risk_level,
                    "reason": reason,
                    "source_url": RISK_FEED_URL,
                }
                for risk_id, initiative_id, risk_level, reason in INITIATIVE_RISKS
            ],
        },
    }
    return {
        "http_request": {
            "rules": [
                {
                    "url_contains": url,
                    "result": {
                        "status": "ok",
                        "status_code": 200,
                        "url": url,
                        "content": payload,
                    },
                }
                for url, payload in payloads.items()
            ],
            "default": {"status": "error", "message": "Unknown eval URL."},
        }
    }


def _prospect_pipeline_mock() -> dict:
    organizations = [
        {
            "id": "org-care-01",
            "name": "Harbor Clinical Staffing",
            "estimated_num_employees": 42,
            "website_url": "https://harbor-clinical.example.test",
        },
        {
            "id": "org-north-02",
            "name": "Northline Allied Health",
            "estimated_num_employees": 67,
            "website_url": "https://northline-allied.example.test",
        },
        {
            "id": "org-lumen-03",
            "name": "Lumen Nurse Partners",
            "estimated_num_employees": 28,
            "website_url": "https://lumen-nurse.example.test",
        },
        {
            "id": "org-cascade-04",
            "name": "Cascade Medical Search",
            "estimated_num_employees": 81,
            "website_url": "https://cascade-medical.example.test",
        },
    ]
    people = [
        {
            "id": "person-maya-01",
            "name": "Maya Chen",
            "title": "Founder and CEO",
            "organization_id": "org-care-01",
            "organization_name": "Harbor Clinical Staffing",
            "linkedin_url": PROSPECT_PROFILE_URLS[0],
        },
        {
            "id": "person-leo-02",
            "name": "Leo Martin",
            "title": "Managing Partner",
            "organization_id": "org-north-02",
            "organization_name": "Northline Allied Health",
            "linkedin_url": PROSPECT_PROFILE_URLS[1],
        },
        {
            "id": "person-priya-03",
            "name": "Priya Nwosu",
            "title": "Head of Recruiting",
            "organization_id": "org-lumen-03",
            "organization_name": "Lumen Nurse Partners",
            "linkedin_url": PROSPECT_PROFILE_URLS[2],
        },
        {
            "id": "person-evan-04",
            "name": "Evan Cho",
            "title": "Owner",
            "organization_id": "org-cascade-04",
            "organization_name": "Cascade Medical Search",
            "linkedin_url": PROSPECT_PROFILE_URLS[3],
        },
    ]
    return {
        "http_request": {
            "rules": [
                {
                    "url_contains": PROSPECT_COMPANY_FEED_URL,
                    "result": {
                        "status": "ok",
                        "status_code": 200,
                        "url": PROSPECT_COMPANY_FEED_URL,
                        "content": {
                            "source_url": PROSPECT_COMPANY_FEED_URL,
                            "organizations": organizations,
                        },
                    },
                },
                {
                    "url_contains": PROSPECT_PEOPLE_FEED_URL,
                    "result": {
                        "status": "ok",
                        "status_code": 200,
                        "url": PROSPECT_PEOPLE_FEED_URL,
                        "content": {
                            "source_url": PROSPECT_PEOPLE_FEED_URL,
                            "people": people,
                        },
                    },
                },
            ],
            "default": {"status": "error", "message": "Unknown eval URL."},
        }
    }


def _enrichment_pressure_mock() -> dict:
    rules = []
    for index, url in enumerate(ENRICHMENT_FEED_URLS):
        batch = ENRICHED_CONTACTS[index * 3:(index + 1) * 3]
        rules.append({
            "url_contains": url,
            "result": {
                "status": "ok",
                "status_code": 200,
                "url": url,
                "content": {
                    "matches": [
                        {
                            "provider_id": provider_id,
                            "full_name": full_name,
                            "account_name": account_name,
                            "owner": owner,
                            "verified_email": email,
                            "profile_url": (
                                "https://profiles.example.test/people/"
                                f"{provider_id}"
                            ),
                        }
                        for provider_id, full_name, account_name, owner, email in batch
                    ],
                },
            },
        })
    return {
        "http_request": {
            "rules": rules,
            "default": {"status": "error", "message": "Unknown eval URL."},
        }
    }


def _seed_pressure_history(agent: PersistentAgent, run_id: str) -> None:
    """Create realistic background traffic without making it part of the active request."""
    run_token = str(run_id).replace("-", "")[:8]
    thread_specs = (
        (
            CommsChannel.DISCORD,
            f"discord://eval/launch-ops-{run_token}",
            f"discord://eval/priya-{run_token}",
            "Priya: I own the latency review and will post the verified cause here.",
            24,
        ),
        (
            CommsChannel.EMAIL,
            f"renewals-{run_id}@eval.example.test",
            f"finance-{run_id}@eval.example.test",
            "For tomorrow: please reconcile the renewal forecast after today's launch roster is complete.",
            20,
        ),
        (
            CommsChannel.DISCORD,
            f"discord://eval/customer-escalations-{run_token}",
            f"discord://eval/mateo-{run_token}",
            "Mateo: the customer escalation is contained; support owns the follow-up.",
            16,
        ),
        (
            CommsChannel.SMS,
            f"+1555000{run_token[:4]}",
            f"+1555111{run_token[:4]}",
            "Hana: I uploaded the west-region export and am checking its source freshness.",
            12,
        ),
    )
    for channel, conversation_address, sender_address, body, age_minutes in thread_specs:
        conversation = PersistentAgentConversation.objects.create(
            channel=channel,
            address=conversation_address,
            owner_agent=agent,
        )
        sender = PersistentAgentCommsEndpoint.objects.create(
            channel=channel,
            address=sender_address,
        )
        message = PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=sender,
            conversation=conversation,
            is_outbound=False,
            body=body,
        )
        PersistentAgentMessage.objects.filter(id=message.id).update(
            timestamp=timezone.now() - timedelta(minutes=age_minutes),
        )


MOCK_BUILDERS = {
    "web": _web_mock,
    "aged_web": lambda: _web_mock(facts_last=True),
    "product": _product_mock,
    "dedupe": _dedupe_mock,
    "inventory": _inventory_mock,
    "release_calendar": _release_calendar_mock,
    "operating_feeds": _operating_feeds_mock,
}


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
        narrows_rows = re.search(r"\bwhere\b", statement, re.I) or re.search(r"\bgroup\s+by\b", statement, re.I)
        if narrows_rows and re.search(r"\border\s+by\b", statement, re.I):
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


def _seed_hidden_handoff_ledger(agent_id: str) -> None:
    with agent_sqlite_db(str(agent_id)) as db_path:
        conn = open_guarded_sqlite_connection(db_path)
        try:
            for index in range(28):
                conn.execute(
                    f"CREATE TABLE a_reference_{index:02d} "
                    "(record_key TEXT PRIMARY KEY, recorded_value TEXT);"
                )
            conn.execute(
                f"CREATE TABLE {HANDOFF_LEDGER_TABLE} ("
                "handoff_key TEXT PRIMARY KEY, worker_ref TEXT NOT NULL, "
                "resolution_code TEXT NOT NULL);"
            )
            conn.executemany(
                f"INSERT INTO {HANDOFF_LEDGER_TABLE} "
                "(handoff_key, worker_ref, resolution_code) VALUES (?, ?, ?);",
                HANDOFF_ROWS,
            )
            conn.commit()
        finally:
            clear_guarded_connection(conn)
            conn.close()


def _seed_claimed_contacts_ledger(agent_id: str) -> None:
    with agent_sqlite_db(str(agent_id)) as db_path:
        conn = open_guarded_sqlite_connection(db_path)
        try:
            conn.execute(
                "CREATE TABLE claimed_contacts ("
                "contact_id TEXT PRIMARY KEY, full_name TEXT NOT NULL, company TEXT NOT NULL, "
                "email TEXT NOT NULL, profile_url TEXT NOT NULL, source_result_id TEXT NOT NULL);"
            )
            conn.commit()
        finally:
            clear_guarded_connection(conn)
            conn.close()


def _seed_empty_launch_handoffs(agent_id: str) -> None:
    with agent_sqlite_db(str(agent_id)) as db_path:
        conn = open_guarded_sqlite_connection(db_path)
        try:
            conn.execute(
                "CREATE TABLE launch_handoffs ("
                "work_key TEXT PRIMARY KEY, owner TEXT NOT NULL, due_on TEXT NOT NULL, "
                "source_kind TEXT NOT NULL, source_ref TEXT NOT NULL);"
            )
            conn.commit()
        finally:
            clear_guarded_connection(conn)
            conn.close()


def _seed_empty_operational_events(agent_id: str) -> None:
    with agent_sqlite_db(str(agent_id)) as db_path:
        conn = open_guarded_sqlite_connection(db_path)
        try:
            conn.execute(
                "CREATE TABLE operational_events ("
                "event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, thread_key TEXT NOT NULL, "
                "occurred_at TEXT NOT NULL, provider_message_id TEXT, source_message_id TEXT NOT NULL);"
            )
            conn.commit()
        finally:
            clear_guarded_connection(conn)
            conn.close()


def _seed_outreach_reconciliation_model(agent_id: str) -> None:
    with agent_sqlite_db(str(agent_id)) as db_path:
        conn = open_guarded_sqlite_connection(db_path)
        try:
            conn.execute(
                "CREATE TABLE outreach_threads ("
                "thread_id TEXT PRIMARY KEY, recipient TEXT NOT NULL UNIQUE, owner_name TEXT NOT NULL, "
                "state TEXT NOT NULL, provider_message_id TEXT, sent_at TEXT, source_message_id TEXT);"
            )
            conn.executemany(
                "INSERT INTO outreach_threads "
                "(thread_id, recipient, owner_name, state) VALUES (?, ?, ?, ?);",
                (
                    (
                        "manager:wave:prospect-77",
                        "jordan@northstar.example.test",
                        "Seller One",
                        "prepared",
                    ),
                    (
                        "manager:wave:prospect-78",
                        "avery@harbor.example.test",
                        "Seller Two",
                        "prepared",
                    ),
                ),
            )
            conn.commit()
        finally:
            clear_guarded_connection(conn)
            conn.close()


def _schema_grounded_read_failures(calls) -> list[str]:
    calls = list(calls)
    failures = _sqlite_attempt_failures(calls)
    data_reads = []
    schema_inspections = []
    for call_index, call in enumerate(calls):
        sql = str((call.tool_params or {}).get("sql") or "")
        for statement_index, statement in enumerate(sqlparse.split(sql)):
            structural = _structural_sql(statement)
            if (
                re.search(
                    r"\bpragma\s+(?:(?:main|temp)\.)?table_(?:info|xinfo)\s*\(",
                    structural,
                    re.I,
                )
                or re.search(r"\bpragma_table_(?:info|xinfo)\s*\(", structural, re.I)
                or re.search(
                    r"\bfrom\s+(?:(?:main|temp)\.)?sqlite_(?:master|schema)\b",
                    structural,
                    re.I,
                )
            ):
                schema_inspections.append((call_index, statement_index))
            if _reads_table(structural, HANDOFF_LEDGER_TABLE):
                data_reads.append((call_index, statement_index))

    if not data_reads:
        failures.append("existing ledger was not queried")
    if schema_inspections:
        failures.append("live prompt schema was ignored in favor of a redundant schema-inspection round trip")
    if len(calls) > 1:
        failures.append(f"existing ledger required {len(calls)} SQLite attempts instead of one")
    return failures


def _schema_grounded_write_failures(calls) -> list[str]:
    calls = list(calls)
    failures = _sqlite_attempt_failures(calls)
    schema_inspections = []
    update_indexes = []
    data_reads = []
    for call_index, call in enumerate(calls):
        sql = str((call.tool_params or {}).get("sql") or "")
        for statement_index, statement in enumerate(sqlparse.split(sql)):
            structural = _structural_sql(statement)
            if (
                re.search(
                    r"\bpragma\s+(?:(?:main|temp)\.)?table_(?:info|xinfo)\s*\(",
                    structural,
                    re.I,
                )
                or re.search(
                    r"\bfrom\s+(?:(?:main|temp)\.)?sqlite_(?:master|schema)\b",
                    structural,
                    re.I,
                )
            ):
                schema_inspections.append((call_index, statement_index))
            if re.search(rf"\bupdate\s+{re.escape(HANDOFF_LEDGER_TABLE)}\b", structural, re.I):
                update_indexes.append((call_index, statement_index))
            if _reads_table(structural, HANDOFF_LEDGER_TABLE):
                data_reads.append((call_index, statement_index))

    if schema_inspections:
        failures.append("live prompt schema was ignored in favor of a redundant schema-inspection round trip")
    if not update_indexes:
        failures.append("existing ledger was not updated")
    if not data_reads:
        failures.append("updated ledger was not queried")
    if len(calls) > 2:
        failures.append(f"existing ledger required {len(calls)} SQLite attempts instead of at most two")
    return failures


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


def _inspect_release_model(agent_id: str) -> tuple[list[str], str | None]:
    expected = {
        (*row, RELEASE_CALENDAR_URL, RELEASE_CALENDAR_OBSERVED_AT)
        for row in RELEASE_EVENTS
    }
    failures = []
    model_table = None
    with agent_sqlite_db(str(agent_id)) as db_path:
        conn = open_guarded_sqlite_connection(db_path)
        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND substr(name, 1, 2) != '__';"
            ).fetchall()
            for (table_name,) in tables:
                quoted = '"' + table_name.replace('"', '""') + '"'
                columns = conn.execute(f"PRAGMA table_info({quoted});").fetchall()
                names = {column[1] for column in columns}
                required = {
                    "release_id", "service", "starts_at", "owner", "status",
                    "source_url", "observed_at",
                }
                if not required.issubset(names):
                    continue
                rows = conn.execute(
                    f"SELECT release_id, service, starts_at, owner, status, source_url, observed_at "
                    f"FROM {quoted};"
                ).fetchall()
                unique_indexes = [
                    row[1] for row in conn.execute(f"PRAGMA index_list({quoted});") if row[2]
                ]
                keyed = any(column[1] == "release_id" and column[5] for column in columns) or any(
                    "release_id" in {
                        row[2] for row in conn.execute(f'PRAGMA index_info("{name}");')
                    }
                    for name in unique_indexes
                )
                model_table = table_name
                if not keyed:
                    failures.append("release model lacked stable identity")
                if set(rows) != expected:
                    failures.append("release model omitted or altered source array rows")
                break
            if model_table is None:
                failures.append("source array was not persisted as a release domain model")
        finally:
            clear_guarded_connection(conn)
            conn.close()
    return failures, model_table


def _persisted_identity_tables(agent_id: str, table_names: set[str]) -> set[str]:
    identity_tables = set()
    with agent_sqlite_db(str(agent_id)) as db_path:
        conn = open_guarded_sqlite_connection(db_path)
        try:
            for table_name in table_names:
                quoted = '"' + table_name.replace('"', '""') + '"'
                columns = conn.execute(f"PRAGMA table_info({quoted});").fetchall()
                if any(column[5] for column in columns) or any(
                    row[2] for row in conn.execute(f"PRAGMA index_list({quoted});")
                ):
                    identity_tables.add(table_name)
        finally:
            clear_guarded_connection(conn)
            conn.close()
    return identity_tables


def _source_array_first_write_failures(sqlite_calls, model_table: str | None) -> list[str]:
    calls = list(sqlite_calls)
    if len(calls) != 1:
        return [f"expected one first-shot SQLite batch, found {len(calls)} attempts"]
    failures = _sqlite_attempt_failures(calls)
    call = calls[0]
    payload = _result_payload(call) or {}
    if "Query not executed:" in str(call.result or "") or _contains_auto_correction(payload):
        failures.append("source import depended on guard-error recovery")
    if not model_table:
        return failures

    statements = [
        statement for statement in sqlparse.split(str((call.tool_params or {}).get("sql") or ""))
        if statement.strip()
    ]
    writes = []
    for index, statement in enumerate(statements):
        mutation = _MUTATION_TARGET_RE.search(statement)
        if mutation and mutation.group("table").casefold() == model_table.casefold():
            writes.append((index, statement))
    if not writes:
        failures.append("no source write targeted the persisted release model")
        return failures

    write_index, first_write = writes[0]
    structural = _structural_sql(first_write)
    source_derived = model_table.casefold() in source_derived_model_mutation_tables((first_write,))
    if not (
        re.search(r"\binsert\b[\s\S]*\bselect\b", structural, re.I)
        and source_derived
        and _reads_table(structural, "__tool_results")
        and re.search(r"\bjson_each\s*\(", structural, re.I)
        and not re.search(r"\bvalues\s*\(", structural, re.I)
    ):
        failures.append("first release write did not derive array rows directly from __tool_results")

    queried_after_write = any(
        index > write_index
        and re.search(r"\bselect\b", statement, re.I)
        and _reads_table(statement, model_table)
        for index, statement in enumerate(statements)
    )
    if not queried_after_write:
        failures.append("new release model was not queried after its first write")
    return failures


def _uses_queryable_source_model(summary) -> bool:
    source_models = set(summary.row_derived_working_table_names)
    return bool(
        source_models
        and summary.single_tool_result_imports
        and summary.creates_working_table
        and summary.reads_working_table
        and source_models.isdisjoint(summary.unkeyed_explicit_table_names)
    )


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
    accept_queryable_source_model = False
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
        modeled_usage = self.accept_queryable_source_model and _uses_queryable_source_model(summary)
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
            (summary.aggregate_tool_result_queries < 1 and not modeled_usage, "no aggregate __tool_results query observed"),
            (summary.smart_tool_result_queries < 1 and not modeled_usage, "no smart __tool_results query observed"),
            (summary.direct_result_text_fetches > max_direct_fetches, f"direct result_text fetches {summary.direct_result_text_fetches} > {max_direct_fetches}"),
            (bool(summary.duplicate_direct_fetches), f"duplicate direct result_text fetches={summary.duplicate_direct_fetches}"),
            (bool(summary.manual_values_working_tables), f"manual VALUES working tables={summary.manual_values_working_tables}"),
            (self.reject_result_id_case_rows and bool(result_id_case_calls), "comparison rows were hand-built with CASE result_id"),
            (max_single_result_filters is not None and summary.single_result_id_filters > max_single_result_filters, f"single-result filters {summary.single_result_id_filters} > {max_single_result_filters}"),
            (require_working_table and not (summary.creates_working_table and summary.reads_working_table), "no durable working table created from __tool_results and queried"),
        ) if bad]
        failures[:0] = _sqlite_attempt_failures(calls)
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
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_smart_sqlite_synthesis", assertion_type="tool_call"),
        ScenarioTask(name="verify_sourced_answer", assertion_type="manual"),
        ScenarioTask(name="verify_answer_grounding", assertion_type="llm_judge"),
    ]
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
    # Inspection + model write/read is ideal. One additional query of that
    # durable model is still coherent; the regression is per-result probing or
    # a retry loop, which the structural checks and attempt ceiling catch.
    max_sqlite_usage_calls = 3
    max_sqlite_attempts = 3
    reject_result_id_case_rows = True

    def run(self, run_id: str, agent_id: str) -> None:
        super().run(run_id, agent_id)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_answer_grounding",
        )
        inbound = (
            PersistentAgentMessage.objects.filter(owner_agent_id=agent_id, is_outbound=False)
            .order_by("-timestamp", "-id")
            .first()
        )
        outbound = _outbound_messages_after(agent_id, inbound.timestamp) if inbound else []
        body = outbound[-1].body if outbound else ""
        evidence = "\n\n".join(
            f"{heading}\n" + "\n".join(f"- {fact}" for fact in facts)
            for heading, facts in WEB_SOURCE_FACTS
        )
        choice, reasoning = self.llm_judge(
            question=(
                "Is every concrete factual detail in the answer supported by the supplied source evidence? "
                "Faithful normalization and paraphrase are supported (for example, 'annual pricing' may be shown as "
                "'Annual'). Recommendations and clearly labeled reasoning are allowed. Fail invented prices, "
                "capabilities, certifications, deployment modes, or other details that the evidence does not state."
            ),
            context=f"Source evidence:\n{evidence}\n\nAgent answer:\n{body}",
            options=("Grounded", "Contains unsupported factual details"),
            params={"temperature": 0.0, "max_tokens": 500, "reasoning_effort": "low"},
        )
        status = (
            EvalRunTask.Status.PASSED
            if choice == "Grounded"
            else EvalRunTask.Status.ERRORED
            if choice == "Error"
            else EvalRunTask.Status.FAILED
        )
        self.record_task_result(
            run_id,
            None,
            status,
            task_name="verify_answer_grounding",
            observed_summary=f"{choice}: {reasoning}",
        )


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
    description = (
        "Multi-turn catalog reasoning should set-import same-shaped siblings into related domain entities once "
        "and reuse them."
    )
    max_single_result_filters = 0
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
        repeated_import_tables = _repeated_source_import_tables(
            str((call.tool_params or {}).get("sql") or "") for call in successful_calls
        )
        model_advisories = sorted({
            str(advisory.get("code") or "")
            for call in successful_calls
            for advisory in (_result_payload(call) or {}).get("advisories", ())
            if isinstance(advisory, dict)
            and advisory.get("code") in {
                "bulk_manual_working_table_from_visible_results",
                "manual_working_table_from_visible_results",
                "source_facts_copied_into_model",
                "tool_result_row_loop",
            }
        })
        failures = [message for failed, message in (
            (not successful_calls, "no successful sqlite_batch call observed"),
            (summary.tool_result_statement_count < 1 or summary.uses_json_functions < 1, "domain model was not derived from tool-result JSON"),
            (summary.aggregate_tool_result_queries < 1, "domain model did not import tool results in aggregate"),
            (
                summary.single_result_id_filters > self.max_single_result_filters,
                f"domain model imported tool results one result at a time "
                f"({summary.single_result_id_filters} > {self.max_single_result_filters})",
            ),
            (
                bool(repeated_import_tables),
                f"same-shaped sibling rows used repeated imports into {repeated_import_tables}",
            ),
            (not model_tables, "no reusable domain table was created"),
            (not has_stable_identity, "domain model lacked stable identity constraints"),
            (not row_derived_model_tables, "repeating child rows were not extracted into the domain model"),
            (not re.search(r"\b(?:source_url|source_id|provenance)\b", strategy_sql, re.I), "domain model lacked source provenance"),
            (not read_tables, "initial decision did not query the reusable domain model"),
            (not re.search(r"\bwhere\b", successful_sql, re.I), "initial decision did not filter in SQL"),
            (not re.search(r"\border\s+by\b", successful_sql, re.I), "initial decision did not rank in SQL"),
            (
                bool(manually_populated_model_tables),
                f"durable source rows were hand-entered with VALUES in {sorted(manually_populated_model_tables)}",
            ),
            (bool(model_advisories), f"SQLite reported unreliable model writes: {model_advisories}"),
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
class SqliteSchemaGroundedExistingTableScenario(SqliteDomainModelScenario):
    slug = SQLITE_SCHEMA_GROUNDED_EXISTING_TABLE
    description = "Large existing databases should expose the relevant live schema for one-shot grounded queries."
    expected_runtime = "short"
    tags = (*SqliteToolResultScenario.tags, "schema_discovery", "trajectory_regression")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_schema_grounded_query", assertion_type="tool_call"),
        ScenarioTask(name="verify_unresolved_handoff_answer", assertion_type="manual"),
    ]
    prompt = (
        "Check the existing handoff ledger and tell me who still has unfinished work, "
        "with the count for each. Use the ledger as the source of truth."
    )

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        PersistentAgent.objects.filter(id=agent_id).update(
            charter="Own internal operations and treat established ledger state as the source of truth."
        )
        _seed_hidden_handoff_ledger(agent_id)
        inbound = self._inject_and_wait(
            run_id,
            agent_id,
            self.prompt,
            {},
            allowed_tool_names={"sqlite_batch", "send_chat_message"},
            max_relevant_tool_calls=6,
        )
        calls = _tool_calls_for_run(
            run_id,
            after=inbound.timestamp,
            tool_names={"sqlite_batch"},
        )
        self._record_check(
            run_id,
            "verify_schema_grounded_query",
            _schema_grounded_read_failures(calls),
            "Used the live prompt schema for one successful ledger query without inspection or recovery.",
        )
        self._record_sourced_answer(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_unresolved_handoff_answer",
            source_urls=(),
            required_terms=("agent-red", "2", "agent-blue", "1"),
            min_sources=0,
        )


@register_scenario
class SqliteSchemaGroundedExistingTableWriteScenario(SqliteDomainModelScenario):
    slug = SQLITE_SCHEMA_GROUNDED_EXISTING_TABLE_WRITE
    description = "Large existing databases should expose the relevant live schema for one-shot grounded writes."
    expected_runtime = "short"
    tags = (*SqliteToolResultScenario.tags, "schema_discovery", "trajectory_regression", "first_time_correctness")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_schema_grounded_write", assertion_type="tool_call"),
        ScenarioTask(name="verify_updated_handoff_answer", assertion_type="manual"),
    ]
    prompt = (
        "Mark handoff-02 resolved in the existing handoff ledger. Then tell me who still has unfinished work "
        "and the count for each. Use the ledger as the source of truth."
    )

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        PersistentAgent.objects.filter(id=agent_id).update(
            charter="Maintain internal operating ledgers precisely and report their current state."
        )
        _seed_hidden_handoff_ledger(agent_id)
        inbound = self._inject_and_wait(
            run_id,
            agent_id,
            self.prompt,
            {},
            allowed_tool_names={"sqlite_batch", "send_chat_message"},
            max_relevant_tool_calls=7,
        )
        calls = list(
            _tool_calls_for_run(
                run_id,
                after=inbound.timestamp,
                tool_names={"sqlite_batch"},
            )
        )
        failures = _schema_grounded_write_failures(calls)

        with agent_sqlite_db(str(agent_id)) as db_path:
            conn = open_guarded_sqlite_connection(db_path)
            try:
                resolution = conn.execute(
                    f"SELECT resolution_code FROM {HANDOFF_LEDGER_TABLE} WHERE handoff_key=?;",
                    ("handoff-02",),
                ).fetchone()
            finally:
                clear_guarded_connection(conn)
                conn.close()
        if resolution != ("resolved",):
            failures.append(f"handoff-02 persisted state was {resolution!r}")

        self._record_check(
            run_id,
            "verify_schema_grounded_write",
            failures,
            "Used the live prompt schema for one successful update and follow-up query.",
        )
        self._record_sourced_answer(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_updated_handoff_answer",
            source_urls=(),
            required_terms=("agent-red", "1", "agent-blue", "1"),
            min_sources=0,
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
        source_write_attempts = [
            call
            for call in sqlite_attempts
            if source_derived_model_mutation_tables(
                (str((call.tool_params or {}).get("sql") or ""),)
            )
        ]
        source_mutations = set(source_derived_model_mutation_tables(sql_values))
        state_failures, child_table = _inspect_domain_refresh_state(agent_id)
        if PersistentAgent.objects.values_list("charter", flat=True).get(id=agent_id) != self.domain_charter:
            state_failures.append("ordinary CRM review changed the agent charter")
        expected_tables = {"accounts"}
        if child_table:
            expected_tables.add(child_table.casefold())
        failures = _first_shot_source_phase_failures(calls)
        failures.extend(_sqlite_attempt_failures(sqlite_attempts))
        failures.extend(_source_write_effect_failures(source_write_attempts, expected_tables))
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
            "Fetched the newer snapshot once, ingested it in one clean batch, and then replied.",
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
class SqliteSourceArrayFirstWriteScenario(SqliteDomainModelScenario):
    slug = SQLITE_SOURCE_ARRAY_FIRST_WRITE
    description = "A new source-bearing array model should be populated directly on its first SQLite write."
    expected_runtime = "short"
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_first_source_write", assertion_type="tool_call"),
        ScenarioTask(name="verify_persisted_release_model", assertion_type="persisted_state"),
        ScenarioTask(name="verify_release_answer", assertion_type="manual"),
    ]
    prompt = (
        "Bring our current release calendar up to date from this feed; it is the operating roster we'll use for "
        "release changes and follow-ups. Summarize what is scheduled and call out anything blocked or canceled "
        f"with its owner. Include the source link: {RELEASE_CALENDAR_URL}"
    )

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        PersistentAgent.objects.filter(id=agent_id).update(
            charter="Own release operations and keep the latest sourced schedule coherent."
        )
        self._enable_tools(agent_id, ("http_request",))
        inbound = self._inject_and_wait(
            run_id,
            agent_id,
            self.prompt,
            _release_calendar_mock(),
            allowed_tool_names={"http_request", "sqlite_batch", "send_chat_message"},
            max_relevant_tool_calls=6,
        )
        calls = _tool_calls_for_run(run_id, after=inbound.timestamp)
        sqlite_attempts = [call for call in calls if call.tool_name == "sqlite_batch"]
        state_failures, model_table = _inspect_release_model(agent_id)
        failures = _first_shot_source_phase_failures(
            calls,
            expected_url=RELEASE_CALENDAR_URL,
        )
        failures.extend(_source_array_first_write_failures(sqlite_attempts, model_table))
        if model_table:
            failures.extend(_source_write_effect_failures(sqlite_attempts, {model_table.casefold()}))
        failures.extend(_orphan_completion_failures(run_id, inbound.timestamp))
        self._record_check(
            run_id,
            "verify_first_source_write",
            failures,
            "Fetched once and created, populated, and queried the release model in one clean SQLite batch.",
        )
        self._record_check(
            run_id,
            "verify_persisted_release_model",
            state_failures,
            "Persisted every keyed release row with complete source provenance.",
        )
        self._record_sourced_answer(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_release_answer",
            source_urls=(RELEASE_CALENDAR_URL,),
            required_terms=("Search index", "blocked", "Mateo Ruiz", "Mobile client", "canceled"),
            min_sources=1,
        )


@register_scenario
class SqliteSiblingResultSetFirstWriteScenario(SqliteDomainModelScenario):
    slug = SQLITE_SIBLING_RESULT_SET_FIRST_WRITE
    description = (
        "The latest same-shaped source batch should remain importable after unrelated SQLite work and become one "
        "keyed domain model in one set-wise write."
    )
    expected_runtime = "short"
    tags = (
        *SqliteToolResultScenario.tags,
        "trajectory_regression",
        "set_wise_import",
        "current_batch_persistence",
    )
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_first_shaped_model_write", assertion_type="tool_call"),
        ScenarioTask(name="verify_segment_answer", assertion_type="manual"),
    ]
    prompt = (
        "The three account export calls have finished. Turn that evidence into the reusable account picture we'll "
        "use for follow-ups, then tell me which buyer segment has the most verified contacts and the count. Include "
        "the source links behind the winning segment."
    )

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        PersistentAgent.objects.filter(id=agent_id).update(
            charter="Maintain a sourced account and buyer model for pipeline decisions and follow-up questions."
        )
        historical_completion = PersistentAgentCompletion.objects.create(
            agent_id=agent_id,
        )
        historical_step = _seed_account_export(
            agent_id, historical_completion, *HISTORICAL_ACCOUNT_BATCH,
            "2026-06-01T14:00:00Z", "Completed an older account export call.",
        )
        PersistentAgentStep.objects.filter(id=historical_step.id).update(
            created_at=timezone.now() - timedelta(days=1),
        )
        current_completion = PersistentAgentCompletion.objects.create(
            agent_id=agent_id,
        )
        for source_url, accounts in SIBLING_ACCOUNT_BATCHES:
            _seed_account_export(
                agent_id, current_completion, source_url, accounts,
                "2026-07-26T14:00:00Z", "Completed account export call.",
            )
        with agent_sqlite_db(str(agent_id)) as db_path:
            conn = open_guarded_sqlite_connection(db_path)
            try:
                conn.executescript(
                    "CREATE TABLE accounts("
                    "account_id TEXT PRIMARY KEY, company TEXT NOT NULL, buyer_segment TEXT NOT NULL, "
                    "verified_contacts INTEGER NOT NULL, source_url TEXT, observed_at TEXT, result_id TEXT NOT NULL);"
                    "CREATE TABLE research_notes("
                    "note_id TEXT PRIMARY KEY, note TEXT NOT NULL);"
                )
                conn.commit()
            finally:
                clear_guarded_connection(conn)
                conn.close()
        intervening_completion = PersistentAgentCompletion.objects.create(agent_id=agent_id)
        intervening_step = PersistentAgentStep.objects.create(
            agent_id=agent_id,
            completion=intervening_completion,
            description="Created an unrelated working-notes table.",
        )
        PersistentAgentToolCall.objects.create(
            step=intervening_step,
            tool_name="sqlite_batch",
            tool_params={
                "sql": "CREATE TABLE research_notes(note_id TEXT PRIMARY KEY, note TEXT NOT NULL);",
            },
            result=json.dumps(
                {"status": "ok", "results": [{"message": "Query 0 affected 0 rows."}]}
            ),
            status="complete",
        )

        inbound = self._inject_and_wait(
            run_id,
            agent_id,
            self.prompt,
            {},
            allowed_tool_names={"sqlite_batch", "send_chat_message"},
            max_relevant_tool_calls=5,
        )
        calls = _tool_calls_for_run(run_id, after=inbound.timestamp)
        sqlite_attempts = [call for call in calls if call.tool_name == "sqlite_batch"]
        successful_calls, strategy_calls = _sqlite_calls_with_persisted_effects(sqlite_attempts)
        summary = summarize_sqlite_tool_result_calls(strategy_calls)
        sql_values = [str((call.tool_params or {}).get("sql") or "") for call in successful_calls]
        sql = "\n".join(sql_values)
        model_write_calls = [
            call for call in successful_calls
            if source_derived_model_mutation_tables(
                (str((call.tool_params or {}).get("sql") or ""),)
            )
        ]
        model_tables = set(source_derived_model_mutation_tables(sql_values))
        _all_models, _row_models, identity_tables = _domain_model_lineage(
            sql,
            direct_tables=model_tables,
            row_direct_tables=summary.row_derived_working_table_names,
            candidate_tables=summary.working_table_names,
        )
        identity_tables.update(_persisted_identity_tables(agent_id, model_tables))
        decision_tables = _decision_model_tables(sql, model_tables)
        relevant_advisories = sorted({
            str(advisory.get("code") or "")
            for call in successful_calls
            for advisory in (_result_payload(call) or {}).get("advisories", ())
            if isinstance(advisory, dict)
            and advisory.get("code") in {
                "bulk_manual_working_table_from_visible_results",
                "manual_working_table_from_visible_results",
                "source_facts_copied_into_model",
                "tool_result_row_loop",
            }
        })
        failures = _sqlite_attempt_failures(sqlite_attempts)
        failures.extend(message for failed, message in (
            (
                not 1 <= len(sqlite_attempts) <= 2,
                f"expected one set-wise write and at most one model read, found {len(sqlite_attempts)} SQLite attempts",
            ),
            (
                len(model_write_calls) != 1,
                f"expected one source-derived model write, found {len(model_write_calls)}",
            ),
            (summary.aggregate_tool_result_queries < 1, "source siblings were not read as one set"),
            (
                not re.search(r"\bis_current_batch\b\s*=\s*1", sql, re.I),
                "source import did not use the current-batch boundary",
            ),
            (
                summary.single_result_id_filters > 0,
                f"source siblings were handled one result_id at a time ({summary.single_result_id_filters})",
            ),
            (not model_tables, "no source-derived named account model was written"),
            (
                not model_tables.issubset(identity_tables),
                f"account model lacked stable identity: {sorted(model_tables - identity_tables)}",
            ),
            (not decision_tables, "the new account model was not queried after import"),
            (not re.search(r"\bgroup\s+by\b", sql, re.I), "buyer segments were not aggregated in SQL"),
            (bool(summary.manual_values_working_tables), "source rows were copied into a VALUES table"),
            (bool(relevant_advisories), f"SQLite reported inefficient source handling: {relevant_advisories}"),
        ) if failed)
        self._record_check(
            run_id,
            "verify_first_shaped_model_write",
            failures,
            f"Imported all sibling results into {sorted(model_tables)} in one set-wise write and queried only the model.",
        )
        self._record_sourced_answer(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_segment_answer",
            source_urls=(SIBLING_ACCOUNT_BATCHES[0][0], SIBLING_ACCOUNT_BATCHES[1][0]),
            required_terms=("procurement", "5"),
            min_sources=2,
        )


@register_scenario
class SqliteUnstructuredBindingsFirstWriteScenario(SqliteDomainModelScenario):
    slug = SQLITE_UNSTRUCTURED_BINDINGS_FIRST_WRITE
    description = (
        "Unstructured sibling evidence should become one bound, provenance-linked domain model without literal rows."
    )
    expected_runtime = "short"
    tags = (*SqliteToolResultScenario.tags, "trajectory_regression", "bound_unstructured_import")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_bound_model_write", assertion_type="tool_call"),
        ScenarioTask(name="verify_evidence_answer", assertion_type="manual"),
    ]
    prompt = (
        "The completed customer interview notes are ready. Turn them into the reusable customer-evidence picture "
        "we'll use for product decisions, then tell me the most common primary pain point, its count, and the affected "
        "companies. Include the interview links."
    )

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        PersistentAgent.objects.filter(id=agent_id).update(
            charter="Maintain a sourced customer-evidence model for product decisions and follow-up questions."
        )
        source_step_ids = []
        for source_url, note in UNSTRUCTURED_INTERVIEW_NOTES:
            step = PersistentAgentStep.objects.create(
                agent_id=agent_id,
                description="Completed customer interview scrape.",
            )
            source_step_ids.append(str(step.id))
            PersistentAgentToolCall.objects.create(
                step=step,
                tool_name="mcp_brightdata_scrape_as_markdown",
                tool_params={"url": source_url},
                result=json.dumps({"status": "success", "result": note}),
                status="complete",
            )

        inbound = self._inject_and_wait(
            run_id,
            agent_id,
            self.prompt,
            {},
            allowed_tool_names={"sqlite_batch", "send_chat_message"},
            # A valid run may use a progress send, one model write, one bounded
            # decision read, and the terminal answer. Leave one slot so the
            # stop policy does not cancel that answer while it is being queued.
            max_relevant_tool_calls=5,
        )
        calls = _tool_calls_for_run(run_id, after=inbound.timestamp)
        sqlite_attempts = [call for call in calls if call.tool_name == "sqlite_batch"]
        successful_calls, strategy_calls = _sqlite_calls_with_persisted_effects(sqlite_attempts)
        summary = summarize_sqlite_tool_result_calls(strategy_calls)
        sql_values = [str((call.tool_params or {}).get("sql") or "") for call in successful_calls]
        sql = "\n".join(sql_values)
        model_write_calls = [
            call for call in successful_calls
            if source_derived_model_mutation_tables(
                (str((call.tool_params or {}).get("sql") or ""),)
            )
        ]
        model_tables = set(source_derived_model_mutation_tables(sql_values))
        _all_models, _row_models, identity_tables = _domain_model_lineage(
            sql,
            direct_tables=model_tables,
            row_direct_tables=summary.row_derived_working_table_names,
            candidate_tables=summary.working_table_names,
        )
        decision_tables = set(source_derived_model_reconciled_tables(sql_values)).intersection(model_tables)
        bound_rows = []
        if len(model_write_calls) == 1:
            model_params = model_write_calls[0].tool_params or {}
            if isinstance(model_params.get("rows"), list):
                bound_rows = model_params["rows"]
            else:
                bindings = model_params.get("bindings")
                if isinstance(bindings, dict) and isinstance(bindings.get("rows"), list):
                    bound_rows = bindings["rows"]
        bound_result_ids = {
            str(row.get("result_id") or "")
            for row in bound_rows
            if isinstance(row, dict)
        }
        bound_rows_have_fields = bool(bound_rows) and all(
            isinstance(row, dict)
            and isinstance(row.get("fields"), dict)
            and bool(row["fields"])
            for row in bound_rows
        )
        expected_result_ids = set(build_short_result_id_map(source_step_ids).values())
        expected_source_urls = {
            source_url for source_url, _note in UNSTRUCTURED_INTERVIEW_NOTES
        }
        modeled_source_urls = _modeled_source_urls(agent_id, model_tables)
        relevant_advisories = sorted({
            str(advisory.get("code") or "")
            for call in successful_calls
            for advisory in (_result_payload(call) or {}).get("advisories", ())
            if isinstance(advisory, dict)
        })
        failures = _sqlite_attempt_failures(sqlite_attempts)
        failures.extend(message for failed, message in (
            (
                not 1 <= len(sqlite_attempts) <= 2,
                f"expected one aggregate inspection plus one model batch at most, found {len(sqlite_attempts)} attempts",
            ),
            (len(model_write_calls) != 1, f"expected one provenance-linked model write, found {len(model_write_calls)}"),
            (len(bound_rows) != len(UNSTRUCTURED_INTERVIEW_NOTES), f"expected {len(UNSTRUCTURED_INTERVIEW_NOTES)} bound rows, found {len(bound_rows)}"),
            (
                bound_result_ids != expected_result_ids,
                f"bound rows contained incomplete or incorrect source result IDs: {sorted(bound_result_ids)}",
            ),
            (
                not bound_rows_have_fields,
                "bound rows did not keep interpreted source facts in non-empty fields objects",
            ),
            (
                modeled_source_urls != expected_source_urls,
                f"modeled rows did not preserve the trusted source URLs: {sorted(modeled_source_urls)}",
            ),
            (
                not re.search(r"\bjson_each\s*\(\s*:rows\s*\)", sql, re.I),
                "model write did not expand native source rows",
            ),
            (
                not re.search(r"\$\.fields\.[a-z_]", sql, re.I),
                "model write did not derive interpreted facts from bound row fields",
            ),
            (
                not re.search(r"\b__tool_results\b.*\bsource_url\b|\bsource_url\b.*\b__tool_results\b", sql, re.I | re.S),
                "model write did not derive source URLs from trusted tool-result metadata",
            ),
            (summary.single_result_id_filters > 0, "unstructured siblings were inspected one result_id at a time"),
            (not model_tables, "no sourced customer-evidence model was written"),
            (not model_tables.issubset(identity_tables), f"evidence model lacked stable identity: {sorted(model_tables - identity_tables)}"),
            (not decision_tables, "the new evidence model was not queried after import"),
            (bool(summary.manual_values_working_tables), "visible interview facts were copied into SQL VALUES"),
            (bool(relevant_advisories), f"SQLite reported avoidable advice/errors: {relevant_advisories}"),
        ) if failed)
        if model_tables:
            failures.extend(_source_write_effect_failures(model_write_calls, model_tables))
        self._record_check(
            run_id,
            "verify_bound_model_write",
            failures,
            "Inspected sibling notes as a set, bound every interpreted row once with exact provenance, and queried the model.",
        )
        self._record_sourced_answer(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_evidence_answer",
            source_urls=tuple(source_url for source_url, _note in UNSTRUCTURED_INTERVIEW_NOTES),
            required_terms=("manual research handoffs", "3", "O'Brien Advisory"),
            min_sources=2,
        )


@register_scenario
class SqliteIncrementalDomainModelScenario(SqliteDomainModelScenario):
    slug = SQLITE_INCREMENTAL_DOMAIN_MODEL
    description = "Multi-source operating work should build a keyed relational model before research ends."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_incremental_domain_model", assertion_type="tool_call"),
        ScenarioTask(name="verify_operating_answer", assertion_type="manual"),
    ]
    prompt = (
        "We're coordinating this work across people and feeds. Build the current ownership and risk picture from "
        "these sources. Tell me every active initiative without a primary owner, then rank the owned active "
        "initiatives by risk. Include the source links; I'll have follow-ups.\n\n"
        + "\n".join(f"- {url}" for url in OPERATING_FEED_URLS)
    )

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        PersistentAgent.objects.filter(id=agent_id).update(
            charter="Keep current cross-team operating ownership and risk evidence coherent."
        )
        self._enable_tools(agent_id, ("http_request",))
        inbound = self._inject_and_wait(
            run_id,
            agent_id,
            self.prompt,
            _operating_feeds_mock(),
            allowed_tool_names={"http_request", "sqlite_batch", "send_chat_message"},
            max_relevant_tool_calls=12,
        )
        self._record_incremental_model(run_id, after=inbound.timestamp)
        self._record_sourced_answer(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_operating_answer",
            source_urls=OPERATING_FEED_URLS,
            required_terms=(
                "Lumen Migration",
                "Onboarding Simplification",
                "Checkout Recovery",
                "critical",
                "Priya Shah",
            ),
            min_sources=3,
        )

    def _record_incremental_model(self, run_id: str, *, after) -> None:
        task_name = "verify_incremental_domain_model"
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        calls = _tool_calls_for_run(run_id, after=after)
        source_calls = [call for call in calls if call.tool_name == "http_request"]
        sqlite_calls = [call for call in calls if call.tool_name == "sqlite_batch"]
        successful_sqlite = [
            call for call in sqlite_calls
            if str(getattr(call, "status", "")).casefold() == "complete"
            and str((_result_payload(call) or {}).get("status") or "").casefold() == "ok"
        ]
        source_positions = [
            index
            for index, call in enumerate(calls)
            if call.tool_name == "http_request"
            and str(getattr(call, "status", "")).casefold() == "complete"
        ]
        last_source_position = max(source_positions) if source_positions else -1
        after_last_source = [
            call for index, call in enumerate(calls)
            if call in successful_sqlite and index > last_source_position
        ]

        def sql_values(selected_calls) -> list[str]:
            return [str((call.tool_params or {}).get("sql") or "") for call in selected_calls]

        all_sql_values = sql_values(successful_sqlite)
        combined_sql = "\n".join(all_sql_values)
        after_targets = set(source_derived_model_mutation_tables(sql_values(after_last_source)))
        all_targets = set(source_derived_model_mutation_tables(all_sql_values))
        summary = summarize_sqlite_tool_result_calls(successful_sqlite)
        candidates = tuple(dict.fromkeys((*summary.working_table_names, *all_targets)))
        _modeled, _row_modeled, identity_tables = _domain_model_lineage(
            combined_sql,
            direct_tables=all_targets,
            row_direct_tables=summary.row_derived_working_table_names,
            candidate_tables=candidates,
        )
        statements = [
            _structural_sql(statement)
            for statement in sqlparse.split(combined_sql)
            if statement.strip()
        ]
        literal_model_writes = []
        for statement in statements:
            mutation = _MUTATION_TARGET_RE.search(statement)
            if (
                mutation
                and mutation.group("table").casefold() in all_targets
                and mutation.group("table").casefold()
                not in source_derived_model_mutation_tables((statement,))
            ):
                literal_model_writes.append(statement)
        decision_reads = [
            statement
            for statement in statements
            if re.search(r"\bselect\b", statement, re.I)
            and any(_reads_table(statement, table) for table in all_targets)
        ]
        fetch_counts = _source_fetch_counts(
            source_calls,
            tool_names={"http_request"},
            source_urls=OPERATING_FEED_URLS,
        )
        failures = _tool_attempt_failures(source_calls, "Source fetch")
        failures.extend(_sqlite_attempt_failures(sqlite_calls))
        failures.extend(message for failed, message in (
            (
                any(count != 1 for count in fetch_counts.values()),
                f"expected each operating feed once, found {fetch_counts}",
            ),
            (
                not after_targets,
                "the completed source batch was not reconciled before answering",
            ),
            (
                len(all_targets) < 2,
                f"expected related entity models, found {sorted(all_targets)}",
            ),
            (
                not all_targets.issubset(identity_tables),
                f"modeled tables lacked stable identity: {sorted(all_targets - identity_tables)}",
            ),
            (bool(literal_model_writes), "source facts were copied into model writes as SQL literals"),
            (
                not re.search(r"\binitiative_id\b", combined_sql, re.I),
                "model did not preserve the cross-feed initiative relationship",
            ),
            (
                not re.search(r"\b(?:source_url|source_id|provenance)\b", combined_sql, re.I),
                "modeled entities lacked source provenance",
            ),
            (
                not any(re.search(r"\b(?:left\s+join|not\s+exists|having)\b", statement, re.I)
                        for statement in decision_reads),
                "model was not queried for missing ownership with set logic",
            ),
            (
                not any("risk" in statement.casefold() and re.search(r"\border\s+by\b", statement, re.I)
                        for statement in decision_reads),
                "modeled risk was not ranked in SQL",
            ),
        ) if failed)
        self._record_check(
            run_id,
            task_name,
            failures,
            f"Incrementally modeled and queried related operating tables: {sorted(all_targets)}.",
        )


@register_scenario
class SqliteProspectPipelineCompletesScenario(SqliteDomainModelScenario):
    slug = SQLITE_PROSPECT_PIPELINE_COMPLETES
    description = (
        "High burn rate should not turn an organization seed batch into a terminal status update; prospect work "
        "should model companies and people, measure coverage, and deliver the requested contacts."
    )
    cost_class = "medium"
    tags = (*SqliteToolResultScenario.tags, "coverage", "effort_calibration", "trajectory_regression")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_prospect_pipeline", assertion_type="tool_call"),
        ScenarioTask(name="verify_complete_prospect_report", assertion_type="manual"),
    ]
    prompt = (
        "Build a reusable first batch of four US healthcare staffing prospects at firms under 100 employees. "
        "For each one I need the recruiter or owner's name, title, company, company size, profile link, and a short "
        "qualification note. The current company and people exports are below. Give me the completed batch as a "
        "concise report; I'll have follow-ups by company and person.\n\n"
        + "\n".join(f"- {url}" for url in PROSPECT_FEED_URLS)
    )

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        PersistentAgent.objects.filter(id=agent_id).update(
            charter=(
                "Source qualified staffing prospects and keep company, person, evidence, and coverage state coherent "
                "for follow-up work."
            ),
            daily_credit_limit=50,
        )
        burn_step = PersistentAgentStep.objects.create(
            agent_id=agent_id,
            description="Recent prospect research eval work",
        )
        PersistentAgentStep.objects.filter(id=burn_step.id).update(credits_cost=Decimal("5.5"))
        self._enable_tools(agent_id, ("http_request",))
        inbound = self._inject_and_wait(
            run_id,
            agent_id,
            self.prompt,
            _prospect_pipeline_mock(),
            allowed_tool_names={
                "http_request",
                "sqlite_batch",
                "send_chat_message",
                "update_plan",
            },
            max_relevant_tool_calls=12,
        )
        self._record_prospect_pipeline(run_id, after=inbound.timestamp)
        self._record_sourced_answer(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_complete_prospect_report",
            source_urls=PROSPECT_PROFILE_URLS,
            required_terms=("Maya Chen", "Leo Martin", "Priya Nwosu", "Evan Cho"),
            min_sources=4,
        )

    def _record_prospect_pipeline(self, run_id: str, *, after) -> None:
        task_name = "verify_prospect_pipeline"
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        calls = _tool_calls_for_run(run_id, after=after)
        company_positions = [
            index for index, call in enumerate(calls)
            if call.tool_name == "http_request"
            and str(resolved_tool_param(call, "url") or "").rstrip("/")
            == PROSPECT_COMPANY_FEED_URL.rstrip("/")
            and str(getattr(call, "status", "")).casefold() == "complete"
        ]
        people_positions = [
            index for index, call in enumerate(calls)
            if call.tool_name == "http_request"
            and str(resolved_tool_param(call, "url") or "").rstrip("/")
            == PROSPECT_PEOPLE_FEED_URL.rstrip("/")
            and str(getattr(call, "status", "")).casefold() == "complete"
        ]
        sqlite_attempts = [call for call in calls if call.tool_name == "sqlite_batch"]
        sqlite_calls = [
            call for call in sqlite_attempts
            if str(getattr(call, "status", "")).casefold() == "complete"
            and str((_result_payload(call) or {}).get("status") or "").casefold() == "ok"
        ]
        sql_values = [str((call.tool_params or {}).get("sql") or "") for call in sqlite_calls]
        combined_sql = "\n".join(sql_values)
        model_tables = set(source_derived_model_mutation_tables(sql_values))
        summary = summarize_sqlite_tool_result_calls(sqlite_calls)
        _modeled, _row_modeled, identity_tables = _domain_model_lineage(
            combined_sql,
            direct_tables=model_tables,
            row_direct_tables=summary.row_derived_working_table_names,
            candidate_tables=summary.working_table_names,
        )
        statements = [
            _structural_sql(statement)
            for statement in sqlparse.split(combined_sql)
            if statement.strip()
        ]
        cross_entity_queries = [
            statement for statement in statements
            if re.search(r"\bselect\b", statement, re.I)
            and re.search(r"\b(?:join|exists|having)\b", statement, re.I)
            and sum(_reads_table(statement, table) for table in model_tables) >= 2
        ]
        terminal_positions = [
            index for index, call in enumerate(calls)
            if call.tool_name == "send_chat_message"
            and (call.tool_params or {}).get("will_continue_work") is False
            and str(getattr(call, "status", "")).casefold() == "complete"
            and not (_result_payload(call) or {}).get("skipped")
        ]
        last_source_position = max((*company_positions, *people_positions), default=-1)
        failures = _tool_attempt_failures(
            [
                call for call in calls
                if call.tool_name == "http_request"
            ],
            "Prospect source",
        )
        failures.extend(_sqlite_attempt_failures(sqlite_attempts))
        failures.extend(message for failed, message in (
            (not company_positions, "company discovery never completed"),
            (not people_positions, "person discovery never completed"),
            (
                len(model_tables) < 2,
                f"expected related company/person model tables, found {sorted(model_tables)}",
            ),
            (
                not model_tables.issubset(identity_tables),
                f"prospect model lacked stable identity: {sorted(model_tables - identity_tables)}",
            ),
            (
                not re.search(r"\b(?:organization|company)_id\b", combined_sql, re.I),
                "company-to-person identity was not preserved",
            ),
            (not cross_entity_queries, "model was not queried across the company-person relationship"),
            (
                len(terminal_positions) != 1,
                f"expected one terminal prospect report, found {len(terminal_positions)}",
            ),
            (
                bool(terminal_positions) and terminal_positions[0] <= last_source_position,
                "agent sent a terminal status before person-level sourcing finished",
            ),
        ) if failed)
        self._record_check(
            run_id,
            task_name,
            failures,
            f"Modeled {sorted(model_tables)}, queried the company-person relationship, and delivered after sourcing.",
        )


@register_scenario
class SqliteEnrichmentRefreshUnderPressureScenario(SqliteDomainModelScenario):
    slug = SQLITE_ENRICHMENT_REFRESH_UNDER_PRESSURE
    description = (
        "An agent carrying unrelated cross-channel traffic should refresh an existing domain model from every "
        "same-shaped enrichment result in one clean set-wise pass."
    )
    expected_runtime = "medium"
    tags = (
        *SqliteToolResultScenario.tags,
        "trajectory_regression",
        "set_wise_import",
        "multi_channel_pressure",
        "existing_model_refresh",
    )
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_pressure_refresh", assertion_type="tool_call"),
        ScenarioTask(name="verify_missing_contact_answer", assertion_type="manual"),
    ]
    prompt = (
        "I need the launch contact roster now. Refresh our existing contacts from all three completed regional "
        "exports, then tell me which contacts still lack a verified email, give me the missing count by owner, and "
        "link each missing contact's known profile. "
        "Keep the other active threads intact; I'll return to those after this.\n\n"
        + "\n".join(f"- {url}" for url in ENRICHMENT_FEED_URLS)
    )

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        agent = PersistentAgent.objects.get(id=agent_id)
        PersistentAgent.objects.filter(id=agent_id).update(
            charter=(
                "Maintain the launch contact model while coordinating work across the owner, operations, support, "
                "and finance channels. Preserve explicit ownership and priority."
            ),
        )
        _seed_pressure_history(agent, run_id)
        with agent_sqlite_db(str(agent_id)) as db_path:
            conn = open_guarded_sqlite_connection(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE contacts(
                        provider_id TEXT PRIMARY KEY,
                        full_name TEXT,
                        account_name TEXT,
                        owner TEXT NOT NULL,
                        verified_email TEXT,
                        profile_url TEXT,
                        source_url TEXT,
                        result_id TEXT
                    );
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO contacts(provider_id, owner)
                    VALUES (?, ?);
                    """,
                    [(provider_id, owner) for provider_id, _name, _account, owner, _email in ENRICHED_CONTACTS],
                )
                conn.commit()
            finally:
                clear_guarded_connection(conn)
                conn.close()
        self._enable_tools(agent_id, ("http_request",))
        inbound = self._inject_and_wait(
            run_id,
            agent_id,
            self.prompt,
            _enrichment_pressure_mock(),
            allowed_tool_names={
                "http_request",
                "sqlite_batch",
                "send_chat_message",
                "update_plan",
            },
            max_relevant_tool_calls=16,
        )
        self._record_pressure_refresh(run_id, agent_id=agent_id, after=inbound.timestamp)
        missing = tuple(
            f"https://profiles.example.test/people/{provider_id}"
            for provider_id, _name, _account, _owner, email in ENRICHED_CONTACTS
            if email is None
        )
        self._record_sourced_answer(
            run_id,
            agent_id=agent_id,
            after=inbound.timestamp,
            task_name="verify_missing_contact_answer",
            source_urls=missing,
            required_terms=("Jonah Reed", "Evan Cho", "Avery Cole", "3"),
            min_sources=3,
        )

    def _record_pressure_refresh(self, run_id: str, *, agent_id: str, after) -> None:
        task_name = "verify_pressure_refresh"
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        calls = _tool_calls_for_run(run_id, after=after)
        source_calls = [call for call in calls if call.tool_name == "http_request"]
        sqlite_attempts = [call for call in calls if call.tool_name == "sqlite_batch"]
        successful_sqlite = [
            call for call in sqlite_attempts
            if str(getattr(call, "status", "")).casefold() == "complete"
            and str((_result_payload(call) or {}).get("status") or "").casefold() == "ok"
        ]
        sql_values = [str((call.tool_params or {}).get("sql") or "") for call in successful_sqlite]
        combined_sql = "\n".join(sql_values)
        model_write_calls = [
            call for call in successful_sqlite
            if "contacts" in source_derived_model_mutation_tables(
                (str((call.tool_params or {}).get("sql") or ""),)
            )
        ]
        summary = summarize_sqlite_tool_result_calls(successful_sqlite)
        source_positions = [
            index for index, call in enumerate(calls)
            if call.tool_name == "http_request"
            and str(getattr(call, "status", "")).casefold() == "complete"
        ]
        model_write_positions = [
            index for index, call in enumerate(calls)
            if call in model_write_calls
        ]
        terminal_positions = [
            index for index, call in enumerate(calls)
            if call.tool_name == "send_chat_message"
            and resolved_tool_param(call, "will_continue_work") is False
            and str(getattr(call, "status", "")).casefold() == "complete"
            and not (_result_payload(call) or {}).get("skipped")
        ]
        fetch_counts = _source_fetch_counts(
            source_calls,
            tool_names={"http_request"},
            source_urls=ENRICHMENT_FEED_URLS,
        )
        scalar_json_misuse = bool(
            re.search(
                r"\bjson\s*\(\s*(?:[a-z_]\w*\.)?(?:provider_id|result_id)\s*\)"
                r"|\bjson_extract\s*\(\s*(?:[a-z_]\w*\.)?(?:provider_id|result_id)\b",
                combined_sql,
                re.I,
            )
        )
        with agent_sqlite_db(str(agent_id)) as db_path:
            conn = open_guarded_sqlite_connection(db_path)
            try:
                rows = conn.execute(
                    """
                    SELECT provider_id, full_name, account_name, owner, verified_email,
                           profile_url, source_url, result_id
                    FROM contacts
                    ORDER BY provider_id;
                    """
                ).fetchall()
            finally:
                clear_guarded_connection(conn)
                conn.close()
        expected_rows = [
            (
                provider_id,
                full_name,
                account_name,
                owner,
                email,
                f"https://profiles.example.test/people/{provider_id}",
            )
            for provider_id, full_name, account_name, owner, email in ENRICHED_CONTACTS
        ]
        modeled_rows_match = (
            len(rows) == len(expected_rows)
            and all(
                row[:6] == expected
                and row[6] in ENRICHMENT_FEED_URLS
                and bool(row[7])
                for row, expected in zip(rows, expected_rows)
            )
        )
        failures = _tool_attempt_failures(source_calls, "Enrichment fetch")
        failures.extend(_sqlite_attempt_failures(sqlite_attempts))
        failures.extend(message for failed, message in (
            (
                any(count != 1 for count in fetch_counts.values()),
                f"expected each enrichment feed once, found {fetch_counts}",
            ),
            (
                len(model_write_calls) != 1,
                f"expected one source-derived contact refresh, found {len(model_write_calls)}",
            ),
            (
                bool(source_positions)
                and bool(model_write_positions)
                and model_write_positions[0] <= max(source_positions),
                "contact refresh began before every regional result was available",
            ),
            (
                summary.aggregate_tool_result_queries < 1,
                "regional enrichment siblings were not handled as one source set",
            ),
            (
                summary.single_result_id_filters > 0,
                f"regional results were handled one result_id at a time ({summary.single_result_id_filters})",
            ),
            (
                not re.search(r"\bis_current_batch\b\s*=\s*1", combined_sql, re.I)
                or not re.search(r"\btool_name\b\s*=\s*'http_request'", combined_sql, re.I)
                or not re.search(r"\bjson_each\s*\([^;]*\$\.(?:content\.)?matches", combined_sql, re.I),
                "contact refresh did not use the complete typed current-source shape",
            ),
            (scalar_json_misuse, "plain scalar identity columns were treated as JSON"),
            (not modeled_rows_match, "contact model was incomplete, stale, or missing row provenance"),
            (
                not re.search(r"\bgroup\s+by\s+(?:[a-z_]\w*\.)?owner\b", combined_sql, re.I)
                or not re.search(r"\bverified_email\b[\s\S]*\bis\s+null\b", combined_sql, re.I),
                "contact model was not queried for missing-email coverage by owner",
            ),
            (
                len(terminal_positions) != 1,
                f"expected one terminal roster report, found {len(terminal_positions)}",
            ),
            (
                bool(terminal_positions)
                and bool(model_write_positions)
                and terminal_positions[0] <= model_write_positions[-1],
                "agent reported before refreshing the contact model",
            ),
        ) if failed)
        self._record_check(
            run_id,
            task_name,
            failures,
            "Kept cross-channel work intact and refreshed every contact from all sibling exports in one clean set.",
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
    max_single_result_filters = 0


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
    max_single_result_filters = 0
    require_working_table = True
    accept_queryable_source_model = True
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
            or re.search(
                rf"\b{state}\b[^\n]{{0,40}}\b(?:8\s*/\s*8|all\s+{eight})\b",
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


@register_scenario
class SqliteSourceCardinalityAndIdentityScenario(SqliteDomainModelScenario):
    slug = SQLITE_SOURCE_CARDINALITY_AND_IDENTITY
    version = "1.0"
    description = (
        "A bounded source batch should produce only real entities while preserving each source record's identity "
        "and associations."
    )
    expected_runtime = "short"
    tags = (*SqliteToolResultScenario.tags, "trajectory_regression", "identity_integrity")
    tasks = [
        ScenarioTask(name="inject_claim_batch", assertion_type="agent_processing"),
        ScenarioTask(name="verify_exact_source_rows", assertion_type="persisted_state"),
        ScenarioTask(name="verify_honest_claim_count", assertion_type="manual"),
    ]
    prompt = (
        "The intake batch is ready. Add every real contact in it to the existing claimed-contacts ledger, "
        "up to five if available, then tell me how many contacts were actually present and claimed. "
        f"Include the source link: {CLAIM_INTAKE_URL}"
    )

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        PersistentAgent.objects.filter(id=agent_id).update(
            charter=(
                "Maintain the sourced contact ledger for outbound operations. Keep contact identity, company, "
                "email, profile, and provenance accurate."
            ),
        )
        _seed_claimed_contacts_ledger(agent_id)
        self._enable_tools(agent_id, ("http_request",))
        inbound = self._inject_and_wait(
            run_id,
            agent_id,
            self.prompt,
            _claim_intake_mock(),
            allowed_tool_names={"http_request", "sqlite_batch", "send_chat_message"},
            max_relevant_tool_calls=6,
            task_name="inject_claim_batch",
        )
        calls = _tool_calls_for_run(run_id, after=inbound.timestamp)
        http_calls = [call for call in calls if call.tool_name == "http_request"]
        sqlite_calls = [call for call in calls if call.tool_name == "sqlite_batch"]
        with agent_sqlite_db(str(agent_id)) as db_path:
            conn = open_guarded_sqlite_connection(db_path)
            try:
                rows = conn.execute(
                    "SELECT contact_id, full_name, company, email, profile_url, source_result_id "
                    "FROM claimed_contacts ORDER BY contact_id;"
                ).fetchall()
            finally:
                clear_guarded_connection(conn)
                conn.close()

        expected_result_ids = (
            set(build_short_result_id_map([str(http_calls[0].step_id)]).values())
            if len(http_calls) == 1
            else set()
        )
        expected_rows = sorted(CLAIM_INTAKE_CONTACTS)
        actual_rows = sorted(tuple(row[:5]) for row in rows)
        actual_result_ids = {str(row[5]) for row in rows}
        failures = _tool_attempt_failures(http_calls, "Source fetch")
        failures.extend(_sqlite_attempt_failures(sqlite_calls))
        failures.extend(
            message
            for failed, message in (
                (len(http_calls) != 1, f"expected one source fetch, found {len(http_calls)}"),
                (not sqlite_calls, "no SQLite model write observed"),
                (actual_rows != expected_rows, f"persisted rows were {rows!r}"),
                (
                    actual_result_ids != expected_result_ids,
                    f"source result ids were {sorted(actual_result_ids)!r}; expected {sorted(expected_result_ids)!r}",
                ),
            )
            if failed
        )
        self._record_check(
            run_id,
            "verify_exact_source_rows",
            failures,
            "Persisted exactly the two real contacts with intact identity, associations, and provenance.",
        )

        outbound = _outbound_messages_after(agent_id, inbound.timestamp)
        body = outbound[-1].body if outbound else ""
        honest_count = (
            len(outbound) == 1
            and bool(re.search(r"\b(?:2|two)\b", body or "", re.I))
            and CLAIM_INTAKE_URL in (body or "")
            and not re.search(r"\b(?:unused|placeholder|unknown[- ]?[345])\b", body or "", re.I)
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if honest_count else EvalRunTask.Status.FAILED,
            task_name="verify_honest_claim_count",
            observed_summary=(
                "Reported the actual two-contact source cardinality."
                if honest_count
                else f"Expected one sourced answer reporting two real contacts; body={body!r}."
            ),
            artifacts={"message": outbound[-1]} if outbound else {},
        )


@register_scenario
class SqliteFreshPeerFactOverEmptyModelScenario(SqliteDomainModelScenario):
    slug = SQLITE_FRESH_PEER_FACT_OVER_EMPTY_MODEL
    version = "1.0"
    description = (
        "Fresh peer evidence should update an empty local model rather than being discounted as absent or false."
    )
    expected_runtime = "short"
    tags = (*SqliteToolResultScenario.tags, "trajectory_regression", "peer_handoff")
    tasks = [
        ScenarioTask(name="inject_handoff_request", assertion_type="agent_processing"),
        ScenarioTask(name="verify_peer_fact_modeled", assertion_type="persisted_state"),
        ScenarioTask(name="verify_current_handoff_answer", assertion_type="manual"),
    ]
    prompt = (
        "Who currently owns launch readiness, and when is it due? Check the handoff picture you maintain "
        "and give me the confirmed assignment."
    )

    def run(self, run_id: str, agent_id: str) -> None:
        from api.evals.scenarios.responsibility_boundaries import ResponsibilityBoundaryScenario

        self._ready_agent(agent_id)
        PersistentAgent.objects.filter(id=agent_id).update(
            charter="Maintain the current launch handoff ledger and report confirmed ownership and due dates.",
        )
        _seed_empty_launch_handoffs(agent_id)
        agent = PersistentAgent.objects.get(id=agent_id)
        ResponsibilityBoundaryScenario._peer_inbound(
            agent,
            run_id,
            (
                "Confirmed handoff: work_key=launch-readiness, owner=Maya Chen, due_on=Friday, "
                "source_ref=peer-note-418. Please keep the operating picture current."
            ),
        )
        inbound = self._inject_and_wait(
            run_id,
            agent_id,
            self.prompt,
            {},
            allowed_tool_names={"sqlite_batch", "send_chat_message"},
            max_relevant_tool_calls=5,
            task_name="inject_handoff_request",
        )
        calls = _tool_calls_for_run(run_id, after=inbound.timestamp)
        sqlite_calls = [call for call in calls if call.tool_name == "sqlite_batch"]
        with agent_sqlite_db(str(agent_id)) as db_path:
            conn = open_guarded_sqlite_connection(db_path)
            try:
                rows = conn.execute(
                    "SELECT work_key, owner, due_on, source_kind, source_ref "
                    "FROM launch_handoffs ORDER BY work_key;"
                ).fetchall()
            finally:
                clear_guarded_connection(conn)
                conn.close()

        modeled_handoff = (
            len(rows) == 1
            and tuple(rows[0][:3]) == ("launch-readiness", "Maya Chen", "Friday")
            and bool(str(rows[0][3]).strip())
            and rows[0][4] == "peer-note-418"
        )
        failures = _sqlite_attempt_failures(sqlite_calls)
        failures.extend(
            message
            for failed, message in (
                (not sqlite_calls, "no SQLite handoff reconciliation observed"),
                (not modeled_handoff, f"persisted handoffs were {rows!r}"),
            )
            if failed
        )
        self._record_check(
            run_id,
            "verify_peer_fact_modeled",
            failures,
            "Reconciled the fresh peer handoff into the previously empty local model.",
        )

        outbound = _outbound_messages_after(agent_id, inbound.timestamp)
        body = outbound[-1].body if outbound else ""
        current_answer = (
            len(outbound) == 1
            and "maya chen" in (body or "").casefold()
            and "friday" in (body or "").casefold()
            and not re.search(r"\b(?:no|not|without)\s+(?:confirmed\s+)?(?:assignment|owner|evidence)\b", body or "", re.I)
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if current_answer else EvalRunTask.Status.FAILED,
            task_name="verify_current_handoff_answer",
            observed_summary=(
                "Reported the fresh confirmed owner and due date."
                if current_answer
                else f"Expected one answer naming Maya Chen and Friday; body={body!r}."
            ),
            artifacts={"message": outbound[-1]} if outbound else {},
        )


@register_scenario
class SqliteStructuredPeerEventPersistenceScenario(SqliteDomainModelScenario):
    slug = SQLITE_STRUCTURED_PEER_EVENT_PERSISTENCE
    version = "1.1"
    description = (
        "A structured peer event should be durably modeled and read back before the agent reports its outcome."
    )
    expected_runtime = "short"
    tags = (*SqliteToolResultScenario.tags, "trajectory_regression", "peer_handoff", "event_truth")
    tasks = [
        ScenarioTask(name="inject_event_request", assertion_type="agent_processing"),
        ScenarioTask(name="verify_structured_event_modeled", assertion_type="persisted_state"),
        ScenarioTask(name="verify_persisted_outcome_reported", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        from api.evals.scenarios.responsibility_boundaries import ResponsibilityBoundaryScenario

        self._ready_agent(agent_id)
        PersistentAgent.objects.filter(id=agent_id).update(
            charter=(
                "Maintain the central operational event ledger. Ingest structured peer events idempotently into "
                "operational_events before reporting outcomes. The source_message_id is the receiving peer message's "
                "message_id. Missing provider evidence remains null, never invented. Report counts from the modeled "
                "ledger, not memory."
            ),
        )
        _seed_empty_operational_events(agent_id)
        agent = PersistentAgent.objects.get(id=agent_id)
        peer_event = ResponsibilityBoundaryScenario._peer_inbound(
            agent,
            run_id,
            "A finalized accepted-setup event is attached structurally. Persist it before reporting.",
        )
        peer_event.raw_payload = {
            **(peer_event.raw_payload or {}),
            "structured_payload": {
                "kind": "operational_event",
                "event_id": "evt-2048",
                "event_type": "accepted_setup",
                "thread_key": "thread-2048",
                "occurred_at": "2026-07-28T15:42:00Z",
            },
        }
        peer_event.save(update_fields=["raw_payload"])

        inbound = self._inject_and_wait(
            run_id,
            agent_id,
            (
                "Give me the current accepted-setup count and identify the confirmed thread. "
                "Do not report it until the peer event is durably recorded."
            ),
            {},
            allowed_tool_names={"sqlite_batch", "send_chat_message"},
            max_relevant_tool_calls=6,
            task_name="inject_event_request",
        )
        calls = _tool_calls_for_run(run_id, after=inbound.timestamp)
        sqlite_calls = [call for call in calls if call.tool_name == "sqlite_batch"]
        sql = "\n".join(str((call.tool_params or {}).get("sql") or "") for call in sqlite_calls)
        statement_entries = [
            (call_index, statement_index, call, statement)
            for call_index, call in enumerate(sqlite_calls)
            for statement_index, statement in enumerate(
                sqlparse.split(str((call.tool_params or {}).get("sql") or ""))
            )
            if statement.strip()
        ]
        write_entries = [
            (call_index, statement_index, call, statement)
            for call_index, statement_index, call, statement in statement_entries
            if (
                (match := _MUTATION_TARGET_RE.search(statement))
                and match.group("table").casefold() == "operational_events"
            )
        ]
        write_entry = write_entries[0] if write_entries else None
        write_call_index, write_statement_index, write_call, write_statement = (
            write_entry or (-1, -1, None, "")
        )
        read_after_write = write_entry is not None and any(
            call_index == write_call_index
            and statement_index > write_statement_index
            and re.search(r"\bselect\b", statement, re.I)
            and _reads_table(statement, "operational_events")
            for call_index, statement_index, _call, statement in statement_entries
        )
        expected_payload = {
            "event_id": "evt-2048",
            "event_type": "accepted_setup",
            "thread_key": "thread-2048",
            "occurred_at": "2026-07-28T15:42:00Z",
        }
        statement_without_comments = sqlparse.format(write_statement, strip_comments=True)
        direct_message_import = (
            write_entry is not None
            and _reads_table(write_statement, "__messages")
            and "structured_payload_json" in write_statement.casefold()
            and not any(
                value in statement_without_comments
                for value in (*expected_payload.values(), str(peer_event.id))
            )
        )
        bound_message_import = (
            write_call is not None
            and _uses_bound_source_values(
                write_call,
                write_statement,
                {str(peer_event.id)},
            )
            and (
                bound_payload_placeholder := _bound_json_payload_placeholder(
                    write_call,
                    write_statement,
                    expected_payload,
                )
            )
            and _insert_values_derive_bound_payload_fields(
                write_statement,
                table_name="operational_events",
                placeholder=bound_payload_placeholder,
                expected_fields=set(expected_payload),
            )
        )
        message_grounded_import = direct_message_import or bound_message_import

        with agent_sqlite_db(str(agent_id)) as db_path:
            conn = open_guarded_sqlite_connection(db_path)
            try:
                rows = conn.execute(
                    "SELECT event_id, event_type, thread_key, occurred_at, "
                    "provider_message_id, source_message_id FROM operational_events;"
                ).fetchall()
            finally:
                clear_guarded_connection(conn)
                conn.close()

        expected = [
            (
                "evt-2048",
                "accepted_setup",
                "thread-2048",
                "2026-07-28T15:42:00Z",
                None,
                str(peer_event.id),
            )
        ]
        failures = _sqlite_attempt_failures(sqlite_calls)
        failures.extend(
            message
            for failed, message in (
                (not sqlite_calls, "no SQLite event ingestion observed"),
                (
                    not message_grounded_import,
                    "event write neither derived from __messages nor safely bound an inspected structured payload",
                ),
                (not read_after_write, "event write was not read back before reporting"),
                (rows != expected, f"persisted operational events were {rows!r}"),
            )
            if failed
        )
        self._record_check(
            run_id,
            "verify_structured_event_modeled",
            failures,
            "Persisted the exact inspected peer event without SQL literals, retained provenance, and read it back.",
        )

        outbound = _outbound_messages_after(agent_id, inbound.timestamp)
        body = outbound[-1].body if outbound else ""
        reported_persisted_truth = (
            len(outbound) == 1
            and "thread-2048" in (body or "").casefold()
            and "accepted" in (body or "").casefold()
            and bool(re.search(r"\b(?:1|one)\b", body or "", re.I))
            and not re.search(r"\b(?:0|zero|none)\b", body or "", re.I)
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if reported_persisted_truth else EvalRunTask.Status.FAILED,
            task_name="verify_persisted_outcome_reported",
            observed_summary=(
                "Reported the one durably modeled accepted setup."
                if reported_persisted_truth
                else f"Expected one accepted setup for thread-2048 after persistence; body={body!r}."
            ),
            artifacts={"message": outbound[-1]} if outbound else {},
        )


@register_scenario
class SqlitePeerOutcomeReconcilesCanonicalModelScenario(SqliteDomainModelScenario):
    slug = SQLITE_PEER_OUTCOME_RECONCILES_CANONICAL_MODEL
    version = "1.1"
    description = (
        "A manager should reconcile a fresh seller outcome into canonical state before counting or selecting next work."
    )
    expected_runtime = "short"
    tags = (*SqliteToolResultScenario.tags, "trajectory_regression", "peer_handoff", "event_truth")
    tasks = [
        ScenarioTask(name="inject_board_request", assertion_type="agent_processing"),
        ScenarioTask(name="verify_canonical_outcome", assertion_type="persisted_state"),
        ScenarioTask(name="verify_current_board_answer", assertion_type="manual"),
    ]

    prompt = "Give me the current delivery-state counts, then tell me which recipient is next in the queue."
    peer_name_prefix = "Outreach Seller"
    peer_charter = "Own assigned prospect outreach and report exact delivery outcomes to the outbound manager."
    outcome_state = "bounced"

    def run(self, run_id: str, agent_id: str) -> None:
        from api.evals.scenarios.responsibility_boundaries import ResponsibilityBoundaryScenario

        self._ready_agent(agent_id)
        PersistentAgent.objects.filter(id=agent_id).update(
            charter=(
                "Manage the outbound team from the canonical outreach ledger. Seller outcome updates are operating "
                "inputs. Keep recipient ownership unique, report the current board, and choose follow-on work only "
                "from recipients that remain prepared."
            ),
        )
        _seed_outreach_reconciliation_model(agent_id)
        agent = PersistentAgent.objects.get(id=agent_id)
        peer_event = ResponsibilityBoundaryScenario._peer_inbound(
            agent,
            run_id,
            "A finalized outreach outcome is attached structurally. Reconcile it before reporting the board.",
            peer_name_prefix=self.peer_name_prefix,
            peer_charter=self.peer_charter,
        )
        peer_event.raw_payload = {
            **(peer_event.raw_payload or {}),
            "structured_payload": {
                "kind": "outreach_outcome",
                "recipient": "jordan@northstar.example.test",
                "delivery_status": self.outcome_state,
                "provider_message_id": "provider-message-998",
                "sent_at": "2026-07-30T14:12:09Z",
            },
        }
        peer_event.save(update_fields=["raw_payload"])
        inbound = self._inject_and_wait(
            run_id,
            agent_id,
            self.prompt,
            {},
            allowed_tool_names={"sqlite_batch", "send_chat_message"},
            max_relevant_tool_calls=7,
            task_name="inject_board_request",
        )
        calls = _tool_calls_for_run(run_id, after=inbound.timestamp)
        sqlite_calls = [call for call in calls if call.tool_name == "sqlite_batch"]
        statements = [
            (call_index, statement_index, call, statement)
            for call_index, call in enumerate(sqlite_calls)
            for statement_index, statement in enumerate(
                sqlparse.split(str((call.tool_params or {}).get("sql") or ""))
            )
            if statement.strip()
        ]
        writes = [
            item
            for item in statements
            if _mutation_target_table(item[3]) == "outreach_threads"
        ]
        write = writes[0] if writes else None
        write_call_index, write_statement_index, write_call, write_sql = (
            write or (-1, -1, None, "")
        )
        read_after_write = write is not None and any(
            call_index == write_call_index
            and statement_index > write_statement_index
            and _reads_table(statement, "outreach_threads")
            for call_index, statement_index, _call, statement in statements
        )
        read_after_write = read_after_write or (
            write is not None
            and "returning" in write_sql.casefold()
            and "recipient" in write_sql.casefold()
            and "state" in write_sql.casefold()
        )
        expected_bound_values = {
            "jordan@northstar.example.test",
            self.outcome_state,
            "provider-message-998",
            "2026-07-30T14:12:09Z",
        }
        bound_write = (
            write_call is not None
            and _uses_bound_source_values(write_call, write_sql, expected_bound_values)
        )
        structured_write = (
            write_call is not None
            and _derives_structured_message_fields(
                write_sql,
                {"recipient", "delivery_status", "provider_message_id", "sent_at"},
            )
        )
        bound_payload_write = (
            write_call is not None
            and _derives_bound_structured_message_fields(
                write_call,
                write_sql,
                {
                    "recipient": "jordan@northstar.example.test",
                    "delivery_status": self.outcome_state,
                    "provider_message_id": "provider-message-998",
                    "sent_at": "2026-07-30T14:12:09Z",
                },
            )
        )
        grounded_write = bound_write or structured_write or bound_payload_write

        with agent_sqlite_db(str(agent_id)) as db_path:
            conn = open_guarded_sqlite_connection(db_path)
            try:
                rows = conn.execute(
                    "SELECT thread_id, recipient, state, provider_message_id, sent_at "
                    "FROM outreach_threads ORDER BY thread_id;"
                ).fetchall()
            finally:
                clear_guarded_connection(conn)
                conn.close()

        expected_rows = [
            (
                "manager:wave:prospect-77",
                "jordan@northstar.example.test",
                self.outcome_state,
                "provider-message-998",
                "2026-07-30T14:12:09Z",
            ),
            (
                "manager:wave:prospect-78",
                "avery@harbor.example.test",
                "prepared",
                None,
                None,
            ),
        ]
        failures = _sqlite_attempt_failures(sqlite_calls)
        failures.extend(
            message
            for failed, message in (
                (not sqlite_calls, "no SQLite board reconciliation observed"),
                (len(writes) != 1, f"expected one canonical thread write, saw {len(writes)}"),
                (not grounded_write, "seller outcome was not grounded in the peer message"),
                (not read_after_write, "canonical state was not read back in the write batch"),
                (rows != expected_rows, f"canonical outreach rows were {rows!r}"),
            )
            if failed
        )
        self._record_check(
            run_id,
            "verify_canonical_outcome",
            failures,
            "Reconciled the seller outcome into the existing recipient row and read back current state.",
        )

        outbound = _outbound_messages_after(agent_id, inbound.timestamp)
        body = outbound[-1].body if outbound else ""
        answer_choice, answer_reasoning = self.llm_judge(
            question=(
                "Does the response report exactly one bounced recipient and one prepared recipient, and identify "
                "Avery at Harbor as the next queued recipient? It may also identify Jordan as the bounced recipient."
            ),
            context=f"Response:\n{body or '(none)'}",
            options=["Correct current board", "Incorrect or ambiguous board"],
        )
        current_answer = len(outbound) == 1 and answer_choice == "Correct current board"
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if current_answer else EvalRunTask.Status.FAILED,
            task_name="verify_current_board_answer",
            observed_summary=(
                "Reported the reconciled board and selected only the remaining prepared recipient."
                if current_answer
                else (
                    f"Expected one bounced, one prepared, and Avery next; "
                    f"judge={answer_choice}: {answer_reasoning}; body={body!r}."
                )
            ),
            artifacts={"message": outbound[-1]} if outbound else {},
        )
