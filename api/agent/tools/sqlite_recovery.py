import contextvars
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

from opentelemetry import trace


logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")

SQLITE_HEADER = b"SQLite format 3\x00"
SQLITE_STATE_RECOVERED_ERROR = "sqlite_state_recovered"
SQLITE_STATE_UNRECOVERABLE_ERROR = "sqlite_state_unrecoverable"
SQLITE_RECOVERY_NOTICE = (
    "SQLite state failed validation and was restored to the latest validated checkpoint. "
    "Any SQLite changes since that checkpoint were rolled back; retry any required SQLite "
    "operation and do not assume the prior write succeeded."
)
_SQLITE_SIDECAR_SUFFIXES = ("-journal", "-shm", "-wal")


class SQLiteStateError(RuntimeError):
    pass


class SQLiteStateValidationError(SQLiteStateError):
    pass


class SQLiteStateUnrecoverableError(SQLiteStateError):
    pass


def validate_sqlite_file(db_path: str) -> None:
    started_at = time.monotonic()
    try:
        size_bytes = os.path.getsize(db_path)
        if size_bytes:
            with open(db_path, "rb") as db_file:
                header = db_file.read(len(SQLITE_HEADER))
            if header != SQLITE_HEADER:
                raise SQLiteStateValidationError("SQLite header is invalid.")

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout=5000;")
            row = conn.execute("PRAGMA quick_check(1);").fetchone()
        finally:
            conn.close()
    except SQLiteStateValidationError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise SQLiteStateValidationError(f"SQLite quick validation failed: {exc}") from exc

    message = str(row[0]) if row and row[0] is not None else "empty quick_check result"
    if message.lower() != "ok":
        raise SQLiteStateValidationError(f"SQLite quick validation failed: {message}")

    logger.debug(
        "SQLite validation succeeded path=%s db_size_bytes=%s duration_ms=%s",
        db_path,
        size_bytes,
        int(round((time.monotonic() - started_at) * 1000)),
    )


def _remove_sqlite_sidecars(db_path: str) -> None:
    for suffix in _SQLITE_SIDECAR_SUFFIXES:
        try:
            os.remove(f"{db_path}{suffix}")
        except FileNotFoundError:
            continue


def _initialize_sqlite_file(db_path: str) -> None:
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.execute("PRAGMA user_version=0;")
        conn.commit()
    finally:
        conn.close()


def _backup_sqlite_file(source_path: str, destination_path: str) -> None:
    source_conn = sqlite3.connect(source_path, timeout=5)
    destination_conn = sqlite3.connect(destination_path, timeout=5)
    try:
        source_conn.execute("PRAGMA busy_timeout=5000;")
        destination_conn.execute("PRAGMA busy_timeout=5000;")
        source_conn.backup(destination_conn)
        destination_conn.commit()
    finally:
        destination_conn.close()
        source_conn.close()


def create_validated_sqlite_snapshot(source_path: str, destination_path: str) -> None:
    destination_dir = os.path.dirname(destination_path)
    stage_descriptor, stage_path = tempfile.mkstemp(
        prefix=".gobii-sqlite-checkpoint-",
        suffix=".db",
        dir=destination_dir,
    )
    os.close(stage_descriptor)
    try:
        _backup_sqlite_file(source_path, stage_path)
        validate_sqlite_file(stage_path)
        os.replace(stage_path, destination_path)
    except (OSError, sqlite3.Error, SQLiteStateValidationError):
        try:
            os.remove(stage_path)
        except FileNotFoundError:
            pass
        raise


@dataclass
class SQLiteStateSession:
    agent_uuid: str
    db_path: str
    checkpoint_path: str
    recovery_count: int = 0

    def ensure_initialized(self) -> None:
        if not os.path.exists(self.db_path) or os.path.getsize(self.db_path) == 0:
            _initialize_sqlite_file(self.db_path)

    def validate(self, *, phase: str) -> None:
        started_at = time.monotonic()
        db_size_bytes = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        with tracer.start_as_current_span("Validate Agent SQLite State") as span:
            span.set_attribute("persistent_agent.id", self.agent_uuid)
            span.set_attribute("sqlite.validation.phase", phase)
            span.set_attribute("sqlite.validation.db_size_bytes", db_size_bytes)
            span.set_attribute("sqlite.recovery_count", self.recovery_count)
            try:
                validate_sqlite_file(self.db_path)
            except SQLiteStateValidationError as exc:
                span.set_attribute("sqlite.validation.ok", False)
                span.record_exception(exc)
                logger.error(
                    "SQLite state validation failed agent=%s phase=%s db_size_bytes=%s "
                    "recovery_count=%s duration_ms=%s",
                    self.agent_uuid,
                    phase,
                    db_size_bytes,
                    self.recovery_count,
                    int(round((time.monotonic() - started_at) * 1000)),
                    exc_info=True,
                )
                raise
            span.set_attribute("sqlite.validation.ok", True)
            span.set_attribute(
                "sqlite.validation.duration_ms",
                int(round((time.monotonic() - started_at) * 1000)),
            )

    def checkpoint(self, *, phase: str) -> None:
        started_at = time.monotonic()
        with tracer.start_as_current_span("Checkpoint Agent SQLite State") as span:
            span.set_attribute("persistent_agent.id", self.agent_uuid)
            span.set_attribute("sqlite.validation.phase", phase)
            try:
                self.ensure_initialized()
                create_validated_sqlite_snapshot(self.db_path, self.checkpoint_path)
            except (OSError, sqlite3.Error, SQLiteStateValidationError) as exc:
                span.set_attribute("sqlite.validation.ok", False)
                span.record_exception(exc)
                raise SQLiteStateValidationError(
                    f"Failed to create validated SQLite checkpoint during {phase}: {exc}"
                ) from exc
            span.set_attribute("sqlite.validation.ok", True)
            span.set_attribute(
                "sqlite.validation.db_size_bytes",
                os.path.getsize(self.checkpoint_path),
            )
            span.set_attribute(
                "sqlite.validation.duration_ms",
                int(round((time.monotonic() - started_at) * 1000)),
            )
        logger.debug(
            "SQLite checkpoint refreshed agent=%s phase=%s db_size_bytes=%s duration_ms=%s",
            self.agent_uuid,
            phase,
            os.path.getsize(self.checkpoint_path),
            int(round((time.monotonic() - started_at) * 1000)),
        )

    def recover(self, *, phase: str, cause: BaseException) -> None:
        if self.recovery_count >= 1:
            raise SQLiteStateUnrecoverableError(
                f"SQLite state failed validation again during {phase}; automatic recovery is exhausted."
            ) from cause
        if not os.path.exists(self.checkpoint_path):
            raise SQLiteStateUnrecoverableError(
                f"SQLite state failed validation during {phase} and no checkpoint is available."
            ) from cause

        started_at = time.monotonic()
        with tracer.start_as_current_span("Recover Agent SQLite State") as span:
            span.set_attribute("persistent_agent.id", self.agent_uuid)
            span.set_attribute("sqlite.validation.phase", phase)
            span.set_attribute("sqlite.recovery_count", self.recovery_count)
            stage_descriptor, stage_path = tempfile.mkstemp(
                prefix=".gobii-sqlite-recovery-",
                suffix=".db",
                dir=os.path.dirname(self.db_path),
            )
            try:
                with os.fdopen(stage_descriptor, "wb") as stage_file, open(
                    self.checkpoint_path, "rb"
                ) as checkpoint_file:
                    shutil.copyfileobj(checkpoint_file, stage_file)
                validate_sqlite_file(stage_path)
                _remove_sqlite_sidecars(self.db_path)
                os.replace(stage_path, self.db_path)
                validate_sqlite_file(self.db_path)
            except (OSError, sqlite3.Error, SQLiteStateValidationError) as exc:
                span.set_attribute("sqlite.recovery.ok", False)
                span.record_exception(exc)
                try:
                    os.remove(stage_path)
                except FileNotFoundError:
                    pass
                raise SQLiteStateUnrecoverableError(
                    f"Failed to restore the validated SQLite checkpoint during {phase}: {exc}"
                ) from exc

            self.recovery_count += 1
            span.set_attribute("sqlite.recovery.ok", True)
            span.set_attribute("sqlite.recovery_count", self.recovery_count)
            span.set_attribute("sqlite.recovery.db_size_bytes", os.path.getsize(self.db_path))
            span.set_attribute(
                "sqlite.recovery.duration_ms",
                int(round((time.monotonic() - started_at) * 1000)),
            )
            logger.error(
                "SQLite state recovered agent=%s phase=%s db_size_bytes=%s recovery_count=%s duration_ms=%s",
                self.agent_uuid,
                phase,
                os.path.getsize(self.db_path),
                self.recovery_count,
                int(round((time.monotonic() - started_at) * 1000)),
            )

    def validate_or_recover(self, *, phase: str) -> bool:
        try:
            self.validate(phase=phase)
        except SQLiteStateValidationError as exc:
            self.recover(phase=phase, cause=exc)
            return True
        return False

    def checkpoint_or_recover(self, *, phase: str) -> bool:
        try:
            self.checkpoint(phase=phase)
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


def checkpoint_current_sqlite_state(*, phase: str) -> bool:
    session = get_sqlite_state_session()
    if session is None:
        return False
    return session.checkpoint_or_recover(phase=phase)


def validate_current_sqlite_state(*, phase: str) -> bool:
    session = get_sqlite_state_session()
    if session is None:
        return False
    return session.validate_or_recover(phase=phase)
