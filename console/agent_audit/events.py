from datetime import datetime, timezone as dt_timezone
from typing import Iterable, Literal, Sequence

from django.db.models import Q
from django.utils import timezone

from api.models import (
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
)
from console.agent_audit.serializers import serialize_completion, serialize_message, serialize_prompt_meta, serialize_tool_call

DEFAULT_LIMIT = 30
MAX_LIMIT = 100

AuditKind = Literal["completion", "tool_call", "message", "step", "pivot"]


def _normalize_dt(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _dt_to_iso(dt: datetime | None) -> str | None:
    dt = _normalize_dt(dt)
    if dt is None:
        return None
    dt = dt.astimezone(dt_timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _microsecond_epoch(dt: datetime) -> int:
    normalized = _normalize_dt(dt)
    if normalized is None:
        return 0
    dt_utc = normalized.astimezone(dt_timezone.utc)
    return int(dt_utc.timestamp() * 1_000_000)


class Cursor:
    def __init__(self, value: int, kind: AuditKind, identifier: str):
        self.value = value
        self.kind = kind
        self.identifier = identifier

    def encode(self) -> str:
        ts = datetime.fromtimestamp(self.value / 1_000_000, tz=dt_timezone.utc).isoformat()
        return f"{ts}|{self.kind}|{self.identifier}"

    @staticmethod
    def decode(raw: str | None) -> "Cursor | None":
        if not raw:
            return None
        try:
            ts_str, kind, identifier = raw.split("|", 2)
            dt_val = datetime.fromisoformat(ts_str)
            if timezone.is_naive(dt_val):
                dt_val = timezone.make_aware(dt_val, timezone.get_current_timezone())
            return Cursor(value=_microsecond_epoch(dt_val), kind=kind, identifier=identifier)  # type: ignore[arg-type]
        except Exception:
            return None


def _filter_events_by_cursor(events: list[dict], cursor: Cursor | None) -> list[dict]:
    # Cursor filtering is applied at the query layer; keep full list here.
    return events


def _truncate_events(events: list[dict], limit: int) -> tuple[list[dict], bool]:
    has_more = len(events) > limit
    return (events[:limit], has_more)


def _steps_with_prompt(agent: PersistentAgent, cursor: Cursor | None, limit: int) -> dict:
    multiplier = 20
    qs = (
        PersistentAgentStep.objects.filter(agent=agent, llm_prompt_archive__isnull=False)
        .select_related("llm_prompt_archive", "completion")
        .order_by("-created_at", "-id")
    )
    if cursor:
        dt = datetime.fromtimestamp(cursor.value / 1_000_000, tz=dt_timezone.utc)
        if cursor.kind == "pivot":
            qs = qs.filter(created_at__lt=dt)
        else:
            qs = qs.filter(Q(created_at__lt=dt) | Q(created_at=dt, id__lt=cursor.identifier))
    steps = list(qs[: limit * multiplier])
    return {
        step.completion_id: serialize_prompt_meta(step.llm_prompt_archive)
        for step in steps
        if step.completion_id
    }


def _steps_with_prompt_between(agent: PersistentAgent, start: datetime, end: datetime) -> dict:
    qs = (
        PersistentAgentStep.objects.filter(
            agent=agent,
            llm_prompt_archive__isnull=False,
            created_at__gte=start,
            created_at__lt=end,
        )
        .select_related("llm_prompt_archive", "completion")
        .order_by("-created_at", "-id")
    )
    return {
        step.completion_id: serialize_prompt_meta(step.llm_prompt_archive)
        for step in qs
        if step.completion_id
    }


def _completion_events(agent: PersistentAgent, cursor: Cursor | None, limit: int, prompt_map: dict) -> list[dict]:
    multiplier = 20
    qs = (
        PersistentAgentCompletion.objects.filter(agent=agent)
        .order_by("-created_at", "-id")
    )
    if cursor:
        dt = datetime.fromtimestamp(cursor.value / 1_000_000, tz=dt_timezone.utc)
        if cursor.kind == "pivot":
            qs = qs.filter(created_at__lt=dt)
        else:
            qs = qs.filter(Q(created_at__lt=dt) | Q(created_at=dt, id__lt=cursor.identifier))
    completions = list(qs.select_related(None)[: limit * multiplier])
    events: list[dict] = []
    for completion in completions:
        ts = _normalize_dt(completion.created_at)
        sort_value = _microsecond_epoch(ts) if ts else 0
        prompt_data = prompt_map.get(completion.id)
        if prompt_data and not isinstance(prompt_data, dict):
            prompt_data = serialize_prompt_meta(prompt_data)
        events.append(
            {
                **serialize_completion(completion, prompt_archive=None, tool_calls=[]),
                "prompt_archive": prompt_data,
                "_sort_key": (sort_value, "completion", str(completion.id)),
            }
        )
    return events


def _tool_call_events(agent: PersistentAgent, cursor: Cursor | None, limit: int) -> list[dict]:
    multiplier = 20
    qs = (
        PersistentAgentStep.objects.filter(agent=agent, tool_call__isnull=False)
        .select_related("tool_call", "completion")
        .order_by("-created_at", "-id")
    )
    if cursor:
        dt = datetime.fromtimestamp(cursor.value / 1_000_000, tz=dt_timezone.utc)
        if cursor.kind == "pivot":
            qs = qs.filter(created_at__lt=dt)
        else:
            qs = qs.filter(Q(created_at__lt=dt) | Q(created_at=dt, id__lt=cursor.identifier))
    steps = list(qs[: limit * multiplier])
    events: list[dict] = []
    for step in steps:
        ts = _normalize_dt(step.created_at)
        sort_value = _microsecond_epoch(ts) if ts else 0
        try:
            payload = serialize_tool_call(step)
        except Exception:
            continue
        payload["_sort_key"] = (sort_value, "tool_call", str(step.id))
        events.append(payload)
    return events


def _message_events(agent: PersistentAgent, cursor: Cursor | None, limit: int) -> list[dict]:
    multiplier = 20
    qs = (
        PersistentAgentMessage.objects.filter(owner_agent=agent)
        .select_related("from_endpoint", "to_endpoint", "conversation__peer_link", "peer_agent", "owner_agent")
        .prefetch_related("attachments")
        .order_by("-timestamp", "-seq")
    )
    if cursor:
        dt = datetime.fromtimestamp(cursor.value / 1_000_000, tz=dt_timezone.utc)
        if cursor.kind == "pivot":
            qs = qs.filter(timestamp__lt=dt)
        else:
            qs = qs.filter(Q(timestamp__lt=dt) | Q(timestamp=dt, seq__lt=cursor.identifier))
    messages = list(qs[: limit * multiplier])
    events: list[dict] = []
    for message in messages:
        ts = _normalize_dt(message.timestamp)
        sort_value = _microsecond_epoch(ts) if ts else 0
        payload = serialize_message(message)
        payload["_sort_key"] = (sort_value, "message", message.seq)
        events.append(payload)
    return events


def _step_events(agent: PersistentAgent, cursor: Cursor | None, limit: int) -> list[dict]:
    multiplier = 20
    qs = (
        PersistentAgentStep.objects.filter(agent=agent, tool_call__isnull=True)
        .select_related("completion", "system_step")
        .order_by("-created_at", "-id")
    )
    if cursor:
        dt = datetime.fromtimestamp(cursor.value / 1_000_000, tz=dt_timezone.utc)
        if cursor.kind == "pivot":
            qs = qs.filter(created_at__lt=dt)
        else:
            qs = qs.filter(Q(created_at__lt=dt) | Q(created_at=dt, id__lt=cursor.identifier))

    steps = list(qs[: limit * multiplier])
    events: list[dict] = []
    for step in steps:
        ts = _normalize_dt(step.created_at)
        sort_value = _microsecond_epoch(ts) if ts else 0
        system_step: PersistentAgentSystemStep | None = getattr(step, "system_step", None)
        events.append(
            {
                "kind": "step",
                "id": str(step.id),
                "timestamp": _dt_to_iso(step.created_at),
                "description": step.description or "",
                "completion_id": str(step.completion_id) if step.completion_id else None,
                "is_system": bool(system_step),
                "system_code": system_step.code if system_step else None,
                "system_notes": system_step.notes if system_step else None,
                "_sort_key": (sort_value, "step", str(step.id)),
            }
        )
    return events


def _cursor_from_datetime(dt: datetime | None) -> Cursor | None:
    if dt is None:
        return None
    normalized = _normalize_dt(dt)
    if normalized is None:
        return None
    return Cursor(value=_microsecond_epoch(normalized), kind="pivot", identifier="0")


def fetch_audit_events(
    agent: PersistentAgent,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
    at: datetime | None = None,
) -> tuple[list[dict], bool, str | None]:
    limit = max(1, min(limit, MAX_LIMIT))
    cursor_obj = Cursor.decode(cursor) or _cursor_from_datetime(at)

    prompt_map = _steps_with_prompt(agent, cursor_obj, limit)
    events: list[dict] = []
    events.extend(_completion_events(agent, cursor_obj, limit, prompt_map))
    events.extend(_tool_call_events(agent, cursor_obj, limit))
    events.extend(_message_events(agent, cursor_obj, limit))
    events.extend(_step_events(agent, cursor_obj, limit))

    events.sort(key=lambda e: e.get("_sort_key") or (0, "", ""), reverse=True)
    filtered = _filter_events_by_cursor(events, cursor_obj)
    truncated, has_more = _truncate_events(filtered, limit)

    for ev in truncated:
        ev.pop("_sort_key", None)

    next_cursor: str | None = None
    if has_more and truncated:
        last = truncated[-1]
        last_ts = last.get("timestamp")
        last_kind = last.get("kind")
        last_id = last.get("id")
        if last_ts and last_kind and last_id:
            try:
                dt_val = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                next_cursor = Cursor(_microsecond_epoch(dt_val), last_kind, last_id).encode()
            except Exception:
                next_cursor = None

    return truncated, has_more, next_cursor


def fetch_audit_events_between(agent: PersistentAgent, *, start: datetime, end: datetime) -> list[dict]:
    """Debug helper: return all events between [start, end)."""
    prompt_map = _steps_with_prompt_between(agent, start, end)

    events: list[dict] = []
    # Completions
    for completion in (
        PersistentAgentCompletion.objects.filter(agent=agent, created_at__gte=start, created_at__lt=end)
        .order_by("-created_at", "-id")
    ):
        ts = _normalize_dt(completion.created_at)
        sort_value = _microsecond_epoch(ts) if ts else 0
        prompt_data = prompt_map.get(completion.id)
        if prompt_data and not isinstance(prompt_data, dict):
            prompt_data = serialize_prompt_meta(prompt_data)
        events.append(
            {
                **serialize_completion(completion, prompt_archive=None, tool_calls=[]),
                "prompt_archive": prompt_data,
                "_sort_key": (sort_value, "completion", str(completion.id)),
            }
        )

    # Tool calls
    for step in (
        PersistentAgentStep.objects.filter(agent=agent, tool_call__isnull=False, created_at__gte=start, created_at__lt=end)
        .select_related("tool_call", "completion")
        .order_by("-created_at", "-id")
    ):
        ts = _normalize_dt(step.created_at)
        sort_value = _microsecond_epoch(ts) if ts else 0
        try:
            payload = serialize_tool_call(step)
        except Exception:
            continue
        payload["_sort_key"] = (sort_value, "tool_call", str(step.id))
        events.append(payload)

    # Messages
    for message in (
        PersistentAgentMessage.objects.filter(owner_agent=agent, timestamp__gte=start, timestamp__lt=end)
        .select_related("from_endpoint", "to_endpoint", "conversation__peer_link", "peer_agent", "owner_agent")
        .prefetch_related("attachments")
        .order_by("-timestamp", "-seq")
    ):
        ts = _normalize_dt(message.timestamp)
        sort_value = _microsecond_epoch(ts) if ts else 0
        payload = serialize_message(message)
        payload["_sort_key"] = (sort_value, "message", message.seq)
        events.append(payload)

    # Steps (non-tool)
    for step in (
        PersistentAgentStep.objects.filter(agent=agent, tool_call__isnull=True, created_at__gte=start, created_at__lt=end)
        .select_related("system_step", "completion")
        .order_by("-created_at", "-id")
    ):
        ts = _normalize_dt(step.created_at)
        sort_value = _microsecond_epoch(ts) if ts else 0
        system_step: PersistentAgentSystemStep | None = getattr(step, "system_step", None)
        events.append(
            {
                "kind": "step",
                "id": str(step.id),
                "timestamp": _dt_to_iso(step.created_at),
                "description": step.description or "",
                "completion_id": str(step.completion_id) if step.completion_id else None,
                "is_system": bool(system_step),
                "system_code": system_step.code if system_step else None,
                "system_notes": system_step.notes if system_step else None,
                "_sort_key": (sort_value, "step", str(step.id)),
            }
        )

    events.sort(key=lambda e: e.get("_sort_key") or (0, "", ""), reverse=True)
    for ev in events:
        ev.pop("_sort_key", None)
    return events
