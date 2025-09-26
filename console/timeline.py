from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple

from django.db.models import Q
from django.utils import timezone

from api.models import (
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
)

CursorTuple = Tuple[datetime, str, str]


@dataclass
class TimelineEvent:
    kind: str
    timestamp: datetime
    cursor: str
    sort_key: Tuple[datetime, str, str]
    payload: PersistentAgentMessage | PersistentAgentStep

    @property
    def message(self) -> PersistentAgentMessage | None:
        return self.payload if self.kind == "message" else None

    @property
    def step(self) -> PersistentAgentStep | None:
        return self.payload if self.kind == "step" else None


@dataclass
class TimelineWindow:
    events: List[TimelineEvent]
    has_more_older: bool
    has_more_newer: bool
    window_oldest_cursor: str | None
    window_newest_cursor: str | None


def _encode_cursor(kind: str, timestamp: datetime, discriminator: str) -> str:
    return f"{timestamp.isoformat()}|{kind}|{discriminator}"


def _decode_cursor(cursor: str) -> CursorTuple:
    ts_str, kind, discriminator = cursor.split("|", 2)
    return datetime.fromisoformat(ts_str), kind, discriminator


def compare_cursors(cursor_a: str | None, cursor_b: str | None) -> int:
    """Compare two timeline cursors, returning -1, 0, or 1."""

    if cursor_a is None and cursor_b is None:
        return 0
    if cursor_a is None:
        return -1
    if cursor_b is None:
        return 1

    ts_a, kind_a, discr_a = _decode_cursor(cursor_a)
    ts_b, kind_b, discr_b = _decode_cursor(cursor_b)

    key_a = (ts_a, kind_a, discr_a)
    key_b = (ts_b, kind_b, discr_b)

    if key_a < key_b:
        return -1
    if key_a > key_b:
        return 1
    return 0


def _has_history_before_cursor(
    messages_qs,
    steps_qs,
    cursor: str | None,
) -> bool:
    """Return True if there are timeline records older than the given cursor."""

    if not cursor:
        return False

    ts, kind, discriminator = _decode_cursor(cursor)

    message_filter = Q(timestamp__lt=ts)
    step_filter = Q(created_at__lt=ts)

    if kind == "message":
        message_filter |= Q(timestamp=ts, seq__lt=discriminator)
    elif kind == "step":
        step_filter |= Q(created_at=ts, id__lt=discriminator)

    return (
        messages_qs.filter(message_filter).exists()
        or steps_qs.filter(step_filter).exists()
    )


def _has_history_after_cursor(
    messages_qs,
    steps_qs,
    cursor: str | None,
) -> bool:
    """Return True if there are timeline records newer than the given cursor."""

    if not cursor:
        return (
            messages_qs.exists()
            or steps_qs.exists()
        )

    ts, kind, discriminator = _decode_cursor(cursor)

    message_filter = Q(timestamp__gt=ts)
    step_filter = Q(created_at__gt=ts)

    if kind == "message":
        message_filter |= Q(timestamp=ts, seq__gt=discriminator)
    elif kind == "step":
        step_filter |= Q(created_at=ts, id__gt=discriminator)

    return (
        messages_qs.filter(message_filter).exists()
        or steps_qs.filter(step_filter).exists()
    )


def _build_message_event(message: PersistentAgentMessage) -> TimelineEvent:
    timestamp = message.timestamp or timezone.now()
    discriminator = message.seq or str(message.id)
    cursor = _encode_cursor("message", timestamp, discriminator)
    sort_key = (timestamp, "message", discriminator)
    return TimelineEvent(
        kind="message",
        timestamp=timestamp,
        cursor=cursor,
        sort_key=sort_key,
        payload=message,
    )


def _build_step_event(step: PersistentAgentStep) -> TimelineEvent:
    timestamp = step.created_at or timezone.now()
    discriminator = str(step.id)
    cursor = _encode_cursor("step", timestamp, discriminator)
    sort_key = (timestamp, "step", discriminator)
    return TimelineEvent(
        kind="step",
        timestamp=timestamp,
        cursor=cursor,
        sort_key=sort_key,
        payload=step,
    )


def _get_base_message_qs(agent: PersistentAgent):
    return (
        PersistentAgentMessage.objects
        .filter(owner_agent=agent)
        .select_related(
            "from_endpoint",
            "to_endpoint",
            "conversation",
            "from_endpoint__owner_agent",
            "to_endpoint__owner_agent",
        )
        .prefetch_related("attachments")
    )


def _get_base_step_qs(agent: PersistentAgent):
    return (
        PersistentAgentStep.objects
        .filter(agent=agent)
        .exclude(system_step__code=PersistentAgentSystemStep.Code.PROCESS_EVENTS)
        .exclude(tool_call__tool_name="sleep_until_next_trigger")
        .select_related("agent", "tool_call")
    )


def fetch_timeline_window(
    agent: PersistentAgent,
    *,
    limit: int = 150,
    direction: str = "initial",
    cursor: str | None = None,
) -> TimelineWindow:
    if limit <= 0:
        limit = 1

    messages_qs = _get_base_message_qs(agent)
    steps_qs = _get_base_step_qs(agent)

    has_more_older = False
    has_more_newer = False

    oversample = limit + 10

    if cursor and direction in {"older", "newer"}:
        ts, kind, discriminator = _decode_cursor(cursor)

        if direction == "older":
            message_filter = Q(timestamp__lt=ts)
            step_filter = Q(created_at__lt=ts)
            if kind == "message":
                message_filter |= Q(timestamp=ts, seq__lt=discriminator)
            elif kind == "step":
                step_filter |= Q(created_at=ts, id__lt=discriminator)

            messages = list(messages_qs.filter(message_filter).order_by("-timestamp")[:oversample])
            steps = list(steps_qs.filter(step_filter).order_by("-created_at")[:oversample])

            events = [_build_message_event(m) for m in messages]
            events += [_build_step_event(s) for s in steps]

            events.sort(key=lambda e: e.sort_key, reverse=True)
            if len(events) > limit:
                has_more_older = True
            events = events[:limit]
            events.sort(key=lambda e: e.sort_key)
            if events:
                has_more_newer = True

        else:  # direction == "newer"
            message_filter = Q(timestamp__gt=ts)
            step_filter = Q(created_at__gt=ts)
            if kind == "message":
                message_filter |= Q(timestamp=ts, seq__gt=discriminator)
            elif kind == "step":
                step_filter |= Q(created_at=ts, id__gt=discriminator)

            messages = list(messages_qs.filter(message_filter).order_by("timestamp")[:oversample])
            steps = list(steps_qs.filter(step_filter).order_by("created_at")[:oversample])

            events = [_build_message_event(m) for m in messages]
            events += [_build_step_event(s) for s in steps]

            events.sort(key=lambda e: e.sort_key)
            if len(events) > limit:
                has_more_newer = True
            events = events[:limit]
    else:
        messages = list(messages_qs.order_by("-timestamp")[: oversample * 2])
        steps = list(steps_qs.order_by("-created_at")[: oversample * 2])

        events = [_build_message_event(m) for m in messages]
        events += [_build_step_event(s) for s in steps]

        events.sort(key=lambda e: e.sort_key)
        if len(events) > limit:
            has_more_older = True
        events = events[-limit:]

    events.sort(key=lambda e: e.sort_key)

    window_oldest_cursor = events[0].cursor if events else None
    window_newest_cursor = events[-1].cursor if events else None

    cursor_for_history = window_oldest_cursor or cursor
    if not has_more_older and cursor_for_history:
        if _has_history_before_cursor(messages_qs, steps_qs, cursor_for_history):
            has_more_older = True

    return TimelineWindow(
        events=events,
        has_more_older=has_more_older,
        has_more_newer=has_more_newer,
        window_oldest_cursor=window_oldest_cursor,
        window_newest_cursor=window_newest_cursor,
    )


def has_timeline_history_before(agent: PersistentAgent, cursor: str | None) -> bool:
    """Public helper that mirrors the internal before-cursor check."""

    messages_qs = _get_base_message_qs(agent)
    steps_qs = _get_base_step_qs(agent)
    return _has_history_before_cursor(messages_qs, steps_qs, cursor)


def has_timeline_history_after(agent: PersistentAgent, cursor: str | None) -> bool:
    """Return True if the agent has events newer than the provided cursor."""

    messages_qs = _get_base_message_qs(agent)
    steps_qs = _get_base_step_qs(agent)
    return _has_history_after_cursor(messages_qs, steps_qs, cursor)


def get_timeline_extents(agent: PersistentAgent) -> tuple[str | None, str | None]:
    """Return a tuple of (oldest_cursor, newest_cursor) for the agent timeline."""

    messages_oldest = (
        _get_base_message_qs(agent)
        .order_by("timestamp", "id")
        .first()
    )
    messages_newest = (
        _get_base_message_qs(agent)
        .order_by("-timestamp", "-id")
        .first()
    )

    steps_oldest = (
        _get_base_step_qs(agent)
        .order_by("created_at", "id")
        .first()
    )
    steps_newest = (
        _get_base_step_qs(agent)
        .order_by("-created_at", "-id")
        .first()
    )

    oldest_candidates = []
    newest_candidates = []

    if messages_oldest:
        oldest_candidates.append(_build_message_event(messages_oldest))
    if steps_oldest:
        oldest_candidates.append(_build_step_event(steps_oldest))
    if messages_newest:
        newest_candidates.append(_build_message_event(messages_newest))
    if steps_newest:
        newest_candidates.append(_build_step_event(steps_newest))

    oldest_cursor = None
    if oldest_candidates:
        oldest_cursor = min(oldest_candidates, key=lambda e: e.sort_key).cursor

    newest_cursor = None
    if newest_candidates:
        newest_cursor = max(newest_candidates, key=lambda e: e.sort_key).cursor

    return oldest_cursor, newest_cursor
