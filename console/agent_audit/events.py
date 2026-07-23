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
from console.agent_audit.serializers import (
    serialize_completion,
    serialize_error,
    serialize_message,
    serialize_step,
    serialize_system_message,
    serialize_tool_call,
)
from console.agent_chat.timeline import serialize_message_event, visible_tool_steps_queryset

DEFAULT_LIMIT = 30
MAX_LIMIT = 100
EVENT_KINDS = frozenset({"completion", "tool_call", "message", "step", "system_message", "error", "pivot"})


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


class Cursor:
    def __init__(self, value: int, kind: str, identifier: str):
        self.value = value
        self.kind = kind
        self.identifier = identifier

    def encode(self) -> str:
        return f"{self.value}:{self.kind}:{self.identifier}"

    @staticmethod
    def decode(raw: str | None) -> "Cursor | None":
        if not raw:
            return None
        try:
            value, kind, identifier = raw.split(":", 2)
            if kind not in EVENT_KINDS:
                return None
            return Cursor(int(value), kind, identifier)
        except (TypeError, ValueError):
            pass

        # Accept cursors issued by the removed auditor during a rolling deploy.
        try:
            timestamp, kind, identifier = raw.split("|", 2)
            if kind not in EVENT_KINDS:
                return None
            value = _microsecond_epoch(datetime.fromisoformat(timestamp))
            return Cursor(value, kind, identifier)
        except (TypeError, ValueError):
            return None


def _cursor_datetime(cursor: Cursor) -> datetime:
    return datetime.fromtimestamp(cursor.value / 1_000_000, tz=dt_timezone.utc)


def _coerce_identifier(value: str, field_name: str):
    if field_name == "seq":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return value


def _apply_cursor(queryset, cursor: Cursor | None, *, direction: str, kind: str, dt_field: str, id_field: str):
    if cursor is None:
        return queryset
    pivot = _cursor_datetime(cursor)
    if direction == "older":
        if kind < cursor.kind:
            return queryset.filter(**{f"{dt_field}__lte": pivot})
        if kind > cursor.kind:
            return queryset.filter(**{f"{dt_field}__lt": pivot})
        identifier = _coerce_identifier(cursor.identifier, id_field)
        return queryset.filter(
            Q(**{f"{dt_field}__lt": pivot})
            | Q(**{dt_field: pivot, f"{id_field}__lt": identifier})
        )
    if kind > cursor.kind:
        return queryset.filter(**{f"{dt_field}__gte": pivot})
    if kind < cursor.kind:
        return queryset.filter(**{f"{dt_field}__gt": pivot})
    identifier = _coerce_identifier(cursor.identifier, id_field)
    return queryset.filter(
        Q(**{f"{dt_field}__gt": pivot})
        | Q(**{dt_field: pivot, f"{id_field}__gt": identifier})
    )


def _ordered(queryset, *, direction: str, dt_field: str, id_field: str, limit: int, at: datetime | None):
    if at is not None:
        queryset = queryset.filter(**{f"{dt_field}__lt": _normalize_datetime(at)})
    prefix = "" if direction == "newer" else "-"
    return list(queryset.order_by(f"{prefix}{dt_field}", f"{prefix}{id_field}")[: limit + 1])


def _sort_identifier(kind: str, identifier):
    if kind == "message":
        try:
            return int(identifier)
        except (TypeError, ValueError):
            pass
    return str(identifier)


def _event(payload: dict, *, timestamp: datetime, kind: str, identifier, developer: bool) -> dict:
    cursor = Cursor(_microsecond_epoch(timestamp), kind, str(identifier)).encode()
    return {
        **payload,
        "kind": f"developer_{kind}" if developer else kind,
        "cursor": cursor,
        "_sort_key": (_microsecond_epoch(timestamp), kind, _sort_identifier(kind, identifier)),
    }


def _completion_events(agent, cursor, direction, limit, at, developer):
    queryset = _apply_cursor(
        PersistentAgentCompletion.objects.filter(agent=agent).select_related("prompt_archive"),
        cursor,
        direction=direction,
        kind="completion",
        dt_field="created_at",
        id_field="id",
    )
    completions = _ordered(queryset, direction=direction, dt_field="created_at", id_field="id", limit=limit, at=at)
    prompt_archives = {
        step.completion_id: step.llm_prompt_archive
        for step in (
            PersistentAgentStep.objects
            .filter(completion_id__in=[completion.id for completion in completions], llm_prompt_archive__isnull=False)
            .select_related("llm_prompt_archive")
            .order_by("created_at")
        )
    }
    return [
        _event(
            serialize_completion(
                completion,
                prompt_archive=completion.prompt_archive or prompt_archives.get(completion.id),
                tool_calls=[],
            ),
            timestamp=completion.created_at,
            kind="completion",
            identifier=completion.id,
            developer=developer,
        )
        for completion in completions
    ]


def _tool_events(agent, cursor, direction, limit, at, developer):
    queryset = (
        visible_tool_steps_queryset(agent)
        .select_related("tool_call", "completion", "llm_prompt_archive")
        .prefetch_related("human_input_requests")
    )
    queryset = _apply_cursor(queryset, cursor, direction=direction, kind="tool_call", dt_field="created_at", id_field="id")
    steps = _ordered(queryset, direction=direction, dt_field="created_at", id_field="id", limit=limit, at=at)
    return [
        _event(serialize_tool_call(step), timestamp=step.created_at, kind="tool_call", identifier=step.id, developer=developer)
        for step in steps
    ]


def _message_events(agent, cursor, direction, limit, at, developer):
    queryset = (
        PersistentAgentMessage.objects
        .filter(owner_agent=agent)
        .select_related("from_endpoint", "to_endpoint", "conversation__peer_link", "peer_agent", "owner_agent")
        .prefetch_related("attachments__filespace_node")
    )
    queryset = _apply_cursor(queryset, cursor, direction=direction, kind="message", dt_field="timestamp", id_field="seq")
    messages = _ordered(queryset, direction=direction, dt_field="timestamp", id_field="seq", limit=limit, at=at)
    events = []
    for message in messages:
        if developer:
            payload = serialize_message_event(message)
            payload["_sort_key"] = (_microsecond_epoch(message.timestamp), "message", message.seq)
            events.append(payload)
        else:
            events.append(
                _event(serialize_message(message), timestamp=message.timestamp, kind="message", identifier=message.seq, developer=False)
            )
    return events


def _step_events(agent, cursor, direction, limit, at, developer):
    queryset = (
        PersistentAgentStep.objects
        .filter(agent=agent, tool_call__isnull=True)
        .select_related("completion", "system_step")
    )
    queryset = _apply_cursor(queryset, cursor, direction=direction, kind="step", dt_field="created_at", id_field="id")
    steps = _ordered(queryset, direction=direction, dt_field="created_at", id_field="id", limit=limit, at=at)
    return [
        _event(serialize_step(step), timestamp=step.created_at, kind="step", identifier=step.id, developer=developer)
        for step in steps
        if not (step.description or "").startswith("Tool call")
    ]


def _system_message_events(agent, cursor, direction, limit, at, developer):
    queryset = PersistentAgentSystemMessage.objects.filter(agent=agent).select_related("created_by")
    queryset = _apply_cursor(queryset, cursor, direction=direction, kind="system_message", dt_field="created_at", id_field="id")
    messages = _ordered(queryset, direction=direction, dt_field="created_at", id_field="id", limit=limit, at=at)
    return [
        _event(serialize_system_message(message), timestamp=message.created_at, kind="system_message", identifier=message.id, developer=developer)
        for message in messages
    ]


def _error_events(agent, cursor, direction, limit, at, developer):
    queryset = PersistentAgentError.objects.filter(agent=agent)
    queryset = _apply_cursor(queryset, cursor, direction=direction, kind="error", dt_field="created_at", id_field="id")
    errors = _ordered(queryset, direction=direction, dt_field="created_at", id_field="id", limit=limit, at=at)
    return [
        _event(serialize_error(error), timestamp=error.created_at, kind="error", identifier=error.id, developer=developer)
        for error in errors
    ]


def fetch_event_page(
    agent: PersistentAgent,
    *,
    cursor: str | None = None,
    direction: str = "older",
    limit: int = DEFAULT_LIMIT,
    at: datetime | None = None,
    developer: bool = False,
) -> tuple[list[dict], bool]:
    limit = max(1, min(limit, MAX_LIMIT))
    cursor_payload = Cursor.decode(cursor)
    query_direction = "newer" if direction == "newer" else "older"
    upper_bound = at if cursor_payload is None else None
    args = (agent, cursor_payload, query_direction, limit, upper_bound, developer)
    events = [
        *_completion_events(*args),
        *_tool_events(*args),
        *_message_events(*args),
        *_step_events(*args),
        *_system_message_events(*args),
        *_error_events(*args),
    ]
    events.sort(key=lambda event: event["_sort_key"])
    has_more = len(events) > limit
    events = events[:limit] if query_direction == "newer" else events[-limit:]
    for event in events:
        event.pop("_sort_key", None)
    return events, has_more


def fetch_audit_events(
    agent: PersistentAgent,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
    at: datetime | None = None,
) -> tuple[list[dict], bool, str | None]:
    events, has_more = fetch_event_page(agent, cursor=cursor, limit=limit, at=at)
    newest_first = list(reversed(events))
    next_cursor = events[0]["cursor"] if has_more and events else None
    return newest_first, has_more, next_cursor
