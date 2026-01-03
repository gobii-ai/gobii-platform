import os
import sqlite3
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.tools.sqlite_agent_config import (
    apply_sqlite_agent_config_updates,
    seed_sqlite_agent_config,
)
from api.agent.tools.sqlite_state import AGENT_CONFIG_TABLE, reset_sqlite_db_path, set_sqlite_db_path
from api.models import BrowserUseAgent, PersistentAgent


@tag("batch_sqlite")
class SqliteAgentConfigTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="sqlite-config@example.com",
            email="sqlite-config@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="SQLite Config Browser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="SQLite Config Agent",
            charter="Original charter",
            schedule="0 9 * * *",
            browser_use_agent=self.browser_agent,
        )

    def test_sqlite_agent_config_applies_updates_and_drops_table(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "state.db")
            token = set_sqlite_db_path(db_path)
            try:
                snapshot = seed_sqlite_agent_config(self.agent)
                conn = sqlite3.connect(db_path)
                try:
                    conn.execute(
                        f"""
                        UPDATE "{AGENT_CONFIG_TABLE}"
                        SET charter = ?, schedule = ?
                        WHERE id = 1;
                        """,
                        ("Updated charter", "0 10 * * *"),
                    )
                    conn.commit()
                finally:
                    conn.close()

                result = apply_sqlite_agent_config_updates(self.agent, snapshot)
                conn = sqlite3.connect(db_path)
                try:
                    cur = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?;",
                        (AGENT_CONFIG_TABLE,),
                    )
                    self.assertIsNone(cur.fetchone())
                finally:
                    conn.close()
            finally:
                reset_sqlite_db_path(token)

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.charter, "Updated charter")
        self.assertEqual(self.agent.schedule, "0 10 * * *")
        self.assertFalse(result.errors)
        self.assertIn("charter", result.updated_fields)
        self.assertIn("schedule", result.updated_fields)
