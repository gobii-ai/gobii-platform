import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentStep,
)
from console.agent_audit.events import Cursor, fetch_audit_events
from console.agent_audit.serializers import serialize_prompt_meta
from console.agent_chat.timeline import (
    MAX_PAGE_SIZE as TIMELINE_MAX_PAGE_SIZE,
    fetch_timeline_window,
    serialize_processing_snapshot,
)


DEBUG_TRACE_TOOL_NAME = "gobii_get_agent_debug_trace"
DEBUG_TRACE_DEFAULT_LIMIT = 20
DEBUG_TRACE_MAX_LIMIT = 50
DEBUG_TRACE_DEFAULT_RECENT_MINUTES = 60
DEBUG_TRACE_MAX_RECENT_MINUTES = 7 * 24 * 60
DEBUG_TRACE_DETAIL_LEVELS = ("summary", "standard", "verbose")
DEBUG_TRACE_INCLUDE_SECTIONS = (
    "timeline",
    "audit_events",
    "completions",
    "eval_debug_artifacts",
    "diagnostics",
)
DEBUG_TRACE_DEFAULT_INCLUDE = DEBUG_TRACE_INCLUDE_SECTIONS
REDACTED = "[REDACTED]"

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b("
    r"api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|bearer|"
    r"authorization|password|passwd|secret|client[_-]?secret|private[_-]?key|"
    r"cookie|set-cookie"
    r")\b\s*[:=]\s*([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
_STRIPE_SECRET_RE = re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{12,}\b")
_GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")
_SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")
_AWS_ACCESS_KEY_RE = re.compile(r"\bA(?:KIA|SIA)[A-Z0-9]{16}\b")
_HIGH_ENTROPY_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(token|secret|password|api[_-]?key)\b\s*[:=]\s*[A-Za-z0-9_./+=-]{12,}"
)

_SENSITIVE_EXACT_KEYS = {
    "api_key",
    "apikey",
    "x_api_key",
    "authorization",
    "auth",
    "auth_header",
    "bearer",
    "password",
    "passwd",
    "secret",
    "client_secret",
    "private_key",
    "access_token",
    "refresh_token",
    "id_token",
    "session_token",
    "cookie",
    "set_cookie",
    "webhook_secret",
    "token",
    "key",
    "credentials",
    "credential",
}


class AgentDebugTraceValidationError(ValueError):
    def __init__(self, message: str, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.data = data


@dataclass(frozen=True)
class DebugTraceBounds:
    since: datetime | None
    until: datetime | None
    recent_minutes: int | None


def build_agent_debug_trace(
    agent: PersistentAgent,
    *,
    limit: int = DEBUG_TRACE_DEFAULT_LIMIT,
    cursor: str | None = None,
    recent_minutes: int | None = None,
    recent_minutes_provided: bool = False,
    since: str | None = None,
    until: str | None = None,
    include: tuple[str, ...] = DEBUG_TRACE_DEFAULT_INCLUDE,
    detail: str = "standard",
    eval_run_id: UUID | None = None,
) -> dict[str, Any]:
    if detail not in DEBUG_TRACE_DETAIL_LEVELS:
        raise AgentDebugTraceValidationError(
            "detail must be one of summary, standard, or verbose.",
            {"field": "detail", "supported_values": list(DEBUG_TRACE_DETAIL_LEVELS)},
        )
    if cursor and Cursor.decode(cursor) is None:
        raise AgentDebugTraceValidationError(
            "cursor must be a valid Gobii audit/debug cursor.",
            {"field": "cursor"},
        )
    include = _normalize_include(include)
    limit = max(1, min(int(limit), DEBUG_TRACE_MAX_LIMIT))
    bounds = _resolve_bounds(
        cursor=cursor,
        recent_minutes=recent_minutes,
        recent_minutes_provided=recent_minutes_provided,
        since=since,
        until=until,
    )
    requested_at = timezone.now()

    warnings: list[str] = [
        "Debug traces are sanitized previews. Prompt archive payloads and raw secret-bearing values are not returned."
    ]
    payload: dict[str, Any] = {
        "agent": _serialize_agent_debug_ref(agent),
        "scope": {
            "agent_id": str(agent.id),
            "requested_at": _iso(requested_at),
            "since": _iso(bounds.since),
            "until": _iso(bounds.until),
            "recent_minutes": bounds.recent_minutes,
            "cursor": cursor,
            "limit": limit,
            "include": list(include),
            "detail": detail,
            "eval_run_id": str(eval_run_id) if eval_run_id else None,
        },
        "redaction": {
            "mode": "standard",
            "replacement": REDACTED,
            "notes": [
                "Sensitive keys and common credential/token patterns are redacted recursively.",
                "Long strings are truncated by detail level.",
                "Prompt archives are represented by metadata and IDs only.",
            ],
        },
        "warnings": warnings,
    }

    if "timeline" in include:
        payload["timeline"] = _build_timeline_section(agent, limit=limit, cursor=cursor, bounds=bounds, detail=detail)
    if "audit_events" in include:
        audit_section = _build_audit_events_section(agent, limit=limit, cursor=cursor, bounds=bounds, detail=detail)
        payload["audit_events"] = audit_section["events"]
        payload["audit"] = {
            "source": "console.agent_audit.events.fetch_audit_events",
            "has_more": audit_section["has_more"],
            "next_cursor": audit_section["next_cursor"],
            "returned": len(audit_section["events"]),
        }
    if "completions" in include:
        payload["completions"] = _build_completions_section(agent, limit=limit, bounds=bounds, detail=detail)
    if "eval_debug_artifacts" in include:
        payload["eval_debug_artifacts"] = _build_eval_debug_artifacts_section(
            agent,
            limit=min(limit, 10),
            bounds=bounds,
            detail=detail,
            eval_run_id=eval_run_id,
        )
    if "diagnostics" in include:
        payload["diagnostics"] = _build_diagnostics_section(payload)

    return payload


def _normalize_include(include: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if include in (None, ()):
        return DEBUG_TRACE_DEFAULT_INCLUDE
    if not isinstance(include, (tuple, list)):
        raise AgentDebugTraceValidationError(
            "include must be an array of debug sections.",
            {"field": "include", "supported_values": list(DEBUG_TRACE_INCLUDE_SECTIONS)},
        )
    normalized: list[str] = []
    unsupported: list[Any] = []
    for item in include:
        if not isinstance(item, str):
            unsupported.append(item)
            continue
        section = item.strip()
        if section not in DEBUG_TRACE_INCLUDE_SECTIONS:
            unsupported.append(item)
            continue
        if section not in normalized:
            normalized.append(section)
    if unsupported:
        raise AgentDebugTraceValidationError(
            "include contains unsupported debug sections.",
            {
                "field": "include",
                "unsupported_values": unsupported,
                "supported_values": list(DEBUG_TRACE_INCLUDE_SECTIONS),
            },
        )
    if not normalized:
        raise AgentDebugTraceValidationError(
            "include must request at least one debug section.",
            {"field": "include", "supported_values": list(DEBUG_TRACE_INCLUDE_SECTIONS)},
        )
    return tuple(normalized)


def _resolve_bounds(
    *,
    cursor: str | None,
    recent_minutes: int | None,
    recent_minutes_provided: bool,
    since: str | None,
    until: str | None,
) -> DebugTraceBounds:
    if since and recent_minutes_provided and recent_minutes is not None:
        raise AgentDebugTraceValidationError(
            "Use either since or recent_minutes, not both.",
            {"fields": ["since", "recent_minutes"]},
        )

    until_dt = _parse_iso_datetime(until, "until") if until else timezone.now()
    since_dt = _parse_iso_datetime(since, "since") if since else None
    resolved_recent = recent_minutes
    if resolved_recent is None and not recent_minutes_provided and not cursor and since_dt is None:
        resolved_recent = DEBUG_TRACE_DEFAULT_RECENT_MINUTES
    if resolved_recent is not None:
        if resolved_recent < 1 or resolved_recent > DEBUG_TRACE_MAX_RECENT_MINUTES:
            raise AgentDebugTraceValidationError(
                f"recent_minutes must be between 1 and {DEBUG_TRACE_MAX_RECENT_MINUTES}.",
                {"field": "recent_minutes", "maximum": DEBUG_TRACE_MAX_RECENT_MINUTES},
            )
        since_dt = until_dt - timedelta(minutes=resolved_recent)

    if since_dt and until_dt and since_dt > until_dt:
        raise AgentDebugTraceValidationError(
            "since must be earlier than until.",
            {"fields": ["since", "until"]},
        )
    if since_dt and until_dt and until_dt - since_dt > timedelta(minutes=DEBUG_TRACE_MAX_RECENT_MINUTES):
        raise AgentDebugTraceValidationError(
            "Debug trace time windows cannot exceed 7 days.",
            {"fields": ["since", "until"], "maximum_minutes": DEBUG_TRACE_MAX_RECENT_MINUTES},
        )
    return DebugTraceBounds(since=since_dt, until=until_dt, recent_minutes=resolved_recent)


def _parse_iso_datetime(raw: str, field: str):
    if not isinstance(raw, str) or not raw.strip():
        raise AgentDebugTraceValidationError(f"{field} must be an ISO8601 datetime string.", {"field": field})
    parsed = parse_datetime(raw.strip())
    if parsed is None:
        raise AgentDebugTraceValidationError(f"{field} must be an ISO8601 datetime string.", {"field": field})
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _serialize_agent_debug_ref(agent: PersistentAgent) -> dict[str, Any]:
    return {
        "id": str(agent.id),
        "name": agent.name,
        "is_active": agent.is_active,
        "life_state": agent.life_state,
        "planning_state": agent.planning_state,
        "schedule": agent.schedule,
        "organization_id": str(agent.organization_id) if agent.organization_id else None,
        "user_id": str(agent.user_id) if agent.user_id else None,
        "preferred_llm_tier": getattr(getattr(agent, "preferred_llm_tier", None), "key", None),
        "created_at": _iso(agent.created_at),
        "updated_at": _iso(agent.updated_at),
        "last_interaction_at": _iso(agent.last_interaction_at),
    }


def _build_timeline_section(
    agent: PersistentAgent,
    *,
    limit: int,
    cursor: str | None,
    bounds: DebugTraceBounds,
    detail: str,
) -> dict[str, Any]:
    timeline_limit = min(limit, TIMELINE_MAX_PAGE_SIZE)
    window = fetch_timeline_window(
        agent,
        cursor=None,
        direction="initial",
        limit=timeline_limit,
    )
    events = [
        _sanitize_debug_value(event, detail=detail)
        for event in window.events
        if _event_in_bounds(event, bounds)
    ]
    return {
        "events": events,
        "latest_cursor": window.newest_cursor,
        "oldest_cursor": window.oldest_cursor,
        "newest_cursor": window.newest_cursor,
        "has_more_older": window.has_more_older,
        "has_more_newer": window.has_more_newer,
        "processing_active": window.processing_active,
        "processing_snapshot": _sanitize_debug_value(
            serialize_processing_snapshot(window.processing_snapshot),
            detail=detail,
        ),
        "cursor_note": "Timeline events are always the latest bounded window; use gobii_get_agent_timeline for full timeline paging.",
    }


def _build_audit_events_section(
    agent: PersistentAgent,
    *,
    limit: int,
    cursor: str | None,
    bounds: DebugTraceBounds,
    detail: str,
) -> dict[str, Any]:
    events, has_more, next_cursor = fetch_audit_events(
        agent,
        cursor=cursor,
        limit=limit,
        at=bounds.until,
    )
    bounded_events = [
        _sanitize_audit_event(event, detail=detail)
        for event in events
        if _event_in_bounds(event, bounds)
    ]
    return {"events": bounded_events[:limit], "has_more": has_more, "next_cursor": next_cursor}


def _build_completions_section(
    agent: PersistentAgent,
    *,
    limit: int,
    bounds: DebugTraceBounds,
    detail: str,
) -> dict[str, Any]:
    queryset = PersistentAgentCompletion.objects.filter(agent=agent).order_by("-created_at", "-id")
    queryset = _apply_created_bounds(queryset, "created_at", bounds)
    completions = list(queryset[:limit])
    completion_ids = [completion.id for completion in completions]

    prompt_archives_by_completion_id: dict[Any, Any] = {}
    tool_counts_by_completion_id: dict[Any, int] = {}
    if completion_ids:
        steps_with_archives = (
            PersistentAgentStep.objects.filter(
                agent=agent,
                completion_id__in=completion_ids,
                llm_prompt_archive__isnull=False,
            )
            .select_related("llm_prompt_archive")
            .order_by("completion_id", "-created_at", "-id")
        )
        for step in steps_with_archives:
            prompt_archives_by_completion_id.setdefault(step.completion_id, step.llm_prompt_archive)

        tool_counts = (
            PersistentAgentStep.objects.filter(
                agent=agent,
                completion_id__in=completion_ids,
                tool_call__isnull=False,
            )
            .values("completion_id")
            .annotate(count=Count("id"))
        )
        tool_counts_by_completion_id = {
            item["completion_id"]: item["count"]
            for item in tool_counts
        }

    items = [
        _serialize_completion_debug(
            completion,
            prompt_archive=prompt_archives_by_completion_id.get(completion.id),
            tool_call_count=tool_counts_by_completion_id.get(completion.id, 0),
            detail=detail,
        )
        for completion in completions
    ]
    totals = _completion_totals(items)
    return {
        "items": items,
        "returned": len(items),
        "totals_for_returned": totals,
    }


def _serialize_completion_debug(
    completion: PersistentAgentCompletion,
    *,
    prompt_archive,
    tool_call_count: int,
    detail: str,
) -> dict[str, Any]:
    return {
        "id": str(completion.id),
        "timestamp": _iso(completion.created_at),
        "completion_type": completion.completion_type,
        "response_id": sanitize_text(completion.response_id or "", detail=detail) or None,
        "llm_model": completion.llm_model,
        "llm_provider": completion.llm_provider,
        "llm_tool_names": _sanitize_debug_value(completion.llm_tool_names or [], detail=detail),
        "request_duration_ms": completion.request_duration_ms,
        "usage": {
            "prompt_tokens": completion.prompt_tokens,
            "completion_tokens": completion.completion_tokens,
            "total_tokens": completion.total_tokens,
            "cached_tokens": completion.cached_tokens,
        },
        "cost": {
            "input_cost_total": _decimal_to_string(completion.input_cost_total),
            "input_cost_uncached": _decimal_to_string(completion.input_cost_uncached),
            "input_cost_cached": _decimal_to_string(completion.input_cost_cached),
            "output_cost": _decimal_to_string(completion.output_cost),
            "total_cost": _decimal_to_string(completion.total_cost),
            "credits_cost": _decimal_to_string(completion.credits_cost),
        },
        "billing": {
            "plan": completion.billing_plan,
            "is_trial": completion.billing_is_trial,
            "billed": completion.billed,
            "billed_at": _iso(completion.billed_at),
        },
        "thinking_preview": sanitize_text(completion.thinking_content or "", detail=detail) or None,
        "prompt_archive": serialize_prompt_meta(prompt_archive) if prompt_archive else None,
        "tool_call_count": tool_call_count,
    }


def _completion_totals(items: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "total_cost": Decimal("0"),
        "credits_cost": Decimal("0"),
    }
    for item in items:
        usage = item.get("usage") or {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens"):
            value = usage.get(key)
            if isinstance(value, int):
                totals[key] += value
        cost = item.get("cost") or {}
        for key in ("total_cost", "credits_cost"):
            value = cost.get(key)
            if value not in (None, ""):
                totals[key] += Decimal(str(value))
    totals["total_cost"] = _decimal_to_string(totals["total_cost"])
    totals["credits_cost"] = _decimal_to_string(totals["credits_cost"])
    return totals


def _build_eval_debug_artifacts_section(
    agent: PersistentAgent,
    *,
    limit: int,
    bounds: DebugTraceBounds,
    detail: str,
    eval_run_id: UUID | None,
) -> dict[str, Any]:
    queryset = (
        EvalRunTask.objects.filter(run__agent=agent)
        .select_related("run")
        .order_by("-updated_at", "sequence")
    )
    queryset = _apply_created_bounds(queryset, "updated_at", bounds)
    if eval_run_id is not None:
        queryset = queryset.filter(run_id=eval_run_id)
    tasks = list(queryset[:limit])
    return {
        "items": [_serialize_eval_task_debug(task, detail=detail) for task in tasks],
        "returned": len(tasks),
    }


def _serialize_eval_task_debug(task: EvalRunTask, *, detail: str) -> dict[str, Any]:
    run = task.run
    return {
        "id": task.id,
        "run_id": str(task.run_id),
        "scenario_slug": run.scenario_slug,
        "scenario_version": run.scenario_version,
        "status": task.status,
        "sequence": task.sequence,
        "name": task.name,
        "assertion_type": task.assertion_type,
        "expected_summary": sanitize_text(task.expected_summary or "", detail=detail),
        "observed_summary": sanitize_text(task.observed_summary or "", detail=detail),
        "artifact_links": {
            "message_id": str(task.first_message_id) if task.first_message_id else None,
            "step_id": str(task.first_step_id) if task.first_step_id else None,
            "browser_task_id": str(task.first_browser_task_id) if task.first_browser_task_id else None,
        },
        "debug_artifacts": _sanitize_debug_value(task.debug_artifacts or {}, detail=detail),
        "llm_question": sanitize_text(task.llm_question or "", detail=detail),
        "llm_answer": sanitize_text(task.llm_answer or "", detail=detail),
        "llm_model": task.llm_model,
        "started_at": _iso(task.started_at),
        "finished_at": _iso(task.finished_at),
        "usage": {
            "prompt_tokens": task.prompt_tokens,
            "completion_tokens": task.completion_tokens,
            "total_tokens": task.total_tokens,
            "cached_tokens": task.cached_tokens,
        },
        "cost": {
            "input_cost_total": _decimal_to_string(task.input_cost_total),
            "input_cost_uncached": _decimal_to_string(task.input_cost_uncached),
            "input_cost_cached": _decimal_to_string(task.input_cost_cached),
            "output_cost": _decimal_to_string(task.output_cost),
            "total_cost": _decimal_to_string(task.total_cost),
            "credits_cost": _decimal_to_string(task.credits_cost),
        },
    }


def _build_diagnostics_section(payload: dict[str, Any]) -> dict[str, Any]:
    audit_events = payload.get("audit_events") or []
    recent_errors = [event for event in audit_events if event.get("kind") == "error"]
    timeline = payload.get("timeline") or {}
    return {
        "processing_active": bool(timeline.get("processing_active")),
        "recent_error_count": len(recent_errors),
        "recent_error_samples": recent_errors[:5],
        "audit_events_returned": len(audit_events),
        "timeline_events_returned": len(timeline.get("events") or []),
    }


def _sanitize_audit_event(event: dict[str, Any], *, detail: str) -> dict[str, Any]:
    kind = event.get("kind")
    if kind == "completion":
        return {
            "kind": "completion",
            "id": event.get("id"),
            "timestamp": event.get("timestamp"),
            "completion_type": event.get("completion_type"),
            "response_id": sanitize_text(event.get("response_id") or "", detail=detail) or None,
            "prompt_tokens": event.get("prompt_tokens"),
            "completion_tokens": event.get("completion_tokens"),
            "total_tokens": event.get("total_tokens"),
            "cached_tokens": event.get("cached_tokens"),
            "llm_model": event.get("llm_model"),
            "llm_provider": event.get("llm_provider"),
            "llm_tool_names": _sanitize_debug_value(event.get("llm_tool_names") or [], detail=detail),
            "thinking_preview": sanitize_text(event.get("thinking") or "", detail=detail) or None,
            "prompt_archive": _sanitize_debug_value(event.get("prompt_archive"), detail=detail),
            "tool_calls": [
                _sanitize_audit_event(tool_call, detail=detail)
                for tool_call in event.get("tool_calls") or []
                if isinstance(tool_call, dict)
            ],
        }
    if kind == "tool_call":
        return {
            "kind": "tool_call",
            "id": event.get("id"),
            "timestamp": event.get("timestamp"),
            "completion_id": event.get("completion_id"),
            "tool_name": event.get("tool_name"),
            "parameters": _sanitize_debug_value(event.get("parameters"), detail=detail),
            "result_summary": _sanitize_debug_value(event.get("result"), detail=detail),
            "execution_duration_ms": event.get("execution_duration_ms"),
            "prompt_archive": _sanitize_debug_value(event.get("prompt_archive"), detail=detail),
        }
    if kind == "message":
        return {
            "kind": "message",
            "id": event.get("id"),
            "timestamp": event.get("timestamp"),
            "is_outbound": event.get("is_outbound"),
            "channel": event.get("channel"),
            "body_text": sanitize_text(event.get("body_text") or "", detail=detail),
            "body_html": sanitize_text(event.get("body_html") or "", detail=detail) if detail == "verbose" else None,
            "attachments": _sanitize_debug_value(event.get("attachments") or [], detail=detail),
            "peer_agent": _sanitize_debug_value(event.get("peer_agent"), detail=detail),
            "peer_link_id": event.get("peer_link_id"),
            "self_agent_name": event.get("self_agent_name"),
        }
    if kind == "step":
        return {
            "kind": "step",
            "id": event.get("id"),
            "timestamp": event.get("timestamp"),
            "description": sanitize_text(event.get("description") or "", detail=detail),
            "completion_id": event.get("completion_id"),
            "is_system": event.get("is_system"),
            "system_code": event.get("system_code"),
            "system_notes": sanitize_text(event.get("system_notes") or "", detail=detail),
        }
    if kind == "system_message":
        return {
            "kind": "system_message",
            "id": event.get("id"),
            "timestamp": event.get("timestamp"),
            "delivered_at": event.get("delivered_at"),
            "body": sanitize_text(event.get("body") or "", detail=detail),
            "is_active": event.get("is_active"),
            "broadcast_id": event.get("broadcast_id"),
            "created_by": _sanitize_debug_value(event.get("created_by"), detail=detail),
        }
    if kind == "error":
        return {
            "kind": "error",
            "id": event.get("id"),
            "timestamp": event.get("timestamp"),
            "category": event.get("category"),
            "source": event.get("source"),
            "level": event.get("level"),
            "message": sanitize_text(event.get("message") or "", detail=detail),
            "exception_class": event.get("exception_class"),
            "traceback_preview": sanitize_text(event.get("traceback") or "", detail=detail),
            "context": _sanitize_debug_value(event.get("context") or {}, detail=detail),
            "completion_id": event.get("completion_id"),
        }
    return _sanitize_debug_value(event, detail=detail)


def _sanitize_debug_value(value: Any, *, detail: str, depth: int = 0) -> Any:
    if depth > 5:
        return sanitize_text(value, detail="summary")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return _decimal_to_string(value)
    if isinstance(value, str):
        return sanitize_text(value, detail=detail)
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                sanitized[key_text] = REDACTED
            else:
                sanitized[key_text] = _sanitize_debug_value(item, detail=detail, depth=depth + 1)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        values = list(value)
        item_limit = 10 if detail == "summary" else 25 if detail == "standard" else 50
        sanitized_items = [
            _sanitize_debug_value(item, detail=detail, depth=depth + 1)
            for item in values[:item_limit]
        ]
        if len(values) > item_limit:
            sanitized_items.append({"truncated": len(values) - item_limit})
        return sanitized_items
    if hasattr(value, "pk") and hasattr(value, "_meta"):
        return {
            "type": value._meta.label_lower,
            "id": str(value.pk),
        }
    return sanitize_text(value, detail=detail)


def sanitize_text(value: Any, *, detail: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    for pattern in (
        _BEARER_RE,
        _JWT_RE,
        _OPENAI_KEY_RE,
        _STRIPE_SECRET_RE,
        _GITHUB_TOKEN_RE,
        _SLACK_TOKEN_RE,
        _AWS_ACCESS_KEY_RE,
    ):
        text = pattern.sub(REDACTED, text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}={REDACTED}", text)
    text = _HIGH_ENTROPY_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}={REDACTED}", text)
    limit = _string_limit(detail)
    if len(text) > limit:
        return text[: limit - 15] + "...[truncated]"
    return text


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    return normalized.endswith(("_api_key", "_access_token", "_refresh_token", "_secret", "_password", "_private_key"))


def _string_limit(detail: str) -> int:
    if detail == "summary":
        return 400
    if detail == "verbose":
        return 4000
    return 1200


def _apply_created_bounds(queryset, field_name: str, bounds: DebugTraceBounds):
    if bounds.since is not None:
        queryset = queryset.filter(**{f"{field_name}__gte": bounds.since})
    if bounds.until is not None:
        queryset = queryset.filter(**{f"{field_name}__lte": bounds.until})
    return queryset


def _event_in_bounds(event: dict[str, Any], bounds: DebugTraceBounds) -> bool:
    timestamp = event.get("timestamp")
    if not timestamp:
        return True
    parsed = parse_datetime(str(timestamp))
    if parsed is None:
        return True
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    if bounds.since is not None and parsed < bounds.since:
        return False
    if bounds.until is not None and parsed > bounds.until:
        return False
    return True


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt.astimezone(dt_timezone.utc).isoformat().replace("+00:00", "Z")


def _decimal_to_string(value: Decimal | int | str | None) -> str | None:
    if value is None:
        return None
    return str(value)
