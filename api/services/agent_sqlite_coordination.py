import contextlib
import contextvars
import logging
import threading

from pottery import Redlock
from pottery.exceptions import PotteryError, ReleaseUnlockedLock

from config.redis_client import get_redis_client

_ACTIVE_AGENT_SQLITE_LOCK = contextvars.ContextVar("active_agent_sqlite_lock", default=None)
_LOCK_TIMEOUT_SECONDS = 3600
_LOCK_ACQUIRE_TIMEOUT_SECONDS = 1800
_LOCK_MAX_EXTENSIONS = 200

logger = logging.getLogger(__name__)


class AgentSQLiteBusy(RuntimeError):
    pass


def agent_sqlite_busy_result(exc: AgentSQLiteBusy) -> dict:
    return {
        "status": "error",
        "error_code": "agent_sqlite_busy",
        "message": str(exc),
        "retryable": True,
    }


def _agent_sqlite_lock(agent_id: str) -> Redlock:
    return Redlock(
        key=f"agent-sqlite-execution:{agent_id}",
        masters={get_redis_client()},
        auto_release_time=_LOCK_TIMEOUT_SECONDS,
        num_extensions=_LOCK_MAX_EXTENSIONS,
    )


def agent_sqlite_execution_is_active(agent_id: str) -> bool:
    lock = _agent_sqlite_lock(str(agent_id))
    return any(bool(master.exists(lock.key)) for master in lock.masters)


def _extend_lease(lock: Redlock, stopped: threading.Event) -> None:
    while not stopped.wait(max(1, _LOCK_TIMEOUT_SECONDS // 2)):
        try:
            lock.extend()
        except PotteryError as exc:
            logger.warning("Agent SQLite lease extension stopped: %s", exc)
            return


@contextlib.contextmanager
def agent_sqlite_execution(agent_id: str):
    normalized_id = str(agent_id)
    if _ACTIVE_AGENT_SQLITE_LOCK.get() == normalized_id:
        yield
        return

    lock = _agent_sqlite_lock(normalized_id)
    if not lock.acquire(blocking=True, timeout=_LOCK_ACQUIRE_TIMEOUT_SECONDS):
        raise AgentSQLiteBusy("Another SQLite-capable tool is already running for this agent.")

    token = _ACTIVE_AGENT_SQLITE_LOCK.set(normalized_id)
    stopped = threading.Event()
    extender = threading.Thread(
        target=_extend_lease,
        args=(lock, stopped),
        name="agent-sqlite-lease",
        daemon=True,
    )
    extender.start()
    try:
        yield
    finally:
        stopped.set()
        _ACTIVE_AGENT_SQLITE_LOCK.reset(token)
        try:
            lock.release()
        except ReleaseUnlockedLock:
            pass
