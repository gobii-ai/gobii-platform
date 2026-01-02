"""
SQLite query tool for persistent agents.

This module provides SQLite database functionality for persistent agents,
allowing them to store and query structured data as part of their memory
and working state. It also handles persistent storage of the SQLite databases
using compressed archives in object storage.
"""
import contextlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import contextvars
from typing import Dict, Any

import zstandard as zstd
from django.core.files import File
from django.core.files.storage import default_storage

from ...models import PersistentAgent
from .sqlite_guardrails import (
    clear_guarded_connection,
    get_blocked_statement_reason,
    open_guarded_sqlite_connection,
    start_query_timer,
    stop_query_timer,
)
from .sqlite_helpers import is_write_statement

logger = logging.getLogger(__name__)

# Context variable to expose the SQLite DB path to tool execution helpers
_sqlite_db_path_var: contextvars.ContextVar[str] = contextvars.ContextVar("sqlite_db_path", default=None)


def execute_sqlite_query(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an arbitrary SQL query against the agent's SQLite memory DB."""

    query = params.get("query")
    if not query or not isinstance(query, str):
        return {"status": "error", "message": "Missing required parameter: query"}

    # Log the query (truncated if very long)
    query_preview = query.strip()
    if len(query_preview) > 500:
        query_preview = query_preview[:500] + f"... [TRUNCATED, total {len(query)} chars]"

    block_reason = get_blocked_statement_reason(query)
    if block_reason:
        return {"status": "error", "message": f"Query blocked: {block_reason}"}

    should_auto_sleep = is_write_statement(query)

    logger.info(
        "Agent %s executing SQL query: %s",
        agent.id, query_preview
    )

    db_path = _sqlite_db_path_var.get(None)
    if not db_path:
        return {"status": "error", "message": "SQLite DB path unavailable"}

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        cursor = conn.cursor()
        start_query_timer(conn)
        cursor.execute(query)

        # Get database size on disk
        db_size_bytes = 0
        if os.path.exists(db_path):
            db_size_bytes = os.path.getsize(db_path)
        db_size_mb = db_size_bytes / (1024 * 1024)  # Convert to MB

        # Check if size exceeds limit
        size_warning = ""
        if db_size_mb > 50:
            size_warning = " WARNING: DB SIZE EXCEEDS 50MB. YOU MUST EXECUTE MORE QUERIES TO SHRINK THE SIZE, OR THE WHOLE DB WILL BE WIPED!!!"

        # Determine if query returned rows (i.e., SELECT)
        if cursor.description is not None:
            should_auto_sleep = False
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
            # Convert to list of dicts for readability
            results = [dict(zip(columns, row)) for row in rows]
            conn.commit()
            
            # Log query results
            logger.info(
                "Agent %s SQL query returned %d rows, DB size: %.2f MB",
                agent.id, len(results), db_size_mb
            )

            response = {
                "status": "ok", 
                "result": results, 
                "db_size_mb": round(db_size_mb, 2),
                "message": f"Query returned {len(results)} rows. Database size: {db_size_mb:.2f} MB.{size_warning}"
            }
            # Any result set requires inspection, so auto_sleep_ok stays unset
            return response
        else:
            affected = cursor.rowcount
            conn.commit()

            # Log query results for non-SELECT queries
            logger.info(
                "Agent %s SQL query affected %d rows, DB size: %.2f MB",
                agent.id, affected, db_size_mb
            )

            response = {
                "status": "ok", 
                "message": f"{affected} rows affected. Database size: {db_size_mb:.2f} MB.{size_warning}",
                "db_size_mb": round(db_size_mb, 2)
            }
            if should_auto_sleep:
                response["auto_sleep_ok"] = True
            return response
    except Exception as e:
        return {"status": "error", "message": f"SQLite query failed: {e}"}
    finally:
        if conn is not None:
            stop_query_timer(conn)
        try:
            if conn is not None:
                clear_guarded_connection(conn)
                conn.close()
        except Exception:
            pass


def get_sqlite_query_tool() -> Dict[str, Any]:
    """Return the SQLite query tool definition."""
    return {
        "type": "function",
        "function": {
            "name": "sqlite_query",
            "description": "Executes a single SQL statement (including DDL) against the agent's private SQLite memory. Returns rows for SELECT statements. For multiple operations, prefer the sqlite_batch tool to run them in one call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SQL to execute. You are responsible for managing schema and selective retrieval."
                    },
                },
                "required": ["query"],
            },
        },
    }


def get_sqlite_schema_prompt() -> str:
    """Return a human-readable SQLite schema summary capped to 30 KB.

    The summary includes the CREATE TABLE statement of each user table
    followed by its row count, e.g.::

        Table users (rows: 42):
        CREATE TABLE users(id INTEGER PRIMARY KEY, ...);

    The function returns plain text – the caller (event processing)
wraps it with <sqlite_schema> tags automatically. If the database
has no user tables yet, we explicitly state that so the agent knows
it can create its own schema. If the resulting text exceeds 30 KB,
we truncate and add a notice at the end."""

    db_path = _sqlite_db_path_var.get(None)
    if not db_path or not os.path.exists(db_path):
        return "SQLite database not initialised – no schema present yet."

    try:
        conn = open_guarded_sqlite_connection(db_path)
        cur = conn.cursor()
        cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")
        tables = cur.fetchall()

        if not tables:
            return "SQLite database has no user tables yet."

        lines: list[str] = []
        for name, create_stmt in tables:
            # Get row count for each table (cheap thanks to SQLite statistics)
            try:
                cur.execute(f"SELECT COUNT(*) FROM \"{name}\";")
                (count,) = cur.fetchone()
            except Exception:
                count = "?"  # Fallback if the table is newly created or inaccessible
            # Normalise whitespace in CREATE statement to keep size down
            create_stmt_single_line = " ".join(create_stmt.split())
            lines.append(f"Table {name} (rows: {count}): {create_stmt_single_line}")


        block = "\n".join(lines)
        encoded = block.encode("utf-8")
        max_bytes = 30000
        if len(encoded) > max_bytes:
            # Truncate to first max_bytes bytes and decode, ignoring partial utf-8 char
            truncated_text = encoded[:max_bytes].decode("utf-8", errors="ignore")
            truncated_text += "\n... (truncated – schema exceeds 30KB limit)"
            return truncated_text
        return block
    except Exception as e:
        # Fail silently but return notice so the LLM is aware
        return f"Failed to inspect SQLite DB: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


# Expose the context variable for use by the event processing module
def set_sqlite_db_path(db_path: str) -> contextvars.Token:
    """Set the SQLite DB path in the context variable."""
    return _sqlite_db_path_var.set(db_path)


def reset_sqlite_db_path(token: contextvars.Token) -> None:
    """Reset the SQLite DB path context variable."""
    try:
        _sqlite_db_path_var.reset(token)
    except Exception:
        pass 


def sqlite_storage_key(agent_uuid: str) -> str:
    """Return hierarchical object key for a persistent agent SQLite DB archive.

    We shard two levels deep using the first four hexadecimal characters of the
    UUID (hyphens stripped) to avoid performance issues with large buckets while
    keeping the layout human-friendly, mirroring the browser profile strategy.
    """
    clean_uuid = str(agent_uuid).replace("-", "")
    return f"agent_state/{clean_uuid[:2]}/{clean_uuid[2:4]}/{agent_uuid}.db.zst"


@contextlib.contextmanager
def agent_sqlite_db(agent_uuid: str):  # noqa: D401 – simple generator context mgr
    """Context manager that restores/persists the per-agent SQLite DB.

    1. Attempts to download and decompress the DB from object storage.
    2. Yields the on-disk path to the SQLite file in a temporary directory.
    3. On exit, compresses the (potentially modified) DB with zstd and uploads
       it back to object storage, replacing any previous version.
    The temporary directory is cleaned up automatically, ensuring no leakage.
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
                # Corrupt or unreadable archive – emit warning and fall back to fresh DB
                logger.warning(
                    "Failed to restore SQLite DB for agent %s – starting fresh.",
                    agent_uuid,
                    exc_info=True,
                )

        # Expose DB path via context variable for duration of processing
        token = set_sqlite_db_path(db_path)

        # Yield the path to caller for read/write operations
        try:
            yield db_path
        finally:
            # ---------------- Persist phase ---------------- #
            if os.path.exists(db_path):  # Only persist if DB was created/modified

                # ---------------- SQLite maintenance ---------------- #
                try:
                    conn = sqlite3.connect(db_path)
                    try:
                        conn.execute("VACUUM;")
                        # PRAGMA optimize collects statistics & cleans freelist pages
                        try:
                            conn.execute("PRAGMA optimize;")
                        except Exception:
                            # Older SQLite versions may not support this pragma
                            pass
                        conn.commit()
                    finally:
                        conn.close()
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "SQLite maintenance (VACUUM/optimize) failed for agent %s",
                        agent_uuid,
                        exc_info=True,
                    )

                # Check database size before persisting
                db_size_bytes = os.path.getsize(db_path)
                db_size_mb = db_size_bytes / (1024 * 1024)
                
                if db_size_mb > 100:
                    # Database is too large, wipe it instead of persisting
                    logger.info(
                        "SQLite DB for agent %s exceeds 100MB (%.2f MB) - wiping database instead of persisting",
                        agent_uuid,
                        db_size_mb
                    )
                    
                    # Delete the persisted database if it exists, but don't store a new one
                    if default_storage.exists(storage_key):
                        default_storage.delete(storage_key)
                else:
                    # Database size is acceptable, proceed with normal persistence
                    tmp_zst_path = db_path + ".zst"
                    try:
                        cctx = zstd.ZstdCompressor(level=3)
                        with open(db_path, "rb") as f_in, open(tmp_zst_path, "wb") as f_out:
                            cctx.copy_stream(f_in, f_out)

                        # Replace existing object if it exists
                        if default_storage.exists(storage_key):
                            default_storage.delete(storage_key)

                        with open(tmp_zst_path, "rb") as f_in:
                            default_storage.save(storage_key, File(f_in))
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Failed to persist SQLite DB for agent %s", agent_uuid
                        )
                    finally:
                        # Remove compressed temp file if it was created
                        try:
                            os.remove(tmp_zst_path)
                        except Exception:
                            pass

            # Always reset context var so it doesn't leak to other tasks
            reset_sqlite_db_path(token)
