import contextlib
import os
import shutil
import sqlite3
import tempfile
from unittest.mock import patch

import zstandard as zstd
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.test import SimpleTestCase, TestCase, tag

from api.agent.tools.sqlite_batch import execute_sqlite_batch
from api.agent.tools.sqlite_recovery import (
    SQLITE_STATE_RECOVERED_ERROR,
    SQLITE_STATE_UNRECOVERABLE_ERROR,
    SQLiteStateSession,
    SQLiteStateUnrecoverableError,
    SQLiteStateValidationError,
    reset_sqlite_state_session,
    set_sqlite_state_session,
    validate_sqlite_file,
)
from api.agent.tools.sqlite_state import (
    _agent_sqlite_db_uncoordinated,
    reset_sqlite_db_path,
    set_sqlite_db_path,
    sqlite_storage_key,
)
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentError


def _create_test_database(db_path: str, values: tuple[str, ...] = ("safe",)) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE durable_state (value TEXT NOT NULL);")
        conn.executemany(
            "INSERT INTO durable_state (value) VALUES (?);",
            [(value,) for value in values],
        )
        conn.commit()
    finally:
        conn.close()


def _overwrite_header_with_tls_record(db_path: str) -> None:
    tls_record = bytes.fromhex(
        "1703030013"
        "c2e016957863953496c8dcf905001923000017"
    )
    with open(db_path, "r+b") as db_file:
        db_file.seek(5)
        db_file.write(tls_record)


@tag("batch_sqlite")
class SQLiteRecoveryFileTests(SimpleTestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))
        self.db_path = os.path.join(self.tmp_dir, "state.db")
        self.checkpoint_path = os.path.join(self.tmp_dir, "checkpoint.db")

    def test_tls_record_header_overwrite_is_rejected(self):
        _create_test_database(self.db_path)
        _overwrite_header_with_tls_record(self.db_path)

        with self.assertRaisesMessage(SQLiteStateValidationError, "header is invalid"):
            validate_sqlite_file(self.db_path)

    def test_empty_database_is_rejected(self):
        with open(self.db_path, "wb"):
            pass

        with self.assertRaisesMessage(SQLiteStateValidationError, "file is empty"):
            validate_sqlite_file(self.db_path)

    def test_non_header_page_corruption_is_rejected_by_quick_check(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA page_size=4096;")
            conn.execute("CREATE TABLE durable_state (value TEXT NOT NULL);")
            conn.executemany(
                "INSERT INTO durable_state (value) VALUES (?);",
                [("x" * 1000,) for _ in range(100)],
            )
            conn.commit()
        finally:
            conn.close()

        with open(self.db_path, "r+b") as db_file:
            db_file.seek(4096)
            db_file.write(b"\xff" * 32)

        with self.assertRaisesMessage(SQLiteStateValidationError, "quick validation failed"):
            validate_sqlite_file(self.db_path)

    def test_recovery_restores_latest_checkpoint_and_only_runs_once(self):
        _create_test_database(self.db_path, ("first",))
        session = SQLiteStateSession(
            agent_uuid="agent-id",
            db_path=self.db_path,
            checkpoint_path=self.checkpoint_path,
        )
        session.checkpoint(phase="initial")

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("INSERT INTO durable_state (value) VALUES ('second');")
            conn.commit()
        finally:
            conn.close()
        session.checkpoint(phase="after_successful_batch")

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("INSERT INTO durable_state (value) VALUES ('rolled-back');")
            conn.commit()
        finally:
            conn.close()
        _overwrite_header_with_tls_record(self.db_path)

        self.assertTrue(session.protect(phase="after_llm"))
        conn = sqlite3.connect(self.db_path)
        try:
            values = [
                row[0]
                for row in conn.execute(
                    "SELECT value FROM durable_state ORDER BY rowid;"
                ).fetchall()
            ]
        finally:
            conn.close()
        self.assertEqual(values, ["first", "second"])

        _overwrite_header_with_tls_record(self.db_path)
        with self.assertRaises(SQLiteStateUnrecoverableError):
            session.protect(phase="repeated_failure")

    def test_checkpoint_io_failure_does_not_roll_back_valid_state(self):
        _create_test_database(self.db_path, ("first",))
        session = SQLiteStateSession(
            agent_uuid="agent-id",
            db_path=self.db_path,
            checkpoint_path=self.checkpoint_path,
        )
        session.checkpoint(phase="initial")

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("INSERT INTO durable_state (value) VALUES ('second');")
            conn.commit()
        finally:
            conn.close()

        with patch(
            "api.agent.tools.sqlite_recovery.create_validated_sqlite_snapshot",
            side_effect=OSError("checkpoint unavailable"),
        ), self.assertRaises(SQLiteStateUnrecoverableError):
            session.protect(phase="before_llm", checkpoint=True)

        conn = sqlite3.connect(self.db_path)
        try:
            values = [
                row[0]
                for row in conn.execute(
                    "SELECT value FROM durable_state ORDER BY rowid;"
                ).fetchall()
            ]
        finally:
            conn.close()
        self.assertEqual(values, ["first", "second"])
        self.assertEqual(session.recovery_count, 0)

    def test_empty_live_database_is_restored_instead_of_reinitialized(self):
        _create_test_database(self.db_path, ("safe",))
        session = SQLiteStateSession(
            agent_uuid="agent-id",
            db_path=self.db_path,
            checkpoint_path=self.checkpoint_path,
        )
        session.checkpoint(phase="initial")

        with open(self.db_path, "wb"):
            pass

        self.assertTrue(session.protect(phase="before_llm", checkpoint=True))
        conn = sqlite3.connect(self.db_path)
        try:
            value = conn.execute("SELECT value FROM durable_state;").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(value, "safe")


@tag("batch_sqlite")
class SQLitePersistenceSafetyTests(SimpleTestCase):
    def setUp(self):
        self.storage_dir = tempfile.mkdtemp()
        self.storage = FileSystemStorage(location=self.storage_dir)
        self.addCleanup(lambda: shutil.rmtree(self.storage_dir, ignore_errors=True))
        self.agent_uuid = "5dd4b3a1-f698-428e-8779-81fd9b9c6c45"
        self.storage_key = sqlite_storage_key(self.agent_uuid)

    def _stored_bytes(self) -> bytes:
        with self.storage.open(self.storage_key, "rb") as stored_file:
            return stored_file.read()

    def test_healthy_database_round_trips_through_validated_persistence(self):
        with patch(
            "api.agent.tools.sqlite_state.default_storage", self.storage
        ), patch.object(self.storage, "delete", wraps=self.storage.delete) as delete:
            with _agent_sqlite_db_uncoordinated(self.agent_uuid) as db_path:
                _create_test_database(db_path, ("persisted",))

            with _agent_sqlite_db_uncoordinated(self.agent_uuid) as restored_path:
                conn = sqlite3.connect(restored_path)
                try:
                    value = conn.execute(
                        "SELECT value FROM durable_state;"
                    ).fetchone()[0]
                finally:
                    conn.close()

        self.assertEqual(value, "persisted")
        delete.assert_not_called()

    def test_corrupt_stored_database_is_not_replaced_with_fresh_state(self):
        corrupt_path = os.path.join(self.storage_dir, "corrupt.db")
        _create_test_database(corrupt_path)
        _overwrite_header_with_tls_record(corrupt_path)
        with open(corrupt_path, "rb") as corrupt_file:
            corrupt_archive = zstd.ZstdCompressor(level=3).compress(corrupt_file.read())
        self.storage.save(self.storage_key, ContentFile(corrupt_archive))
        original_bytes = self._stored_bytes()

        with patch("api.agent.tools.sqlite_state.default_storage", self.storage):
            with self.assertRaises(SQLiteStateValidationError):
                with _agent_sqlite_db_uncoordinated(self.agent_uuid):
                    self.fail("Corrupt restored state must not be exposed.")

        self.assertEqual(self._stored_bytes(), original_bytes)

    def test_empty_stored_database_is_not_replaced_with_fresh_state(self):
        empty_archive = zstd.ZstdCompressor(level=3).compress(b"")
        self.storage.save(self.storage_key, ContentFile(empty_archive))
        original_bytes = self._stored_bytes()

        with patch("api.agent.tools.sqlite_state.default_storage", self.storage):
            with self.assertRaisesMessage(SQLiteStateValidationError, "file is empty"):
                with _agent_sqlite_db_uncoordinated(self.agent_uuid):
                    self.fail("Empty restored state must not be exposed.")

        self.assertEqual(self._stored_bytes(), original_bytes)

    def test_maintenance_failure_leaves_canonical_archive_unchanged(self):
        with patch("api.agent.tools.sqlite_state.default_storage", self.storage):
            with _agent_sqlite_db_uncoordinated(self.agent_uuid) as db_path:
                _create_test_database(db_path, ("known-good",))
            original_bytes = self._stored_bytes()

            with patch(
                "api.agent.tools.sqlite_state._maintain_sqlite_persistence_candidate",
                side_effect=sqlite3.DatabaseError("maintenance failed"),
            ), patch("api.agent.tools.sqlite_state._log_sqlite_persistence_error"):
                with self.assertRaises(SQLiteStateValidationError):
                    with _agent_sqlite_db_uncoordinated(self.agent_uuid):
                        pass

        self.assertEqual(self._stored_bytes(), original_bytes)

    def test_final_validation_failure_recovers_locally_without_uploading(self):
        with patch("api.agent.tools.sqlite_state.default_storage", self.storage):
            with _agent_sqlite_db_uncoordinated(self.agent_uuid) as db_path:
                _create_test_database(db_path, ("known-good",))
            original_bytes = self._stored_bytes()

            with patch("api.agent.tools.sqlite_state._log_sqlite_persistence_error"):
                with self.assertRaises(SQLiteStateValidationError):
                    with _agent_sqlite_db_uncoordinated(self.agent_uuid) as db_path:
                        conn = sqlite3.connect(db_path)
                        try:
                            conn.execute(
                                "INSERT INTO durable_state (value) VALUES ('not-persisted');"
                            )
                            conn.commit()
                        finally:
                            conn.close()
                        _overwrite_header_with_tls_record(db_path)

        self.assertEqual(self._stored_bytes(), original_bytes)


@tag("batch_sqlite")
class SQLiteBatchRecoveryResultTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(
            username="sqlite-recovery@example.com",
            email="sqlite-recovery@example.com",
            password="secret",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Recovery BA")
        cls.agent = PersistentAgent.objects.create(
            user=user,
            name="SQLite Recovery Agent",
            charter="Test SQLite recovery results",
            browser_use_agent=browser_agent,
        )

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))
        self.db_path = os.path.join(self.tmp_dir, "state.db")
        self.checkpoint_path = os.path.join(self.tmp_dir, "checkpoint.db")
        _create_test_database(self.db_path, ("safe",))
        self.session = SQLiteStateSession(
            agent_uuid=str(self.agent.id),
            db_path=self.db_path,
            checkpoint_path=self.checkpoint_path,
        )
        self.session.checkpoint(phase="initial")
        self.db_token = set_sqlite_db_path(self.db_path)
        self.session_token = set_sqlite_state_session(self.session)
        self.addCleanup(reset_sqlite_state_session, self.session_token)
        self.addCleanup(reset_sqlite_db_path, self.db_token)

    def test_sqlite_batch_returns_recovery_then_unrecoverable_errors(self):
        _overwrite_header_with_tls_record(self.db_path)
        with patch(
            "api.services.agent_sqlite_coordination.agent_sqlite_execution",
            return_value=contextlib.nullcontext(),
        ):
            recovered = execute_sqlite_batch(
                self.agent,
                {"sql": "SELECT value FROM durable_state;"},
            )

            _overwrite_header_with_tls_record(self.db_path)
            unrecoverable = execute_sqlite_batch(
                self.agent,
                {"sql": "SELECT value FROM durable_state;"},
            )

        self.assertEqual(recovered["error_code"], SQLITE_STATE_RECOVERED_ERROR)
        self.assertTrue(recovered["retryable"])
        self.assertEqual(
            unrecoverable["error_code"],
            SQLITE_STATE_UNRECOVERABLE_ERROR,
        )
        self.assertFalse(unrecoverable["retryable"])

    def test_sqlite_batch_rolls_back_corruption_detected_after_execution(self):
        def _corrupt_after_execution(**_kwargs):
            _overwrite_header_with_tls_record(self.db_path)
            return {"status": "ok", "message": "write appeared successful"}

        with patch(
            "api.services.agent_sqlite_coordination.agent_sqlite_execution",
            return_value=contextlib.nullcontext(),
        ), patch(
            "api.agent.tools.sqlite_batch._run_sqlite_batch_in_subprocess",
            side_effect=_corrupt_after_execution,
        ):
            result = execute_sqlite_batch(
                self.agent,
                {"sql": "INSERT INTO durable_state (value) VALUES ('unsafe');"},
            )

        self.assertEqual(result["error_code"], SQLITE_STATE_RECOVERED_ERROR)
        self.assertTrue(result["retryable"])
        validate_sqlite_file(self.db_path)

    def test_finalizer_failure_records_tool_persistence_error(self):
        storage = FileSystemStorage(location=os.path.join(self.tmp_dir, "storage"))

        with patch("api.agent.tools.sqlite_state.default_storage", storage):
            with self.assertRaises(SQLiteStateValidationError):
                with _agent_sqlite_db_uncoordinated(str(self.agent.id)) as db_path:
                    _create_test_database(db_path, ("known-good",))
                    _overwrite_header_with_tls_record(db_path)

        error = PersistentAgentError.objects.get(agent=self.agent)
        self.assertEqual(
            error.category,
            PersistentAgentError.Category.TOOL_PERSISTENCE,
        )
        self.assertEqual(error.context["error_code"], SQLITE_STATE_RECOVERED_ERROR)
        self.assertTrue(error.context["recovered"])
