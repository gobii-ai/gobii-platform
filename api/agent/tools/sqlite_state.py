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
    followed by its row count and sample data, e.g.::

        Table users (rows: 42): CREATE TABLE users(id INTEGER PRIMARY KEY, ...)
          sample: (1, 'alice', 25), (2, 'bob', 30)
          stats: id[1-42], name[42 distinct], age[18-65]

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

            # Add sample rows and stats for non-ephemeral tables with data
            if name not in EPHEMERAL_TABLES and isinstance(count, int) and count > 0:
                sample_stats = _get_table_sample_and_stats(cur, name, count)
                if sample_stats:
                    lines.append(sample_stats)

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


def _get_table_sample_and_stats(cur, table_name: str, row_count: int) -> str:
    """Get sample rows and column stats for a table.

    Returns a formatted string with sample data and basic statistics,
    or empty string if unable to fetch.
    """
    try:
        # Get column info
        cur.execute(f"PRAGMA table_info(\"{table_name}\");")
        columns = [(row[1], row[2].upper()) for row in cur.fetchall()]  # (name, type)
        if not columns:
            return ""

        parts = []

        # Get 2 sample rows (first and a middle one for variety)
        sample_rows = []
        try:
            cur.execute(f"SELECT * FROM \"{table_name}\" LIMIT 1;")
            first_row = cur.fetchone()
            if first_row:
                sample_rows.append(first_row)

            if row_count > 2:
                # Get a row from the middle
                mid_offset = row_count // 2
                cur.execute(f"SELECT * FROM \"{table_name}\" LIMIT 1 OFFSET {mid_offset};")
                mid_row = cur.fetchone()
                if mid_row and mid_row != first_row:
                    sample_rows.append(mid_row)
        except Exception:
            pass

        if sample_rows:
            formatted_rows = []
            for row in sample_rows:
                formatted_vals = []
                for val in row:
                    if val is None:
                        formatted_vals.append("NULL")
                    elif isinstance(val, str):
                        # Truncate long strings
                        display = val[:30] + "..." if len(val) > 30 else val
                        formatted_vals.append(f"'{display}'")
                    else:
                        formatted_vals.append(str(val))
                formatted_rows.append(f"({', '.join(formatted_vals)})")
            parts.append(f"  sample: {', '.join(formatted_rows)}")

        # Get basic stats per column (only for tables < 5000 rows to avoid slow queries)
        if row_count < 5000:
            stats = []
            for col_name, col_type in columns:
                try:
                    if col_type in ("INTEGER", "INT", "REAL", "FLOAT", "NUMERIC", "DOUBLE"):
                        # Numeric: show range
                        cur.execute(f"SELECT MIN(\"{col_name}\"), MAX(\"{col_name}\") FROM \"{table_name}\";")
                        min_val, max_val = cur.fetchone()
                        if min_val is not None and max_val is not None:
                            if isinstance(min_val, float):
                                stats.append(f"{col_name}[{min_val:.2f}-{max_val:.2f}]")
                            else:
                                stats.append(f"{col_name}[{min_val}-{max_val}]")
                    elif col_type in ("TEXT", "VARCHAR", "CHAR"):
                        # Text: show distinct count and sample values
                        cur.execute(f"SELECT COUNT(DISTINCT \"{col_name}\") FROM \"{table_name}\";")
                        (distinct_count,) = cur.fetchone()
                        if distinct_count and distinct_count <= 10:
                            # Show actual values if few distinct
                            cur.execute(f"SELECT DISTINCT \"{col_name}\" FROM \"{table_name}\" LIMIT 5;")
                            distinct_vals = [row[0] for row in cur.fetchall() if row[0]]
                            if distinct_vals:
                                # Truncate long values
                                display_vals = [v[:20] if len(v) <= 20 else v[:17] + "..." for v in distinct_vals[:5]]
                                stats.append(f"{col_name}[{', '.join(display_vals)}]")
                        elif distinct_count:
                            stats.append(f"{col_name}[{distinct_count} distinct]")
                except Exception:
                    pass

            if stats:
                parts.append(f"  stats: {', '.join(stats)}")

        return "\n".join(parts)
    except Exception:
        return ""


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
