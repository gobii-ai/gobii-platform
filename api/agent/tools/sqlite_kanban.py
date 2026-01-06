"""
SQLite-backed kanban helpers.

Seeds an ephemeral kanban table for each LLM invocation and applies updates
back to Postgres after tool execution.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Optional, Sequence

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from api.models import PersistentAgentKanbanCard

from .sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from .sqlite_state import KANBAN_CARDS_TABLE, get_sqlite_db_path

logger = logging.getLogger(__name__)


def format_kanban_friendly_id(title: str, card_id: Optional[str] = None) -> str:
    slug = slugify((title or "").strip())
    if slug:
        return slug
    if card_id:
        short_id = str(card_id).replace("-", "")[:8]
        return f"card-{short_id}"
    return "card"


@dataclass(frozen=True)
class KanbanCardSnapshot:
    card_id: str
    title: str
    description: str
    status: str
    priority: int
    assigned_agent_id: str


@dataclass(frozen=True)
class KanbanSnapshot:
    cards: dict[str, KanbanCardSnapshot]


@dataclass(frozen=True)
class KanbanCardChange:
    """Represents a single card change for timeline visualization."""

    card_id: str
    title: str
    action: str  # "created", "started", "completed", "updated", "archived", "deleted"
    from_status: Optional[str] = None
    to_status: Optional[str] = None


@dataclass(frozen=True)
class KanbanBoardSnapshot:
    """Current board state for visualization."""

    todo_count: int
    doing_count: int
    done_count: int
    todo_titles: Sequence[str]  # Top few titles
    doing_titles: Sequence[str]
    done_titles: Sequence[str]


@dataclass(frozen=True)
class KanbanApplyResult:
    created_ids: Sequence[str]
    updated_ids: Sequence[str]
    archived_ids: Sequence[str] = ()
    deleted_ids: Sequence[str] = ()
    errors: Sequence[str] = ()
    changes: Sequence[KanbanCardChange] = ()
    snapshot: Optional[KanbanBoardSnapshot] = None


def seed_sqlite_kanban(agent) -> Optional[KanbanSnapshot]:
    """Create/reset the kanban table and seed it with current card data."""
    db_path = get_sqlite_db_path()
    if not db_path:
        logger.warning("SQLite DB path unavailable; cannot seed kanban table.")
        return None

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        conn.execute(f'DROP TABLE IF EXISTS "{KANBAN_CARDS_TABLE}";')
        conn.execute(
            f"""
            CREATE TABLE "{KANBAN_CARDS_TABLE}" (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                friendly_id TEXT,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'todo',
                priority INTEGER NOT NULL DEFAULT 0,
                assigned_agent_id TEXT NOT NULL DEFAULT '{agent.id}',
                created_at TEXT,
                updated_at TEXT,
                completed_at TEXT,
                CHECK (status IN ('todo', 'doing', 'done'))
            );
            """
        )

        card_filter = _kanban_scope_filter(agent)
        cards = list(
            PersistentAgentKanbanCard.objects.filter(**card_filter).order_by(
                "-priority",
                "created_at",
            )
        )
        rows = []
        snapshot_cards: dict[str, KanbanCardSnapshot] = {}
        for card in cards:
            card_id = str(card.id)
            title = card.title.strip()
            friendly_id = format_kanban_friendly_id(title, card_id)
            description = (card.description or "").strip()
            status = (card.status or PersistentAgentKanbanCard.Status.TODO).strip()
            priority = int(card.priority or 0)
            assigned_agent_id = str(card.assigned_agent_id or "")
            rows.append(
                (
                    card_id,
                    friendly_id,
                    title,
                    description,
                    status,
                    priority,
                    assigned_agent_id,
                    _format_timestamp(card.created_at),
                    _format_timestamp(card.updated_at),
                    _format_timestamp(card.completed_at),
                )
            )
            snapshot_cards[card_id] = KanbanCardSnapshot(
                card_id=card_id,
                title=title,
                description=description,
                status=status,
                priority=priority,
                assigned_agent_id=assigned_agent_id,
            )

        if rows:
            conn.executemany(
                f"""
                INSERT INTO "{KANBAN_CARDS_TABLE}"
                    (id, friendly_id, title, description, status, priority, assigned_agent_id, created_at, updated_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                rows,
            )
        conn.commit()
        return KanbanSnapshot(cards=snapshot_cards)
    except Exception:
        logger.exception("Failed to seed kanban table for agent %s", getattr(agent, "id", None))
        return None
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except Exception:
                pass


def apply_sqlite_kanban_updates(agent, baseline: Optional[KanbanSnapshot]) -> KanbanApplyResult:
    """Apply SQLite kanban updates to the persistent card records."""
    updated_ids: list[str] = []
    created_ids: list[str] = []
    errors: list[str] = []
    changes: list[KanbanCardChange] = []

    current_cards, read_errors = _read_kanban_cards(agent)
    if read_errors:
        errors.extend(read_errors)

    if baseline is None or current_cards is None:
        _drop_kanban_table()
        return KanbanApplyResult(created_ids=created_ids, updated_ids=updated_ids, errors=errors)

    baseline_cards = baseline.cards
    baseline_ids = set(baseline_cards.keys())
    current_ids = set(current_cards.keys())

    created = current_ids - baseline_ids
    shared = current_ids & baseline_ids

    # Detect duplicate title inserts (common LLM mistake: INSERT existing cards instead of UPDATE)
    baseline_titles = {card.title.lower(): card for card in baseline_cards.values()}
    duplicate_ids: set[str] = set()
    for card_id in created:
        card = current_cards.get(card_id)
        if card and card.title.lower() in baseline_titles:
            existing = baseline_titles[card.title.lower()]
            errors.append(
                f"Kanban duplicate blocked: '{card.title}' already exists (friendly_id: {format_kanban_friendly_id(existing.title, existing.card_id)}). "
                f"Use UPDATE to change status, not INSERT. Cards persist across turns."
            )
            duplicate_ids.add(card_id)

    with transaction.atomic():
        for card_id in (cid for cid in current_cards if cid in created):
            card = current_cards.get(card_id)
            if not card:
                continue
            if card_id in duplicate_ids:
                continue  # Skip duplicate title insertions
            if card.assigned_agent_id != str(agent.id):
                errors.append(
                    f"Kanban create denied for {card.card_id}: only tasks assigned to this agent may be created."
                )
                continue
            card_uuid = _coerce_uuid(card.card_id)
            if not card_uuid:
                errors.append(f"Kanban create ignored for invalid card id: {card.card_id}")
                continue
            try:
                completed_at = timezone.now() if card.status == PersistentAgentKanbanCard.Status.DONE else None
                PersistentAgentKanbanCard.objects.create(
                    id=card_uuid,
                    assigned_agent=agent,
                    title=card.title,
                    description=card.description,
                    status=card.status,
                    priority=card.priority,
                    completed_at=completed_at,
                )
                created_ids.append(str(card_uuid))
                # Track the change for timeline
                changes.append(
                    KanbanCardChange(
                        card_id=str(card_uuid),
                        title=card.title,
                        action="created",
                        to_status=card.status,
                    )
                )
            except Exception as exc:
                errors.append(f"Kanban create failed for {card.card_id}: {exc}")

        for card_id in sorted(shared):
            baseline_card = baseline_cards.get(card_id)
            current_card = current_cards.get(card_id)
            if not baseline_card or not current_card:
                continue
            if current_card == baseline_card:
                continue
            if baseline_card.assigned_agent_id != str(agent.id):
                errors.append(
                    f"Kanban update denied for {card_id}: only tasks assigned to this agent may be updated."
                )
                continue
            if current_card.assigned_agent_id != str(agent.id):
                errors.append(
                    f"Kanban update denied for {card_id}: assigned_agent_id cannot be changed by the agent."
                )
                continue

            card_uuid = _coerce_uuid(current_card.card_id)
            if not card_uuid:
                errors.append(f"Kanban update ignored for invalid card id: {current_card.card_id}")
                continue

            card_obj = (
                PersistentAgentKanbanCard.objects.filter(id=card_uuid, assigned_agent=agent).first()
            )
            if not card_obj:
                errors.append(
                    f"Kanban update ignored for {current_card.card_id}: card not owned by this agent."
                )
                continue

            update_fields: list[str] = []
            old_status = card_obj.status
            status_changed = False
            non_status_changed = False
            if card_obj.title != current_card.title:
                card_obj.title = current_card.title
                update_fields.append("title")
                non_status_changed = True
            if (card_obj.description or "") != (current_card.description or ""):
                card_obj.description = current_card.description
                update_fields.append("description")
                non_status_changed = True
            if card_obj.priority != current_card.priority:
                card_obj.priority = current_card.priority
                update_fields.append("priority")
                non_status_changed = True

            if card_obj.status != current_card.status:
                card_obj.status = current_card.status
                update_fields.append("status")
                status_changed = True
                if current_card.status == PersistentAgentKanbanCard.Status.DONE:
                    if card_obj.completed_at is None:
                        card_obj.completed_at = timezone.now()
                        update_fields.append("completed_at")
                else:
                    if card_obj.completed_at is not None:
                        card_obj.completed_at = None
                        update_fields.append("completed_at")

            if update_fields:
                update_fields.append("updated_at")
                card_obj.save(update_fields=update_fields)
                updated_ids.append(str(card_uuid))

                # Track meaningful changes for timeline
                if status_changed:
                    action = _determine_action(old_status, current_card.status)
                    changes.append(
                        KanbanCardChange(
                            card_id=str(card_uuid),
                            title=current_card.title,
                            action=action,
                            from_status=old_status,
                            to_status=current_card.status,
                        )
                    )
                elif non_status_changed:
                    changes.append(
                        KanbanCardChange(
                            card_id=str(card_uuid),
                            title=current_card.title,
                            action="updated",
                            from_status=old_status,
                            to_status=current_card.status,
                        )
                    )

        # Handle cards that were removed from SQLite table
        # Done cards are "archived" (clean removal), non-done cards are "deleted" (forceful removal)
        removed = baseline_ids - current_ids
        archived_ids: list[str] = []
        deleted_ids: list[str] = []
        for card_id in sorted(removed):
            baseline_card = baseline_cards.get(card_id)
            if not baseline_card:
                continue
            if baseline_card.assigned_agent_id != str(agent.id):
                errors.append(
                    f"Kanban removal denied for {card_id}: only tasks assigned to this agent may be removed."
                )
                continue

            card_uuid = _coerce_uuid(card_id)
            if not card_uuid:
                errors.append(f"Kanban removal ignored for invalid card id: {card_id}")
                continue

            deleted_count, _ = PersistentAgentKanbanCard.objects.filter(
                id=card_uuid, assigned_agent=agent
            ).delete()
            if deleted_count:
                # Track as "archived" if done, "deleted" if forcefully removed while incomplete
                if baseline_card.status == PersistentAgentKanbanCard.Status.DONE:
                    archived_ids.append(str(card_uuid))
                    changes.append(
                        KanbanCardChange(
                            card_id=str(card_uuid),
                            title=baseline_card.title,
                            action="archived",
                            from_status=baseline_card.status,
                        )
                    )
                else:
                    deleted_ids.append(str(card_uuid))
                    changes.append(
                        KanbanCardChange(
                            card_id=str(card_uuid),
                            title=baseline_card.title,
                            action="deleted",
                            from_status=baseline_card.status,
                        )
                    )

    _drop_kanban_table()

    # Build board snapshot if there were changes
    snapshot = None
    if changes:
        snapshot = _build_board_snapshot(agent)

    return KanbanApplyResult(
        created_ids=created_ids,
        updated_ids=updated_ids,
        archived_ids=archived_ids,
        deleted_ids=deleted_ids,
        errors=errors,
        changes=changes,
        snapshot=snapshot,
    )


def _read_kanban_cards(agent) -> tuple[Optional[dict[str, KanbanCardSnapshot]], list[str]]:
    db_path = get_sqlite_db_path()
    if not db_path:
        return None, []

    conn = None
    errors: list[str] = []
    try:
        conn = open_guarded_sqlite_connection(db_path)
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT id, title, description, status, priority, assigned_agent_id
            FROM "{KANBAN_CARDS_TABLE}"
            ORDER BY rowid;
            """
        )
        rows = cur.fetchall()
    except Exception:
        logger.exception("Failed to read kanban table.")
        return None, ["Failed to read kanban table."]
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except Exception:
                pass

    cards: dict[str, KanbanCardSnapshot] = {}
    for row in rows:
        card = _parse_kanban_row(row, default_agent_id=str(agent.id), errors=errors)
        if card:
            cards[card.card_id] = card
    return cards, errors


def _parse_kanban_row(
    row: tuple,
    *,
    default_agent_id: str,
    errors: list[str],
) -> Optional[KanbanCardSnapshot]:
    card_id_raw, title_raw, description_raw, status_raw, priority_raw, assigned_agent_id_raw = row
    card_id = (card_id_raw or "").strip()
    if not card_id:
        errors.append("Kanban row skipped: missing card id.")
        return None

    title = (title_raw or "").strip()
    if not title:
        errors.append(f"Kanban row skipped for {card_id}: title is required.")
        return None
    if len(title) > 255:
        title = title[:255]

    status = _normalize_status(status_raw)
    if not status:
        errors.append(f"Kanban row skipped for {card_id}: invalid status '{status_raw}'.")
        return None

    try:
        priority = int(priority_raw) if priority_raw is not None else 0
    except Exception:
        priority = 0

    description = (description_raw or "").strip()
    assigned_agent_id = (assigned_agent_id_raw or "").strip() or default_agent_id

    return KanbanCardSnapshot(
        card_id=card_id,
        title=title,
        description=description,
        status=status,
        priority=priority,
        assigned_agent_id=assigned_agent_id,
    )


def _normalize_status(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    allowed = set(PersistentAgentKanbanCard.Status.values)
    return normalized if normalized in allowed else None


def _format_timestamp(value) -> Optional[str]:
    if not value:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.utc)
    return value.isoformat()


def _coerce_uuid(value: str) -> Optional[uuid.UUID]:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _drop_kanban_table() -> None:
    db_path = get_sqlite_db_path()
    if not db_path:
        return

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        conn.execute(f'DROP TABLE IF EXISTS "{KANBAN_CARDS_TABLE}";')
        conn.commit()
    except Exception:
        logger.exception("Failed to drop kanban table.")
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except Exception:
                pass


def _kanban_scope_filter(agent) -> dict:
    if getattr(agent, "organization_id", None):
        return {"assigned_agent__organization_id": agent.organization_id}
    return {
        "assigned_agent__organization__isnull": True,
        "assigned_agent__user_id": agent.user_id,
    }


def _determine_action(old_status: str, new_status: str) -> str:
    """Determine the action name for a status change."""
    if new_status == PersistentAgentKanbanCard.Status.DONE:
        return "completed"
    if new_status == PersistentAgentKanbanCard.Status.DOING:
        return "started"
    # Moving back to todo or other changes
    return "updated"


def _build_board_snapshot(agent) -> KanbanBoardSnapshot:
    """Build a snapshot of the current board state for visualization."""
    MAX_TITLES = 5

    card_filter = {"assigned_agent": agent}
    cards = PersistentAgentKanbanCard.objects.filter(**card_filter).order_by("-priority", "created_at")

    todo_cards = []
    doing_cards = []
    done_cards = []

    for card in cards:
        if card.status == PersistentAgentKanbanCard.Status.TODO:
            todo_cards.append(card.title)
        elif card.status == PersistentAgentKanbanCard.Status.DOING:
            doing_cards.append(card.title)
        elif card.status == PersistentAgentKanbanCard.Status.DONE:
            done_cards.append(card.title)

    return KanbanBoardSnapshot(
        todo_count=len(todo_cards),
        doing_count=len(doing_cards),
        done_count=len(done_cards),
        todo_titles=todo_cards[:MAX_TITLES],
        doing_titles=doing_cards[:MAX_TITLES],
        done_titles=done_cards[:MAX_TITLES],
    )
