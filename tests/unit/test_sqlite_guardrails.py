import os
import sqlite3
import tempfile

from django.test import SimpleTestCase, tag

from api.agent.tools.sqlite_guardrails import (
    clear_guarded_connection,
    open_guarded_sqlite_connection,
)


@tag("batch_sqlite")
class SqliteGuardrailsMaintenanceTests(SimpleTestCase):
    def _run_vacuum(self, *, allow_attach: bool) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "state.db")
            conn = open_guarded_sqlite_connection(db_path, allow_attach=allow_attach)
            try:
                conn.execute("CREATE TABLE test (id INTEGER);")
                conn.execute("INSERT INTO test (id) VALUES (1);")
                conn.commit()
                conn.execute("VACUUM;")
            finally:
                clear_guarded_connection(conn)
                conn.close()

    def test_guarded_connection_blocks_vacuum_by_default(self):
        with self.assertRaises(sqlite3.DatabaseError):
            self._run_vacuum(allow_attach=False)

    def test_guarded_connection_allows_vacuum_with_attach_enabled(self):
        self._run_vacuum(allow_attach=True)
