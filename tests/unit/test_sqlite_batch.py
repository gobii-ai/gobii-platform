import json
import os
import sqlite3
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, tag, override_settings
from django.utils import timezone

from api.agent.tools.sqlite_batch import (
    execute_sqlite_batch,
    _apply_all_sql_fixes,
    _autocorrect_ambiguous_column,
    _autocorrect_cte_typos,
    _extract_cte_names,
    _extract_select_aliases,
    _extract_table_refs,
    _fix_dialect_functions,
    _fix_dialect_syntax,
    _fix_escaped_quotes,
    _fix_unescaped_single_quote_runs,
    _fix_json_key_vs_alias,
    _fix_python_operators,
    _fix_singular_plural_columns,
    _fix_singular_plural_tables,
    _fix_unbalanced_parens,
    _is_typo,
    _strip_markdown_fences,
    _strip_trailing_tool_params,
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

    def test_corr_function_is_available(self):
        with self._with_temp_db():
            create_sql = "CREATE TABLE t(x REAL, y REAL)"
            insert_sql = "INSERT INTO t(x, y) VALUES (1, 1), (2, 2), (3, 3), (4, 4)"
            execute_sqlite_batch(self.agent, {"queries": [create_sql, insert_sql]})

            out = execute_sqlite_batch(self.agent, {"queries": "SELECT CORR(x, y) AS corr FROM t"})
            self.assertEqual(out.get("status"), "ok")
            results = out.get("results", [])
            self.assertEqual(len(results), 1)
            corr_value = results[0]["result"][0]["corr"]
            self.assertAlmostEqual(corr_value, 1.0, places=6)

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
            self.assertNotIn("[!]", message)

    @override_settings(SQLITE_BATCH_WALL_TIMEOUT_SECONDS=0.01, SQLITE_BATCH_PROCESS_START_METHOD="spawn")
    def test_batch_timeout_is_enforced(self):
        with self._with_temp_db():
            out = execute_sqlite_batch(self.agent, {"queries": "SELECT 1"})
            self.assertEqual(out.get("status"), "error")
            self.assertIn("timed out", out.get("message", "").lower())

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
        self.assertIn("'comment'->'comments'", corrections[0])

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
            self.assertIn("AUTO-CORRECTED", out.get("message", ""))
            self.assertIn("'number'->'numbers'", out.get("message", ""))
            auto_fix = results[0].get("auto_correction")
            self.assertIsNotNone(auto_fix)
            self.assertIn("FROM number", auto_fix["before"])
            self.assertIn("FROM numbers", auto_fix["after"])

    def test_autocorrect_with_before_create_table_as(self):
        with self._with_temp_db():
            sql = """
            WITH nums AS (SELECT 1 AS id UNION ALL SELECT 2)
            CREATE TABLE t AS
            SELECT * FROM nums
            """
            out = execute_sqlite_batch(
                self.agent,
                {"queries": [sql, "SELECT id FROM t ORDER BY id"]},
            )
            self.assertEqual(out.get("status"), "ok", out.get("message"))
            self.assertIn("AUTO-CORRECTED", out.get("message", ""))
            results = out.get("results", [])
            self.assertEqual(results[1]["result"], [{"id": 1}, {"id": 2}])
            auto_fix = results[0].get("auto_correction")
            self.assertIsNotNone(auto_fix)
            self.assertIn("WITH nums", auto_fix["before"])
            self.assertIn("CREATE TABLE t AS WITH nums", auto_fix["after"])
            self.assertTrue(any("moved WITH clause" in fix for fix in auto_fix["fixes"]))

    def test_autocorrect_multi_pass_cte_and_alias(self):
        """Fixes multiple typos across retries (CTE + alias)."""
        with self._with_temp_db() as (db_path, token, tmp):
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute(
                    "CREATE TABLE __tool_results (result_id TEXT PRIMARY KEY, result_json TEXT)"
                )
                payload = {
                    "content": {
                        "hits": [
                            {
                                "title": "Example",
                                "points": 12,
                                "num_comment": 3,
                                "story_id": "s1",
                                "url": "https://example.com",
                                "author": "alice",
                            }
                        ]
                    }
                }
                cur.execute(
                    "INSERT INTO __tool_results (result_id, result_json) VALUES (?, ?)",
                    ("a10c57d6-b8f1-451e-ac21-4600edb86c5a", json.dumps(payload)),
                )
                conn.commit()
            finally:
                conn.close()

            sql = """
            WITH hits AS (
              SELECT json_extract(r.value,'$.title') as title,
                     COALESCE(json_extract(r.value,'$.points'), 0) as points,
                     COALESCE(json_extract(r.value,'$.num_comment'), 0) as comments,
                     json_extract(r.value,'$.story_id') as story_id,
                     COALESCE(json_extract(r.value,'$.url'), '') as url,
                     json_extract(r.value,'$.author') as author
              FROM __tool_results, json_each(result_json,'$.content.hits') AS r
              WHERE result_id='a10c57d6-b8f1-451e-ac21-4600edb86c5a'
              ORDER BY point DESC
            )
            SELECT title, point, comments, story_id, url, author FROM hit
            """
            out = execute_sqlite_batch(self.agent, {"sql": sql})
            self.assertEqual(out.get("status"), "ok", out.get("message"))
            self.assertIn("AUTO-CORRECTED", out.get("message", ""))
            rows = out["results"][0]["result"]
            self.assertEqual(rows[0]["points"], 12)
            auto_fix = out["results"][0].get("auto_correction")
            self.assertIsNotNone(auto_fix)
            self.assertIn("'hit'->'hits'", auto_fix["fixes"])
            self.assertTrue(any("point" in fix for fix in auto_fix["fixes"]))

    def test_autocorrect_unescaped_single_quote_runs_in_replace(self):
        """Auto-corrects triple-quote literals and runs the query."""
        with self._with_temp_db() as (db_path, token, tmp):
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute(
                    "CREATE TABLE __tool_results (result_id TEXT PRIMARY KEY, result_json TEXT)"
                )
                payload = {
                    "content": {
                        "id": "c1",
                        "author": "alice",
                        "points": 10,
                        "text": "<p>alice&#x27;s comment</p>",
                        "children": [
                            {
                                "id": "c2",
                                "author": "bob",
                                "points": 5,
                                "text": "<p>bob&#x27;s reply</p>",
                            }
                        ],
                    }
                }
                cur.execute(
                    "INSERT INTO __tool_results (result_id, result_json) VALUES (?, ?)",
                    ("79b92bdd-ef82-405c-893e-e26989ce6a48", json.dumps(payload)),
                )
                conn.commit()
            finally:
                conn.close()

            sql = """
            WITH RECURSIVE comment_tree AS (
              SELECT
                json_extract(result_json, '$.content.id') as comment_id,
                json_extract(result_json, '$.content.author') as author,
                json_extract(result_json, '$.content.points') as points,
                json_extract(result_json, '$.content.text') as text,
                0 as depth,
                1 as level
              FROM __tool_results
              WHERE result_id='79b92bdd-ef82-405c-893e-e26989ce6a48'

              UNION ALL

              SELECT
                json_extract(c.value, '$.id') as comment_id,
                json_extract(c.value, '$.author') as author,
                json_extract(c.value, '$.points') as points,
                json_extract(c.value, '$.text') as text,
                1 as depth,
                2 as level
              FROM __tool_results, json_each(result_json, '$.content.children') AS c
              WHERE result_id='79b92bdd-ef82-405c-893e-e26989ce6a48'
            )
            SELECT comment_id, author, points,
                   REPLACE(REPLACE(text, '&#x27;', '''), '</p>', '  ') as clean_text,
                   level
            FROM comment_tree
            WHERE text IS NOT NULL AND text != ''
            ORDER BY level, points DESC
            """
            out = execute_sqlite_batch(self.agent, {"sql": sql})
            self.assertEqual(out.get("status"), "ok", out.get("message"))
            self.assertIn("AUTO-CORRECTED", out.get("message", ""))
            rows = out.get("results", [])[0]["result"]
            self.assertEqual(len(rows), 2)
            self.assertNotIn("&#x27;", rows[0]["clean_text"])
            self.assertNotIn("</p>", rows[0]["clean_text"])

    def test_autocorrect_missing_column_with_json_path_base(self):
        """Auto-corrects missing column to json_extract using result_json paths."""
        with self._with_temp_db() as (db_path, token, tmp):
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute(
                    "CREATE TABLE __tool_results (result_id TEXT PRIMARY KEY, created_at TEXT, result_json TEXT)"
                )
                payload = {
                    "content": {
                        "by": "hn-user",
                        "text": "Hello world",
                        "kids": [1, 2],
                        "url": "https://hacker-news.firebaseio.com/item/123",
                    }
                }
                cur.execute(
                    "INSERT INTO __tool_results (result_id, created_at, result_json) VALUES (?, ?, ?)",
                    ("r1", "2025-01-01T00:00:00Z", json.dumps(payload)),
                )
                conn.commit()
            finally:
                conn.close()

            sql = """
            SELECT result_id, json_extract(result_json,'$.content.by') as author,
                   substr(json_extract(result_json,'$.content.text'),1,1000) as text_preview,
                   json_extract(result_json,'$.content.kids') as has_replies
            FROM __tool_results
            WHERE url LIKE '%hacker-news.firebaseio.com%item%'
            ORDER BY created_at DESC
            """
            out = execute_sqlite_batch(self.agent, {"sql": sql})
            self.assertEqual(out.get("status"), "ok", out.get("message"))
            self.assertIn("AUTO-CORRECTED", out.get("message", ""))
            auto_fix = out["results"][0].get("auto_correction")
            self.assertIsNotNone(auto_fix)
            self.assertIn("$.content.url", auto_fix["after"])
    def test_autocorrect_missing_table_with_schema(self):
        """Auto-corrects missing table using actual schema tables."""
        with self._with_temp_db():
            setup = """
            CREATE TABLE hn_comments (comment_id INTEGER, author TEXT);
            INSERT INTO hn_comments (comment_id, author) VALUES (1, 'alice');
            """
            execute_sqlite_batch(self.agent, {"sql": setup})

            out = execute_sqlite_batch(
                self.agent,
                {"sql": "SELECT comment_id FROM hn_comment ORDER BY comment_id"},
            )

            self.assertEqual(out.get("status"), "ok", out.get("message"))
            self.assertIn("AUTO-CORRECTED", out.get("message", ""))
            self.assertIn("'hn_comment' -> 'hn_comments'", out.get("message", ""))
            self.assertEqual(out["results"][0]["result"], [{"comment_id": 1}])
            auto_fix = out["results"][0].get("auto_correction")
            self.assertIsNotNone(auto_fix)
            self.assertIn("FROM hn_comment", auto_fix["before"])
            self.assertIn("FROM hn_comments", auto_fix["after"])

    def test_autocorrect_missing_table_ambiguous_skips(self):
        """Avoids auto-correct when multiple near matches exist."""
        with self._with_temp_db():
            setup = """
            CREATE TABLE metrics (id INTEGER);
            CREATE TABLE metricx (id INTEGER);
            """
            execute_sqlite_batch(self.agent, {"sql": setup})

            out = execute_sqlite_batch(self.agent, {"sql": "SELECT * FROM metric"})
            self.assertEqual(out.get("status"), "error")
            self.assertIn("no such table: metric", out.get("message", "").lower())
            self.assertNotIn("AUTO-CORRECTED", out.get("message", ""))

    def test_autocorrect_missing_column_with_schema_unqualified(self):
        """Auto-corrects unqualified column names using schema."""
        with self._with_temp_db():
            setup = """
            CREATE TABLE hn_comments (comment_id INTEGER, note TEXT);
            INSERT INTO hn_comments (comment_id, note) VALUES (1, 'ok');
            """
            execute_sqlite_batch(self.agent, {"sql": setup})

            out = execute_sqlite_batch(
                self.agent,
                {"sql": "SELECT coment_id FROM hn_comments ORDER BY coment_id"},
            )

            self.assertEqual(out.get("status"), "ok", out.get("message"))
            self.assertIn("AUTO-CORRECTED", out.get("message", ""))
            self.assertEqual(out["results"][0]["result"], [{"comment_id": 1}])
            auto_fix = out["results"][0].get("auto_correction")
            self.assertIsNotNone(auto_fix)
            self.assertIn("coment_id", auto_fix["before"])
            self.assertIn("comment_id", auto_fix["after"])

    def test_autocorrect_missing_column_with_schema_qualified(self):
        """Auto-corrects qualified column names based on table aliases."""
        with self._with_temp_db():
            setup = """
            CREATE TABLE hn_comments (comment_id INTEGER, note TEXT);
            INSERT INTO hn_comments (comment_id, note) VALUES (1, 'ok');
            """
            execute_sqlite_batch(self.agent, {"sql": setup})

            out = execute_sqlite_batch(
                self.agent,
                {"sql": "SELECT h.commment_id FROM hn_comments h"},
            )

            self.assertEqual(out.get("status"), "ok", out.get("message"))
            self.assertIn("AUTO-CORRECTED", out.get("message", ""))
            self.assertEqual(out["results"][0]["result"], [{"comment_id": 1}])
            auto_fix = out["results"][0].get("auto_correction")
            self.assertIsNotNone(auto_fix)
            self.assertIn("h.commment_id", auto_fix["before"])
            self.assertIn("h.comment_id", auto_fix["after"])

    def test_autocorrect_missing_column_preserves_string_literals(self):
        """Doesn't change string literals when auto-correcting columns."""
        with self._with_temp_db():
            setup = """
            CREATE TABLE hn_comments (comment_id INTEGER, note TEXT);
            INSERT INTO hn_comments (comment_id, note) VALUES (1, 'commentd');
            """
            execute_sqlite_batch(self.agent, {"sql": setup})

            out = execute_sqlite_batch(
                self.agent,
                {"sql": "SELECT commentd FROM hn_comments WHERE note = 'commentd'"},
            )

            self.assertEqual(out.get("status"), "ok", out.get("message"))
            self.assertEqual(out["results"][0]["result"], [{"comment_id": 1}])
            auto_fix = out["results"][0].get("auto_correction")
            self.assertIsNotNone(auto_fix)
            self.assertIn("commentd FROM hn_comments", auto_fix["before"])
            self.assertIn("comment_id FROM hn_comments", auto_fix["after"])
            self.assertIn("note = 'commentd'", auto_fix["after"])

    # -------------------------------------------------------------------------
    # Table reference extraction tests
    # -------------------------------------------------------------------------

    def test_extract_table_refs_simple(self):
        """Extracts single table from FROM."""
        sql = "SELECT * FROM users WHERE id = 1"
        refs = _extract_table_refs(sql)
        self.assertEqual(refs, [("users", "users")])

    def test_extract_table_refs_with_alias(self):
        """Extracts table with AS alias."""
        sql = "SELECT * FROM users AS u WHERE u.id = 1"
        refs = _extract_table_refs(sql)
        self.assertEqual(refs, [("users", "u")])

    def test_extract_table_refs_implicit_alias(self):
        """Extracts table with implicit alias (no AS keyword)."""
        sql = "SELECT * FROM users u WHERE u.id = 1"
        refs = _extract_table_refs(sql)
        self.assertEqual(refs, [("users", "u")])

    def test_extract_table_refs_comma_join(self):
        """Extracts tables from comma-separated FROM clause."""
        sql = "SELECT * FROM users, orders WHERE users.id = orders.user_id"
        refs = _extract_table_refs(sql)
        self.assertIn(("users", "users"), refs)
        self.assertIn(("orders", "orders"), refs)

    def test_extract_table_refs_with_cte(self):
        """Extracts tables including CTE references."""
        sql = "WITH stats AS (SELECT 1) SELECT * FROM iris, stats WHERE 1=1"
        refs = _extract_table_refs(sql)
        self.assertIn(("iris", "iris"), refs)
        self.assertIn(("stats", "stats"), refs)

    # -------------------------------------------------------------------------
    # Ambiguous column auto-correction tests
    # -------------------------------------------------------------------------

    def test_autocorrect_ambiguous_column_simple(self):
        """Qualifies ambiguous column with first non-CTE table."""
        sql = "SELECT species FROM iris, stats WHERE species = stats.species"
        corrected, fix = _autocorrect_ambiguous_column(sql, "species")
        self.assertIn("iris.species", corrected)
        self.assertIsNotNone(fix)
        self.assertIn("'species'->'iris.species'", fix)

    def test_autocorrect_ambiguous_column_order_by(self):
        """Fixes ambiguous column in ORDER BY clause."""
        sql = "SELECT * FROM iris, species_stats WHERE 1=1 ORDER BY species"
        corrected, fix = _autocorrect_ambiguous_column(sql, "species")
        self.assertIn("ORDER BY iris.species", corrected)

    def test_autocorrect_ambiguous_column_preserves_qualified(self):
        """Doesn't change already-qualified column references."""
        sql = "SELECT iris.species, stats.species FROM iris, stats"
        corrected, fix = _autocorrect_ambiguous_column(sql, "species")
        # Should not double-qualify
        self.assertNotIn("iris.iris.species", corrected)
        self.assertNotIn("stats.iris.species", corrected)

    def test_autocorrect_ambiguous_prefers_real_table_over_cte(self):
        """Prefers real table over CTE when qualifying."""
        sql = """
        WITH species_stats AS (SELECT species, AVG(x) FROM iris GROUP BY species)
        SELECT species FROM iris, species_stats WHERE species = species_stats.species
        """
        corrected, fix = _autocorrect_ambiguous_column(sql, "species")
        # Should prefer 'iris' (real table) over 'species_stats' (CTE)
        self.assertIn("iris.species", corrected)

    def test_autocorrect_ambiguous_single_table_no_fix(self):
        """Doesn't attempt fix for single-table queries."""
        sql = "SELECT species FROM iris WHERE species = 'setosa'"
        corrected, fix = _autocorrect_ambiguous_column(sql, "species")
        self.assertEqual(sql, corrected)
        self.assertIsNone(fix)

    # -------------------------------------------------------------------------
    # Ambiguous column integration tests
    # -------------------------------------------------------------------------

    def test_ambiguous_column_integration_auto_fixes(self):
        """Full integration: ambiguous column is fixed and query executes."""
        with self._with_temp_db():
            # Create tables that will cause ambiguous column error
            setup = """
            CREATE TABLE iris (species TEXT, sepal_length REAL);
            INSERT INTO iris VALUES ('setosa', 5.0), ('versicolor', 6.0);
            """
            execute_sqlite_batch(self.agent, {"sql": setup})

            # Query with ambiguous 'species' in ORDER BY
            sql = """
            WITH species_stats AS (
                SELECT species, AVG(sepal_length) as avg_sl FROM iris GROUP BY species
            )
            SELECT species, avg_sl
            FROM iris, species_stats
            WHERE iris.species = species_stats.species
            ORDER BY species
            """
            out = execute_sqlite_batch(self.agent, {"sql": sql})

            # Should succeed because ambiguous column was auto-fixed
            self.assertEqual(out.get("status"), "ok", out.get("message"))
            self.assertIn("AUTO-CORRECTED", out.get("message", ""))
            self.assertIn("'species'->'iris.species'", out.get("message", ""))

    def test_autocorrect_failure_shows_original_error(self):
        """When auto-correction fails, shows original error not retry error."""
        with self._with_temp_db():
            # Query with an error that can't be auto-corrected
            sql = "SELECT nonexistent_column FROM nonexistent_table"
            out = execute_sqlite_batch(self.agent, {"sql": sql})

            self.assertEqual(out.get("status"), "error")
            # Should show original error about the table
            self.assertIn("no such table", out.get("message", "").lower())

    def test_autocorrect_runs_original_first(self):
        """Correct queries run without attempting corrections."""
        with self._with_temp_db():
            # A perfectly valid query - should run without corrections
            sql = "SELECT 1 as x, 2 as y"
            out = execute_sqlite_batch(self.agent, {"sql": sql})

            self.assertEqual(out.get("status"), "ok")
            # Should NOT mention auto-correction
            self.assertNotIn("AUTO-CORRECTED", out.get("message", ""))

    # -------------------------------------------------------------------------
    # LLM artifact cleanup tests
    # -------------------------------------------------------------------------

    def test_strip_trailing_tool_params_will_continue_work(self):
        """Strips trailing will_continue_work=true from SQL."""
        sql = 'SELECT * FROM t ORDER BY x", will_continue_work=true'
        cleaned, fix = _strip_trailing_tool_params(sql)
        self.assertEqual(cleaned, "SELECT * FROM t ORDER BY x")
        self.assertIsNotNone(fix)

    def test_strip_trailing_tool_params_json_style(self):
        """Strips trailing JSON-style params from SQL."""
        sql = 'SELECT * FROM t", "will_continue_work": false}'
        cleaned, fix = _strip_trailing_tool_params(sql)
        self.assertEqual(cleaned, "SELECT * FROM t")
        self.assertIsNotNone(fix)

    def test_strip_trailing_tool_params_brace(self):
        """Strips trailing "} from SQL."""
        sql = 'SELECT * FROM t"}'
        cleaned, fix = _strip_trailing_tool_params(sql)
        self.assertEqual(cleaned, "SELECT * FROM t")
        self.assertIsNotNone(fix)

    def test_strip_trailing_tool_params_preserves_valid(self):
        """Doesn't modify valid SQL without trailing params."""
        sql = "SELECT * FROM t ORDER BY x"
        cleaned, fix = _strip_trailing_tool_params(sql)
        self.assertEqual(cleaned, sql)
        self.assertIsNone(fix)

    def test_strip_markdown_fences(self):
        """Strips markdown code fences from SQL."""
        sql = "```sql\nSELECT * FROM t\n```"
        cleaned, fix = _strip_markdown_fences(sql)
        self.assertEqual(cleaned, "SELECT * FROM t")
        self.assertIsNotNone(fix)

    def test_strip_markdown_fences_no_language(self):
        """Strips markdown code fences without language specifier."""
        sql = "```\nSELECT * FROM t\n```"
        cleaned, fix = _strip_markdown_fences(sql)
        self.assertEqual(cleaned, "SELECT * FROM t")
        self.assertIsNotNone(fix)

    def test_fix_escaped_quotes(self):
        r"""Fixes escaped quotes like \" to '."""
        sql = r'SELECT * FROM t WHERE name = \"John\"'
        fixed, fix = _fix_escaped_quotes(sql)
        self.assertEqual(fixed, "SELECT * FROM t WHERE name = 'John'")
        self.assertIsNotNone(fix)

    def test_fix_unescaped_single_quote_runs(self):
        """Balances odd-length runs of single quotes."""
        sql = "SELECT REPLACE(text, '&#x27;', ''') FROM t"
        fixed, fix = _fix_unescaped_single_quote_runs(sql)
        self.assertEqual(fixed, "SELECT REPLACE(text, '&#x27;', '''') FROM t")
        self.assertIsNotNone(fix)

    # -------------------------------------------------------------------------
    # Python/C operator fix tests
    # -------------------------------------------------------------------------

    def test_fix_python_operators_double_equals(self):
        """Fixes == to = in SQL."""
        sql = "SELECT * FROM t WHERE x == 5"
        fixed, fix = _fix_python_operators(sql)
        self.assertIn("x = 5", fixed)
        self.assertIn("==", fix)

    def test_fix_python_operators_and(self):
        """Fixes && to AND in SQL."""
        sql = "SELECT * FROM t WHERE x = 1 && y = 2"
        fixed, fix = _fix_python_operators(sql)
        self.assertIn("AND", fixed)
        self.assertNotIn("&&", fixed)

    def test_fix_python_operators_preserves_valid(self):
        """Doesn't modify valid SQL."""
        sql = "SELECT * FROM t WHERE x = 5 AND y = 10"
        fixed, fix = _fix_python_operators(sql)
        self.assertEqual(fixed, sql)
        self.assertIsNone(fix)

    # -------------------------------------------------------------------------
    # Dialect function fix tests
    # -------------------------------------------------------------------------

    def test_fix_dialect_functions_if_to_iif(self):
        """Fixes IF() to IIF() for MySQL compatibility."""
        sql = "SELECT IF(x > 0, 'positive', 'negative') FROM t"
        fixed, fix = _fix_dialect_functions(sql)
        self.assertIn("IIF(", fixed)
        self.assertNotIn("IF(", fixed.replace("IIF", ""))
        self.assertIn("IF()", fix)

    def test_fix_dialect_functions_preserves_iif(self):
        """Doesn't double-convert IIF to IIIF."""
        sql = "SELECT IIF(x > 0, 'positive', 'negative') FROM t"
        fixed, fix = _fix_dialect_functions(sql)
        self.assertIn("IIF(", fixed)
        self.assertNotIn("IIIF(", fixed)

    def test_fix_dialect_functions_ilike(self):
        """Fixes ILIKE to LIKE for PostgreSQL compatibility."""
        sql = "SELECT * FROM t WHERE name ILIKE '%john%'"
        fixed, fix = _fix_dialect_functions(sql)
        self.assertIn("LIKE", fixed)
        self.assertNotIn("ILIKE", fixed)

    def test_fix_dialect_functions_concat(self):
        """Fixes CONCAT(a, b) to (a || b)."""
        sql = "SELECT CONCAT(first_name, last_name) FROM users"
        fixed, fix = _fix_dialect_functions(sql)
        self.assertIn("||", fixed)
        self.assertNotIn("CONCAT", fixed)

    def test_fix_dialect_functions_string_agg(self):
        """Fixes STRING_AGG to GROUP_CONCAT."""
        sql = "SELECT STRING_AGG(name, ', ') FROM t GROUP BY category"
        fixed, fix = _fix_dialect_functions(sql)
        self.assertIn("GROUP_CONCAT", fixed)
        self.assertNotIn("STRING_AGG", fixed)

    def test_fix_dialect_functions_nvl2(self):
        """Fixes NVL2(x, y, z) to IIF(x IS NOT NULL, y, z)."""
        sql = "SELECT NVL2(col, 'has value', 'null') FROM t"
        fixed, fix = _fix_dialect_functions(sql)
        self.assertIn("IIF(", fixed)
        self.assertIn("IS NOT NULL", fixed)
        self.assertNotIn("NVL2", fixed)

    # -------------------------------------------------------------------------
    # Dialect syntax fix tests
    # -------------------------------------------------------------------------

    def test_fix_dialect_syntax_top_n(self):
        """Fixes SELECT TOP N to SELECT ... LIMIT N."""
        sql = "SELECT TOP 10 * FROM users ORDER BY created_at"
        fixed, fix = _fix_dialect_syntax(sql)
        self.assertIn("LIMIT 10", fixed)
        self.assertNotIn("TOP", fixed)

    def test_fix_dialect_syntax_truncate(self):
        """Fixes TRUNCATE TABLE to DELETE FROM."""
        sql = "TRUNCATE TABLE users"
        fixed, fix = _fix_dialect_syntax(sql)
        self.assertIn("DELETE FROM users", fixed)
        self.assertNotIn("TRUNCATE", fixed)

    def test_fix_dialect_syntax_postgres_cast(self):
        """Fixes PostgreSQL :: cast to CAST()."""
        sql = "SELECT price::integer FROM products"
        fixed, fix = _fix_dialect_syntax(sql)
        self.assertIn("CAST(price AS integer)", fixed)
        self.assertNotIn("::", fixed)

    # -------------------------------------------------------------------------
    # Unbalanced parentheses fix tests
    # -------------------------------------------------------------------------

    def test_fix_unbalanced_parens_missing_close(self):
        """Adds missing closing paren."""
        sql = "SELECT * FROM t WHERE (x = 1 AND y = 2"
        fixed, fix = _fix_unbalanced_parens(sql)
        self.assertTrue(fixed.endswith(")"))
        self.assertIn("added", fix)

    def test_fix_unbalanced_parens_extra_close(self):
        """Removes extra closing paren at end."""
        sql = "SELECT * FROM t WHERE x = 1)"
        fixed, fix = _fix_unbalanced_parens(sql)
        self.assertFalse(fixed.rstrip().endswith(")"))
        self.assertIn("removed", fix)

    def test_fix_unbalanced_parens_balanced(self):
        """Doesn't modify balanced parentheses."""
        sql = "SELECT * FROM t WHERE (x = 1) AND (y = 2)"
        fixed, fix = _fix_unbalanced_parens(sql)
        self.assertEqual(fixed, sql)
        self.assertIsNone(fix)

    def test_fix_unbalanced_parens_ignores_strings(self):
        """Doesn't count parens inside string literals."""
        sql = "SELECT * FROM t WHERE name = '(test)'"
        fixed, fix = _fix_unbalanced_parens(sql)
        self.assertEqual(fixed, sql)
        self.assertIsNone(fix)

    # -------------------------------------------------------------------------
    # Singular/plural fix tests
    # -------------------------------------------------------------------------

    def test_fix_singular_plural_tables_singular_to_plural(self):
        """Fixes 'user' to 'users' when CTE is 'users'."""
        sql = "WITH users AS (SELECT 1) SELECT * FROM user"
        fixed, fix = _fix_singular_plural_tables(sql, "no such table: user")
        self.assertIn("FROM users", fixed)
        self.assertIn("'user' -> 'users'", fix)

    def test_fix_singular_plural_tables_plural_to_singular(self):
        """Fixes 'items' to 'item' when CTE is 'item'."""
        sql = "WITH item AS (SELECT 1) SELECT * FROM items"
        fixed, fix = _fix_singular_plural_tables(sql, "no such table: items")
        self.assertIn("FROM item", fixed)

    def test_fix_singular_plural_columns(self):
        """Fixes 'point' to 'points' when alias is 'points'."""
        sql = "SELECT x AS points FROM t ORDER BY point"
        fixed, fix = _fix_singular_plural_columns(sql, "no such column: point")
        self.assertIn("ORDER BY points", fixed)

    # -------------------------------------------------------------------------
    # JSON key vs alias fix tests
    # -------------------------------------------------------------------------

    def test_fix_json_key_vs_alias_order_by(self):
        """Fixes ORDER BY using JSON key instead of alias."""
        sql = """SELECT json_extract(r.value,'$.objectID') as comment_id
                 FROM t ORDER BY objectID"""
        fixed, fix = _fix_json_key_vs_alias(sql, "no such column: objectID")
        self.assertIn("ORDER BY comment_id", fixed)
        self.assertIn("objectID", fix)
        self.assertIn("comment_id", fix)

    def test_fix_json_key_vs_alias_where_clause(self):
        """Fixes WHERE clause using JSON key instead of alias."""
        sql = """SELECT json_extract(data,'$.user_id') as uid
                 FROM t WHERE user_id = 123"""
        fixed, fix = _fix_json_key_vs_alias(sql, "no such column: user_id")
        self.assertIn("WHERE uid = 123", fixed)

    def test_fix_json_key_vs_alias_preserves_select(self):
        """Doesn't modify the SELECT clause itself."""
        sql = """SELECT json_extract(r.value,'$.objectID') as comment_id
                 FROM t ORDER BY objectID"""
        fixed, fix = _fix_json_key_vs_alias(sql, "no such column: objectID")
        # The SELECT clause should still have objectID in the json_extract
        self.assertIn("$.objectID", fixed)
        # But ORDER BY should use the alias
        self.assertIn("ORDER BY comment_id", fixed)

    def test_fix_json_key_vs_alias_real_world_example(self):
        """Fixes the exact query from the user's example."""
        sql = """SELECT
          json_extract(r.value,'$.author') as author,
          json_extract(r.value,'$.objectID') as comment_id,
          json_extract(r.value,'$.parent_id') as parent_id
        FROM __tool_results, json_each(result_json,'$.content.hits') AS r
        WHERE result_id='test'
        ORDER BY objectID
        LIMIT 20"""
        fixed, fix = _fix_json_key_vs_alias(sql, "no such column: objectID")
        self.assertIn("ORDER BY comment_id", fixed)
        # Verify the SELECT clause json_extract is unchanged
        self.assertIn("json_extract(r.value,'$.objectID') as comment_id", fixed)

    # -------------------------------------------------------------------------
    # Integration tests for combined fixes
    # -------------------------------------------------------------------------

    def test_apply_all_fixes_multiple(self):
        """Applies multiple fixes in one pass."""
        sql = "```sql\nSELECT * FROM t WHERE x == 5 && y ILIKE '%test%'\n```"
        fixed, fixes = _apply_all_sql_fixes(sql)
        self.assertNotIn("```", fixed)
        self.assertIn("x = 5", fixed)
        self.assertIn("AND", fixed)
        self.assertIn("LIKE", fixed)
        self.assertTrue(len(fixes) >= 3)

    def test_integration_trailing_tool_params(self):
        """Full integration: trailing tool params are stripped and query runs."""
        with self._with_temp_db():
            # Simulate LLM including tool params in SQL
            sql = 'SELECT 1 AS result", will_continue_work=true'
            out = execute_sqlite_batch(self.agent, {"sql": sql})

            self.assertEqual(out.get("status"), "ok", out.get("message"))
            self.assertIn("AUTO-CORRECTED", out.get("message", ""))
            auto_fix = out["results"][0].get("auto_correction")
            self.assertIsNotNone(auto_fix)
            self.assertIn("will_continue_work=true", auto_fix["before"])
            self.assertNotIn("will_continue_work", auto_fix["after"])

    def test_integration_python_operators(self):
        """Full integration: Python operators are fixed and query runs."""
        with self._with_temp_db():
            setup = "CREATE TABLE t (x INT, y INT); INSERT INTO t VALUES (1, 2), (3, 4);"
            execute_sqlite_batch(self.agent, {"sql": setup})

            # Query with Python operators
            sql = "SELECT * FROM t WHERE x == 1 && y == 2"
            out = execute_sqlite_batch(self.agent, {"sql": sql})

            self.assertEqual(out.get("status"), "ok", out.get("message"))
            self.assertEqual(len(out["results"][0]["result"]), 1)
            auto_fix = out["results"][0].get("auto_correction")
            self.assertIsNotNone(auto_fix)
            self.assertIn("x == 1", auto_fix["before"])
            self.assertIn("x = 1", auto_fix["after"])
            self.assertIn("AND", auto_fix["after"])

    def test_integration_dialect_functions(self):
        """Full integration: dialect functions are fixed and query runs."""
        with self._with_temp_db():
            setup = "CREATE TABLE t (x INT); INSERT INTO t VALUES (1), (-1), (0);"
            execute_sqlite_batch(self.agent, {"sql": setup})

            # Query with MySQL IF() function
            sql = "SELECT IF(x > 0, 'positive', 'non-positive') AS sign FROM t"
            out = execute_sqlite_batch(self.agent, {"sql": sql})

            self.assertEqual(out.get("status"), "ok", out.get("message"))
            results = out["results"][0]["result"]
            self.assertEqual(len(results), 3)

    def test_integration_unbalanced_parens(self):
        """Full integration: missing paren is added and query runs."""
        with self._with_temp_db():
            setup = "CREATE TABLE t (x INT, y INT); INSERT INTO t VALUES (1, 2), (3, 4);"
            execute_sqlite_batch(self.agent, {"sql": setup})

            # Query with missing closing paren
            sql = "SELECT * FROM t WHERE (x = 1 AND y = 2"
            out = execute_sqlite_batch(self.agent, {"sql": sql})

            self.assertEqual(out.get("status"), "ok", out.get("message"))
            self.assertIn("AUTO-CORRECTED", out.get("message", ""))
            auto_fix = out["results"][0].get("auto_correction")
            self.assertIsNotNone(auto_fix)
            self.assertIn("WHERE (x = 1 AND y = 2", auto_fix["before"])
            self.assertIn("WHERE (x = 1 AND y = 2)", auto_fix["after"])
