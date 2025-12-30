import os
import sqlite3
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.tools.sqlite_batch import execute_sqlite_batch
from api.agent.tools.sqlite_state import reset_sqlite_db_path, set_sqlite_db_path
from api.models import BrowserUseAgent, PersistentAgent


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
        token_state = set_sqlite_db_path(db_path)

        class _Cxt:
            def __enter__(self_inner):
                return (db_path, token_state, tmp)

            def __exit__(self_inner, exc_type, exc, tb):
                try:
                    reset_sqlite_db_path(token_state)
                finally:
                    tmp.cleanup()

        return _Cxt()

    def test_executes_multiple_queries(self):
        with self._with_temp_db() as (db_path, token, tmp):
            queries = [
                "CREATE TABLE t(a INTEGER)",
                "INSERT INTO t(a) VALUES (1),(2)",
                "SELECT a FROM t ORDER BY a",
            ]
            out = execute_sqlite_batch(self.agent, {"queries": queries})
            self.assertEqual(out.get("status"), "ok")
            results = out.get("results", [])
            self.assertEqual(len(results), len(queries))
            self.assertEqual(results[-1]["result"], [{"a": 1}, {"a": 2}])
            self.assertIsInstance(out.get("db_size_mb"), (int, float))
            self.assertIn("Executed 3 queries", out.get("message", ""))

    def test_stops_on_error_and_reports_index(self):
        with self._with_temp_db() as (db_path, token, tmp):
            queries = [
                "CREATE TABLE t(a INTEGER PRIMARY KEY)",
                "INSERT INTO t(a) VALUES (1)",
                "INSERT INTO t(a) VALUES (1)",  # duplicate -> error
                "INSERT INTO t(a) VALUES (2)",
            ]
            out = execute_sqlite_batch(self.agent, {"queries": queries})
            self.assertEqual(out.get("status"), "error")
            results = out.get("results", [])
            self.assertEqual(len(results), 2)  # stops before failing query
            self.assertIn("Query 2 failed", out.get("message", ""))

            # First insert should have committed; later queries not executed
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM t;")
                (count,) = cur.fetchone()
                self.assertEqual(count, 1)
            finally:
                conn.close()

    def test_single_query_field_is_normalized(self):
        with self._with_temp_db():
            out = execute_sqlite_batch(self.agent, {"queries": "SELECT 42 AS answer"})
            self.assertEqual(out.get("status"), "ok")
            result = out["results"][0]
            self.assertEqual(result["result"][0]["answer"], 42)

    def test_splits_multi_statement_string(self):
        with self._with_temp_db():
            query = "CREATE TABLE t(a INTEGER); INSERT INTO t(a) VALUES (1),(2); SELECT a FROM t ORDER BY a;"
            out = execute_sqlite_batch(self.agent, {"queries": query})
            self.assertEqual(out.get("status"), "ok")
            results = out.get("results", [])
            self.assertEqual(len(results), 3)
            self.assertEqual(results[-1]["result"], [{"a": 1}, {"a": 2}])

    def test_splits_statements_with_extra_separators(self):
        with self._with_temp_db():
            queries = [
                "CREATE TABLE t(a INTEGER); INSERT INTO t(a) VALUES (1);",
                "  ",
                "INSERT INTO t(a) VALUES (2);; SELECT a FROM t ORDER BY a;",
            ]
            out = execute_sqlite_batch(self.agent, {"queries": queries})
            self.assertEqual(out.get("status"), "ok")
            results = out.get("results", [])
            self.assertEqual(len(results), 4)
            self.assertEqual(results[-1]["result"], [{"a": 1}, {"a": 2}])

    def test_handles_semicolons_in_string_literals(self):
        with self._with_temp_db():
            query = "CREATE TABLE t(a TEXT); INSERT INTO t(a) VALUES ('a; b'); SELECT a FROM t;"
            out = execute_sqlite_batch(self.agent, {"queries": query})
            self.assertEqual(out.get("status"), "ok")
            results = out.get("results", [])
            self.assertEqual(results[-1]["result"], [{"a": "a; b"}])

    def test_handles_trigger_with_internal_semicolons(self):
        with self._with_temp_db():
            query = (
                "CREATE TABLE t(a INTEGER);"
                "CREATE TABLE log(x INTEGER);"
                "CREATE TRIGGER t_ai AFTER INSERT ON t BEGIN "
                "INSERT INTO log(x) VALUES (NEW.a); "
                "INSERT INTO log(x) VALUES (NEW.a + 1); "
                "END;"
                "INSERT INTO t(a) VALUES (5);"
                "SELECT x FROM log ORDER BY x;"
            )
            out = execute_sqlite_batch(self.agent, {"queries": query})
            self.assertEqual(out.get("status"), "ok")
            results = out.get("results", [])
            self.assertEqual(results[-1]["result"], [{"x": 5}, {"x": 6}])

    def test_will_continue_work_false_sets_auto_sleep(self):
        with self._with_temp_db():
            out = execute_sqlite_batch(self.agent, {"queries": "SELECT 1", "will_continue_work": False})
            self.assertEqual(out.get("status"), "ok")
            self.assertTrue(out.get("auto_sleep_ok"))

    def test_invalid_queries_are_rejected(self):
        with self._with_temp_db():
            out = execute_sqlite_batch(self.agent, {"queries": ["  "]})
            self.assertEqual(out.get("status"), "error")
            self.assertIn("queries", out.get("message", ""))

    def test_string_or_array_only(self):
        with self._with_temp_db():
            out = execute_sqlite_batch(self.agent, {"queries": {"sql": "SELECT 1"}})
            self.assertEqual(out.get("status"), "error")
