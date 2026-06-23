import logging
import sqlite3
from dataclasses import dataclass
from typing import Optional, Sequence

from ..tools.sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from ..tools.sqlite_state import CONTACTS_TABLE, get_sqlite_db_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContactSQLiteRecord:
    contact_id: str
    channel: str
    address: str
    normalized_address: str
    display_name: str
    source: str
    status: str
    allow_inbound: bool
    allow_outbound: bool
    can_configure: bool
    requested_at: Optional[str]
    responded_at: Optional[str]
    updated_at: Optional[str]
    last_conversed_at: Optional[str]
    relevance_at: Optional[str]


def store_contacts_for_prompt(records: Sequence[ContactSQLiteRecord]) -> None:
    """Store a per-cycle contact authority snapshot in SQLite for agent querying."""
    db_path = get_sqlite_db_path()
    if not db_path:
        logger.warning("SQLite DB path unavailable; contacts snapshot not stored.")
        return

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        _recreate_contacts_table(conn)
        rows = []
        for record in records:
            rows.append(
                (
                    record.contact_id,
                    record.channel,
                    record.address or "",
                    record.normalized_address or "",
                    record.display_name or "",
                    record.source,
                    record.status,
                    1 if record.allow_inbound else 0,
                    1 if record.allow_outbound else 0,
                    1 if record.can_configure else 0,
                    record.requested_at,
                    record.responded_at,
                    record.updated_at,
                    record.last_conversed_at,
                    record.relevance_at,
                )
            )
        if rows:
            conn.executemany(
                f"""
                INSERT INTO "{CONTACTS_TABLE}" (
                    contact_id,
                    channel,
                    address,
                    normalized_address,
                    display_name,
                    source,
                    status,
                    allow_inbound,
                    allow_outbound,
                    can_configure,
                    requested_at,
                    responded_at,
                    updated_at,
                    last_conversed_at,
                    relevance_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                rows,
            )
        conn.commit()
    except (OSError, sqlite3.Error):
        logger.exception("Failed to store contacts in SQLite.")
    finally:
        if conn is not None:
            clear_guarded_connection(conn)
            try:
                conn.close()
            except sqlite3.Error:
                logger.warning("Failed to close SQLite connection during cleanup.", exc_info=True)


def _recreate_contacts_table(conn) -> None:
    conn.execute(f'DROP TABLE IF EXISTS "{CONTACTS_TABLE}";')
    conn.execute(
        f"""
        CREATE TABLE "{CONTACTS_TABLE}" (
            contact_id TEXT,
            channel TEXT,
            address TEXT,
            normalized_address TEXT,
            display_name TEXT,
            source TEXT,
            status TEXT,
            allow_inbound INTEGER,
            allow_outbound INTEGER,
            can_configure INTEGER,
            requested_at TEXT,
            responded_at TEXT,
            updated_at TEXT,
            last_conversed_at TEXT,
            relevance_at TEXT
        );
        """
    )
    conn.execute(
        f"""
        CREATE INDEX "{CONTACTS_TABLE}_channel_address_idx"
        ON "{CONTACTS_TABLE}" (channel, normalized_address);
        """
    )
    conn.execute(
        f"""
        CREATE INDEX "{CONTACTS_TABLE}_relevance_idx"
        ON "{CONTACTS_TABLE}" (relevance_at);
        """
    )
