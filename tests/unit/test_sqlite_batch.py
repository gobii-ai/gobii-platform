from django.test import TestCase, tag
from django.contrib.auth import get_user_model
from django.utils import timezone
import tempfile
import os
import sqlite3

from api.models import PersistentAgent, BrowserUseAgent
from api.agent.tools.sqlite_batch import execute_sqlite_batch
from api.agent.tools.sqlite_state import set_sqlite_db_path, reset_sqlite_db_path


@tag("batch_sqlite")
class SqliteBatchToolTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="sqlite-batch@example.com",
            email="sqlite-batch@example.com",
            password="secret",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="BA")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="SQLiteBatchAgent",
            charter="test sqlite batch",
            browser_use_agent=cls.browser_agent,
            created_at=timezone.now(),
        )

    def _with_temp_db(self):
        """Helper context manager to set/reset the sqlite DB path."""
        tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(tmp.name, "state.db")
        token = set_sqlite_db_path(db_path)

        class _Cxt:
            def __enter__(self_inner):
                return (db_path, token, tmp)

            def __exit__(self_inner, exc_type, exc, tb):
                try:
                    reset_sqlite_db_path(token)
                finally:
                    tmp.cleanup()

        return _Cxt()

    def test_atomic_commit_and_select(self):
        with self._with_temp_db() as (db_path, token, tmp):
            ops = [
                "CREATE TABLE t(a INTEGER)",
                "INSERT INTO t(a) VALUES (1),(2)",
                "SELECT a FROM t ORDER BY a",
            ]
            out = execute_sqlite_batch(self.agent, {"operations": ops, "mode": "atomic"})
            self.assertEqual(out.get("status"), "ok")
            results = out.get("results", [])
            self.assertEqual(len(results), 3)
            self.assertEqual(results[2]["schema"], ["a"])  # SELECT columns
            self.assertEqual([r["a"] for r in results[2]["rows"]], [1, 2])
            # db size present
            self.assertIsInstance(out.get("db_size_mb"), (int, float))

    def test_atomic_rollback_on_error(self):
        with self._with_temp_db() as (db_path, token, tmp):
            ops = [
                "CREATE TABLE t(a INTEGER PRIMARY KEY)",
                "INSERT INTO t(a) VALUES (1)",
                "INSERT INTO t(a) VALUES (1)",  # duplicate -> constraint violation
            ]
            out = execute_sqlite_batch(self.agent, {"operations": ops, "mode": "atomic"})
            self.assertEqual(out.get("status"), "error")

            # Verify rollback: table t should not exist
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='t';")
                rows = cur.fetchall()
                self.assertEqual(len(rows), 0)
            finally:
                conn.close()

    def test_per_statement_continues_on_error(self):
        with self._with_temp_db() as (db_path, token, tmp):
            ops = [
                "CREATE TABLE t(a INTEGER PRIMARY KEY)",
                "INSERT INTO t(a) VALUES (1)",
                "INSERT INTO t(a) VALUES (1)",  # duplicate -> error but should continue
                "INSERT INTO t(a) VALUES (2)",
                "SELECT COUNT(*) as c FROM t",
            ]
            out = execute_sqlite_batch(self.agent, {"operations": ops, "mode": "per_statement"})
            self.assertEqual(out.get("status"), "error")  # at least one op failed
            results = out.get("results", [])
            # Last result should be SELECT with c == 2
            self.assertIn("rows", results[-1])
            self.assertEqual(results[-1]["rows"][0]["c"], 2)

            # Ensure table exists and contains two rows
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM t;")
                (count,) = cur.fetchone()
                self.assertEqual(count, 2)
            finally:
                conn.close()

    def test_select_truncation_flag(self):
        with self._with_temp_db() as (db_path, token, tmp):
            # Create and fill with >1000 rows to trigger truncation flag
            ops = ["CREATE TABLE t(a INTEGER)"] + [
                f"INSERT INTO t(a) VALUES ({i})" for i in range(1001)
            ] + [
                "SELECT a FROM t ORDER BY a",
            ]
            out = execute_sqlite_batch(self.agent, {"operations": ops, "mode": "atomic"})
            results = out.get("results", [])
            select_res = results[-1]
            self.assertEqual(len(select_res["rows"]), 1000)
            # db size present
            self.assertIsInstance(out.get("db_size_mb"), (int, float))
