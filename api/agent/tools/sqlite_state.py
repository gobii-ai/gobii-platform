"""
Shared SQLite state and helpers for persistent agents.

This module centralizes the SQLite DB context management, schema prompt
generation, and storage key logic so multiple tools (e.g., sqlite_batch)
can share the same implementation.
"""

import contextlib
import contextvars
import logging
import os
import shutil
import tempfile
from typing import Optional

import zstandard as zstd
from django.core.files import File
from django.core.files.storage import default_storage

from .sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection

logger = logging.getLogger(__name__)

# Context variable to expose the SQLite DB path to tool execution helpers
_sqlite_db_path_var: contextvars.ContextVar[str] = contextvars.ContextVar("sqlite_db_path", default=None)

TOOL_RESULTS_TABLE = "__tool_results"
EPHEMERAL_TABLES = {TOOL_RESULTS_TABLE}
BUILTIN_TABLE_NOTES = {
    TOOL_RESULTS_TABLE: "built-in, ephemeral (dropped before persistence)",
}


def get_sqlite_schema_prompt() -> str:
    """Return a human-readable SQLite schema summary capped to ~30 KB.

    The summary includes the CREATE TABLE statement of each user table
    followed by its row count, e.g.::

        Table users (rows: 42): CREATE TABLE users(id INTEGER PRIMARY KEY, ...)

    Returns plain text; callers can wrap/label it as desired. If no user
    tables exist yet, we state that explicitly. Truncates aggressively
    if it exceeds ~30KB.
    """

    db_path = _sqlite_db_path_var.get(None)
    if not db_path or not os.path.exists(db_path):
        return "SQLite database not initialised – no schema present yet."

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        cur = conn.cursor()
        cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")
        tables = cur.fetchall()

        if not tables:
            return "SQLite database has no user tables yet."

        lines: list[str] = []
        for name, create_stmt in tables:
            # Get row count for each table (best-effort)
            try:
                cur.execute(f"SELECT COUNT(*) FROM \"{name}\";")
                (count,) = cur.fetchone()
            except Exception:
                count = "?"
            create_stmt_single_line = " ".join((create_stmt or "").split())
            note = BUILTIN_TABLE_NOTES.get(name)
            if note:
                lines.append(
                    f"Table {name} (rows: {count}, {note}): {create_stmt_single_line}"
                )
            else:
                lines.append(f"Table {name} (rows: {count}): {create_stmt_single_line}")

        block = "\n".join(lines)
        encoded = block.encode("utf-8")
        max_bytes = 30000
        if len(encoded) > max_bytes:
            truncated_text = encoded[:max_bytes].decode("utf-8", errors="ignore")
            truncated_text += "\n... (truncated – schema exceeds 30KB limit)"
            return truncated_text
        return block
    except Exception as e:  # noqa: BLE001
        return f"Failed to inspect SQLite DB: {e}"
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except Exception:
                pass


def set_sqlite_db_path(db_path: str) -> contextvars.Token:
    """Set the SQLite DB path in the context variable."""
    return _sqlite_db_path_var.set(db_path)


def get_sqlite_db_path() -> Optional[str]:
    """Return the current SQLite DB path from context, if available."""
    return _sqlite_db_path_var.get(None)


def reset_sqlite_db_path(token: contextvars.Token) -> None:
    """Reset the SQLite DB path context variable."""
    try:
        _sqlite_db_path_var.reset(token)
    except Exception:
        pass


def sqlite_storage_key(agent_uuid: str) -> str:
    """Return hierarchical object key for a persistent agent SQLite DB archive."""
    clean_uuid = str(agent_uuid).replace("-", "")
    return f"agent_state/{clean_uuid[:2]}/{clean_uuid[2:4]}/{agent_uuid}.db.zst"


@contextlib.contextmanager
def agent_sqlite_db(agent_uuid: str):  # noqa: D401 – simple generator context mgr
    """Context manager that restores/persists the per-agent SQLite DB.

    1. Attempts to download and decompress the DB from object storage.
    2. Yields the on-disk path to the SQLite file in a temporary directory.
    3. On exit, runs maintenance (VACUUM/PRAGMA optimize), then compresses
       the DB with zstd and uploads to object storage, unless the DB grew
       beyond 100MB, in which case we wipe persisted state.
    """
    storage_key = sqlite_storage_key(agent_uuid)

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "state.db")

        # ---------------- Restore phase ---------------- #
        if default_storage.exists(storage_key):
            try:
                with default_storage.open(storage_key, "rb") as src:
                    dctx = zstd.ZstdDecompressor()
                    with dctx.stream_reader(src) as reader, open(db_path, "wb") as dst:
                        shutil.copyfileobj(reader, dst)
            except Exception:
                logger.warning(
                    "Failed to restore SQLite DB for agent %s – starting fresh.",
                    agent_uuid,
                    exc_info=True,
                )

        token = set_sqlite_db_path(db_path)

        try:
            yield db_path
        finally:
            if os.path.exists(db_path):
                try:
                    conn = open_guarded_sqlite_connection(db_path)
                    try:
                        _drop_ephemeral_tables(conn)
                        conn.execute("VACUUM;")
                        try:
                            conn.execute("PRAGMA optimize;")
                        except Exception:
                            pass
                        conn.commit()
                    finally:
                        try:
                            clear_guarded_connection(conn)
                            conn.close()
                        except Exception:
                            pass
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "SQLite maintenance (VACUUM/optimize) failed for agent %s",
                        agent_uuid,
                        exc_info=True,
                    )

                db_size_bytes = os.path.getsize(db_path)
                db_size_mb = db_size_bytes / (1024 * 1024)

                if db_size_mb > 100:
                    logger.info(
                        "SQLite DB for agent %s exceeds 100MB (%.2f MB) - wiping database instead of persisting",
                        agent_uuid,
                        db_size_mb,
                    )
                    if default_storage.exists(storage_key):
                        default_storage.delete(storage_key)
                else:
                    tmp_zst_path = db_path + ".zst"
                    try:
                        cctx = zstd.ZstdCompressor(level=3)
                        with open(db_path, "rb") as f_in, open(tmp_zst_path, "wb") as f_out:
                            cctx.copy_stream(f_in, f_out)

                        if default_storage.exists(storage_key):
                            default_storage.delete(storage_key)

                        with open(tmp_zst_path, "rb") as f_in:
                            default_storage.save(storage_key, File(f_in))
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Failed to persist SQLite DB for agent %s", agent_uuid
                        )
                    finally:
                        try:
                            os.remove(tmp_zst_path)
                        except Exception:
                            pass

            reset_sqlite_db_path(token)


def _drop_ephemeral_tables(conn) -> None:
    for table_name in EPHEMERAL_TABLES:
        try:
            conn.execute(f'DROP TABLE IF EXISTS "{table_name}";')
        except Exception:
            logger.debug("Failed to drop ephemeral table %s", table_name, exc_info=True)
