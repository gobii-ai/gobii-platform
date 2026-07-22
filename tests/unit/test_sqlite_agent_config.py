import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.emotions import normalize_emotion_update
from api.agent.tools.sqlite_agent_config import (
    apply_sqlite_agent_config_updates,
    seed_sqlite_agent_config,
    sqlite_statement_assigns_agent_config_field,
)
from api.agent.tools.sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
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

    @contextmanager
    def _sqlite_state(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "state.db")
            token = set_sqlite_db_path(db_path)
            try:
                yield db_path
            finally:
                reset_sqlite_db_path(token)

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
        self.assertEqual(self.agent.charter, "Updated planning charter")
        self.assertEqual(self.agent.schedule, "0 9 * * *")
        self.assertIn("charter", result.updated_fields)
        self.assertNotIn("schedule", result.updated_fields)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("planning mode", result.errors["schedule"].lower())

    def test_sqlite_agent_config_reports_attempted_noop(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "state.db")
            token = set_sqlite_db_path(db_path)
            try:
                snapshot = seed_sqlite_agent_config(self.agent)
                conn = sqlite3.connect(db_path)
                try:
                    conn.execute(
                        f'UPDATE "{AGENT_CONFIG_TABLE}" SET charter = charter WHERE id = 1;'
                    )
                    conn.commit()
                finally:
                    conn.close()

                result = apply_sqlite_agent_config_updates(
                    self.agent,
                    snapshot,
                )
            finally:
                reset_sqlite_db_path(token)

        self.assertFalse(result.updated_fields)
        self.assertFalse(result.errors)

    def test_failed_patch_does_not_persist_or_schedule_charter_update(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "state.db")
            token = set_sqlite_db_path(db_path)
            try:
                snapshot = seed_sqlite_agent_config(self.agent)
                conn = open_guarded_sqlite_connection(db_path)
                try:
                    with self.assertRaises(sqlite3.OperationalError):
                        conn.execute(
                            f'''UPDATE "{AGENT_CONFIG_TABLE}"
                                SET charter = patch_text(charter, 'Missing clause', 'New clause')
                                WHERE id = 1;'''
                        )
                    conn.rollback()
                finally:
                    clear_guarded_connection(conn)
                    conn.close()

                with patch("api.agent.tools.sqlite_agent_config.execute_update_charter") as update_charter:
                    result = apply_sqlite_agent_config_updates(
                        self.agent,
                        snapshot,
                    )
            finally:
                reset_sqlite_db_path(token)

        update_charter.assert_not_called()
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.charter, "Original charter")
        self.assertFalse(result.updated_fields)
        self.assertFalse(result.errors)

    def test_sqlite_agent_config_sets_complex_emotion_at_max_timeout(self):
        before = timezone.now()
        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    f'''UPDATE "{AGENT_CONFIG_TABLE}"
                        SET emotion = ?, emotion_timeout_seconds = 86400
                        WHERE id = 1;''',
                    ("👨🏽‍💻",),
                )
                conn.commit()
            finally:
                conn.close()
            result = apply_sqlite_agent_config_updates(self.agent, snapshot)
        after = timezone.now()

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.emotion, "👨🏽‍💻")
        self.assertGreaterEqual(self.agent.emotion_expires_at, before + timedelta(seconds=86400))
        self.assertLessEqual(self.agent.emotion_expires_at, after + timedelta(seconds=86400))
        self.assertEqual(self.agent.get_active_emotion_state()[0], "👨🏽‍💻")
        self.assertIn("emotion", result.updated_fields)
        self.assertFalse(result.errors)

    def test_sqlite_agent_config_clears_emotion_with_paired_nulls(self):
        self.agent.emotion = "🙂"
        self.agent.emotion_expires_at = timezone.now() + timedelta(hours=2)
        self.agent.save(update_fields=["emotion", "emotion_expires_at"])

        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    f'''UPDATE "{AGENT_CONFIG_TABLE}"
                        SET emotion = NULL, emotion_timeout_seconds = NULL
                        WHERE id = 1;'''
                )
                conn.commit()
            finally:
                conn.close()
            result = apply_sqlite_agent_config_updates(self.agent, snapshot)

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.emotion, "")
        self.assertIsNone(self.agent.emotion_expires_at)
        self.assertEqual(self.agent.get_active_emotion_state(), (None, None))
        self.assertIn("emotion", result.updated_fields)
        self.assertFalse(result.errors)

    def test_reapplying_same_emotion_and_timeout_restarts_expiry(self):
        initial_now = timezone.now()
        original_expiry = initial_now + timedelta(hours=1)
        self.agent.emotion = "🙂"
        self.agent.emotion_expires_at = original_expiry
        self.agent.save(update_fields=["emotion", "emotion_expires_at"])

        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    f'''UPDATE "{AGENT_CONFIG_TABLE}"
                        SET emotion = ?, emotion_timeout_seconds = ?
                        WHERE id = 1;''',
                    (snapshot.emotion, snapshot.emotion_timeout_seconds),
                )
                conn.commit()
            finally:
                conn.close()
            reset_at = initial_now + timedelta(minutes=1)
            with patch("api.agent.emotions.timezone.now", return_value=reset_at):
                result = apply_sqlite_agent_config_updates(self.agent, snapshot)

        self.agent.refresh_from_db()
        self.assertEqual(
            self.agent.emotion_expires_at,
            reset_at + timedelta(seconds=snapshot.emotion_timeout_seconds),
        )
        self.assertGreater(self.agent.emotion_expires_at, original_expiry)
        self.assertIn("emotion", result.updated_fields)
        self.assertFalse(result.errors)

    def test_partial_replace_cannot_wipe_durable_config(self):
        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "is update-only; use UPDATE"):
                    conn.execute(f'DELETE FROM "{AGENT_CONFIG_TABLE}" WHERE id = 1;')
                conn.rollback()
                with self.assertRaisesRegex(sqlite3.IntegrityError, "is update-only; use UPDATE"):
                    conn.execute(
                        f'''REPLACE INTO "{AGENT_CONFIG_TABLE}"
                            (id, emotion, emotion_timeout_seconds)
                            VALUES (1, '🙂', 3600);'''
                    )
                conn.rollback()
            finally:
                conn.close()
            result = apply_sqlite_agent_config_updates(self.agent, snapshot)

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.charter, "Original charter")
        self.assertEqual(self.agent.schedule, "0 9 * * *")
        self.assertEqual(self.agent.get_active_emotion_state(), (None, None))
        self.assertFalse(result.updated_fields)
        self.assertFalse(result.errors)

    def test_emotion_control_rejects_invalid_timeout_and_non_emoji(self):
        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        f'''UPDATE "{AGENT_CONFIG_TABLE}"
                            SET emotion = '🙂', emotion_timeout_seconds = 86401
                            WHERE id = 1;'''
                    )
                conn.rollback()
                conn.execute(
                    f'''UPDATE "{AGENT_CONFIG_TABLE}"
                        SET emotion = 'happy', emotion_timeout_seconds = 3600
                        WHERE id = 1;'''
                )
                conn.commit()
            finally:
                conn.close()
            result = apply_sqlite_agent_config_updates(self.agent, snapshot)

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.get_active_emotion_state(), (None, None))
        self.assertFalse(result.updated_fields)
        self.assertIn("exactly one emoji", result.errors["emotion"])

    def test_emotion_validation_accepts_flags_and_keycaps_but_not_multiple_emoji(self):
        for emoji in ("🇺🇸", "1️⃣", "👨‍👩‍👧‍👦", "🏽"):
            normalized, expires_at = normalize_emotion_update(emoji, 60)
            self.assertEqual(normalized, emoji)
            self.assertGreater(expires_at, timezone.now())

        with self.assertRaisesMessage(ValidationError, "exactly one emoji"):
            normalize_emotion_update("🙂🚀", 60)

    def test_expired_emotion_is_suppressed_when_seeding_sqlite(self):
        self.agent.emotion = "😴"
        self.agent.emotion_expires_at = timezone.now() - timedelta(seconds=1)
        self.agent.save(update_fields=["emotion", "emotion_expires_at"])

        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    f'''SELECT emotion, emotion_timeout_seconds
                        FROM "{AGENT_CONFIG_TABLE}" WHERE id = 1;'''
                ).fetchone()
            finally:
                conn.close()
            result = apply_sqlite_agent_config_updates(self.agent, snapshot)

        self.assertEqual(row, (None, None))
        self.assertIsNone(snapshot.emotion)
        self.assertEqual(self.agent.get_active_emotion_state(), (None, None))
        self.assertFalse(result.updated_fields)
        self.assertFalse(result.errors)

    def test_emotion_assignment_detection_covers_update_and_insert(self):
        self.assertTrue(
            sqlite_statement_assigns_agent_config_field(
                "UPDATE __agent_config SET emotion='🙂', emotion_timeout_seconds=60 WHERE id=1",
                "emotion",
            )
        )
        self.assertTrue(
            sqlite_statement_assigns_agent_config_field(
                "INSERT INTO __agent_config (id, emotion, emotion_timeout_seconds) VALUES (1, '🙂', 60)",
                "emotion_timeout_seconds",
            )
        )
        self.assertFalse(
            sqlite_statement_assigns_agent_config_field(
                "UPDATE notes SET body='emotion_timeout_seconds=60'",
                "emotion_timeout_seconds",
            )
        )
