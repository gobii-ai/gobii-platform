import os
import sqlite3
import tempfile
import json
import math

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

    def test_patch_text_replaces_or_appends_without_rewriting_other_text(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            conn = open_guarded_sqlite_connection(os.path.join(tmp_dir, "state.db"))
            try:
                replaced = conn.execute(
                    "SELECT patch_text(?, ?, ?)",
                    ("Research leads. Send weekly.", "Send weekly.", "Send daily."),
                ).fetchone()[0]
                appended = conn.execute(
                    "SELECT patch_text(?, '', ?)",
                    (replaced, "Keep outreach natural."),
                ).fetchone()[0]
                duplicate = conn.execute(
                    "SELECT patch_text(?, '', ?)",
                    (appended, "Keep outreach natural."),
                ).fetchone()[0]
            finally:
                clear_guarded_connection(conn)
                conn.close()

        self.assertEqual(replaced, "Research leads. Send daily.")
        self.assertEqual(appended, "Research leads. Send daily.\nKeep outreach natural.")
        self.assertEqual(duplicate, appended)

    def test_patch_text_rejects_missing_replacement_target(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            conn = open_guarded_sqlite_connection(os.path.join(tmp_dir, "state.db"))
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("SELECT patch_text('Keep A.', 'Missing B.', 'Use C.')")
            finally:
                clear_guarded_connection(conn)
                conn.close()

    def test_patch_text_rejects_ambiguous_replacement_target(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            conn = open_guarded_sqlite_connection(os.path.join(tmp_dir, "state.db"))
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("SELECT patch_text('Keep A. Keep A.', 'Keep A.', 'Use B.')")
            finally:
                clear_guarded_connection(conn)
                conn.close()

    def test_statistical_aggregates_match_sample_and_population_semantics(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "state.db")
            conn = open_guarded_sqlite_connection(db_path)
            try:
                conn.execute("CREATE TABLE values_table (value);")
                conn.executemany(
                    "INSERT INTO values_table (value) VALUES (?);",
                    [(1,), (2,), (3,), (None,), ("not-a-number",)],
                )

                row = conn.execute(
                    """
                    SELECT
                        STDDEV(value),
                        STDEV(value),
                        STDDEV_SAMP(value),
                        STDDEV_POP(value),
                        VARIANCE(value),
                        VAR_SAMP(value),
                        VAR_POP(value)
                    FROM values_table;
                    """
                ).fetchone()
            finally:
                clear_guarded_connection(conn)
                conn.close()

        self.assertEqual(row[0], 1.0)
        self.assertEqual(row[1], 1.0)
        self.assertEqual(row[2], 1.0)
        self.assertAlmostEqual(row[3], math.sqrt(2 / 3))
        self.assertEqual(row[4], 1.0)
        self.assertEqual(row[5], 1.0)
        self.assertAlmostEqual(row[6], 2 / 3)

    def test_statistical_aggregates_handle_single_numeric_row(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "state.db")
            conn = open_guarded_sqlite_connection(db_path)
            try:
                conn.execute("CREATE TABLE values_table (value);")
                conn.execute("INSERT INTO values_table (value) VALUES (5);")
                row = conn.execute(
                    """
                    SELECT
                        STDDEV(value),
                        STDDEV_POP(value),
                        VARIANCE(value),
                        VAR_POP(value)
                    FROM values_table;
                    """
                ).fetchone()
            finally:
                clear_guarded_connection(conn)
                conn.close()

        self.assertIsNone(row[0])
        self.assertEqual(row[1], 0.0)
        self.assertIsNone(row[2])
        self.assertEqual(row[3], 0.0)
