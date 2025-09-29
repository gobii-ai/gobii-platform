from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
import uuid
from typing import Iterable, Literal, Sequence

from django.contrib.humanize.templatetags.humanize import naturaltime
from django.db.models import Q
from django.utils import timezone
from django.utils.html import escape
from django.utils.timesince import timesince

from api.models import (
    BrowserUseAgentTask,
    BrowserUseAgentTaskQuerySet,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentMessageAttachment,
    PersistentAgentStep,
    PersistentAgentToolCall,
    ToolFriendlyName,
)

DEFAULT_PAGE_SIZE = 40
MAX_PAGE_SIZE = 100
COLLAPSE_THRESHOLD = 3

TimelineDirection = Literal["initial", "older", "newer"]


@dataclass(slots=True)
class CursorPayload:
    value: int
    kind: Literal["message", "step"]
    identifier: str

    def encode(self) -> str:
        return f"{self.value}:{self.kind}:{self.identifier}"

    @staticmethod
    def decode(raw: str | None) -> "CursorPayload | None":
        if not raw:
            return None
        try:
            value_str, kind, identifier = raw.split(":", 2)
            return CursorPayload(value=int(value_str), kind=kind, identifier=identifier)
        except Exception:
            return None


@dataclass(slots=True)
class MessageEnvelope:
    sort_key: tuple[int, str, str]
    cursor: CursorPayload
    message: PersistentAgentMessage


@dataclass(slots=True)
class StepEnvelope:
    sort_key: tuple[int, str, str]
    cursor: CursorPayload
    step: PersistentAgentStep
    tool_call: PersistentAgentToolCall


@dataclass(slots=True)
class TimelineWindow:
    events: list[dict]
    oldest_cursor: str | None
    newest_cursor: str | None
    has_more_older: bool
    has_more_newer: bool
    processing_active: bool


def _humanize_body(body: str) -> str:
    escaped = escape(body)
    return escaped.replace("\n", "<br />")


def _format_timestamp(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt.astimezone(dt_timezone.utc).isoformat().replace("+00:00", "Z")


def _relative_timestamp(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    now = timezone.now()
    if dt > now:
        return "moments ago"
    try:
        # `naturaltime` may return a lazy translation object; convert to plain str for serialization.
        humanized = naturaltime(dt)
    except Exception:
        # Fallback to timesince when humanize isn't available
        return f"{timesince(dt, now)} ago"
    return str(humanized)


def _microsecond_epoch(dt: datetime) -> int:
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    dt_utc = dt.astimezone(dt_timezone.utc)
    return int(dt_utc.timestamp() * 1_000_000)


def _friendly_tool_label(tool_name: str | None) -> str:
    if not tool_name:
        return "Tool call"
    lookup = ToolFriendlyName.objects.filter(tool_name=tool_name).values_list("display_name", flat=True).first()
    if lookup:
        return lookup
    return tool_name.replace("_", " ").title()


TOOL_ICON_LIBRARY: dict[str, dict[str, object]] = {
    "email": {
        "iconPaths": [
            "M3 8l9 6 9-6",
            "M5 5h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2z",
        ],
        "iconBg": "bg-indigo-50",
        "iconColor": "text-indigo-600",
    },
    "slack": {
        "iconPaths": [
            "M7 9h4a2 2 0 002-2V5a2 2 0 10-4 0v2H7a2 2 0 100 4z",
            "M9 17v-4a2 2 0 00-2-2H5a2 2 0 100 4h2v2a2 2 0 104 0z",
            "M15 7v2h2a2 2 0 100-4h-2V5a2 2 0 10-4 0v2a2 2 0 002 2z",
            "M15 15h-2v2a2 2 0 104 0v-2a2 2 0 00-2-2z",
        ],
        "iconBg": "bg-fuchsia-50",
        "iconColor": "text-fuchsia-600",
    },
    "browser": {
        "iconPaths": [
            "M4 6h16a2 2 0 012 2v8a2 2 0 01-2 2H4a2 2 0 01-2-2V8a2 2 0 012-2z",
            "M2 10h20",
        ],
        "iconBg": "bg-emerald-50",
        "iconColor": "text-emerald-600",
    },
    "database": {
        "iconPaths": [
            "M5 7c0-2.21 3.582-4 8-4s8 1.79 8 4-3.582 4-8 4-8-1.79-8-4z",
            "M5 12c0 2.21 3.582 4 8 4s8-1.79 8-4",
            "M5 17c0 2.21 3.582 4 8 4s8-1.79 8-4",
        ],
        "iconBg": "bg-sky-50",
        "iconColor": "text-sky-600",
    },
    "doc": {
        "iconPaths": [
            "M7 4h7l5 5v11a2 2 0 01-2 2H7a2 2 0 01-2-2V6a2 2 0 012-2z",
            "M14 3v6h6",
        ],
        "iconBg": "bg-amber-50",
        "iconColor": "text-amber-600",
    },
    "default": {
        "iconPaths": [
            "M4 6h16",
            "M4 12h16",
            "M4 18h16",
        ],
        "iconBg": "bg-slate-100",
        "iconColor": "text-slate-600",
    },
}


def _tool_icon_for(name: str | None) -> dict[str, object]:
    if not name:
        return TOOL_ICON_LIBRARY["default"].copy()
    lower = name.lower()
    if "email" in lower or "mail" in lower:
        key = "email"
    elif "slack" in lower or "discord" in lower:
        key = "slack"
    elif any(word in lower for word in ("http", "browser", "crawl", "fetch")):
        key = "browser"
    elif any(word in lower for word in ("sql", "database", "db")):
        key = "database"
    elif any(word in lower for word in ("doc", "sheet", "drive", "notion")):
        key = "doc"
    else:
        key = "default"
    data = TOOL_ICON_LIBRARY[key].copy()
    data.setdefault("iconPaths", TOOL_ICON_LIBRARY["default"]["iconPaths"])
    return data


def _serialize_attachment(att: PersistentAgentMessageAttachment) -> dict:
    size_label = None
    try:
        from django.template.defaultfilters import filesizeformat

        size_label = filesizeformat(att.file_size)
    except Exception:
        size_label = None
    return {
        "id": str(att.id),
        "filename": att.filename,
        "url": att.file.url if att.file else "",
        "fileSizeLabel": size_label,
    }


def _serialize_message(env: MessageEnvelope) -> dict:
    message = env.message
    timestamp = message.timestamp
    channel = "web"
    if message.conversation_id:
        channel = message.conversation.channel
    elif message.from_endpoint_id:
        channel = message.from_endpoint.channel
    attachments = [_serialize_attachment(att) for att in message.attachments.all()]
    return {
        "kind": "message",
        "cursor": env.cursor.encode(),
        "timestamp": _format_timestamp(timestamp),
        "message": {
            "id": str(message.id),
            "cursor": env.cursor.encode(),
            "bodyHtml": _humanize_body(message.body or ""),
            "bodyText": message.body or "",
            "isOutbound": bool(message.is_outbound),
            "channel": channel,
            "attachments": attachments,
            "timestamp": _format_timestamp(timestamp),
            "relativeTimestamp": _relative_timestamp(timestamp),
        },
    }


def _serialize_step_entry(env: StepEnvelope) -> dict:
    step = env.step
    tool_call = env.tool_call
    tool_name = tool_call.tool_name or ""
    meta = _tool_icon_for(tool_name)
    meta["label"] = _friendly_tool_label(tool_name)
    return {
        "id": str(step.id),
        "cursor": env.cursor.encode(),
        "timestamp": _format_timestamp(step.created_at),
        "caption": step.description or meta["label"],
        "toolName": tool_name,
        "meta": meta,
        "parameters": tool_call.tool_params,
        "result": tool_call.result,
    }


def _build_cluster(entries: Sequence[StepEnvelope]) -> dict:
    serialized_entries = [_serialize_step_entry(env) for env in entries]
    earliest = entries[0]
    latest = entries[-1]
    return {
        "kind": "steps",
        "cursor": earliest.cursor.encode(),
        "entryCount": len(serialized_entries),
        "collapsible": len(serialized_entries) >= COLLAPSE_THRESHOLD,
        "collapseThreshold": COLLAPSE_THRESHOLD,
        "earliestTimestamp": serialized_entries[0]["timestamp"],
        "latestTimestamp": serialized_entries[-1]["timestamp"],
        "entries": serialized_entries,
    }


def _messages_queryset(agent: PersistentAgent, direction: TimelineDirection, cursor: CursorPayload | None) -> Sequence[PersistentAgentMessage]:
    limit = MAX_PAGE_SIZE * 3
    qs = (
        PersistentAgentMessage.objects.filter(owner_agent=agent)
        .select_related("from_endpoint", "to_endpoint", "conversation")
        .prefetch_related("attachments")
        .order_by("-timestamp", "-seq")
    )
    if direction == "older" and cursor is not None:
        qs = qs.filter(timestamp__lte=_dt_from_cursor(cursor))
    elif direction == "newer" and cursor is not None:
        qs = qs.filter(timestamp__gte=_dt_from_cursor(cursor))
    return list(qs[:limit])


def _steps_queryset(agent: PersistentAgent, direction: TimelineDirection, cursor: CursorPayload | None) -> Sequence[PersistentAgentStep]:
    limit = MAX_PAGE_SIZE * 3
    qs = (
        PersistentAgentStep.objects.filter(agent=agent, tool_call__isnull=False)
        .select_related("tool_call")
        .order_by("-created_at", "-id")
    )
    if direction == "older" and cursor is not None:
        qs = qs.filter(created_at__lte=_dt_from_cursor(cursor))
    elif direction == "newer" and cursor is not None:
        qs = qs.filter(created_at__gte=_dt_from_cursor(cursor))
    return list(qs[:limit])


def _dt_from_cursor(cursor: CursorPayload) -> datetime:
    micros = cursor.value
    return datetime.fromtimestamp(micros / 1_000_000, tz=dt_timezone.utc)


def _envelop_messages(messages: Iterable[PersistentAgentMessage]) -> list[MessageEnvelope]:
    envelopes: list[MessageEnvelope] = []
    for message in messages:
        sort_value = _microsecond_epoch(message.timestamp)
        cursor = CursorPayload(value=sort_value, kind="message", identifier=message.seq)
        envelopes.append(
            MessageEnvelope(
                sort_key=(sort_value, "message", message.seq),
                cursor=cursor,
                message=message,
            )
        )
    return envelopes


def _envelop_steps(steps: Iterable[PersistentAgentStep]) -> list[StepEnvelope]:
    envelopes: list[StepEnvelope] = []
    for step in steps:
        if not hasattr(step, "tool_call") or step.tool_call is None:
            continue
        sort_value = _microsecond_epoch(step.created_at)
        cursor = CursorPayload(value=sort_value, kind="step", identifier=str(step.id))
        envelopes.append(
            StepEnvelope(
                sort_key=(sort_value, "step", str(step.id)),
                cursor=cursor,
                step=step,
                tool_call=step.tool_call,
            )
        )
    return envelopes


def _filter_by_direction(
    envelopes: Sequence[MessageEnvelope | StepEnvelope],
    direction: TimelineDirection,
    cursor: CursorPayload | None,
) -> list[MessageEnvelope | StepEnvelope]:
    if not cursor or direction == "initial":
        return list(envelopes)
    pivot = (cursor.value, cursor.kind, cursor.identifier)
    filtered: list[MessageEnvelope | StepEnvelope] = []
    for env in envelopes:
        key = env.sort_key
        if direction == "older" and key < pivot:
            filtered.append(env)
        elif direction == "newer" and key > pivot:
            filtered.append(env)
    return filtered


def _truncate_for_direction(
    envelopes: list[MessageEnvelope | StepEnvelope],
    direction: TimelineDirection,
    limit: int,
) -> list[MessageEnvelope | StepEnvelope]:
    if not envelopes:
        return []
    if direction == "older":
        return envelopes[-limit:]
    if direction == "newer":
        return envelopes[:limit]
    # initial snapshot -> latest `limit` events
    return envelopes[-limit:]


def _has_more_before(agent: PersistentAgent, cursor: CursorPayload | None) -> bool:
    if cursor is None:
        return False
    dt = _dt_from_cursor(cursor)
    message_exists = PersistentAgentMessage.objects.filter(
        owner_agent=agent,
        timestamp__lt=dt,
    ).exists()
    if cursor.kind == "message":
        message_exists = message_exists or PersistentAgentMessage.objects.filter(
            owner_agent=agent,
            timestamp=dt,
            seq__lt=cursor.identifier,
        ).exists()
    step_exists = PersistentAgentStep.objects.filter(
        agent=agent,
        created_at__lt=dt,
    ).exists()
    if cursor.kind == "step":
        try:
            uuid_identifier = uuid.UUID(cursor.identifier)
            step_exists = step_exists or PersistentAgentStep.objects.filter(
                agent=agent,
                created_at=dt,
                id__lt=uuid_identifier,
            ).exists()
        except Exception:
            pass
    return message_exists or step_exists


def _has_more_after(agent: PersistentAgent, cursor: CursorPayload | None) -> bool:
    if cursor is None:
        return False
    dt = _dt_from_cursor(cursor)
    message_exists = PersistentAgentMessage.objects.filter(
        owner_agent=agent,
        timestamp__gt=dt,
    ).exists()
    if cursor.kind == "message":
        message_exists = message_exists or PersistentAgentMessage.objects.filter(
            owner_agent=agent,
            timestamp=dt,
            seq__gt=cursor.identifier,
        ).exists()
    step_exists = PersistentAgentStep.objects.filter(
        agent=agent,
        created_at__gt=dt,
    ).exists()
    if cursor.kind == "step":
        try:
            uuid_identifier = uuid.UUID(cursor.identifier)
            step_exists = step_exists or PersistentAgentStep.objects.filter(
                agent=agent,
                created_at=dt,
                id__gt=uuid_identifier,
            ).exists()
        except Exception:
            pass
    return message_exists or step_exists


def _compute_processing(agent: PersistentAgent) -> bool:
    if not getattr(agent, "browser_use_agent_id", None):
        return False
    task_qs: BrowserUseAgentTaskQuerySet = BrowserUseAgentTask.objects
    return task_qs.filter(
        agent=agent.browser_use_agent,
        status__in=[
            BrowserUseAgentTask.StatusChoices.PENDING,
            BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
        ],
        is_deleted=False,
    ).exists()


def fetch_timeline_window(
    agent: PersistentAgent,
    *,
    cursor: str | None = None,
    direction: TimelineDirection = "initial",
    limit: int = DEFAULT_PAGE_SIZE,
) -> TimelineWindow:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    cursor_payload = CursorPayload.decode(cursor)

    message_envelopes = _envelop_messages(_messages_queryset(agent, direction, cursor_payload))
    step_envelopes = _envelop_steps(_steps_queryset(agent, direction, cursor_payload))

    merged: list[MessageEnvelope | StepEnvelope] = sorted(
        [*message_envelopes, *step_envelopes],
        key=lambda env: env.sort_key,
    )

    filtered = _filter_by_direction(merged, direction, cursor_payload)
    truncated = _truncate_for_direction(filtered, direction, limit)

    # Ensure chronological order for presentation
    truncated.sort(key=lambda env: env.sort_key)

    timeline_events: list[dict] = []
    cluster_buffer: list[StepEnvelope] = []
    for env in truncated:
        if isinstance(env, StepEnvelope):
            cluster_buffer.append(env)
            continue
        if cluster_buffer:
            timeline_events.append(_build_cluster(cluster_buffer))
            cluster_buffer = []
        timeline_events.append(_serialize_message(env))
    if cluster_buffer:
        timeline_events.append(_build_cluster(cluster_buffer))

    oldest_cursor = truncated[0].cursor if truncated else None
    newest_cursor = truncated[-1].cursor if truncated else None

    has_more_older = _has_more_before(agent, oldest_cursor)
    has_more_newer = _has_more_after(agent, newest_cursor)

    return TimelineWindow(
        events=timeline_events,
        oldest_cursor=oldest_cursor.encode() if oldest_cursor else None,
        newest_cursor=newest_cursor.encode() if newest_cursor else None,
        has_more_older=has_more_older,
        has_more_newer=has_more_newer,
        processing_active=_compute_processing(agent),
    )


def serialize_message_event(message: PersistentAgentMessage) -> dict:
    envelope = _envelop_messages([message])[0]
    return _serialize_message(envelope)


def serialize_step_entry(step: PersistentAgentStep) -> dict:
    envelopes = _envelop_steps([step])
    if not envelopes:
        raise ValueError("Step does not include a tool call")
    return _serialize_step_entry(envelopes[0])


def compute_processing_status(agent: PersistentAgent) -> bool:
    """Expose processing state computation for external callers."""
    return _compute_processing(agent)


def build_tool_cluster_from_steps(steps: Sequence[PersistentAgentStep]) -> dict:
    envelopes = _envelop_steps(steps)
    if not envelopes:
        raise ValueError("No tool calls available")
    return _build_cluster(envelopes)
