import json
import os
import sqlite3
import tempfile

from django.test import TestCase, tag

from api.agent.tools.sqlite_state import (
    get_sqlite_model_table_columns,
    get_sqlite_schema_prompt,
    reset_sqlite_db_path,
    set_sqlite_db_path,
)


@tag("batch_sqlite")
class SqliteSchemaPromptTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "state.db")
        self.token = set_sqlite_db_path(self.db_path)

        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                "CREATE TABLE events (id INTEGER, payload TEXT, notes TEXT, csv_blob TEXT)"
            )
            payload1 = json.dumps(
                {
                    "type": "signup",
                    "meta": {"ip": "10.0.0.1", "tags": ["alpha", "beta"]},
                    "nested": json.dumps({"deep": {"value": 42}}),
                    "csv": "col1,col2\n1,2\n3,4",
                }
            )
            payload2 = json.dumps(
                {
                    "type": "login",
                    "meta": {"ip": "10.0.0.2", "tags": ["gamma"]},
                }
            )
            cur.execute(
                "INSERT INTO events (id, payload, notes, csv_blob) VALUES (?, ?, ?, ?)",
                (
                    1,
                    payload1,
                    "Contact us at test@example.com or https://example.com/help",
                    "name,age\nAda,37\nBob,41",
                ),
            )
            cur.execute(
                "INSERT INTO events (id, payload, notes, csv_blob) VALUES (?, ?, ?, ?)",
                (
                    2,
                    payload2,
                    "Follow up: user@example.org",
                    "name,age\nCara,29",
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        reset_sqlite_db_path(self.token)
        self.tmp.cleanup()

    def test_schema_prompt_detects_json_csv_text(self):
        prompt = get_sqlite_schema_prompt()
        self.assertIn("Table events", prompt)
        # New deep analysis format: "column_name TYPE → inferred_type: ..."
        self.assertRegex(prompt, r"payload.*json")
        self.assertRegex(prompt, r"csv_blob.*csv")
        # Notes column may or may not detect email pattern depending on analysis
        self.assertIn("notes", prompt)

    def test_model_columns_include_durable_tables_only(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("CREATE TABLE __internal (secret TEXT)")
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(
            get_sqlite_model_table_columns(),
            {"events": {"id", "payload", "notes", "csv_blob"}},
        )

    def test_schema_keeps_new_domain_table_visible_past_detail_cap(self):
        conn = sqlite3.connect(self.db_path)
        try:
            for index in range(30):
                conn.execute(
                    f"CREATE TABLE a_reference_{index:02d} "
                    "(record_key TEXT PRIMARY KEY, recorded_value TEXT)"
                )
            conn.execute(
                "CREATE TABLE research_people "
                "(person_id TEXT PRIMARY KEY, full_name TEXT NOT NULL, role_title TEXT, source_url TEXT)"
            )
            conn.commit()
        finally:
            conn.close()

        prompt = get_sqlite_schema_prompt()

        self.assertIn("Table research_people", prompt)
        self.assertIn("role_title", prompt)

    def test_recent_domain_table_can_be_prioritized_within_detail_cap(self):
        conn = sqlite3.connect(self.db_path)
        try:
            for index in range(30):
                conn.execute(
                    f"CREATE TABLE a_reference_{index:02d} "
                    "(record_key TEXT PRIMARY KEY, recorded_value TEXT)"
                )
            conn.execute(
                "CREATE TABLE m_account_truth "
                "(account_id TEXT PRIMARY KEY, stage TEXT NOT NULL, next_action TEXT)"
            )
            for index in range(10):
                conn.execute(
                    f"CREATE TABLE z_archive_{index:02d} "
                    "(record_key TEXT PRIMARY KEY, recorded_value TEXT)"
                )
            conn.commit()
        finally:
            conn.close()

        prompt = get_sqlite_schema_prompt(prioritized_tables=("m_account_truth",))
        columns = get_sqlite_model_table_columns(prioritized_tables=("m_account_truth",))

        self.assertIn("Table m_account_truth", prompt)
        self.assertEqual(
            columns["m_account_truth"],
            {"account_id", "stage", "next_action"},
        )

    def test_prioritized_domain_table_precedes_prompt_byte_truncation(self):
        conn = sqlite3.connect(self.db_path)
        try:
            wide_columns = ", ".join(
                f"evidence_field_{index:02d} TEXT"
                for index in range(80)
            )
            for index in range(30):
                conn.execute(
                    f"CREATE TABLE a_reference_{index:02d} "
                    f"(record_key TEXT PRIMARY KEY, {wide_columns})"
                )
            conn.execute(
                "CREATE TABLE m_account_truth "
                "(account_id TEXT PRIMARY KEY, stage TEXT NOT NULL, next_action TEXT)"
            )
            conn.commit()
        finally:
            conn.close()

        prompt = get_sqlite_schema_prompt(prioritized_tables=("m_account_truth",))

        self.assertIn("Table m_account_truth", prompt)
        self.assertIn("next_action", prompt)
        self.assertLess(
            prompt.index("Table m_account_truth"),
            prompt.index("Table a_reference_00"),
        )

    def test_new_domain_table_survives_byte_cap_before_table_cap(self):
        conn = sqlite3.connect(self.db_path)
        try:
            wide_columns = ", ".join(
                f"evidence_field_{index:02d} TEXT"
                for index in range(200)
            )
            evidence_values = ["detailed evidence " * 20] * 12
            for index in range(20):
                conn.execute(
                    f"CREATE TABLE a_reference_{index:02d} "
                    f"(record_key TEXT PRIMARY KEY, {wide_columns})"
                )
                selected_columns = ", ".join(
                    f"evidence_field_{field_index:02d}"
                    for field_index in range(12)
                )
                conn.execute(
                    f"INSERT INTO a_reference_{index:02d} "
                    f"(record_key, {selected_columns}) VALUES "
                    f"({', '.join('?' for _ in range(13))})",
                    (f"reference-{index}", *evidence_values),
                )
            conn.execute(
                "CREATE TABLE research_accounts "
                "(account_id TEXT PRIMARY KEY, current_stage TEXT, next_action TEXT)"
            )
            conn.commit()
        finally:
            conn.close()

        prompt = get_sqlite_schema_prompt()

        self.assertIn("Table research_accounts", prompt)
        self.assertIn("next_action", prompt)
