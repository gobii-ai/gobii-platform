import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from opentelemetry import trace

from ..tools.sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from ..tools.sqlite_state import MESSAGES_TABLE, get_sqlite_db_path

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")

MESSAGE_COLUMNS = (
    "message_id",
    "seq",
    "timestamp",
    "channel",
    "is_outbound",
    "direction",
    "from_address",
    "to_address",
    "conversation_id",
    "conversation_address",
    "is_peer_dm",
    "peer_agent_id",
    "subject",
    "body",
    "body_bytes",
    "body_is_truncated",
    "body_truncated_bytes",
    "attachment_paths_json",
    "attachment_count",
    "rejected_attachments_json",
    "latest_status",
    "latest_sent_at",
    "latest_delivered_at",
    "latest_error_code",
    "latest_error_message",
    "is_hidden_in_chat",
    "structured_payload_json",
)
DELETE_CHUNK_SIZE = 500


@dataclass(frozen=True)
class MessageSQLiteRecord:
    message_id: str
    seq: str
    timestamp: str
    channel: str
    is_outbound: bool
    from_address: str
    to_address: str
    conversation_id: Optional[str]
    conversation_address: str
    is_peer_dm: bool
    peer_agent_id: Optional[str]
    subject: str
    body: str
    attachment_paths: Sequence[str]
    rejected_attachments: Sequence[dict[str, Any]]
    latest_status: str
    latest_sent_at: Optional[str]
    latest_delivered_at: Optional[str]
    latest_error_code: Optional[str]
    latest_error_message: Optional[str]
    is_hidden_in_chat: bool
    structured_payload_json: Optional[str] = None


def _message_record_row(record: MessageSQLiteRecord) -> tuple[Any, ...]:
    body = record.body or ""
    latest_error_code = (record.latest_error_code or "").strip() or None
    latest_error_message = (record.latest_error_message or "").strip() or None
    return (
        record.message_id,
        record.seq,
        record.timestamp,
        record.channel,
        1 if record.is_outbound else 0,
        "outbound" if record.is_outbound else "inbound",
        record.from_address or "",
        record.to_address or "",
        record.conversation_id,
        record.conversation_address or "",
        1 if record.is_peer_dm else 0,
        record.peer_agent_id,
        record.subject or "",
        body,
        len(body.encode("utf-8")),
        0,
        0,
        json.dumps(list(record.attachment_paths), ensure_ascii=False),
        len(record.attachment_paths),
        json.dumps(list(record.rejected_attachments), ensure_ascii=False),
        record.latest_status or "",
        record.latest_sent_at,
        record.latest_delivered_at,
        latest_error_code,
        latest_error_message,
        1 if record.is_hidden_in_chat else 0,
        record.structured_payload_json,
    )


@tracer.start_as_current_span("Prompt Messages SQLite Sync")
def store_messages_for_prompt(records: Sequence[MessageSQLiteRecord]) -> None:
    """Store a per-cycle message snapshot in SQLite for agent querying."""
    span = trace.get_current_span()
    db_path = get_sqlite_db_path()
    if not db_path:
        logger.warning("SQLite DB path unavailable; message snapshot not stored.")
        return

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        rebuilt = _ensure_messages_table(conn)
        selected_columns = ", ".join(f'"{column}"' for column in MESSAGE_COLUMNS)
        existing_rows = {
            row[0]: row
            for row in conn.execute(
                f'SELECT {selected_columns} FROM "{MESSAGES_TABLE}"'
            ).fetchall()
        }
        desired_rows = {
            record.message_id: _message_record_row(record)
            for record in records
        }
        rows_to_write = [
            row
            for message_id, row in desired_rows.items()
            if existing_rows.get(message_id) != row
        ]
        deleted_ids = sorted(existing_rows.keys() - desired_rows.keys())

        if rows_to_write:
            update_assignments = ", ".join(
                f'"{column}" = excluded."{column}"'
                for column in MESSAGE_COLUMNS
                if column != "message_id"
            )
            placeholders = ", ".join("?" for _ in MESSAGE_COLUMNS)
            conn.executemany(
                f"""
                INSERT INTO "{MESSAGES_TABLE}" ({selected_columns})
                VALUES ({placeholders})
                ON CONFLICT(message_id) DO UPDATE SET {update_assignments}
                """,
                rows_to_write,
            )
        for offset in range(0, len(deleted_ids), DELETE_CHUNK_SIZE):
            chunk = deleted_ids[offset:offset + DELETE_CHUNK_SIZE]
            placeholders = ", ".join("?" for _ in chunk)
            conn.execute(
                f'DELETE FROM "{MESSAGES_TABLE}" WHERE message_id IN ({placeholders})',
                chunk,
            )
        conn.commit()
        inserted_count = sum(1 for row in rows_to_write if row[0] not in existing_rows)
        span.set_attributes({
            "prompt.messages.sqlite.records": len(desired_rows),
            "prompt.messages.sqlite.inserted": inserted_count,
            "prompt.messages.sqlite.updated": len(rows_to_write) - inserted_count,
            "prompt.messages.sqlite.deleted": len(deleted_ids),
            "prompt.messages.sqlite.rebuilt": rebuilt,
        })
    except Exception:
        logger.exception("Failed to store messages in SQLite.")
    finally:
        if conn is not None:
            clear_guarded_connection(conn)
            try:
                conn.close()
            except sqlite3.Error:
                logger.warning("Failed to close SQLite connection during cleanup.", exc_info=True)


def _ensure_messages_table(conn) -> bool:
    table_info = conn.execute(f'PRAGMA table_info("{MESSAGES_TABLE}")').fetchall()
    existing_columns = {row[1] for row in table_info}
    message_id_is_primary_key = any(
        row[1] == "message_id" and row[5] == 1
        for row in table_info
    )
    if (
        existing_columns
        and set(MESSAGE_COLUMNS).issubset(existing_columns)
        and message_id_is_primary_key
    ):
        return False

    if existing_columns:
        conn.execute(f'DROP TABLE "{MESSAGES_TABLE}";')
    conn.execute(
        f"""
        CREATE TABLE "{MESSAGES_TABLE}" (
            message_id TEXT PRIMARY KEY,
            seq TEXT,
            timestamp TEXT,
            channel TEXT,
            is_outbound INTEGER,
            direction TEXT,
            from_address TEXT,
            to_address TEXT,
            conversation_id TEXT,
            conversation_address TEXT,
            is_peer_dm INTEGER,
            peer_agent_id TEXT,
            subject TEXT,
            body TEXT,
            body_bytes INTEGER,
            body_is_truncated INTEGER,
            body_truncated_bytes INTEGER,
            attachment_paths_json TEXT,
            attachment_count INTEGER,
            rejected_attachments_json TEXT,
            latest_status TEXT,
            latest_sent_at TEXT,
            latest_delivered_at TEXT,
            latest_error_code TEXT,
            latest_error_message TEXT,
            is_hidden_in_chat INTEGER,
            structured_payload_json TEXT
        );
        """
    )
    return True
