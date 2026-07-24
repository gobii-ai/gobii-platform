import contextlib
import contextvars
from functools import wraps

from pottery import Redlock
from pottery.exceptions import ReleaseUnlockedLock

from config.redis_client import get_redis_client

_ACTIVE_AGENT_SQLITE_LOCK = contextvars.ContextVar("active_agent_sqlite_lock", default=None)
_LOCK_TIMEOUT_SECONDS = 3600
_LOCK_ACQUIRE_TIMEOUT_SECONDS = 1800


class AgentSQLiteBusy(RuntimeError):
    pass


def _agent_sqlite_lock(agent_id: str) -> Redlock:
    return Redlock(
        key=f"agent-sqlite-execution:{agent_id}",
        masters={get_redis_client()},
        auto_release_time=_LOCK_TIMEOUT_SECONDS,
    )


def agent_sqlite_execution_is_active(agent_id: str) -> bool:
    lock = _agent_sqlite_lock(str(agent_id))
    return any(bool(master.exists(lock.key)) for master in lock.masters)


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
    try:
        yield
    finally:
        _ACTIVE_AGENT_SQLITE_LOCK.reset(token)
        try:
            lock.release()
        except ReleaseUnlockedLock:
            pass


def coordinate_sandbox_sqlite_call(method):
    @wraps(method)
    def wrapper(self, agent, *args, **kwargs):
        if not kwargs.get("local_sqlite_db_path"):
            return method(self, agent, *args, **kwargs)
        try:
            with agent_sqlite_execution(str(agent.id)):
                return method(self, agent, *args, **kwargs)
        except AgentSQLiteBusy as exc:
            return {
                "status": "error",
                "error_code": "agent_sqlite_busy",
                "message": str(exc),
                "retryable": True,
            }

    return wrapper
