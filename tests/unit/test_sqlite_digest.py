import json
import os
import sqlite3
import tempfile

from django.test import TestCase, tag

from api.agent.tools.sqlite_digest import digest
from api.agent.tools.sqlite_state import (
    get_sqlite_digest_prompt,
    reset_sqlite_db_path,
    set_sqlite_db_path,
)


@tag("batch_sqlite")
class SqliteDigestTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "state.db")
        self.token = set_sqlite_db_path(self.db_path)

    def tearDown(self):
        reset_sqlite_db_path(self.token)
        self.tmp.cleanup()

    def _seed_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    email TEXT NOT NULL,
                    created_at TEXT,
                    metadata TEXT
                );
                CREATE TABLE roles (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL
                );
                CREATE TABLE user_roles (
                    user_id INTEGER REFERENCES users(id),
                    role_id INTEGER REFERENCES roles(id),
                    granted_at TEXT,
                    PRIMARY KEY (user_id, role_id)
                );
                CREATE TABLE orders (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    total_amount REAL,
                    status TEXT,
                    created_at TEXT,
                    deleted_at TEXT
                );
                CREATE TABLE audit_log (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    action TEXT,
                    user_id INTEGER
                );
                """
            )
            cur.execute(
                "INSERT INTO users (email, created_at, metadata) VALUES (?, ?, ?)",
                (
                    "alice@example.com",
                    "2024-01-15T10:30:00Z",
                    json.dumps({"level": 1, "tags": ["a", "b"]}),
                ),
            )
            cur.execute(
                "INSERT INTO users (email, created_at, metadata) VALUES (?, ?, ?)",
                (
                    "bob@example.com",
                    "2024-01-16T09:15:00Z",
                    json.dumps({"level": 2, "tags": ["c"]}),
                ),
            )
            cur.execute("INSERT INTO roles (name) VALUES ('admin')")
            cur.execute("INSERT INTO roles (name) VALUES ('user')")
            cur.execute(
                "INSERT INTO user_roles (user_id, role_id, granted_at) VALUES (1, 1, ?)",
                ("2024-01-20T12:00:00Z",),
            )
            cur.execute(
                "INSERT INTO user_roles (user_id, role_id, granted_at) VALUES (2, 2, ?)",
                ("2024-01-21T12:00:00Z",),
            )
            cur.execute(
                """
                INSERT INTO orders (user_id, total_amount, status, created_at, deleted_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (1, 12.5, "shipped", "2024-02-01T10:00:00Z", None),
            )
            cur.execute(
                """
                INSERT INTO orders (user_id, total_amount, status, created_at, deleted_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (2, 8.25, "pending", "2024-02-02T08:00:00Z", "2024-02-10T08:00:00Z"),
            )
            cur.execute(
                "INSERT INTO audit_log (timestamp, action, user_id) VALUES (?, ?, ?)",
                ("2024-02-03T07:00:00Z", "login", 1),
            )
            conn.commit()
        finally:
            conn.close()

    def test_digest_empty_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.close()
        digest_result = digest(self.db_path)
        self.assertEqual(digest_result.table_count, 0)
        self.assertEqual(digest_result.verdict, "minimal")
        self.assertEqual(digest_result.action, "skip")

    def test_digest_detects_patterns(self):
        self._seed_db()
        digest_result = digest(self.db_path)
        self.assertGreaterEqual(digest_result.table_count, 5)
        self.assertGreater(digest_result.explicit_fk_count, 0)
        self.assertTrue(digest_result.has_junction_tables)
        self.assertTrue(digest_result.has_lookup_tables)
        self.assertTrue(digest_result.has_log_tables)
        self.assertTrue(digest_result.has_soft_deletes)
        self.assertTrue(digest_result.has_timestamps)
        self.assertGreaterEqual(digest_result.detected_json_columns, 1)
        self.assertGreaterEqual(digest_result.detected_datetime_columns, 1)
        self.assertIn(digest_result.schema_pattern, {"normalized", "relational", "star", "flat", "loosely_coupled"})
        self.assertIn(digest_result.verdict, {"clean", "usable", "messy"})
        self.assertIn(digest_result.action, {"query_directly", "inspect_schema", "needs_cleaning"})

    def test_digest_prompt_renders(self):
        self._seed_db()
        prompt = get_sqlite_digest_prompt()
        self.assertIn("<sqlite_digest>", prompt)
        self.assertIn("VERDICT:", prompt)
        self.assertIn("schema_style:", prompt)
