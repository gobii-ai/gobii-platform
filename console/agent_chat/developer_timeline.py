from api.models import PersistentAgent, PersistentAgentMessage
from console.agent_audit.events import fetch_event_page
from console.agent_chat.timeline import (
    TimelineWindow,
    build_processing_snapshot,
    serialize_plan_snapshot,
    serialize_message_event,
    CursorPayload,
    _has_more_after,
    _has_more_before,
    _microsecond_epoch,
)


def fetch_developer_timeline_window(
    agent: PersistentAgent,
    *,
    cursor: str | None = None,
    direction: str = "initial",
    limit: int = 40,
) -> TimelineWindow:
    events, has_more = fetch_event_page(
        agent,
        cursor=cursor,
        direction=direction,
        limit=limit,
        developer=True,
    )
    return TimelineWindow(
        events=events,
        oldest_cursor=events[0]["cursor"] if events else None,
        newest_cursor=events[-1]["cursor"] if events else None,
        has_more_older=has_more if direction in {"initial", "older"} else bool(cursor),
        has_more_newer=has_more if direction == "newer" else False,
        processing_snapshot=build_processing_snapshot(agent),
        current_plan=serialize_plan_snapshot(agent),
    )


def fetch_developer_timeline_window_around_message(
    agent: PersistentAgent,
    message: PersistentAgentMessage,
    *,
    limit: int = 40,
) -> TimelineWindow:
    limit = max(1, min(limit, 100))
    anchor_cursor = CursorPayload(
        value=_microsecond_epoch(message.timestamp),
        kind="message",
        identifier=message.seq,
    )
    older_limit = limit // 2
    newer_limit = max(0, limit - older_limit - 1)
    older = fetch_developer_timeline_window(
        agent,
        cursor=anchor_cursor.encode(),
        direction="older",
        limit=max(1, older_limit),
    )
    newer = fetch_developer_timeline_window(
        agent,
        cursor=anchor_cursor.encode(),
        direction="newer",
        limit=max(1, newer_limit),
    )
    events = [*older.events[:older_limit], serialize_message_event(message)]
    if newer_limit:
        events.extend(newer.events[:newer_limit])
    return TimelineWindow(
        events=events,
        oldest_cursor=older.oldest_cursor or anchor_cursor.encode(),
        newest_cursor=(
            newer.newest_cursor
            if newer_limit and newer.events
            else anchor_cursor.encode()
        ),
        has_more_older=older.has_more_older if older_limit else _has_more_before(agent, anchor_cursor),
        has_more_newer=newer.has_more_newer if newer_limit else _has_more_after(agent, anchor_cursor),
        processing_snapshot=newer.processing_snapshot,
        current_plan=newer.current_plan,
    )
