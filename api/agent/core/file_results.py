import logging
import sqlite3
from dataclasses import dataclass
from typing import Optional, Sequence

from ..tools.sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from ..tools.sqlite_state import FILES_META_TABLE, FILES_TABLE, get_sqlite_db_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileSQLiteRecord:
    node_id: str
    filespace_id: str
    path: str
    name: str
    parent_path: str
    mime_type: str
    size_bytes: Optional[int]
    checksum_sha256: str
    created_at: Optional[str]
    updated_at: Optional[str]


def store_files_for_prompt(
    records: Sequence[FileSQLiteRecord],
    *,
    total_files: Optional[int] = None,
    indexed_files: Optional[int] = None,
    is_truncated: Optional[bool] = None,
) -> None:
    """Store a per-cycle files index in SQLite for agent querying."""
    db_path = get_sqlite_db_path()
    if not db_path:
        logger.warning("SQLite DB path unavailable; files index not stored.")
        return

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        _recreate_files_table(conn)
        _recreate_files_meta_table(conn)
        rows = []
        for record in records:
            rows.append(
                (
                    record.node_id,
                    record.filespace_id,
                    record.path,
                    record.name or "",
                    record.parent_path or "/",
                    record.mime_type or "",
                    int(record.size_bytes) if record.size_bytes is not None else None,
                    record.checksum_sha256 or "",
                    record.created_at,
                    record.updated_at,
                )
            )
        if rows:
            conn.executemany(
                f"""
                INSERT INTO "{FILES_TABLE}" (
                    node_id,
                    filespace_id,
                    path,
                    name,
                    parent_path,
                    mime_type,
                    size_bytes,
                    checksum_sha256,
                    created_at,
                    updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                rows,
            )
        indexed_count = indexed_files if indexed_files is not None else len(rows)
        total_count = total_files if total_files is not None else indexed_count
        truncated_flag = int(bool(is_truncated)) if is_truncated is not None else int(total_count > indexed_count)
        conn.execute(
            f"""
            INSERT INTO "{FILES_META_TABLE}" (
                id,
                total_files,
                indexed_files,
                is_truncated
            ) VALUES (1, ?, ?, ?)
            """,
            (int(total_count), int(indexed_count), truncated_flag),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to store files index in SQLite.")
    finally:
        if conn is not None:
            clear_guarded_connection(conn)
            try:
                conn.close()
            except sqlite3.Error:
                logger.warning("Failed to close SQLite connection during cleanup.", exc_info=True)


def _recreate_files_table(conn) -> None:
    conn.execute(f'DROP TABLE IF EXISTS "{FILES_TABLE}";')
    conn.execute(
        f"""
        CREATE TABLE "{FILES_TABLE}" (
            node_id TEXT PRIMARY KEY,
            filespace_id TEXT,
            path TEXT,
            name TEXT,
            parent_path TEXT,
            mime_type TEXT,
            size_bytes INTEGER,
            checksum_sha256 TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )


def _recreate_files_meta_table(conn) -> None:
    conn.execute(f'DROP TABLE IF EXISTS "{FILES_META_TABLE}";')
    conn.execute(
        f"""
        CREATE TABLE "{FILES_META_TABLE}" (
            id INTEGER PRIMARY KEY,
            total_files INTEGER,
            indexed_files INTEGER,
            is_truncated INTEGER
        );
        """
    )
