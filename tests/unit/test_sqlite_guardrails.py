import os
import sqlite3
import tempfile
import json

from django.test import SimpleTestCase, tag

from api.agent.tools.sqlite_guardrails import (
    _grep_context_all,
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

    def test_grep_context_all_defaults_to_larger_context_window(self):
        text = "A" * 80 + "needle" + "B" * 80

        result = _grep_context_all(text, "needle")

        snippets = json.loads(result)
        self.assertEqual(len(snippets), 1)
        self.assertIn("needle", snippets[0])
        self.assertIn("A" * 80, snippets[0])
        self.assertIn("B" * 80, snippets[0])
