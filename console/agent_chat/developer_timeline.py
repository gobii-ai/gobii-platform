from datetime import datetime, timezone as dt_timezone
import uuid

from django.db.models import Q
from django.utils import timezone

from api.models import (
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentError,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemMessage,
)
from console.agent_audit.events import Cursor
from console.agent_audit.serializers import (
    serialize_completion,
    serialize_error,
    serialize_message,
    serialize_step,
    serialize_system_message,
    serialize_tool_call,
)
from console.agent_chat.timeline import (
    ProcessingSnapshot,
    TimelineWindow,
    build_processing_snapshot,
    serialize_message_event,
    serialize_plan_snapshot,
)


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _microsecond_epoch(value: datetime) -> int:
    normalized = _normalize_datetime(value)
    if normalized is None:
        return 0
    return int(normalized.astimezone(dt_timezone.utc).timestamp() * 1_000_000)


def _cursor_datetime(cursor: Cursor) -> datetime:
    return datetime.fromtimestamp(cursor.value / 1_000_000, tz=dt_timezone.utc)


def _coerce_identifier(value, field_name: str):
    if field_name == "seq":
        return str(value)
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return value


def _apply_cursor(qs, cursor: Cursor | None, *, direction: str, kind: str, dt_field: str, id_field: str):
    if cursor is None:
        return qs
    pivot = _cursor_datetime(cursor)
    if direction == "older":
        if kind < cursor.kind:
            return qs.filter(**{f"{dt_field}__lte": pivot})
        if kind > cursor.kind:
            return qs.filter(**{f"{dt_field}__lt": pivot})
        identifier = _coerce_identifier(cursor.identifier, id_field)
        return qs.filter(
            Q(**{f"{dt_field}__lt": pivot})
            | Q(**{dt_field: pivot, f"{id_field}__lt": identifier})
        )
    if kind > cursor.kind:
        return qs.filter(**{f"{dt_field}__gte": pivot})
    if kind < cursor.kind:
        return qs.filter(**{f"{dt_field}__gt": pivot})
    identifier = _coerce_identifier(cursor.identifier, id_field)
    return qs.filter(
        Q(**{f"{dt_field}__gt": pivot})
        | Q(**{dt_field: pivot, f"{id_field}__gt": identifier})
    )


def _ordered(qs, *, direction: str, dt_field: str, id_field: str, limit: int):
    prefix = "" if direction == "newer" else "-"
    return list(qs.order_by(f"{prefix}{dt_field}", f"{prefix}{id_field}")[: limit + 1])


def _event(payload: dict, *, timestamp: datetime, kind: str, identifier) -> dict:
    cursor = Cursor(_microsecond_epoch(timestamp), kind, str(identifier)).encode()
    return {
        **payload,
        "kind": f"developer_{kind}",
        "cursor": cursor,
        "_sort_key": (_microsecond_epoch(timestamp), kind, str(identifier)),
    }


def _completion_events(agent: PersistentAgent, cursor: Cursor | None, direction: str, limit: int) -> list[dict]:
    qs = _apply_cursor(
        PersistentAgentCompletion.objects.filter(agent=agent),
        cursor,
        direction=direction,
        kind="completion",
        dt_field="created_at",
        id_field="id",
    )
    completions = _ordered(qs, direction=direction, dt_field="created_at", id_field="id", limit=limit)
    completion_ids = [completion.id for completion in completions]
    prompt_archives = {
        step.completion_id: step.llm_prompt_archive
        for step in (
            PersistentAgentStep.objects
            .filter(completion_id__in=completion_ids, llm_prompt_archive__isnull=False)
            .select_related("llm_prompt_archive")
            .order_by("created_at")
        )
    }
    return [
        _event(
            serialize_completion(
                completion,
                prompt_archive=prompt_archives.get(completion.id),
                tool_calls=[],
            ),
            timestamp=completion.created_at,
            kind="completion",
            identifier=completion.id,
        )
        for completion in completions
    ]


def _tool_events(agent: PersistentAgent, cursor: Cursor | None, direction: str, limit: int) -> list[dict]:
    qs = (
        PersistentAgentStep.objects
        .filter(agent=agent, tool_call__isnull=False)
        .select_related("tool_call", "completion", "llm_prompt_archive")
        .prefetch_related("human_input_requests")
    )
    qs = _apply_cursor(qs, cursor, direction=direction, kind="tool_call", dt_field="created_at", id_field="id")
    steps = _ordered(qs, direction=direction, dt_field="created_at", id_field="id", limit=limit)
    events = []
    for step in steps:
        payload = serialize_tool_call(step)
        events.append(_event(payload, timestamp=step.created_at, kind="tool_call", identifier=step.id))
    return events


def _message_events(agent: PersistentAgent, cursor: Cursor | None, direction: str, limit: int) -> list[dict]:
    qs = (
        PersistentAgentMessage.objects
        .filter(owner_agent=agent)
        .select_related("from_endpoint", "to_endpoint", "conversation__peer_link", "peer_agent", "owner_agent")
        .prefetch_related("attachments__filespace_node")
    )
    qs = _apply_cursor(qs, cursor, direction=direction, kind="message", dt_field="timestamp", id_field="seq")
    messages = _ordered(qs, direction=direction, dt_field="timestamp", id_field="seq", limit=limit)
    events = []
    for message in messages:
        payload = serialize_message(message)
        regular_message = serialize_message_event(message)["message"]
        # Developer Mode should render message bodies exactly as regular chat does,
        # including generated and sanitized HTML for email bodies.
        payload["body_html"] = regular_message["bodyHtml"]
        payload["body_text"] = regular_message["bodyText"]
        events.append(_event(payload, timestamp=message.timestamp, kind="message", identifier=message.seq))
    return events


def _step_events(agent: PersistentAgent, cursor: Cursor | None, direction: str, limit: int) -> list[dict]:
    qs = (
        PersistentAgentStep.objects
        .filter(agent=agent, tool_call__isnull=True)
        .select_related("completion", "system_step")
    )
    qs = _apply_cursor(qs, cursor, direction=direction, kind="step", dt_field="created_at", id_field="id")
    steps = _ordered(qs, direction=direction, dt_field="created_at", id_field="id", limit=limit)
    return [
        _event(serialize_step(step), timestamp=step.created_at, kind="step", identifier=step.id)
        for step in steps
        if not (step.description or "").startswith("Tool call")
    ]


def _system_message_events(agent: PersistentAgent, cursor: Cursor | None, direction: str, limit: int) -> list[dict]:
    qs = PersistentAgentSystemMessage.objects.filter(agent=agent).select_related("created_by")
    qs = _apply_cursor(qs, cursor, direction=direction, kind="system_message", dt_field="created_at", id_field="id")
    messages = _ordered(qs, direction=direction, dt_field="created_at", id_field="id", limit=limit)
    return [
        _event(
            serialize_system_message(message),
            timestamp=message.created_at,
            kind="system_message",
            identifier=message.id,
        )
        for message in messages
    ]


def _error_events(agent: PersistentAgent, cursor: Cursor | None, direction: str, limit: int) -> list[dict]:
    qs = PersistentAgentError.objects.filter(agent=agent)
    qs = _apply_cursor(qs, cursor, direction=direction, kind="error", dt_field="created_at", id_field="id")
    errors = _ordered(qs, direction=direction, dt_field="created_at", id_field="id", limit=limit)
    return [
        _event(serialize_error(error), timestamp=error.created_at, kind="error", identifier=error.id)
        for error in errors
    ]


def fetch_developer_timeline_window(
    agent: PersistentAgent,
    *,
    cursor: str | None = None,
    direction: str = "initial",
    limit: int = 40,
) -> TimelineWindow:
    limit = max(1, min(limit, 100))
    cursor_payload = Cursor.decode(cursor)
    query_direction = "newer" if direction == "newer" else "older"
    events = [
        *_completion_events(agent, cursor_payload, query_direction, limit),
        *_tool_events(agent, cursor_payload, query_direction, limit),
        *_message_events(agent, cursor_payload, query_direction, limit),
        *_step_events(agent, cursor_payload, query_direction, limit),
        *_system_message_events(agent, cursor_payload, query_direction, limit),
        *_error_events(agent, cursor_payload, query_direction, limit),
    ]
    events.sort(key=lambda event: event["_sort_key"])
    has_more_in_direction = len(events) > limit
    if direction in {"initial", "older"}:
        events = events[-limit:]
    else:
        events = events[:limit]
    for event in events:
        event.pop("_sort_key", None)

    oldest_cursor = events[0]["cursor"] if events else None
    newest_cursor = events[-1]["cursor"] if events else None
    processing_snapshot: ProcessingSnapshot = build_processing_snapshot(agent)
    return TimelineWindow(
        events=events,
        oldest_cursor=oldest_cursor,
        newest_cursor=newest_cursor,
        has_more_older=has_more_in_direction if direction in {"initial", "older"} else bool(cursor),
        has_more_newer=has_more_in_direction if direction == "newer" else False,
        processing_snapshot=processing_snapshot,
        current_plan=serialize_plan_snapshot(agent),
    )
