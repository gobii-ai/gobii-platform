import os
import sqlite3
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.tools.sqlite_batch import (
    execute_sqlite_batch,
    _autocorrect_cte_typos,
    _extract_cte_names,
    _extract_select_aliases,
    _is_typo,
)
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
            out = execute_sqlite_batch(self.agent, {"sql": "SELECT 42 AS answer"})
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
            out = execute_sqlite_batch(
                self.agent,
                {
                    "queries": "SELECT 1",
                    "will_continue_work": False,
                    "_has_user_facing_message": True,
                },
            )
            self.assertEqual(out.get("status"), "ok")
            self.assertTrue(out.get("auto_sleep_ok"))

    def test_invalid_queries_are_rejected(self):
        with self._with_temp_db():
            out = execute_sqlite_batch(self.agent, {"queries": ["  "]})
            self.assertEqual(out.get("status"), "error")
            self.assertIn("sql", out.get("message", ""))

    def test_string_or_array_only(self):
        with self._with_temp_db():
            out = execute_sqlite_batch(self.agent, {"sql": 123})
            self.assertEqual(out.get("status"), "error")

    def test_attach_database_is_blocked(self):
        with self._with_temp_db() as (_db_path, _token, tmp):
            escape_path = os.path.join(tmp.name, "escape.db")
            out = execute_sqlite_batch(
                self.agent,
                {"queries": f"ATTACH DATABASE '{escape_path}' AS other"},
            )
            self.assertEqual(out.get("status"), "error")
            self.assertIn("not authorized", out.get("message", "").lower())
            self.assertFalse(os.path.exists(escape_path))

    def test_vacuum_is_blocked(self):
        with self._with_temp_db():
            out = execute_sqlite_batch(self.agent, {"queries": "VACUUM"})
            self.assertEqual(out.get("status"), "error")
            self.assertIn("vacuum", out.get("message", "").lower())

    def test_database_list_pragma_is_blocked(self):
        with self._with_temp_db():
            out = execute_sqlite_batch(self.agent, {"queries": "PRAGMA database_list"})
            self.assertEqual(out.get("status"), "error")
            self.assertIn("not authorized", out.get("message", "").lower())

    def test_large_result_is_truncated(self):
        """Results exceeding MAX_RESULT_ROWS are truncated with warning."""
        with self._with_temp_db():
            # Create table with 200 rows
            create_sql = "CREATE TABLE big (id INTEGER PRIMARY KEY, val TEXT)"
            insert_sql = "INSERT INTO big (val) VALUES " + ",".join(["('x')"] * 200)
            execute_sqlite_batch(self.agent, {"queries": [create_sql, insert_sql]})

            # Query without LIMIT
            out = execute_sqlite_batch(self.agent, {"queries": "SELECT * FROM big"})
            self.assertEqual(out.get("status"), "ok")

            results = out.get("results", [])
            self.assertEqual(len(results), 1)
            rows = results[0].get("result", [])

            # Should be truncated to MAX_RESULT_ROWS (100)
            self.assertLessEqual(len(rows), 100)
            self.assertIn("TRUNCATED", results[0].get("message", ""))

    def test_result_with_limit_not_warned(self):
        """Queries with explicit LIMIT don't trigger warnings."""
        with self._with_temp_db():
            create_sql = "CREATE TABLE small (id INTEGER PRIMARY KEY)"
            insert_sql = "INSERT INTO small (id) VALUES " + ",".join([f"({i})" for i in range(30)])
            execute_sqlite_batch(self.agent, {"queries": [create_sql, insert_sql]})

            # Query WITH explicit LIMIT
            out = execute_sqlite_batch(self.agent, {"queries": "SELECT * FROM small LIMIT 10"})
            self.assertEqual(out.get("status"), "ok")

            results = out.get("results", [])
            message = results[0].get("message", "")
            # Should not have warning since we used LIMIT
            self.assertNotIn("TRUNCATED", message)
            self.assertNotIn("⚠️", message)

    # -------------------------------------------------------------------------
    # Auto-correction tests
    # -------------------------------------------------------------------------

    def test_is_typo_missing_char(self):
        """Detects typos where one char is missing (e.g., 'comment' vs 'comments')."""
        self.assertTrue(_is_typo("comment", "comments"))
        self.assertTrue(_is_typo("hit", "hits"))
        self.assertTrue(_is_typo("item", "items"))
        self.assertTrue(_is_typo("point", "points"))

    def test_is_typo_extra_char(self):
        """Detects typos where one char is extra."""
        self.assertTrue(_is_typo("comments", "comment"))
        self.assertTrue(_is_typo("itemss", "items"))

    def test_is_typo_swapped_char(self):
        """Detects typos where one char is different."""
        self.assertTrue(_is_typo("commant", "comment"))
        self.assertTrue(_is_typo("producs", "products"))

    def test_is_typo_rejects_unrelated(self):
        """Rejects strings that aren't typos."""
        self.assertFalse(_is_typo("comment", "comment"))  # same
        self.assertFalse(_is_typo("foo", "bar"))  # completely different
        self.assertFalse(_is_typo("abc", "abcdef"))  # too different

    def test_extract_cte_names_single(self):
        """Extracts single CTE name."""
        sql = "WITH comments AS (SELECT 1) SELECT * FROM comments"
        self.assertEqual(_extract_cte_names(sql), ["comments"])

    def test_extract_cte_names_multiple(self):
        """Extracts multiple CTE names."""
        sql = "WITH a AS (SELECT 1), b AS (SELECT 2), c AS (SELECT 3) SELECT * FROM a, b, c"
        self.assertEqual(_extract_cte_names(sql), ["a", "b", "c"])

    def test_extract_cte_names_recursive(self):
        """Extracts CTE name from WITH RECURSIVE."""
        sql = "WITH RECURSIVE nums AS (SELECT 1 UNION ALL SELECT n+1 FROM nums) SELECT * FROM nums"
        self.assertEqual(_extract_cte_names(sql), ["nums"])

    def test_extract_select_aliases(self):
        """Extracts column aliases from SELECT."""
        sql = "SELECT a AS foo, b AS bar, c AS baz FROM t"
        aliases = _extract_select_aliases(sql)
        self.assertIn("foo", aliases)
        self.assertIn("bar", aliases)
        self.assertIn("baz", aliases)

    def test_autocorrect_cte_singular_to_plural(self):
        """Auto-corrects 'comment' to 'comments' when CTE is 'comments'."""
        sql = "WITH comments AS (SELECT 1) SELECT * FROM comment"
        corrected, corrections = _autocorrect_cte_typos(sql)
        self.assertIn("FROM comments", corrected)
        self.assertEqual(len(corrections), 1)
        self.assertIn("'comment'→'comments'", corrections[0])

    def test_autocorrect_cte_plural_to_singular(self):
        """Auto-corrects 'items' to 'item' when CTE is 'item'."""
        sql = "WITH item AS (SELECT 1) SELECT * FROM items"
        corrected, corrections = _autocorrect_cte_typos(sql)
        self.assertIn("FROM item", corrected)
        self.assertEqual(len(corrections), 1)

    def test_autocorrect_preserves_correct_references(self):
        """Doesn't change already-correct CTE references."""
        sql = "WITH comments AS (SELECT 1) SELECT * FROM comments"
        corrected, corrections = _autocorrect_cte_typos(sql)
        self.assertEqual(sql, corrected)
        self.assertEqual(corrections, [])

    def test_autocorrect_preserves_tool_results_table(self):
        """Doesn't try to 'fix' __tool_results."""
        sql = "WITH results AS (SELECT 1) SELECT * FROM __tool_results"
        corrected, corrections = _autocorrect_cte_typos(sql)
        self.assertIn("__tool_results", corrected)
        self.assertEqual(corrections, [])

    def test_autocorrect_handles_join(self):
        """Auto-corrects typos in JOIN clauses too."""
        sql = "WITH items AS (SELECT 1 as id) SELECT * FROM __tool_results JOIN item ON 1=1"
        corrected, corrections = _autocorrect_cte_typos(sql)
        self.assertIn("JOIN items", corrected)
        self.assertEqual(len(corrections), 1)

    def test_autocorrect_integration_executes_successfully(self):
        """Full integration: typo is fixed and query executes."""
        with self._with_temp_db():
            # Query has typo: 'number' instead of 'numbers'
            sql = """
            WITH numbers AS (SELECT 1 as n UNION ALL SELECT 2 UNION ALL SELECT 3)
            SELECT * FROM number ORDER BY n
            """
            out = execute_sqlite_batch(self.agent, {"queries": sql})

            # Should succeed because typo was auto-fixed
            self.assertEqual(out.get("status"), "ok")
            results = out.get("results", [])
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["result"], [{"n": 1}, {"n": 2}, {"n": 3}])

            # Message should note the auto-fix
            self.assertIn("auto-fixed", out.get("message", ""))
            self.assertIn("'number'→'numbers'", out.get("message", ""))
