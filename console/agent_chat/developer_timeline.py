from api.models import PersistentAgent
from console.agent_audit.events import fetch_event_page
from console.agent_chat.timeline import (
    TimelineWindow,
    build_processing_snapshot,
    serialize_plan_snapshot,
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
