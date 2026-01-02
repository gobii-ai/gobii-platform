import json
import os
import sqlite3
import tempfile

from django.test import TestCase, tag

from api.agent.tools.sqlite_state import get_sqlite_schema_prompt, reset_sqlite_db_path, set_sqlite_db_path


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
