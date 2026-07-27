from contextlib import closing
import os
import sqlite3
import tempfile


SQLITE_HEADER = b"SQLite format 3\x00"
SQLITE_SIDECAR_SUFFIXES = ("-journal", "-shm", "-wal")


class SQLiteFileError(RuntimeError):
    pass


class SQLiteFileValidationError(SQLiteFileError):
    pass


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def initialize_sqlite_file(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    with closing(sqlite3.connect(db_path, timeout=5)) as conn:
        conn.execute("PRAGMA user_version=0;")
        conn.commit()


def validate_sqlite_file(db_path: str) -> None:
    try:
        if os.path.getsize(db_path) == 0:
            raise SQLiteFileValidationError("SQLite file is empty.")
        with open(db_path, "rb") as db_file:
            if db_file.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
                raise SQLiteFileValidationError("SQLite header is invalid.")

        with closing(
            sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        ) as conn:
            conn.execute("PRAGMA busy_timeout=5000;")
            row = conn.execute("PRAGMA quick_check(1);").fetchone()
    except SQLiteFileValidationError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise SQLiteFileValidationError(f"SQLite quick validation failed: {exc}") from exc

    message = str(row[0]) if row and row[0] is not None else "empty quick_check result"
    if message.lower() != "ok":
        raise SQLiteFileValidationError(f"SQLite quick validation failed: {message}")


def remove_sqlite_sidecars(db_path: str) -> None:
    for suffix in SQLITE_SIDECAR_SUFFIXES:
        _remove_file(f"{db_path}{suffix}")


def backup_sqlite_file(source_path: str, destination_path: str) -> None:
    with closing(sqlite3.connect(source_path, timeout=5)) as source_conn, closing(
        sqlite3.connect(destination_path, timeout=5)
    ) as destination_conn:
        source_conn.execute("PRAGMA busy_timeout=5000;")
        destination_conn.execute("PRAGMA busy_timeout=5000;")
        source_conn.backup(destination_conn)
        destination_conn.commit()


def create_validated_sqlite_snapshot(source_path: str, destination_path: str) -> None:
    stage_descriptor, stage_path = tempfile.mkstemp(
        prefix=".gobii-sqlite-checkpoint-",
        suffix=".db",
        dir=os.path.dirname(destination_path) or ".",
    )
    os.close(stage_descriptor)
    try:
        backup_sqlite_file(source_path, stage_path)
        validate_sqlite_file(stage_path)
        os.replace(stage_path, destination_path)
    finally:
        _remove_file(stage_path)


def replace_sqlite_file(source_path: str, destination_path: str) -> None:
    validate_sqlite_file(source_path)
    remove_sqlite_sidecars(destination_path)
    os.replace(source_path, destination_path)
    validate_sqlite_file(destination_path)
