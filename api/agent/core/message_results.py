import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Optional, Sequence

from ..tools.sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from ..tools.sqlite_state import MESSAGES_TABLE, get_sqlite_db_path

logger = logging.getLogger(__name__)


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
    latest_status: str
    latest_sent_at: Optional[str]
    latest_delivered_at: Optional[str]
    latest_error_code: Optional[str]
    latest_error_message: Optional[str]
    is_hidden_in_chat: bool


def store_messages_for_prompt(records: Sequence[MessageSQLiteRecord]) -> None:
    """Store a per-cycle message snapshot in SQLite for agent querying."""
    db_path = get_sqlite_db_path()
    if not db_path:
        logger.warning("SQLite DB path unavailable; message snapshot not stored.")
        return

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        _recreate_messages_table(conn)
        rows = []
        for record in records:
            body = record.body or ""
            latest_error_code = (record.latest_error_code or "").strip() or None
            latest_error_message = (record.latest_error_message or "").strip() or None
            rows.append(
                (
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
                    record.latest_status or "",
                    record.latest_sent_at,
                    record.latest_delivered_at,
                    latest_error_code,
                    latest_error_message,
                    1 if record.is_hidden_in_chat else 0,
                )
            )
        if rows:
            conn.executemany(
                f"""
                INSERT INTO "{MESSAGES_TABLE}" (
                    message_id,
                    seq,
                    timestamp,
                    channel,
                    is_outbound,
                    direction,
                    from_address,
                    to_address,
                    conversation_id,
                    conversation_address,
                    is_peer_dm,
                    peer_agent_id,
                    subject,
                    body,
                    body_bytes,
                    body_is_truncated,
                    body_truncated_bytes,
                    attachment_paths_json,
                    attachment_count,
                    latest_status,
                    latest_sent_at,
                    latest_delivered_at,
                    latest_error_code,
                    latest_error_message,
                    is_hidden_in_chat
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                rows,
            )
        conn.commit()
    except Exception:
        logger.exception("Failed to store messages in SQLite.")
    finally:
        if conn is not None:
            clear_guarded_connection(conn)
            try:
                conn.close()
            except sqlite3.Error:
                logger.warning("Failed to close SQLite connection during cleanup.", exc_info=True)


def _recreate_messages_table(conn) -> None:
    conn.execute(f'DROP TABLE IF EXISTS "{MESSAGES_TABLE}";')
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
            latest_status TEXT,
            latest_sent_at TEXT,
            latest_delivered_at TEXT,
            latest_error_code TEXT,
            latest_error_message TEXT,
            is_hidden_in_chat INTEGER
        );
        """
    )
