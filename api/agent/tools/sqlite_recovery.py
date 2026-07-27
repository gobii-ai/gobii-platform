import contextlib
import contextvars
import logging
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

from opentelemetry import trace

from api.utils.sqlite_files import (
    SQLiteFileError,
    SQLiteFileValidationError,
    create_validated_sqlite_snapshot,
    initialize_sqlite_file,
    replace_sqlite_file,
    validate_sqlite_file,
)

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")

SQLITE_STATE_RECOVERED_ERROR = "sqlite_state_recovered"
SQLITE_STATE_UNRECOVERABLE_ERROR = "sqlite_state_unrecoverable"
SQLITE_RECOVERY_NOTICE = (
    "SQLite state failed validation and was restored to the latest validated checkpoint. "
    "Any SQLite changes since that checkpoint were rolled back; retry any required SQLite "
    "operation and do not assume the prior write succeeded."
)

SQLiteStateError = SQLiteFileError
SQLiteStateValidationError = SQLiteFileValidationError


class SQLiteStateUnrecoverableError(SQLiteStateError):
    pass


class SQLiteStatePersistenceError(SQLiteStateError):
    pass


@dataclass
class SQLiteStateSession:
    agent_uuid: str
    db_path: str
    checkpoint_path: str
    recovery_count: int = 0
    checkpoint_signature: Optional[tuple] = None

    def initialize(self) -> None:
        initialize_sqlite_file(self.db_path)

    def _db_signature(self) -> tuple:
        signature = []
        for suffix in ("", "-wal", "-journal"):
            path = f"{self.db_path}{suffix}"
            with contextlib.suppress(FileNotFoundError):
                stat = os.stat(path)
                signature.append(
                    (path, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                )
        return tuple(signature)

    @contextlib.contextmanager
    def _observe(self, *, operation: str, phase: str):
        started_at = time.monotonic()
        error = None
        with tracer.start_as_current_span(f"{operation.title()} Agent SQLite State") as span:
            try:
                yield
            except Exception as exc:
                # Instrument unexpected failures without changing their propagation.
                error = exc
                span.record_exception(exc)
                raise
            finally:
                duration_ms = int(round((time.monotonic() - started_at) * 1000))
                try:
                    db_size_bytes = os.path.getsize(self.db_path)
                except OSError:
                    db_size_bytes = 0
                outcome = "failed" if error else "succeeded"
                span.set_attributes(
                    {
                        "persistent_agent.id": self.agent_uuid,
                        "sqlite.operation": operation,
                        "sqlite.validation.phase": phase,
                        "sqlite.validation.ok": error is None,
                        "sqlite.validation.duration_ms": duration_ms,
                        "sqlite.validation.db_size_bytes": db_size_bytes,
                        "sqlite.recovery_count": self.recovery_count,
                        "sqlite.recovery.outcome": (
                            outcome if operation == "recover" else "not_attempted"
                        ),
                    }
                )
                logger.log(
                    logging.ERROR if error or operation == "recover" else logging.DEBUG,
                    "SQLite state operation agent=%s operation=%s phase=%s "
                    "db_size_bytes=%s recovery_count=%s duration_ms=%s outcome=%s",
                    self.agent_uuid,
                    operation,
                    phase,
                    db_size_bytes,
                    self.recovery_count,
                    duration_ms,
                    outcome,
                    exc_info=error is not None,
                )

    def validate(self, *, phase: str) -> None:
        with self._observe(operation="validate", phase=phase):
            validate_sqlite_file(self.db_path)

    def checkpoint(self, *, phase: str) -> None:
        with self._observe(operation="checkpoint", phase=phase):
            if (
                os.path.exists(self.checkpoint_path)
                and self._db_signature() == self.checkpoint_signature
            ):
                return
            validate_sqlite_file(self.db_path)
            try:
                create_validated_sqlite_snapshot(self.db_path, self.checkpoint_path)
            except (OSError, sqlite3.Error, SQLiteStateValidationError) as exc:
                try:
                    validate_sqlite_file(self.db_path)
                except SQLiteStateValidationError as validation_exc:
                    raise validation_exc from exc
                raise SQLiteStateUnrecoverableError(
                    f"Failed to create a validated SQLite checkpoint during {phase}; "
                    "the current database remains valid."
                ) from exc
            self.checkpoint_signature = self._db_signature()

    def recover(self, *, phase: str, cause: BaseException) -> None:
        with self._observe(operation="recover", phase=phase):
            if self.recovery_count >= 1:
                raise SQLiteStateUnrecoverableError(
                    f"SQLite state failed validation again during {phase}; "
                    "automatic recovery is exhausted."
                ) from cause
            if not os.path.exists(self.checkpoint_path):
                raise SQLiteStateUnrecoverableError(
                    f"SQLite state failed validation during {phase} and no checkpoint is available."
                ) from cause

            stage_path = f"{self.db_path}.recovery"
            try:
                shutil.copyfile(self.checkpoint_path, stage_path)
                replace_sqlite_file(stage_path, self.db_path)
            except (OSError, sqlite3.Error, SQLiteStateValidationError) as exc:
                raise SQLiteStateUnrecoverableError(
                    f"Failed to restore the validated SQLite checkpoint during {phase}: {exc}"
                ) from exc
            finally:
                try:
                    os.remove(stage_path)
                except FileNotFoundError:
                    pass
            self.recovery_count += 1
            self.checkpoint_signature = self._db_signature()

    def protect(self, *, phase: str, checkpoint: bool = False) -> bool:
        try:
            if checkpoint:
                self.checkpoint(phase=phase)
            else:
                self.validate(phase=phase)
        except SQLiteStateValidationError as exc:
            self.recover(phase=phase, cause=exc)
            return True
        return False


_sqlite_state_session_var: contextvars.ContextVar[Optional[SQLiteStateSession]] = (
    contextvars.ContextVar("sqlite_state_session", default=None)
)


def set_sqlite_state_session(session: SQLiteStateSession) -> contextvars.Token:
    return _sqlite_state_session_var.set(session)


def reset_sqlite_state_session(token: contextvars.Token) -> None:
    _sqlite_state_session_var.reset(token)


def get_sqlite_state_session() -> Optional[SQLiteStateSession]:
    return _sqlite_state_session_var.get(None)


def protect_current_sqlite_state(*, phase: str, checkpoint: bool = False) -> bool:
    session = get_sqlite_state_session()
    if session is None:
        return False
    return session.protect(phase=phase, checkpoint=checkpoint)
