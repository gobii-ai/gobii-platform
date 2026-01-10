import logging
import uuid
from datetime import timezone as dt_timezone

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from api.agent.tools.sqlite_kanban import build_kanban_board_snapshot
from api.models import (
    PersistentAgent,
    PersistentAgentKanbanCard,
    PersistentAgentKanbanEvent,
    PersistentAgentKanbanEventChange,
    PersistentAgentKanbanEventTitle,
)
logger = logging.getLogger(__name__)


def _coerce_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_action(value: str | None) -> str:
    allowed = {choice[0] for choice in PersistentAgentKanbanEvent.Action.choices}
    if value in allowed:
        return value
    return PersistentAgentKanbanEvent.Action.UPDATED


def _normalize_status(value: str | None) -> str | None:
    if value is None:
        return None
    allowed = {choice[0] for choice in PersistentAgentKanbanCard.Status.choices}
    return value if value in allowed else None


def _agent_first_name(agent: PersistentAgent) -> str:
    name = (getattr(agent, "name", None) or "Agent").strip()
    if not name:
        return "Agent"
    return name.split()[0]


def _cursor_value_from_timestamp(value) -> int:
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    value = value.astimezone(dt_timezone.utc)
    return int(value.timestamp() * 1_000_000)


def _parse_cursor(raw: str | None) -> tuple[int, str] | None:
    if not raw:
        return None
    try:
        value_str, _kind, identifier = raw.split(":", 2)
        return int(value_str), identifier
    except (ValueError, TypeError):
        return None


def ensure_kanban_baseline_event(agent: PersistentAgent) -> PersistentAgentKanbanEvent | None:
    """Create a baseline kanban event when cards exist but no events are persisted yet."""
    if not agent or not agent.id:
        return None
    if PersistentAgentKanbanEvent.objects.filter(agent=agent).exists():
        return None

    snapshot = build_kanban_board_snapshot(agent)
    total = snapshot.todo_count + snapshot.doing_count + snapshot.done_count
    if total == 0:
        return None

    last_updated = (
        PersistentAgentKanbanCard.objects.filter(assigned_agent=agent).aggregate(latest=Max("updated_at")).get("latest")
    )
    event_time = last_updated or timezone.now()
    cursor_value = _cursor_value_from_timestamp(event_time)
    cursor_identifier = uuid.uuid5(uuid.NAMESPACE_URL, f"kanban-baseline:{agent.id}")
    display_text = f"{_agent_first_name(agent)} updated kanban"

    try:
        with transaction.atomic():
            event, created = PersistentAgentKanbanEvent.objects.get_or_create(
                cursor_identifier=cursor_identifier,
                defaults={
                    "agent": agent,
                    "cursor_value": cursor_value,
                    "display_text": display_text,
                    "primary_action": PersistentAgentKanbanEvent.Action.UPDATED,
                    "todo_count": snapshot.todo_count,
                    "doing_count": snapshot.doing_count,
                    "done_count": snapshot.done_count,
                },
            )
            if not created:
                return event

            title_rows: list[PersistentAgentKanbanEventTitle] = []
            for status_key, titles in (
                ("todo", snapshot.todo_titles),
                ("doing", snapshot.doing_titles),
                ("done", snapshot.done_titles),
            ):
                for index, title in enumerate(titles):
                    clean_title = str(title).strip()
                    if not clean_title:
                        continue
                    title_rows.append(
                        PersistentAgentKanbanEventTitle(
                            event=event,
                            status=status_key,
                            position=index,
                            title=clean_title[:255],
                        )
                    )
            if title_rows:
                PersistentAgentKanbanEventTitle.objects.bulk_create(title_rows)

        return event
    except IntegrityError:
        return PersistentAgentKanbanEvent.objects.filter(cursor_identifier=cursor_identifier).first()


def persist_kanban_event(agent: PersistentAgent, payload: dict) -> PersistentAgentKanbanEvent | None:
    """Persist a serialized kanban event for timeline rehydration."""
    if not agent or not agent.id or not payload:
        return None

    cursor_raw = payload.get("cursor")
    parsed_cursor = _parse_cursor(cursor_raw)
    if not parsed_cursor:
        logger.debug("Missing kanban cursor; skipping persistence for agent %s", agent.id)
        return None
    cursor_value, cursor_identifier_raw = parsed_cursor
    cursor_identifier = _coerce_uuid(cursor_identifier_raw)
    if not cursor_identifier:
        logger.debug("Invalid kanban cursor identifier; skipping persistence for agent %s", agent.id)
        return None

    existing = PersistentAgentKanbanEvent.objects.filter(cursor_identifier=cursor_identifier).first()
    if existing:
        return existing

    snapshot = payload.get("snapshot") or {}
    changes = payload.get("changes") or []
    if not changes or not snapshot:
        return None

    display_text = (payload.get("displayText") or "Kanban updated").strip() or "Kanban updated"
    primary_action = _normalize_action(payload.get("primaryAction"))

    todo_count = _coerce_int(snapshot.get("todoCount"))
    doing_count = _coerce_int(snapshot.get("doingCount"))
    done_count = _coerce_int(snapshot.get("doneCount"))

    try:
        with transaction.atomic():
            event = PersistentAgentKanbanEvent.objects.create(
                agent=agent,
                cursor_value=cursor_value,
                cursor_identifier=cursor_identifier,
                display_text=display_text,
                primary_action=primary_action,
                todo_count=todo_count,
                doing_count=doing_count,
                done_count=done_count,
            )

            title_rows: list[PersistentAgentKanbanEventTitle] = []
            for status_key, titles in (
                ("todo", snapshot.get("todoTitles") or []),
                ("doing", snapshot.get("doingTitles") or []),
                ("done", snapshot.get("doneTitles") or []),
            ):
                for index, title in enumerate(titles):
                    clean_title = str(title).strip()
                    if not clean_title:
                        continue
                    title_rows.append(
                        PersistentAgentKanbanEventTitle(
                            event=event,
                            status=status_key,
                            position=index,
                            title=clean_title[:255],
                        )
                    )
            if title_rows:
                PersistentAgentKanbanEventTitle.objects.bulk_create(title_rows)

            change_rows: list[PersistentAgentKanbanEventChange] = []
            for change in changes:
                if not isinstance(change, dict):
                    continue
                card_uuid = _coerce_uuid(change.get("cardId"))
                if not card_uuid:
                    continue
                title = str(change.get("title") or "").strip()
                if not title:
                    continue
                change_rows.append(
                    PersistentAgentKanbanEventChange(
                        event=event,
                        card_id=card_uuid,
                        title=title[:255],
                        action=_normalize_action(change.get("action")),
                        from_status=_normalize_status(change.get("fromStatus")),
                        to_status=_normalize_status(change.get("toStatus")),
                    )
                )
            if change_rows:
                PersistentAgentKanbanEventChange.objects.bulk_create(change_rows)

        return event
    except IntegrityError:
        return PersistentAgentKanbanEvent.objects.filter(cursor_identifier=cursor_identifier).first()
