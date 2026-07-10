import os
import sqlite3
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.tools.sqlite_agent_config import (
    apply_sqlite_agent_config_updates,
    seed_sqlite_agent_config,
    sqlite_batch_mutates_agent_config,
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

    def test_sqlite_agent_config_blocks_schedule_updates_during_planning(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])

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
                        ("Updated planning charter", "0 10 * * *"),
                    )
                    conn.commit()
                finally:
                    conn.close()

                result = apply_sqlite_agent_config_updates(self.agent, snapshot)
            finally:
                reset_sqlite_db_path(token)

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.charter, "Original charter")
        self.assertEqual(self.agent.schedule, "0 9 * * *")
        self.assertFalse(result.updated_fields)
        self.assertNotIn("schedule", result.updated_fields)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("planning mode", result.errors[0].lower())

    def test_nested_sql_wrapper_cannot_hide_agent_config_mutation(self):
        self.assertTrue(
            sqlite_batch_mutates_agent_config(
                {"sql": {"sql": "UPDATE __agent_config SET charter = 'bypassed' WHERE id = 1"}}
            )
        )

    def test_reconciliation_denies_triggered_config_change_without_authority(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "state.db")
            token = set_sqlite_db_path(db_path)
            try:
                snapshot = seed_sqlite_agent_config(self.agent)
                conn = sqlite3.connect(db_path)
                try:
                    conn.executescript(
                        f"""
                        CREATE TABLE proxy(value TEXT);
                        CREATE TRIGGER proxy_config_update
                        AFTER INSERT ON proxy
                        BEGIN
                            UPDATE "{AGENT_CONFIG_TABLE}" SET charter = NEW.value WHERE id = 1;
                        END;
                        INSERT INTO proxy(value) VALUES ('Bypassed charter');
                        """
                    )
                    conn.commit()
                finally:
                    conn.close()

                result = apply_sqlite_agent_config_updates(
                    self.agent,
                    snapshot,
                    can_update_config=False,
                )
            finally:
                reset_sqlite_db_path(token)

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.charter, "Original charter")
        self.assertFalse(result.updated_fields)
        self.assertIn("active requester cannot change", result.errors[0])
