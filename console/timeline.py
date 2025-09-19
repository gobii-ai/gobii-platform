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

    return TimelineWindow(
        events=events,
        has_more_older=has_more_older,
        has_more_newer=has_more_newer,
        window_oldest_cursor=window_oldest_cursor,
        window_newest_cursor=window_newest_cursor,
    )
