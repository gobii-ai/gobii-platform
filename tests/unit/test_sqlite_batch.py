from django.test import TestCase, tag
from django.contrib.auth import get_user_model
from django.utils import timezone
import tempfile
import os
import sqlite3
import json

from api.models import PersistentAgent, BrowserUseAgent
from api.agent.tools.sqlite_batch import execute_sqlite_batch
from api.agent.tools.sqlite_query import (
    execute_sqlite_query,
    set_sqlite_db_path as set_query_db_path,
    reset_sqlite_db_path as reset_query_db_path,
)
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
        token_state = set_sqlite_db_path(db_path)
        token_query = set_query_db_path(db_path)

        class _Cxt:
            def __enter__(self_inner):
                return (db_path, token_state, tmp)

            def __exit__(self_inner, exc_type, exc, tb):
                try:
                    reset_sqlite_db_path(token_state)
                finally:
                    try:
                        reset_query_db_path(token_query)
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
            # Create and fill with > default row limit rows to trigger truncation flag
            ops = ["CREATE TABLE t(a INTEGER)"] + [
                f"INSERT INTO t(a) VALUES ({i})" for i in range(400)
            ] + [
                "SELECT a FROM t ORDER BY a",
            ]
            out = execute_sqlite_batch(self.agent, {"operations": ops, "mode": "atomic"})
            results = out.get("results", [])
            select_res = results[-1]
            self.assertEqual(len(select_res["rows"]), 200)
            self.assertTrue(select_res["truncated_rows"])
            self.assertTrue(out.get("truncated_rows"))
            self.assertEqual(out.get("row_limit"), 200)
            # db size present
            self.assertIsInstance(out.get("db_size_mb"), (int, float))

    def test_row_limit_override(self):
        with self._with_temp_db() as (db_path, token, tmp):
            ops = ["CREATE TABLE t(a INTEGER)"] + [
                f"INSERT INTO t(a) VALUES ({i})" for i in range(300)
            ] + [
                "SELECT a FROM t ORDER BY a",
            ]
            out = execute_sqlite_batch(
                self.agent,
                {"operations": ops, "mode": "atomic", "row_limit": 500},
            )
            results = out.get("results", [])
            select_res = results[-1]
            self.assertEqual(len(select_res["rows"]), 300)
            self.assertFalse(select_res["truncated_rows"])
            self.assertFalse(out.get("truncated_rows"))
            self.assertEqual(out.get("row_limit"), 500)

    def test_operations_stringified_json_is_normalized(self):
        with self._with_temp_db() as (db_path, token, tmp):
            ops = [
                "CREATE TABLE t(a INTEGER)",
                "INSERT INTO t(a) VALUES (1)",
                "SELECT a FROM t",
            ]
            payload = {
                "operations": json.dumps(ops),
                "mode": "atomic",
            }
            out = execute_sqlite_batch(self.agent, payload)
            self.assertEqual(out.get("status"), "ok")
            rows = out["results"][-1]["rows"]
            self.assertEqual(rows[0]["a"], 1)

    def test_operations_plain_string_is_wrapped(self):
        with self._with_temp_db() as (db_path, token, tmp):
            payload = {
                "operations": "CREATE TABLE t(a INTEGER)",
                "mode": "atomic",
            }
            out = execute_sqlite_batch(self.agent, payload)
            self.assertEqual(out.get("status"), "ok")
            # Subsequent insert using proper list to confirm table exists
            insert_out = execute_sqlite_batch(
                self.agent,
                {"operations": ["INSERT INTO t(a) VALUES (5)"]},
            )
            self.assertEqual(insert_out.get("status"), "ok")

    def test_operations_list_of_dicts_with_sql_key(self):
        with self._with_temp_db() as (db_path, token, tmp):
            payload = {
                "operations": [
                    {"sql": "CREATE TABLE t(a INTEGER)"},
                    {"sql": "INSERT INTO t(a) VALUES (7)"},
                    {"sql": "SELECT a FROM t"},
                ]
            }
            out = execute_sqlite_batch(self.agent, payload)
            self.assertEqual(out.get("status"), "ok")
            rows = out["results"][-1]["rows"]
            self.assertEqual(rows[0]["a"], 7)

    def test_auto_split_multiple_statements(self):
        with self._with_temp_db() as (db_path, token, tmp):
            payload = {
                "operations": [
                    "CREATE TABLE t(a INTEGER); INSERT INTO t(a) VALUES (1); INSERT INTO t(a) VALUES (2); SELECT COUNT(*) as c FROM t"
                ],
                "mode": "atomic",
            }
            out = execute_sqlite_batch(self.agent, payload)
            self.assertEqual(out.get("status"), "ok")
            results = out.get("results", [])
            # Expect four entries after auto-split
            self.assertEqual(len(results), 4)
            self.assertEqual(results[-1]["rows"][0]["c"], 2)
            warning_texts = out.get("warnings") or []
            self.assertTrue(any("auto-split" in w.lower() for w in warning_texts))

    def test_all_insert_batch_sets_auto_sleep_flag(self):
        with self._with_temp_db() as (db_path, token, tmp):
            execute_sqlite_batch(self.agent, {"operations": ["CREATE TABLE t(a INTEGER)"]})
            out = execute_sqlite_batch(
                self.agent,
                {"operations": ["INSERT INTO t(a) VALUES (1)", "INSERT INTO t(a) VALUES (2)"]},
            )
            self.assertEqual(out.get("status"), "ok")
            self.assertTrue(out.get("auto_sleep_ok"))

    def test_create_only_batch_sets_auto_sleep_flag(self):
        with self._with_temp_db() as (db_path, token, tmp):
            out = execute_sqlite_batch(
                self.agent,
                {"operations": ["CREATE TABLE t(a INTEGER)"]},
            )
            self.assertEqual(out.get("status"), "ok")
            self.assertTrue(out.get("auto_sleep_ok"))

    def test_update_only_batch_sets_auto_sleep_flag(self):
        with self._with_temp_db() as (db_path, token, tmp):
            execute_sqlite_batch(
                self.agent,
                {"operations": [
                    "CREATE TABLE t(a INTEGER)",
                    "INSERT INTO t(a) VALUES (1)",
                ]},
            )

            out = execute_sqlite_batch(
                self.agent,
                {"operations": ["UPDATE t SET a = a + 1"]},
            )
            self.assertEqual(out.get("status"), "ok")
            self.assertTrue(out.get("auto_sleep_ok"))

    def test_batch_with_select_does_not_auto_sleep(self):
        with self._with_temp_db() as (db_path, token, tmp):
            ops = [
                "CREATE TABLE t(a INTEGER)",
                "INSERT INTO t(a) VALUES (1)",
                "SELECT a FROM t",
            ]
            out = execute_sqlite_batch(self.agent, {"operations": ops, "mode": "atomic"})
            self.assertIsNone(out.get("auto_sleep_ok"))

    def test_batch_with_returning_does_not_auto_sleep(self):
        with self._with_temp_db() as (db_path, token, tmp):
            execute_sqlite_batch(
                self.agent,
                {"operations": ["CREATE TABLE t(a INTEGER)"]},
            )
            out = execute_sqlite_batch(
                self.agent,
                {"operations": ["INSERT INTO t(a) VALUES (5)", "UPDATE t SET a = a + 1 RETURNING a"]},
            )
            self.assertEqual(out.get("status"), "ok")
            self.assertIsNone(out.get("auto_sleep_ok"))

    def test_sqlite_query_insert_sets_auto_sleep(self):
        with self._with_temp_db() as (db_path, token, tmp):
            execute_sqlite_batch(self.agent, {"operations": ["CREATE TABLE t(a INTEGER)"]})
            insert_out = execute_sqlite_query(self.agent, {"query": "INSERT INTO t(a) VALUES (5)"})
            self.assertEqual(insert_out.get("status"), "ok")
            self.assertTrue(insert_out.get("auto_sleep_ok"))

            select_out = execute_sqlite_query(self.agent, {"query": "SELECT a FROM t"})
            self.assertEqual(select_out.get("status"), "ok")
            self.assertIsNone(select_out.get("auto_sleep_ok"))

    def test_sqlite_query_create_sets_auto_sleep(self):
        with self._with_temp_db() as (db_path, token, tmp):
            out = execute_sqlite_query(self.agent, {"query": "CREATE TABLE t(a INTEGER)"})
            self.assertEqual(out.get("status"), "ok")
            self.assertTrue(out.get("auto_sleep_ok"))

    def test_sqlite_query_update_sets_auto_sleep(self):
        with self._with_temp_db() as (db_path, token, tmp):
            execute_sqlite_batch(self.agent, {"operations": [
                "CREATE TABLE t(a INTEGER)",
                "INSERT INTO t(a) VALUES (1)",
            ]})
            out = execute_sqlite_query(self.agent, {"query": "UPDATE t SET a = a + 10"})
            self.assertEqual(out.get("status"), "ok")
            self.assertTrue(out.get("auto_sleep_ok"))

    def test_sqlite_query_returning_does_not_auto_sleep(self):
        with self._with_temp_db() as (db_path, token, tmp):
            execute_sqlite_batch(self.agent, {"operations": [
                "CREATE TABLE t(a INTEGER)",
                "INSERT INTO t(a) VALUES (1)",
            ]})
            out = execute_sqlite_query(self.agent, {"query": "UPDATE t SET a = a + 1 RETURNING a"})
            self.assertEqual(out.get("status"), "ok")
            self.assertIsNone(out.get("auto_sleep_ok"))
