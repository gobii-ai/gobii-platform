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
            permanent_instructions="Original permanent instructions",
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
                        SET charter = ?, schedule = ?, permanent_instructions = ?
                        WHERE id = 1;
                        """,
                        (
                            "Updated charter",
                            "0 10 * * *",
                            "Updated permanent instructions",
                        ),
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
        self.assertEqual(self.agent.permanent_instructions, "Updated permanent instructions")
        self.assertFalse(result.errors)
        self.assertIn("charter", result.updated_fields)
        self.assertIn("schedule", result.updated_fields)
        self.assertIn("permanent_instructions", result.updated_fields)

    def test_sqlite_agent_config_seeds_permanent_instructions(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "state.db")
            token = set_sqlite_db_path(db_path)
            try:
                snapshot = seed_sqlite_agent_config(self.agent)
                conn = sqlite3.connect(db_path)
                try:
                    cur = conn.execute(
                        f"""
                        SELECT charter, schedule, permanent_instructions
                        FROM "{AGENT_CONFIG_TABLE}"
                        WHERE id = 1;
                        """
                    )
                    row = cur.fetchone()
                finally:
                    conn.close()
            finally:
                reset_sqlite_db_path(token)

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.permanent_instructions, "Original permanent instructions")
        self.assertEqual(
            row,
            (
                "Original charter",
                "0 9 * * *",
                "Original permanent instructions",
            ),
        )

    def test_sqlite_agent_config_clears_permanent_instructions(self):
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
                        SET permanent_instructions = ?
                        WHERE id = 1;
                        """,
                        ("   ",),
                    )
                    conn.commit()
                finally:
                    conn.close()

                result = apply_sqlite_agent_config_updates(self.agent, snapshot)
            finally:
                reset_sqlite_db_path(token)

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.permanent_instructions, "")
        self.assertFalse(result.errors)
        self.assertEqual(result.updated_fields, ["permanent_instructions"])

    def test_sqlite_agent_config_unchanged_permanent_instructions_are_not_saved(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "state.db")
            token = set_sqlite_db_path(db_path)
            try:
                snapshot = seed_sqlite_agent_config(self.agent)
                result = apply_sqlite_agent_config_updates(self.agent, snapshot)
            finally:
                reset_sqlite_db_path(token)

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.permanent_instructions, "Original permanent instructions")
        self.assertFalse(result.errors)
        self.assertEqual(result.updated_fields, [])

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
        self.assertEqual(self.agent.charter, "Updated planning charter")
        self.assertEqual(self.agent.schedule, "0 9 * * *")
        self.assertIn("charter", result.updated_fields)
        self.assertNotIn("schedule", result.updated_fields)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("planning mode", result.errors[0].lower())

    def test_sqlite_agent_config_blocks_permanent_instruction_updates_during_planning(self):
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
                        SET permanent_instructions = ?
                        WHERE id = 1;
                        """,
                        ("New durable preference",),
                    )
                    conn.commit()
                finally:
                    conn.close()

                result = apply_sqlite_agent_config_updates(self.agent, snapshot)
            finally:
                reset_sqlite_db_path(token)

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.permanent_instructions, "Original permanent instructions")
        self.assertNotIn("permanent_instructions", result.updated_fields)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("permanent instructions", result.errors[0].lower())
        self.assertIn("planning mode", result.errors[0].lower())
