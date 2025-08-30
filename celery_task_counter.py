import os, signal
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from celery.signals import task_postrun, worker_process_init
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)

# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #
# Allow overriding the DB path via env for flexibility in different
# deployment environments. Falls back to /tmp which is writeable in most
# containers / hosts.
DEFAULT_DB_PATH = os.getenv("BROWSER_USE_TASK_COUNTER_DB_PATH", "/tmp/browser_use_task_counts.db")
DB_PATH = Path(DEFAULT_DB_PATH)
_TABLE_NAME = "task_counts"

# Maximum number of tasks this worker should process before shutting down.
# Set via env var; 0 or unset disables the limit.
_MAX_TASK_COUNT = int(os.getenv("BROWSER_USE_TASK_MAX_COUNT", "10"))

# Internal flag to avoid sending duplicate shutdown commands.
_shutdown_requested = False


# --------------------------------------------------------------------------- #
#  Low-level helpers
# --------------------------------------------------------------------------- #

def _get_conn() -> sqlite3.Connection:
    """Return a SQLite connection with WAL journaling enabled for concurrency."""
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,                # Wait up to 30s if the DB is locked
        isolation_level="DEFERRED",
        check_same_thread=False,   # Needed because Celery may share connections
    )
    # Enable WAL for better multi-process concurrency.
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _init_db() -> None:
    """Create the task_counts table if it does not exist."""
    # Ensure the directory exists.
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with _get_conn() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
                worker_hostname TEXT PRIMARY KEY,
                count           INTEGER     NOT NULL DEFAULT 0,
                last_updated    TIMESTAMP   NOT NULL
            );
            """
        )
        conn.commit()


def _increment_count(hostname: str) -> int:
    """Atomically increment the counter for the given worker hostname."""
    for attempt in range(5):
        try:
            with _get_conn() as conn:
                now = datetime.utcnow().isoformat()
                # Upsert and capture new count.
                conn.execute(
                    f"""
                    INSERT INTO {_TABLE_NAME}(worker_hostname, count, last_updated)
                    VALUES (?, 1, ?)
                    ON CONFLICT(worker_hostname) DO UPDATE SET
                        count = count + 1,
                        last_updated = excluded.last_updated;
                    """,
                    (hostname, now),
                )
                # Fetch updated count
                row = conn.execute(
                    f"SELECT count FROM {_TABLE_NAME} WHERE worker_hostname = ?", (hostname,)
                ).fetchone()
                conn.commit()
                return int(row[0]) if row else 0
        except sqlite3.OperationalError as exc:
            # Most likely SQLITE_BUSY – back-off and retry a few times.
            logger.warning(
                "SQLite operational error while incrementing task counter (attempt %s/5): %s",
                attempt + 1,
                exc,
            )
            time.sleep(0.2 * (attempt + 1))
        except Exception:
            logger.exception("Unexpected error while incrementing browser-use task count")
            return 0  # Give up – best effort


def _maybe_trigger_shutdown(hostname: str, current_count: int) -> None:
    """If count exceeds max, request graceful shutdown of this worker."""
    global _shutdown_requested
    if _shutdown_requested:
        return
    if _MAX_TASK_COUNT <= 0:
        return
    if current_count < _MAX_TASK_COUNT:
        return

    try:
        from celery import current_app
        logger.warning(
            "Browser-use task count %s reached max %s on %s – initiating graceful shutdown",
            current_count,
            _MAX_TASK_COUNT,
            hostname,
        )
        # Broadcast shutdown only to this worker instance.
        current_app.control.broadcast("shutdown", destination=[hostname])

        # Send SIGTERM directly to the main worker process as a fallback.
        try:
            parent_pid = os.getppid()
            logger.debug("Sending SIGTERM to parent worker pid %s", parent_pid)
            os.kill(parent_pid, signal.SIGTERM)
        except Exception as exc:
            logger.debug("Unable to signal parent pid %s: %s", parent_pid, exc)
        _shutdown_requested = True
    except Exception:
        logger.exception("Failed to broadcast shutdown command to worker %s", hostname)


# --------------------------------------------------------------------------- #
#  Celery signal hooks
# --------------------------------------------------------------------------- #

@worker_process_init.connect
def _worker_process_init_handler(**_):  # noqa: D401, ANN001
    """Ensure the SQLite DB is ready in every forked worker process."""
    try:
        _init_db()
        logger.debug("Browser-use task counter DB initialised at %s", DB_PATH)
    except Exception:
        logger.exception("Failed to initialise browser-use task counter DB")


@task_postrun.connect
def _task_postrun_handler(task_id=None, task=None, hostname=None, **_):  # noqa: D401, ANN001
    """Increment the counter after every successful or failed Browser-Use task."""
    # We specifically care about the Browser-Use task that orchestrates the agent.
    if task is None:
        return
    if task.name != "gobii_platform.api.tasks.process_browser_use_task":
        return

    try:
        worker_name = (
            hostname
            or getattr(task.request, "hostname", None)
            or os.getenv("CELERY_WORKER_NAME")
            or os.getenv("HOSTNAME", "unknown")
        )
        current_count = _increment_count(worker_name)
        _maybe_trigger_shutdown(worker_name, current_count)
    except Exception:
        logger.exception("Failed to update browser-use task counter")


# --------------------------------------------------------------------------- #
#  Public helper for retrieving counts (optional utility)
# --------------------------------------------------------------------------- #

def get_task_count(hostname: str | None = None) -> int:
    """Return the number of Browser-Use tasks processed by this worker."""
    hostname = hostname or os.getenv("HOSTNAME", "unknown")
    try:
        with _get_conn() as conn:
            row = conn.execute(
                f"SELECT count FROM {_TABLE_NAME} WHERE worker_hostname = ?", (hostname,)
            ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        logger.exception("Failed to read browser-use task count from DB")
        return 0


def cleanup_task_counter_db() -> None:
    """
    Delete the task counter SQLite database file.
    
    This is useful for cleanup during worker shutdown, especially in local
    development environments where you want a fresh start after hitting
    max task limits.
    """
    try:
        if DB_PATH.exists():
            # Also remove WAL and SHM files that SQLite creates in WAL mode
            wal_path = DB_PATH.with_suffix(DB_PATH.suffix + "-wal")
            shm_path = DB_PATH.with_suffix(DB_PATH.suffix + "-shm")
            
            DB_PATH.unlink()
            wal_path.unlink(missing_ok=True)
            shm_path.unlink(missing_ok=True)
            
            logger.info("Cleaned up task counter database at %s", DB_PATH)
        else:
            logger.debug("Task counter database %s does not exist, nothing to clean up", DB_PATH)
    except Exception:
        logger.exception("Failed to cleanup task counter database at %s", DB_PATH) 
