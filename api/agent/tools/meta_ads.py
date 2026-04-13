"""Read-only Meta Ads system tool."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import logging
import sqlite3
from typing import Any, Optional
import uuid

import requests
from django.urls import reverse
from requests import Response
from requests.exceptions import RequestException

from api.agent.system_skills.registry import get_system_skill_definition
from api.models import PersistentAgent
from api.services.system_skill_profiles import resolve_system_skill_profile_for_agent
from config import settings
from .sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from .sqlite_state import get_sqlite_db_path


logger = logging.getLogger(__name__)

SYSTEM_SKILL_KEY = "meta_ads_platform"
GRAPH_BASE_URL = "https://graph.facebook.com"
REQUEST_TIMEOUT_SECONDS = 30
RATE_LIMIT_HEADER_NAMES = {
    "x-ad-account-usage",
    "x-app-usage",
    "x-business-use-case",
    "x-business-use-case-usage",
    "x-fb-ads-insights-throttle",
    "x-fb-trace-id",
}

DEFAULT_ACCOUNT_FIELDS = [
    "id",
    "account_id",
    "name",
    "account_status",
    "currency",
    "timezone_name",
]
DEFAULT_CAMPAIGN_FIELDS = [
    "id",
    "name",
    "status",
    "effective_status",
    "objective",
    "daily_budget",
    "lifetime_budget",
    "start_time",
    "stop_time",
]
DEFAULT_INSIGHTS_FIELDS = [
    "account_id",
    "account_name",
    "campaign_id",
    "campaign_name",
    "impressions",
    "reach",
    "spend",
    "clicks",
    "date_start",
    "date_stop",
]
DEFAULT_PERFORMANCE_FIELDS = [
    "account_id",
    "account_name",
    "campaign_id",
    "campaign_name",
    "adset_id",
    "adset_name",
    "ad_id",
    "ad_name",
    "objective",
    "status",
    "effective_status",
    "impressions",
    "reach",
    "spend",
    "clicks",
    "inline_link_clicks",
    "ctr",
    "cpc",
    "cpm",
    "frequency",
    "actions",
    "action_values",
    "purchase_roas",
    "date_start",
    "date_stop",
]
STANDARD_ACTION_PATTERNS = {
    "purchase": ("purchase", "omni_purchase"),
    "lead": ("lead",),
    "complete_registration": ("complete_registration",),
    "add_to_cart": ("add_to_cart",),
    "initiate_checkout": ("initiate_checkout",),
    "view_content": ("view_content",),
    "landing_page_view": ("landing_page_view",),
    "link_click": ("link_click", "inline_link_click"),
    "subscribe": ("subscribe",),
}
META_ADS_SYNC_RUNS_TABLE = "meta_ads_sync_runs"
META_ADS_RAW_TABLE = "meta_ads_raw"
META_ADS_PERFORMANCE_TABLE = "meta_ads_performance"
META_ADS_CONVERSION_QUALITY_TABLE = "meta_ads_conversion_quality"


def get_meta_ads_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "meta_ads",
            "description": (
                "Read Meta Ads account, campaign, and insights data for a configured Meta Ads profile. "
                "Use profile_key when you need a non-default credential profile. Data operations sync full datasets "
                "directly into the shared agent SQLite DB so the agent can analyze them with sqlite_batch without "
                "shuttling rows through context. If setup is incomplete, help the user finish onboarding before retrying."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "doctor",
                            "accounts",
                            "campaigns",
                            "insights",
                            "performance_snapshot",
                            "performance_timeseries",
                            "conversion_quality",
                        ],
                        "description": "Meta Ads action to perform.",
                    },
                    "profile_key": {
                        "type": "string",
                        "description": "Optional named Meta Ads profile to use.",
                    },
                    "account_id": {
                        "type": "string",
                        "description": "Optional ad account override, for example act_1234567890.",
                    },
                    "business_id": {
                        "type": "string",
                        "description": "Optional business ID override for listing owned ad accounts.",
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional Graph fields override.",
                    },
                    "page_size": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "description": "Page size for paged list requests.",
                    },
                    "fetch_all": {
                        "type": "boolean",
                        "description": "When true, keep following Graph pagination cursors.",
                    },
                    "level": {
                        "type": "string",
                        "enum": ["account", "campaign", "adset", "ad"],
                        "description": "Insights aggregation level.",
                    },
                    "date_preset": {
                        "type": "string",
                        "description": "Meta date preset for insights when since/until are omitted.",
                    },
                    "since": {
                        "type": "string",
                        "description": "Insights start date in YYYY-MM-DD format.",
                    },
                    "until": {
                        "type": "string",
                        "description": "Insights end date in YYYY-MM-DD format.",
                    },
                    "breakdowns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional insights breakdowns.",
                    },
                    "action_breakdowns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional Meta action_breakdowns values for action metrics.",
                    },
                    "summary_action_breakdowns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional Meta summary_action_breakdowns values for action metrics.",
                    },
                    "action_attribution_windows": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional attribution windows for action metrics, for example 1d_click or 7d_click.",
                    },
                    "time_increment": {
                        "type": ["integer", "string"],
                        "description": "Optional Meta time_increment, for example 1 for daily or monthly.",
                    },
                    "filtering": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Optional Meta filtering objects applied to insights queries.",
                    },
                    "sort": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional Meta sort fields for insights queries.",
                    },
                    "dataset_id": {
                        "type": "string",
                        "description": "Optional Meta Pixel or dataset ID for conversion-quality monitoring.",
                    },
                    "agent_name": {
                        "type": "string",
                        "description": "Optional Dataset Quality agent_name filter for partner or platform attribution.",
                    },
                    "destination_table": {
                        "type": "string",
                        "description": "Optional SQLite destination table. Must be a safe SQL identifier. If omitted, each operation uses its default Meta table.",
                    },
                    "return_rows": {
                        "type": "boolean",
                        "description": "Optional. When true, include inline rows in addition to syncing the dataset into SQLite. Leave false for normal monitoring flows.",
                    },
                },
                "required": ["operation"],
            },
        },
    }


def _normalize_api_version(value: Optional[str]) -> str:
    version = str(value or "v25.0").strip()
    if not version.startswith("v"):
        version = f"v{version}"
    return version


def _normalize_account_id(account_id: str) -> str:
    value = str(account_id or "").strip()
    if not value:
        return value
    return value if value.startswith("act_") else f"act_{value}"


def _normalize_dataset_id(dataset_id: Optional[str]) -> str:
    return str(dataset_id or "").strip()


def _string_list(value: Any, *, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return list(default)


def _extract_rate_limit_headers(response: Response) -> dict[str, str]:
    return {
        key.lower(): value
        for key, value in response.headers.items()
        if key.lower() in RATE_LIMIT_HEADER_NAMES
    }


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _to_float(value: Any) -> Optional[float]:
    decimal_value = _to_decimal(value)
    if decimal_value is None:
        return None
    return float(decimal_value)


def _clean_action_key(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _metric_sequence_to_map(value: Any) -> dict[str, float]:
    if not isinstance(value, list):
        return {}
    metrics: dict[str, float] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        key = str(item.get("action_type") or item.get("key") or "").strip()
        metric_value = _to_float(item.get("value"))
        if not key or metric_value is None:
            continue
        metrics[key] = round(metrics.get(key, 0.0) + metric_value, 4)
    return metrics


def _aggregate_standard_actions(metrics: dict[str, float]) -> dict[str, float]:
    aggregated: dict[str, float] = {}
    cleaned_items = [(_clean_action_key(key), value) for key, value in metrics.items()]
    for canonical_key, patterns in STANDARD_ACTION_PATTERNS.items():
        total = 0.0
        for cleaned_key, value in cleaned_items:
            if any(pattern in cleaned_key for pattern in patterns):
                total += value
        if total:
            aggregated[canonical_key] = round(total, 4)
    return aggregated


def _safe_ratio(numerator: Optional[float], denominator: Optional[float], *, multiplier: float = 1.0) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return round((numerator / denominator) * multiplier, 4)


def _entity_identity_for_level(row: dict[str, Any], level: str) -> tuple[Optional[str], Optional[str]]:
    if level == "account":
        return row.get("account_id"), row.get("account_name")
    return row.get(f"{level}_id"), row.get(f"{level}_name")


def _normalize_performance_row(row: dict[str, Any], *, level: str) -> dict[str, Any]:
    action_metrics = _metric_sequence_to_map(row.get("actions"))
    action_value_metrics = _metric_sequence_to_map(row.get("action_values"))
    purchase_roas = _metric_sequence_to_map(row.get("purchase_roas"))
    standard_actions = _aggregate_standard_actions(action_metrics)
    standard_action_values = _aggregate_standard_actions(action_value_metrics)
    standard_purchase_roas = _aggregate_standard_actions(purchase_roas)

    spend = _to_float(row.get("spend"))
    impressions = _to_float(row.get("impressions"))
    clicks = _to_float(row.get("clicks"))
    inline_link_clicks = _to_float(row.get("inline_link_clicks"))
    ctr = _to_float(row.get("ctr")) or _safe_ratio(clicks, impressions, multiplier=100.0)
    cpc = _to_float(row.get("cpc")) or _safe_ratio(spend, clicks)
    cpm = _to_float(row.get("cpm")) or _safe_ratio(spend, impressions, multiplier=1000.0)
    reach = _to_float(row.get("reach"))
    frequency = _to_float(row.get("frequency"))
    purchase_count = standard_actions.get("purchase")
    lead_count = standard_actions.get("lead")
    purchase_value = standard_action_values.get("purchase")
    blended_roas = standard_purchase_roas.get("purchase")
    if blended_roas is None:
        blended_roas = _safe_ratio(purchase_value, spend)

    entity_id, entity_name = _entity_identity_for_level(row, level)
    return {
        "entity_level": level,
        "entity_id": entity_id,
        "entity_name": entity_name,
        "date_start": row.get("date_start"),
        "date_stop": row.get("date_stop"),
        "objective": row.get("objective"),
        "status": row.get("status"),
        "effective_status": row.get("effective_status"),
        "spend": spend,
        "impressions": impressions,
        "reach": reach,
        "clicks": clicks,
        "inline_link_clicks": inline_link_clicks,
        "ctr": ctr,
        "cpc": cpc,
        "cpm": cpm,
        "frequency": frequency,
        "action_metrics": action_metrics,
        "action_value_metrics": action_value_metrics,
        "purchase_roas": purchase_roas,
        "standard_purchase_roas": standard_purchase_roas,
        "standard_actions": standard_actions,
        "standard_action_values": standard_action_values,
        "derived_metrics": {
            "purchase_count": purchase_count,
            "lead_count": lead_count,
            "purchase_value": purchase_value,
            "blended_roas": blended_roas,
            "cost_per_purchase": _safe_ratio(spend, purchase_count),
            "cost_per_lead": _safe_ratio(spend, lead_count),
        },
    }


def _summarize_normalized_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "row_count": 0,
            "spend": 0.0,
            "impressions": 0.0,
            "clicks": 0.0,
            "purchase_count": 0.0,
            "lead_count": 0.0,
            "purchase_value": 0.0,
            "ctr": None,
            "cpc": None,
            "cpm": None,
            "blended_roas": None,
            "cost_per_purchase": None,
            "cost_per_lead": None,
        }

    spend = sum(row.get("spend") or 0.0 for row in rows)
    impressions = sum(row.get("impressions") or 0.0 for row in rows)
    clicks = sum(row.get("clicks") or 0.0 for row in rows)
    purchase_count = sum((row.get("derived_metrics") or {}).get("purchase_count") or 0.0 for row in rows)
    lead_count = sum((row.get("derived_metrics") or {}).get("lead_count") or 0.0 for row in rows)
    purchase_value = sum((row.get("derived_metrics") or {}).get("purchase_value") or 0.0 for row in rows)
    return {
        "row_count": len(rows),
        "spend": round(spend, 4),
        "impressions": round(impressions, 4),
        "clicks": round(clicks, 4),
        "purchase_count": round(purchase_count, 4),
        "lead_count": round(lead_count, 4),
        "purchase_value": round(purchase_value, 4),
        "ctr": _safe_ratio(clicks, impressions, multiplier=100.0),
        "cpc": _safe_ratio(spend, clicks),
        "cpm": _safe_ratio(spend, impressions, multiplier=1000.0),
        "blended_roas": _safe_ratio(purchase_value, spend),
        "cost_per_purchase": _safe_ratio(spend, purchase_count),
        "cost_per_lead": _safe_ratio(spend, lead_count),
    }


def _normalize_dataset_quality_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("web"), list):
        return []

    normalized: list[dict[str, Any]] = []
    for row in payload["web"]:
        if not isinstance(row, dict):
            continue
        event_match_quality = row.get("event_match_quality")
        diagnostics = []
        if isinstance(event_match_quality, dict) and isinstance(event_match_quality.get("diagnostics"), list):
            diagnostics = event_match_quality["diagnostics"]
        data_freshness = row.get("data_freshness") if isinstance(row.get("data_freshness"), dict) else {}
        event_coverage = row.get("event_coverage") if isinstance(row.get("event_coverage"), dict) else {}
        acr = row.get("acr") if isinstance(row.get("acr"), dict) else {}
        event_acr = row.get("event_potential_aly_acr_increase") if isinstance(row.get("event_potential_aly_acr_increase"), dict) else {}
        dedupe_feedback = row.get("dedupe_key_feedback") if isinstance(row.get("dedupe_key_feedback"), list) else []
        normalized.append(
            {
                "event_name": row.get("event_name"),
                "event_match_quality_score": _to_float(
                    event_match_quality.get("composite_score") if isinstance(event_match_quality, dict) else None
                ),
                "acr_percentage": _to_float(acr.get("percentage")),
                "event_potential_acr_percentage": _to_float(event_acr.get("percentage")),
                "upload_frequency": data_freshness.get("upload_frequency"),
                "coverage": event_coverage,
                "diagnostics": diagnostics,
                "diagnostics_count": len(diagnostics),
                "dedupe_key_feedback": dedupe_feedback,
            }
        )
    return normalized


def _dataset_quality_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "event_count": 0,
            "avg_event_match_quality_score": None,
            "events_with_diagnostics": 0,
            "realtime_event_count": 0,
        }

    emq_scores = [row["event_match_quality_score"] for row in rows if row.get("event_match_quality_score") is not None]
    realtime_event_count = sum(1 for row in rows if row.get("upload_frequency") == "real_time")
    return {
        "event_count": len(rows),
        "avg_event_match_quality_score": round(sum(emq_scores) / len(emq_scores), 4) if emq_scores else None,
        "events_with_diagnostics": sum(1 for row in rows if row.get("diagnostics_count")),
        "realtime_event_count": realtime_event_count,
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _optional_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def _return_rows_requested(params: dict[str, Any]) -> bool:
    return _optional_bool(params.get("return_rows")) is True


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _escape_sql_string(value: str) -> str:
    return str(value or "").replace("'", "''")


def _validate_destination_table(
    table_name: Optional[str],
    *,
    default: str,
    reserved_names: Optional[set[str]] = None,
) -> str:
    candidate = str(table_name or "").strip() or default
    if not candidate:
        raise ValueError("Destination table name cannot be empty.")
    if len(candidate) > 64:
        raise ValueError("Destination table name must be 64 characters or fewer.")
    if candidate.startswith("__") or candidate.lower().startswith("sqlite_"):
        raise ValueError("Destination table name is reserved.")
    if not candidate[0].isalpha():
        raise ValueError("Destination table name must start with a letter.")
    for char in candidate:
        if not (char.isalnum() or char == "_"):
            raise ValueError("Destination table name may contain only letters, numbers, and underscores.")
    normalized_reserved_names = {name.lower() for name in reserved_names or set()}
    if candidate.lower() in normalized_reserved_names:
        raise ValueError("Destination table name is reserved for a different Meta sync schema.")
    return candidate


def _quoted_identifier(identifier: str) -> str:
    return f'"{identifier}"'


def _build_query_signature(operation: str, context: dict[str, Any]) -> str:
    payload = {"operation": operation, **context}
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()[:24]


def _build_sync_run_id() -> str:
    return uuid.uuid4().hex


def _open_meta_ads_sqlite_connection() -> sqlite3.Connection:
    db_path = get_sqlite_db_path()
    if not db_path:
        raise sqlite3.OperationalError("Agent SQLite database context is required for Meta Ads sync.")
    return open_guarded_sqlite_connection(db_path)


def _ensure_meta_ads_sync_runs_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS "{META_ADS_SYNC_RUNS_TABLE}" (
            sync_run_id TEXT PRIMARY KEY,
            synced_at TEXT NOT NULL,
            skill_key TEXT NOT NULL,
            operation TEXT NOT NULL,
            profile_key TEXT NOT NULL,
            destination_table TEXT NOT NULL,
            query_signature TEXT NOT NULL,
            account_id TEXT,
            dataset_id TEXT,
            row_count INTEGER NOT NULL DEFAULT 0,
            query_params_json TEXT NOT NULL DEFAULT '{{}}',
            summary_json TEXT NOT NULL DEFAULT '{{}}',
            rate_limit_headers_json TEXT NOT NULL DEFAULT '{{}}'
        );
        CREATE INDEX IF NOT EXISTS idx_meta_ads_sync_runs_profile_time
            ON "{META_ADS_SYNC_RUNS_TABLE}" (profile_key, synced_at DESC);
        CREATE INDEX IF NOT EXISTS idx_meta_ads_sync_runs_query_signature
            ON "{META_ADS_SYNC_RUNS_TABLE}" (query_signature);
        """
    )


def _record_meta_ads_sync_run(
    conn: sqlite3.Connection,
    *,
    sync_run_id: str,
    synced_at: str,
    operation: str,
    profile_key: str,
    destination_table: str,
    query_signature: str,
    account_id: Optional[str],
    dataset_id: Optional[str],
    row_count: int,
    query_params: dict[str, Any],
    summary: dict[str, Any],
    rate_limit_headers: dict[str, str],
) -> None:
    _ensure_meta_ads_sync_runs_table(conn)
    conn.execute(
        f"""
        INSERT INTO "{META_ADS_SYNC_RUNS_TABLE}" (
            sync_run_id,
            synced_at,
            skill_key,
            operation,
            profile_key,
            destination_table,
            query_signature,
            account_id,
            dataset_id,
            row_count,
            query_params_json,
            summary_json,
            rate_limit_headers_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            sync_run_id,
            synced_at,
            SYSTEM_SKILL_KEY,
            operation,
            profile_key,
            destination_table,
            query_signature,
            account_id,
            dataset_id,
            row_count,
            _canonical_json(query_params),
            _canonical_json(summary),
            _canonical_json(rate_limit_headers),
        ),
    )


def _base_sync_response(
    *,
    result: str,
    operation: str,
    profile_key: str,
    destination_table: str,
    sync_run_id: str,
    query_signature: str,
    rows_synced: int,
    rate_limit_headers: dict[str, str],
    summary: Optional[dict[str, Any]] = None,
    account_id: Optional[str] = None,
    dataset_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "status": "success",
        "result": result,
        "operation": operation,
        "profile_key": profile_key,
        "destination_table": destination_table,
        "sync_run_id": sync_run_id,
        "query_signature": query_signature,
        "rows_synced": rows_synced,
        "rate_limit_headers": rate_limit_headers,
        "sqlite_tables": {
            "primary": destination_table,
            "sync_runs": META_ADS_SYNC_RUNS_TABLE,
        },
    }
    if account_id:
        response["account_id"] = account_id
    if dataset_id:
        response["dataset_id"] = dataset_id
    if summary is not None:
        response["summary"] = summary
    if extra:
        response.update(extra)
    return response


def _sync_raw_meta_rows(
    *,
    operation: str,
    profile_key: str,
    rows: list[dict[str, Any]],
    params: dict[str, Any],
    rate_limit_headers: dict[str, str],
    account_id: Optional[str] = None,
    entity_level: Optional[str] = None,
    destination_table: str = META_ADS_RAW_TABLE,
) -> dict[str, Any]:
    validated_table = _validate_destination_table(
        params.get("destination_table"),
        default=destination_table,
        reserved_names={META_ADS_SYNC_RUNS_TABLE, META_ADS_PERFORMANCE_TABLE, META_ADS_CONVERSION_QUALITY_TABLE},
    )
    quoted_table = _quoted_identifier(validated_table)
    query_signature = _build_query_signature(
        operation,
        {
            "profile_key": profile_key,
            "account_id": account_id,
            "entity_level": entity_level,
            "params": params,
        },
    )
    sync_run_id = _build_sync_run_id()
    synced_at = _now_iso()
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _open_meta_ads_sqlite_connection()
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {quoted_table} (
                row_key TEXT PRIMARY KEY,
                sync_run_id TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                query_signature TEXT NOT NULL,
                operation TEXT NOT NULL,
                profile_key TEXT NOT NULL,
                account_id TEXT,
                entity_level TEXT,
                entity_id TEXT,
                entity_name TEXT,
                date_start TEXT,
                date_stop TEXT,
                raw_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_{validated_table}_query_signature
                ON {quoted_table} (query_signature);
            CREATE INDEX IF NOT EXISTS idx_{validated_table}_operation_profile
                ON {quoted_table} (operation, profile_key, synced_at DESC);
            """
        )
        conn.execute(f"DELETE FROM {quoted_table} WHERE query_signature = ?;", (query_signature,))

        insert_rows = []
        for index, row in enumerate(rows):
            resolved_entity_level = entity_level
            if resolved_entity_level == "account":
                entity_id = row.get("id") or row.get("account_id")
                entity_name = row.get("name") or row.get("account_name")
            elif resolved_entity_level == "campaign":
                entity_id = row.get("id") or row.get("campaign_id")
                entity_name = row.get("name") or row.get("campaign_name")
            else:
                resolved_entity_level = str(row.get("level") or entity_level or "").strip() or None
                if resolved_entity_level:
                    entity_id, entity_name = _entity_identity_for_level(row, resolved_entity_level)
                else:
                    entity_id = row.get("id") or row.get("campaign_id") or row.get("account_id")
                    entity_name = row.get("name") or row.get("campaign_name") or row.get("account_name")
            insert_rows.append(
                (
                    f"{sync_run_id}:{index}",
                    sync_run_id,
                    synced_at,
                    query_signature,
                    operation,
                    profile_key,
                    account_id,
                    resolved_entity_level,
                    entity_id,
                    entity_name,
                    row.get("date_start"),
                    row.get("date_stop"),
                    _canonical_json(row),
                )
            )

        if insert_rows:
            conn.executemany(
                f"""
                INSERT INTO {quoted_table} (
                    row_key,
                    sync_run_id,
                    synced_at,
                    query_signature,
                    operation,
                    profile_key,
                    account_id,
                    entity_level,
                    entity_id,
                    entity_name,
                    date_start,
                    date_stop,
                    raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                insert_rows,
            )

        summary = {"row_count": len(rows)}
        _record_meta_ads_sync_run(
            conn,
            sync_run_id=sync_run_id,
            synced_at=synced_at,
            operation=operation,
            profile_key=profile_key,
            destination_table=validated_table,
            query_signature=query_signature,
            account_id=account_id,
            dataset_id=None,
            row_count=len(rows),
            query_params=params,
            summary=summary,
            rate_limit_headers=rate_limit_headers,
        )
        conn.commit()
        query_hints = [
            (
                f"SELECT sync_run_id, operation, destination_table, row_count, synced_at "
                f"FROM {META_ADS_SYNC_RUNS_TABLE} WHERE profile_key='{_escape_sql_string(profile_key)}' "
                f"ORDER BY synced_at DESC LIMIT 10"
            ),
            (
                f"SELECT entity_level, entity_name, raw_json FROM {validated_table} "
                f"WHERE query_signature='{query_signature}' LIMIT 20"
            ),
        ]
        return _base_sync_response(
            result=(
                f"Synced {len(rows)} Meta {operation.replace('_', ' ')} row(s) into SQLite table '{validated_table}'. "
                "Use sqlite_batch to analyze the synced dataset instead of moving rows through agent context."
            ),
            operation=operation,
            profile_key=profile_key,
            destination_table=validated_table,
            sync_run_id=sync_run_id,
            query_signature=query_signature,
            rows_synced=len(rows),
            rate_limit_headers=rate_limit_headers,
            summary=summary,
            account_id=account_id,
            extra={"sqlite_query_hints": query_hints},
        )
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except sqlite3.Error:
                logger.debug("Failed to close SQLite connection after Meta raw sync", exc_info=True)


def _sync_performance_rows(
    *,
    operation: str,
    profile_key: str,
    account_id: str,
    level: str,
    insights_params: dict[str, Any],
    normalized_rows: list[dict[str, Any]],
    summary: dict[str, Any],
    params: dict[str, Any],
    rate_limit_headers: dict[str, str],
) -> dict[str, Any]:
    validated_table = _validate_destination_table(
        params.get("destination_table"),
        default=META_ADS_PERFORMANCE_TABLE,
        reserved_names={META_ADS_SYNC_RUNS_TABLE, META_ADS_RAW_TABLE, META_ADS_CONVERSION_QUALITY_TABLE},
    )
    quoted_table = _quoted_identifier(validated_table)
    query_signature = _build_query_signature(
        operation,
        {
            "profile_key": profile_key,
            "account_id": account_id,
            "level": level,
            "insights_params": insights_params,
        },
    )
    sync_run_id = _build_sync_run_id()
    synced_at = _now_iso()
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _open_meta_ads_sqlite_connection()
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {quoted_table} (
                row_key TEXT PRIMARY KEY,
                sync_run_id TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                query_signature TEXT NOT NULL,
                operation TEXT NOT NULL,
                profile_key TEXT NOT NULL,
                account_id TEXT NOT NULL,
                entity_level TEXT NOT NULL,
                entity_id TEXT,
                entity_name TEXT,
                date_start TEXT,
                date_stop TEXT,
                objective TEXT,
                status TEXT,
                effective_status TEXT,
                spend REAL,
                impressions REAL,
                reach REAL,
                clicks REAL,
                inline_link_clicks REAL,
                ctr REAL,
                cpc REAL,
                cpm REAL,
                frequency REAL,
                purchase_count REAL,
                lead_count REAL,
                purchase_value REAL,
                blended_roas REAL,
                cost_per_purchase REAL,
                cost_per_lead REAL,
                action_metrics_json TEXT NOT NULL DEFAULT '{{}}',
                action_value_metrics_json TEXT NOT NULL DEFAULT '{{}}',
                purchase_roas_json TEXT NOT NULL DEFAULT '{{}}',
                standard_purchase_roas_json TEXT NOT NULL DEFAULT '{{}}',
                standard_actions_json TEXT NOT NULL DEFAULT '{{}}',
                standard_action_values_json TEXT NOT NULL DEFAULT '{{}}'
            );
            CREATE INDEX IF NOT EXISTS idx_{validated_table}_query_signature
                ON {quoted_table} (query_signature);
            CREATE INDEX IF NOT EXISTS idx_{validated_table}_profile_date
                ON {quoted_table} (profile_key, account_id, date_start, date_stop);
            """
        )
        conn.execute(f"DELETE FROM {quoted_table} WHERE query_signature = ?;", (query_signature,))

        insert_rows = []
        for index, row in enumerate(normalized_rows):
            derived_metrics = row.get("derived_metrics") or {}
            insert_rows.append(
                (
                    f"{sync_run_id}:{index}",
                    sync_run_id,
                    synced_at,
                    query_signature,
                    operation,
                    profile_key,
                    account_id,
                    row.get("entity_level"),
                    row.get("entity_id"),
                    row.get("entity_name"),
                    row.get("date_start"),
                    row.get("date_stop"),
                    row.get("objective"),
                    row.get("status"),
                    row.get("effective_status"),
                    row.get("spend"),
                    row.get("impressions"),
                    row.get("reach"),
                    row.get("clicks"),
                    row.get("inline_link_clicks"),
                    row.get("ctr"),
                    row.get("cpc"),
                    row.get("cpm"),
                    row.get("frequency"),
                    derived_metrics.get("purchase_count"),
                    derived_metrics.get("lead_count"),
                    derived_metrics.get("purchase_value"),
                    derived_metrics.get("blended_roas"),
                    derived_metrics.get("cost_per_purchase"),
                    derived_metrics.get("cost_per_lead"),
                    _canonical_json(row.get("action_metrics") or {}),
                    _canonical_json(row.get("action_value_metrics") or {}),
                    _canonical_json(row.get("purchase_roas") or {}),
                    _canonical_json(row.get("standard_purchase_roas") or {}),
                    _canonical_json(row.get("standard_actions") or {}),
                    _canonical_json(row.get("standard_action_values") or {}),
                )
            )

        if insert_rows:
            conn.executemany(
                f"""
                INSERT INTO {quoted_table} (
                    row_key,
                    sync_run_id,
                    synced_at,
                    query_signature,
                    operation,
                    profile_key,
                    account_id,
                    entity_level,
                    entity_id,
                    entity_name,
                    date_start,
                    date_stop,
                    objective,
                    status,
                    effective_status,
                    spend,
                    impressions,
                    reach,
                    clicks,
                    inline_link_clicks,
                    ctr,
                    cpc,
                    cpm,
                    frequency,
                    purchase_count,
                    lead_count,
                    purchase_value,
                    blended_roas,
                    cost_per_purchase,
                    cost_per_lead,
                    action_metrics_json,
                    action_value_metrics_json,
                    purchase_roas_json,
                    standard_purchase_roas_json,
                    standard_actions_json,
                    standard_action_values_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                insert_rows,
            )

        _record_meta_ads_sync_run(
            conn,
            sync_run_id=sync_run_id,
            synced_at=synced_at,
            operation=operation,
            profile_key=profile_key,
            destination_table=validated_table,
            query_signature=query_signature,
            account_id=account_id,
            dataset_id=None,
            row_count=len(normalized_rows),
            query_params=insights_params,
            summary=summary,
            rate_limit_headers=rate_limit_headers,
        )
        conn.commit()
        query_hints = [
            (
                f"SELECT sync_run_id, row_count, synced_at FROM {META_ADS_SYNC_RUNS_TABLE} "
                f"WHERE profile_key='{_escape_sql_string(profile_key)}' AND destination_table='{_escape_sql_string(validated_table)}' "
                f"ORDER BY synced_at DESC LIMIT 10"
            ),
            (
                f"SELECT date_start, entity_name, spend, purchase_count, lead_count, blended_roas "
                f"FROM {validated_table} WHERE query_signature='{query_signature}' ORDER BY date_start DESC, spend DESC LIMIT 50"
            ),
        ]
        return _base_sync_response(
            result=(
                f"Synced {len(normalized_rows)} normalized Meta {operation.replace('_', ' ')} row(s) into SQLite table '{validated_table}'. "
                "Use sqlite_batch for follow-up SQL instead of moving rows through agent context."
            ),
            operation=operation,
            profile_key=profile_key,
            destination_table=validated_table,
            sync_run_id=sync_run_id,
            query_signature=query_signature,
            rows_synced=len(normalized_rows),
            rate_limit_headers=rate_limit_headers,
            summary=summary,
            account_id=account_id,
            extra={"level": level, "sqlite_query_hints": query_hints},
        )
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except sqlite3.Error:
                logger.debug("Failed to close SQLite connection after Meta performance sync", exc_info=True)


def _sync_conversion_quality_rows(
    *,
    profile_key: str,
    dataset_id: str,
    agent_name: str,
    request_params: dict[str, Any],
    quality_rows: list[dict[str, Any]],
    summary: dict[str, Any],
    params: dict[str, Any],
    rate_limit_headers: dict[str, str],
) -> dict[str, Any]:
    validated_table = _validate_destination_table(
        params.get("destination_table"),
        default=META_ADS_CONVERSION_QUALITY_TABLE,
        reserved_names={META_ADS_SYNC_RUNS_TABLE, META_ADS_RAW_TABLE, META_ADS_PERFORMANCE_TABLE},
    )
    quoted_table = _quoted_identifier(validated_table)
    query_signature = _build_query_signature(
        "conversion_quality",
        {
            "profile_key": profile_key,
            "dataset_id": dataset_id,
            "agent_name": agent_name,
            "request_params": request_params,
        },
    )
    sync_run_id = _build_sync_run_id()
    synced_at = _now_iso()
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _open_meta_ads_sqlite_connection()
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {quoted_table} (
                row_key TEXT PRIMARY KEY,
                sync_run_id TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                query_signature TEXT NOT NULL,
                operation TEXT NOT NULL DEFAULT 'conversion_quality',
                profile_key TEXT NOT NULL,
                dataset_id TEXT NOT NULL,
                agent_name TEXT,
                event_name TEXT,
                event_match_quality_score REAL,
                acr_percentage REAL,
                event_potential_acr_percentage REAL,
                upload_frequency TEXT,
                diagnostics_count INTEGER NOT NULL DEFAULT 0,
                coverage_json TEXT NOT NULL DEFAULT '{{}}',
                diagnostics_json TEXT NOT NULL DEFAULT '[]',
                dedupe_key_feedback_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE INDEX IF NOT EXISTS idx_{validated_table}_query_signature
                ON {quoted_table} (query_signature);
            CREATE INDEX IF NOT EXISTS idx_{validated_table}_dataset
                ON {quoted_table} (dataset_id, synced_at DESC);
            """
        )
        conn.execute(f"DELETE FROM {quoted_table} WHERE query_signature = ?;", (query_signature,))

        insert_rows = []
        for index, row in enumerate(quality_rows):
            insert_rows.append(
                (
                    f"{sync_run_id}:{index}",
                    sync_run_id,
                    synced_at,
                    query_signature,
                    profile_key,
                    dataset_id,
                    agent_name or None,
                    row.get("event_name"),
                    row.get("event_match_quality_score"),
                    row.get("acr_percentage"),
                    row.get("event_potential_acr_percentage"),
                    row.get("upload_frequency"),
                    int(row.get("diagnostics_count") or 0),
                    _canonical_json(row.get("coverage") or {}),
                    _canonical_json(row.get("diagnostics") or []),
                    _canonical_json(row.get("dedupe_key_feedback") or []),
                )
            )

        if insert_rows:
            conn.executemany(
                f"""
                INSERT INTO {quoted_table} (
                    row_key,
                    sync_run_id,
                    synced_at,
                    query_signature,
                    profile_key,
                    dataset_id,
                    agent_name,
                    event_name,
                    event_match_quality_score,
                    acr_percentage,
                    event_potential_acr_percentage,
                    upload_frequency,
                    diagnostics_count,
                    coverage_json,
                    diagnostics_json,
                    dedupe_key_feedback_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                insert_rows,
            )

        _record_meta_ads_sync_run(
            conn,
            sync_run_id=sync_run_id,
            synced_at=synced_at,
            operation="conversion_quality",
            profile_key=profile_key,
            destination_table=validated_table,
            query_signature=query_signature,
            account_id=None,
            dataset_id=dataset_id,
            row_count=len(quality_rows),
            query_params=request_params,
            summary=summary,
            rate_limit_headers=rate_limit_headers,
        )
        conn.commit()
        query_hints = [
            (
                f"SELECT sync_run_id, row_count, synced_at FROM {META_ADS_SYNC_RUNS_TABLE} "
                f"WHERE profile_key='{_escape_sql_string(profile_key)}' AND destination_table='{_escape_sql_string(validated_table)}' "
                f"ORDER BY synced_at DESC LIMIT 10"
            ),
            (
                f"SELECT event_name, event_match_quality_score, diagnostics_count, upload_frequency "
                f"FROM {validated_table} WHERE query_signature='{query_signature}' ORDER BY diagnostics_count DESC, event_name ASC LIMIT 50"
            ),
        ]
        return _base_sync_response(
            result=(
                f"Synced {len(quality_rows)} Meta conversion-quality row(s) into SQLite table '{validated_table}'. "
                "Use sqlite_batch to inspect EMQ, diagnostics, freshness, coverage, and deduplication trends."
            ),
            operation="conversion_quality",
            profile_key=profile_key,
            destination_table=validated_table,
            sync_run_id=sync_run_id,
            query_signature=query_signature,
            rows_synced=len(quality_rows),
            rate_limit_headers=rate_limit_headers,
            summary=summary,
            dataset_id=dataset_id,
            extra={"sqlite_query_hints": query_hints},
        )
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except sqlite3.Error:
                logger.debug("Failed to close SQLite connection after Meta conversion-quality sync", exc_info=True)


def _build_setup_path(skill_key: str) -> str:
    return reverse("console-system-skill-profiles", args=[skill_key])


def _build_setup_url(skill_key: str) -> str:
    relative_url = _build_setup_path(skill_key)
    base_url = str(settings.PUBLIC_SITE_URL or "").strip().rstrip("/")
    if not base_url:
        return relative_url
    return f"{base_url}{relative_url}"


def _serialize_field_guidance() -> list[dict[str, Any]]:
    definition = get_system_skill_definition(SYSTEM_SKILL_KEY)
    if definition is None:
        return []

    guidance: list[dict[str, Any]] = []
    for field in definition.profile_fields():
        guidance.append(
            {
                "key": field.key,
                "name": field.name,
                "required": field.required,
                "description": field.description,
                "default": field.default,
                "how_to_get": field.how_to_get,
                "docs": [
                    {
                        "title": doc.title,
                        "url": doc.url,
                        "description": doc.description,
                    }
                    for doc in field.docs
                ],
            }
        )
    return guidance


def _base_onboarding_payload(*, setup_note: Optional[str] = None) -> dict[str, Any]:
    definition = get_system_skill_definition(SYSTEM_SKILL_KEY)
    if definition is None:
        return {}

    return {
        "required_fields": [field.key for field in definition.required_profile_fields],
        "field_guidance": _serialize_field_guidance(),
        "setup_instructions": definition.setup_instructions,
        "setup_steps": list(definition.setup_steps),
        "setup_docs": [
            {
                "title": doc.title,
                "url": doc.url,
                "description": doc.description,
            }
            for doc in definition.setup_docs
        ],
        "troubleshooting_tips": list(definition.troubleshooting_tips),
        "agent_guidance": (
            setup_note
            or "Help the user finish the setup steps, point them to the docs when they get stuck, then retry Meta Ads once they confirm the profile is updated."
        ),
    }


def _default_setup_note_for_resolution(
    resolution: dict[str, object],
    *,
    profile_key: Optional[str],
) -> str:
    status = str(resolution.get("status") or "").strip()
    if status == "multiple_profiles":
        available_profiles = list(resolution.get("available_profile_keys") or [])
        if available_profiles:
            joined = ", ".join(available_profiles)
            return (
                f"Ask the user which Meta Ads profile to use from: {joined}. If none of those are right, "
                "offer to create a new profile. Do not guess."
            )
        return "Ask the user whether they want to create a new Meta Ads profile before retrying. Do not guess."
    if status == "profile_not_found":
        requested = profile_key or "that profile"
        available_profiles = list(resolution.get("available_profile_keys") or [])
        if available_profiles:
            joined = ", ".join(available_profiles)
            return (
                f"Tell the user Meta Ads profile '{requested}' does not exist. Ask whether to use one of: {joined}, "
                "or create a new profile."
            )
        return (
            f"Tell the user Meta Ads profile '{requested}' does not exist yet. Help them create the first profile, "
            "starting with developer registration if needed."
        )

    selected_profile = resolution.get("profile")
    selected_profile_key = getattr(selected_profile, "profile_key", profile_key or "default")
    was_bootstrapped = bool(resolution.get("was_bootstrapped"))
    if was_bootstrapped:
        return (
            f"A default Meta Ads profile '{selected_profile_key}' was created automatically. Walk the user through "
            "developer registration, app creation, system-user setup, and token generation until the profile is complete."
        )
    return (
        f"Meta Ads profile '{selected_profile_key}' is not ready. Walk the user through the setup checklist and docs, "
        "ask which step they are stuck on if needed, then retry once they update the profile."
    )


def _profile_action_required(
    resolution: dict[str, object],
    *,
    profile_key: Optional[str] = None,
    setup_note: Optional[str] = None,
) -> dict[str, Any]:
    setup_path = _build_setup_path(SYSTEM_SKILL_KEY)
    setup_url = setup_path
    setup_url_absolute = _build_setup_url(SYSTEM_SKILL_KEY)
    available_profiles = list(resolution.get("available_profile_keys") or [])

    status = resolution.get("status")
    if status == "profile_not_found":
        result = (
            f"Meta Ads profile '{profile_key}' was not found. "
            f"Available profiles: {', '.join(available_profiles) if available_profiles else 'none'}. "
            f"Manage profiles here: {setup_path}"
        )
    elif status == "multiple_profiles":
        result = (
            "Multiple Meta Ads profiles are configured and no default profile is set. "
            f"Choose one of: {', '.join(available_profiles)}. "
            f"You can set a default profile here: {setup_path}"
        )
    elif status == "incomplete_profile":
        selected_profile = resolution.get("profile")
        selected_profile_key = getattr(selected_profile, "profile_key", profile_key or "default")
        missing_required_keys = list(resolution.get("missing_required_keys") or [])
        was_bootstrapped = bool(resolution.get("was_bootstrapped"))
        if was_bootstrapped:
            result = (
                f"I created a default Meta Ads profile '{selected_profile_key}' for you. "
                f"Open {setup_path} and fill in these values: {', '.join(missing_required_keys)}."
            )
        else:
            result = (
                f"Meta Ads profile '{selected_profile_key}' is missing required values: {', '.join(missing_required_keys)}. "
                f"Complete the profile here: {setup_path}"
            )
    else:
        result = (
            "Meta Ads setup is required before this tool can run. "
            f"Add a Meta Ads profile here: {setup_path}"
        )

    payload = {
        "status": "action_required",
        "result": result,
        "setup_path": setup_path,
        "setup_url": setup_url,
        "setup_url_absolute": setup_url_absolute,
        "skill_key": SYSTEM_SKILL_KEY,
        "available_profiles": available_profiles,
        "selection_required": status == "multiple_profiles",
        "selected_profile_key": getattr(resolution.get("profile"), "profile_key", None),
    }
    payload.update(
        _base_onboarding_payload(
            setup_note=setup_note or _default_setup_note_for_resolution(resolution, profile_key=profile_key)
        )
    )
    return payload


def _graph_error_message(response: Response, payload: Any) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            pieces = []
            if error.get("message"):
                pieces.append(str(error["message"]))
            if error.get("code") is not None:
                pieces.append(f"code={error['code']}")
            if error.get("error_subcode") is not None:
                pieces.append(f"subcode={error['error_subcode']}")
            if pieces:
                return "Meta Graph API error: " + " | ".join(pieces)
    return f"Meta Graph API request failed with HTTP {response.status_code}."


META_ONBOARDING_ERROR_PATTERNS = (
    "invalid oauth",
    "invalid access token",
    "error validating access token",
    "appsecret_proof",
    "requires a valid appsecret_proof",
    "permissions error",
    "does not have permission",
    "do not have permission",
    "missing permission",
    "permission",
    "not authorized",
    "user has not authorized application",
    "no permission",
    "unsupported get request",
    "object with id",
)


def _looks_like_onboarding_error(message: str) -> bool:
    lowered = str(message or "").lower()
    if not lowered:
        return False
    return any(pattern in lowered for pattern in META_ONBOARDING_ERROR_PATTERNS)


def _credentials_action_required(
    resolution: dict[str, object],
    *,
    profile_key: Optional[str],
    error_message: str,
) -> dict[str, Any]:
    selected_profile = resolution.get("profile")
    selected_profile_key = getattr(selected_profile, "profile_key", profile_key or "default")
    payload = _profile_action_required(
        resolution,
        profile_key=profile_key,
        setup_note=(
            "The saved Meta credentials or permissions did not pass a live check. Walk the user through developer registration, app setup, "
            "system user assignment, and token generation, then retry once they confirm the profile is updated."
        ),
    )
    payload["result"] = (
        f"Meta Ads profile '{selected_profile_key}' needs attention before monitoring can start. "
        f"Live check failed with: {error_message} "
        f"Open {payload['setup_path']}, review the onboarding steps and docs, update the profile, and then retry."
    )
    payload["auth_error"] = error_message
    payload["selected_profile_key"] = selected_profile_key
    return payload


def _graph_get(profile_values: dict[str, str], path: str, *, params: Optional[dict[str, Any]] = None) -> tuple[Any, dict[str, str]]:
    api_version = _normalize_api_version(profile_values.get("META_API_VERSION"))
    access_token = profile_values["META_SYSTEM_USER_TOKEN"]
    app_secret = profile_values["META_APP_SECRET"]

    request_params = dict(params or {})
    request_params["access_token"] = access_token
    request_params["appsecret_proof"] = hmac.new(
        app_secret.encode("utf-8"),
        access_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    url = f"{GRAPH_BASE_URL}/{api_version}/{path.lstrip('/')}"
    response = requests.get(url, params=request_params, timeout=REQUEST_TIMEOUT_SECONDS)
    rate_limit_headers = _extract_rate_limit_headers(response)

    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_body": response.text[:1000]}

    if response.status_code >= 400:
        raise ValueError(_graph_error_message(response, payload))

    return payload, rate_limit_headers


def _paginate_graph_get(
    profile_values: dict[str, str],
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    fetch_all: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    page_params = dict(params or {})
    rows: list[dict[str, Any]] = []
    last_headers: dict[str, str] = {}
    after_cursor: Optional[str] = None

    while True:
        current_params = dict(page_params)
        if after_cursor:
            current_params["after"] = after_cursor
        payload, last_headers = _graph_get(profile_values, path, params=current_params)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError(f"Expected a paged Graph API response, got: {json.dumps(payload)[:500]}")

        rows.extend(payload["data"])
        if not fetch_all:
            break

        paging = payload.get("paging")
        cursors = paging.get("cursors") if isinstance(paging, dict) else None
        after_cursor = cursors.get("after") if isinstance(cursors, dict) else None
        if not after_cursor:
            break

    return rows, last_headers


def _doctor_result(profile_key: str, profile_values: dict[str, str]) -> dict[str, Any]:
    api_version = _normalize_api_version(profile_values.get("META_API_VERSION"))
    default_account_id = _normalize_account_id(profile_values.get("META_AD_ACCOUNT_ID") or "")
    business_id = str(profile_values.get("META_BUSINESS_ID") or "").strip() or None
    account_path = f"{business_id}/owned_ad_accounts" if business_id else "me/adaccounts"
    rows, headers = _paginate_graph_get(
        profile_values,
        account_path,
        params={
            "fields": "id,name,account_status",
            "limit": 1,
        },
        fetch_all=False,
    )
    default_account_status = None
    if default_account_id:
        account_payload, account_headers = _graph_get(
            profile_values,
            default_account_id,
            params={"fields": "id,name,account_status"},
        )
        headers.update(account_headers)
        default_account_status = account_payload.get("account_status") if isinstance(account_payload, dict) else None
    dataset_id = _normalize_dataset_id(profile_values.get("META_DATASET_ID") or profile_values.get("META_PIXEL_ID"))
    dataset_quality_event_count = None
    if dataset_id:
        dataset_quality_payload, dataset_quality_headers = _graph_get(
            profile_values,
            "dataset_quality",
            params={
                "dataset_id": dataset_id,
                "fields": "web{event_name}",
            },
        )
        headers.update(dataset_quality_headers)
        if isinstance(dataset_quality_payload, dict) and isinstance(dataset_quality_payload.get("web"), list):
            dataset_quality_event_count = len(dataset_quality_payload["web"])

    result = (
        f"Meta Ads profile '{profile_key}' is connected. "
        f"Default account: {default_account_id or 'not set'}. "
        f"API version: {api_version}. "
        f"Accessible ad account sample count: {len(rows)}."
    )
    if default_account_id and default_account_status is not None:
        result += f" Default account status: {default_account_status}."
    if dataset_id:
        result += (
            f" Conversion quality dataset configured: {dataset_id}. "
            f"Dataset quality event sample count: {dataset_quality_event_count if dataset_quality_event_count is not None else 'unknown'}."
        )
    return {
        "status": "success",
        "result": result,
        "profile_key": profile_key,
        "api_version": api_version,
        "default_account_id": default_account_id or None,
        "business_id": business_id,
        "dataset_id": dataset_id or None,
        "dataset_quality_event_sample_count": dataset_quality_event_count,
        "accessible_account_sample_count": len(rows),
        "rate_limit_headers": headers,
        "next_step_hint": (
            "Call meta_ads with performance_snapshot, performance_timeseries, or conversion_quality to sync monitoring datasets directly into SQLite."
        ),
    }


def _require_account_id(account_id: Optional[str], profile_values: dict[str, str]) -> str:
    resolved_account_id = _normalize_account_id(account_id or profile_values.get("META_AD_ACCOUNT_ID") or "")
    if not resolved_account_id:
        raise ValueError("Missing Meta ad account ID. Provide account_id or configure META_AD_ACCOUNT_ID on the profile.")
    return resolved_account_id


def _require_dataset_id(dataset_id: Optional[str], profile_values: dict[str, str]) -> str:
    resolved_dataset_id = _normalize_dataset_id(
        dataset_id or profile_values.get("META_DATASET_ID") or profile_values.get("META_PIXEL_ID")
    )
    if not resolved_dataset_id:
        raise ValueError(
            "Missing Meta dataset or pixel ID. Provide dataset_id or configure META_DATASET_ID on the profile."
        )
    return resolved_dataset_id


def _build_insights_params(
    params: dict[str, Any],
    *,
    default_fields: list[str],
    default_level: str,
    page_size: int,
) -> dict[str, Any]:
    insights_params: dict[str, Any] = {
        "fields": ",".join(_string_list(params.get("fields"), default=default_fields)),
        "level": str(params.get("level") or default_level),
        "limit": page_size,
    }
    since = str(params.get("since") or "").strip()
    until = str(params.get("until") or "").strip()
    if since and until:
        insights_params["time_range"] = json.dumps({"since": since, "until": until})
    else:
        insights_params["date_preset"] = str(params.get("date_preset") or "last_7d")

    breakdowns = _string_list(params.get("breakdowns"), default=[])
    if breakdowns:
        insights_params["breakdowns"] = ",".join(breakdowns)

    action_breakdowns = _string_list(params.get("action_breakdowns"), default=[])
    if action_breakdowns:
        insights_params["action_breakdowns"] = ",".join(action_breakdowns)

    summary_action_breakdowns = _string_list(params.get("summary_action_breakdowns"), default=[])
    if summary_action_breakdowns:
        insights_params["summary_action_breakdowns"] = ",".join(summary_action_breakdowns)

    action_attribution_windows = _string_list(params.get("action_attribution_windows"), default=[])
    if action_attribution_windows:
        insights_params["action_attribution_windows"] = json.dumps(action_attribution_windows)

    time_increment = params.get("time_increment")
    if time_increment not in (None, ""):
        insights_params["time_increment"] = time_increment

    filtering = params.get("filtering")
    if isinstance(filtering, list) and filtering:
        insights_params["filtering"] = json.dumps(filtering)

    sort = _string_list(params.get("sort"), default=[])
    if sort:
        insights_params["sort"] = ",".join(sort)

    return insights_params


def _performance_snapshot_result(
    profile_key: str,
    profile_values: dict[str, str],
    params: dict[str, Any],
    *,
    page_size: int,
    fetch_all: bool,
) -> dict[str, Any]:
    account_id = _require_account_id(params.get("account_id"), profile_values)
    insights_params = _build_insights_params(
        params,
        default_fields=DEFAULT_PERFORMANCE_FIELDS,
        default_level=str(params.get("level") or "campaign"),
        page_size=page_size,
    )
    rows, headers = _paginate_graph_get(
        profile_values,
        f"{account_id}/insights",
        params=insights_params,
        fetch_all=fetch_all,
    )
    level = str(insights_params.get("level") or "campaign")
    normalized_rows = [_normalize_performance_row(row, level=level) for row in rows if isinstance(row, dict)]
    summary = _summarize_normalized_rows(normalized_rows)
    response = _sync_performance_rows(
        operation="performance_snapshot",
        profile_key=profile_key,
        account_id=account_id,
        level=level,
        insights_params=insights_params,
        normalized_rows=normalized_rows,
        summary=summary,
        params=params,
        rate_limit_headers=headers,
    )
    if _return_rows_requested(params):
        response["normalized_rows"] = normalized_rows
    return response


def _performance_timeseries_result(
    profile_key: str,
    profile_values: dict[str, str],
    params: dict[str, Any],
    *,
    page_size: int,
    fetch_all: bool,
) -> dict[str, Any]:
    account_id = _require_account_id(params.get("account_id"), profile_values)
    enriched_params = dict(params)
    if enriched_params.get("time_increment") in (None, ""):
        enriched_params["time_increment"] = 1
    insights_params = _build_insights_params(
        enriched_params,
        default_fields=DEFAULT_PERFORMANCE_FIELDS,
        default_level=str(enriched_params.get("level") or "campaign"),
        page_size=page_size,
    )
    rows, headers = _paginate_graph_get(
        profile_values,
        f"{account_id}/insights",
        params=insights_params,
        fetch_all=fetch_all,
    )
    level = str(insights_params.get("level") or "campaign")
    normalized_rows = [_normalize_performance_row(row, level=level) for row in rows if isinstance(row, dict)]
    summary = _summarize_normalized_rows(normalized_rows)
    response = _sync_performance_rows(
        operation="performance_timeseries",
        profile_key=profile_key,
        account_id=account_id,
        level=level,
        insights_params=insights_params,
        normalized_rows=normalized_rows,
        summary=summary,
        params=params,
        rate_limit_headers=headers,
    )
    response["time_increment"] = insights_params.get("time_increment")
    if _return_rows_requested(params):
        response["normalized_rows"] = normalized_rows
    return response


def _conversion_quality_result(
    profile_key: str,
    profile_values: dict[str, str],
    params: dict[str, Any],
) -> dict[str, Any]:
    dataset_id = _require_dataset_id(params.get("dataset_id"), profile_values)
    request_params: dict[str, Any] = {
        "dataset_id": dataset_id,
        "fields": "web{event_name,event_match_quality,acr,event_coverage,dedupe_key_feedback,data_freshness,event_potential_aly_acr_increase}",
    }
    agent_name = str(params.get("agent_name") or profile_values.get("META_AGENT_NAME") or "").strip()
    if agent_name:
        request_params["agent_name"] = agent_name

    payload, headers = _graph_get(profile_values, "dataset_quality", params=request_params)
    quality_rows = _normalize_dataset_quality_rows(payload)
    summary = _dataset_quality_summary(quality_rows)
    response = _sync_conversion_quality_rows(
        profile_key=profile_key,
        dataset_id=dataset_id,
        agent_name=agent_name,
        request_params=request_params,
        quality_rows=quality_rows,
        summary=summary,
        params=params,
        rate_limit_headers=headers,
    )
    if _return_rows_requested(params):
        response["quality_rows"] = quality_rows
        response["raw_quality_payload"] = payload
    return response


def execute_meta_ads(agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    operation = str(params.get("operation") or "").strip().lower()
    if operation not in {
        "doctor",
        "accounts",
        "campaigns",
        "insights",
        "performance_snapshot",
        "performance_timeseries",
        "conversion_quality",
    }:
        return {
            "status": "error",
            "message": (
                "operation must be one of: doctor, accounts, campaigns, insights, performance_snapshot, "
                "performance_timeseries, conversion_quality"
            ),
        }

    profile_key = str(params.get("profile_key") or "").strip() or None
    resolution = resolve_system_skill_profile_for_agent(
        agent,
        SYSTEM_SKILL_KEY,
        profile_key=profile_key,
        auto_bootstrap=profile_key is None,
    )
    if resolution.get("status") != "ok":
        return _profile_action_required(resolution, profile_key=profile_key)

    profile = resolution["profile"]
    profile_values = resolution["values"]
    selected_profile_key = profile.profile_key

    try:
        if operation == "doctor":
            return _doctor_result(selected_profile_key, profile_values)

        page_size = int(params.get("page_size") or 100)
        page_size = max(1, min(page_size, 500))
        fetch_all = bool(params.get("fetch_all"))

        if operation == "accounts":
            business_id = str(params.get("business_id") or profile_values.get("META_BUSINESS_ID") or "").strip()
            path = f"{business_id}/owned_ad_accounts" if business_id else "me/adaccounts"
            rows, headers = _paginate_graph_get(
                profile_values,
                path,
                params={
                    "fields": ",".join(_string_list(params.get("fields"), default=DEFAULT_ACCOUNT_FIELDS)),
                    "limit": page_size,
                },
                fetch_all=fetch_all,
            )
            response = _sync_raw_meta_rows(
                operation=operation,
                profile_key=selected_profile_key,
                rows=rows,
                params=params,
                rate_limit_headers=headers,
                entity_level="account",
            )
            if _return_rows_requested(params):
                response["rows"] = rows
            return response

        account_id = _require_account_id(params.get("account_id"), profile_values)
        if operation == "campaigns":
            rows, headers = _paginate_graph_get(
                profile_values,
                f"{account_id}/campaigns",
                params={
                    "fields": ",".join(_string_list(params.get("fields"), default=DEFAULT_CAMPAIGN_FIELDS)),
                    "limit": page_size,
                },
                fetch_all=fetch_all,
            )
            response = _sync_raw_meta_rows(
                operation=operation,
                profile_key=selected_profile_key,
                rows=rows,
                params=params,
                rate_limit_headers=headers,
                account_id=account_id,
                entity_level="campaign",
            )
            if _return_rows_requested(params):
                response["rows"] = rows
            return response

        if operation == "performance_snapshot":
            return _performance_snapshot_result(
                selected_profile_key,
                profile_values,
                params,
                page_size=page_size,
                fetch_all=fetch_all,
            )

        if operation == "performance_timeseries":
            return _performance_timeseries_result(
                selected_profile_key,
                profile_values,
                params,
                page_size=page_size,
                fetch_all=fetch_all,
            )

        if operation == "conversion_quality":
            return _conversion_quality_result(
                selected_profile_key,
                profile_values,
                params,
            )

        insights_params: dict[str, Any] = {
            **_build_insights_params(
                params,
                default_fields=DEFAULT_INSIGHTS_FIELDS,
                default_level="account",
                page_size=page_size,
            )
        }

        rows, headers = _paginate_graph_get(
            profile_values,
            f"{account_id}/insights",
            params=insights_params,
            fetch_all=fetch_all,
        )
        response = _sync_raw_meta_rows(
            operation=operation,
            profile_key=selected_profile_key,
            rows=rows,
            params=params,
            rate_limit_headers=headers,
            account_id=account_id,
            entity_level=str(insights_params.get("level") or "account"),
        )
        if _return_rows_requested(params):
            response["rows"] = rows
        return response
    except RequestException as exc:
        logger.warning("Meta Ads tool request failed for agent %s profile %s: %s", agent.id, selected_profile_key, exc)
        return {"status": "error", "message": str(exc)}
    except sqlite3.Error as exc:
        logger.warning("Meta Ads SQLite sync failed for agent %s profile %s: %s", agent.id, selected_profile_key, exc)
        return {"status": "error", "message": f"Meta Ads SQLite sync failed: {exc}"}
    except ValueError as exc:
        logger.warning("Meta Ads tool failed for agent %s profile %s: %s", agent.id, selected_profile_key, exc)
        if (
            _looks_like_onboarding_error(str(exc))
            or "Missing Meta ad account ID" in str(exc)
            or "Missing Meta dataset or pixel ID" in str(exc)
        ):
            return _credentials_action_required(
                {
                    "status": "incomplete_profile",
                    "profile": profile,
                    "available_profile_keys": resolution.get("available_profile_keys", []),
                    "missing_required_keys": [],
                },
                profile_key=selected_profile_key,
                error_message=str(exc),
            )
        return {"status": "error", "message": str(exc)}
