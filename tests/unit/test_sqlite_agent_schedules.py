import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from api.agent.tools.sqlite_agent_config import (
    apply_sqlite_agent_config_updates,
    seed_sqlite_agent_config,
    sqlite_statement_mutates_agent_schedules,
)
from api.agent.tools.sqlite_state import (
    AGENT_CONFIG_TABLE,
    AGENT_SCHEDULES_TABLE,
    reset_sqlite_db_path,
    set_sqlite_db_path,
)
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentSchedule


@tag("batch_sqlite")
class SqliteAgentSchedulesTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="sqlite-schedules@example.com",
            email="sqlite-schedules@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="SQLite Schedules Browser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="SQLite Schedules Agent",
            charter="Watch operations",
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

    def _create_recurring(self, schedule_key, expression="0 14 * * 1-5"):
        return PersistentAgentSchedule.objects.create(
            agent=self.agent,
            schedule_key=schedule_key,
            name=f"{schedule_key} cadence",
            instruction=f"Handle {schedule_key} work",
            kind=PersistentAgentSchedule.Kind.RECURRING,
            expression=expression,
            timezone="America/New_York",
            enabled=True,
            next_run_at=timezone.now() + timedelta(days=1),
        )

    def test_seed_exposes_primary_and_additional_schedules_with_derived_state(self):
        recurring = self._create_recurring("weekday_review")
        once_at = (timezone.now() + timedelta(hours=2)).replace(microsecond=0)
        PersistentAgentSchedule.objects.create(
            agent=self.agent,
            schedule_key="launch_timer",
            name="Launch timer",
            instruction="Check the launch and report anomalies",
            kind=PersistentAgentSchedule.Kind.ONCE,
            run_at=once_at,
            timezone="UTC",
            enabled=True,
            next_run_at=once_at,
        )

        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    f"""
                    SELECT schedule_key, kind, schedule, run_at, enabled, next_run_at
                    FROM "{AGENT_SCHEDULES_TABLE}"
                    ORDER BY schedule_key;
                    """
                ).fetchall()
            finally:
                conn.close()
            result = apply_sqlite_agent_config_updates(self.agent, snapshot)

        self.assertEqual([row[0] for row in rows], ["launch_timer", "primary", "weekday_review"])
        self.assertEqual(rows[1], ("primary", "recurring", "0 9 * * *", None, 1, None))
        self.assertEqual(rows[2][2], recurring.expression)
        self.assertEqual(rows[0][3], once_at.isoformat())
        self.assertEqual(rows[0][5], once_at.isoformat())
        self.assertFalse(result.errors)
        self.assertEqual(len(result.schedules), 3)

    @override_settings(PERSISTENT_AGENT_SCHEDULE_MIN_ONCE_LEAD_SECONDS=0)
    def test_insert_adds_independent_recurring_cadence_and_exact_timer(self):
        run_at = (timezone.now() + timedelta(minutes=20)).replace(microsecond=0)
        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                conn.executemany(
                    f"""
                    INSERT INTO "{AGENT_SCHEDULES_TABLE}" (
                        schedule_key, name, kind, schedule, timezone, run_at,
                        instruction, enabled
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    [
                        (
                            "monday_pipeline",
                            "Monday pipeline",
                            "recurring",
                            "0 10 * * 1",
                            "America/New_York",
                            None,
                            "Prepare the pipeline review",
                            1,
                        ),
                        (
                            "webinar_start",
                            "Webinar start",
                            "once",
                            None,
                            "UTC",
                            run_at.isoformat(),
                            "Check attendance at the known start second",
                            1,
                        ),
                    ],
                )
                conn.commit()
            finally:
                conn.close()
            result = apply_sqlite_agent_config_updates(self.agent, snapshot)

        schedules = {
            row.schedule_key: row
            for row in PersistentAgentSchedule.objects.filter(agent=self.agent)
        }
        self.assertEqual(set(schedules), {"monday_pipeline", "webinar_start"})
        self.assertEqual(schedules["monday_pipeline"].expression, "0 10 * * 1")
        self.assertEqual(schedules["webinar_start"].run_at, run_at)
        self.assertEqual(self.agent.schedule, "0 9 * * *")
        self.assertFalse(result.errors)
        self.assertIn("schedules", result.updated_fields)

    def test_update_changes_only_targeted_schedule(self):
        target = self._create_recurring("pipeline", "0 14 * * 1")
        neighbor = self._create_recurring("customer_health", "0 15 * * 3")

        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    f"""
                    UPDATE "{AGENT_SCHEDULES_TABLE}"
                    SET schedule = ?, instruction = ?
                    WHERE schedule_key = ?;
                    """,
                    ("30 14 * * 1", "Prepare the revised pipeline review", "pipeline"),
                )
                conn.commit()
            finally:
                conn.close()
            result = apply_sqlite_agent_config_updates(self.agent, snapshot)

        target.refresh_from_db()
        neighbor.refresh_from_db()
        self.assertEqual(target.expression, "30 14 * * 1")
        self.assertEqual(target.revision, 2)
        self.assertEqual(neighbor.expression, "0 15 * * 3")
        self.assertEqual(neighbor.revision, 1)
        self.assertFalse(result.errors)

    def test_delete_removes_only_targeted_schedule(self):
        removed = self._create_recurring("temporary_check")
        preserved = self._create_recurring("weekly_review", "0 12 * * 5")

        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    f'DELETE FROM "{AGENT_SCHEDULES_TABLE}" WHERE schedule_key = ?;',
                    (removed.schedule_key,),
                )
                conn.commit()
            finally:
                conn.close()
            result = apply_sqlite_agent_config_updates(self.agent, snapshot)

        self.assertFalse(PersistentAgentSchedule.objects.filter(pk=removed.pk).exists())
        self.assertTrue(PersistentAgentSchedule.objects.filter(pk=preserved.pk).exists())
        self.assertFalse(result.errors)

    def test_primary_row_updates_and_deletion_use_legacy_schedule(self):
        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    f"""
                    UPDATE "{AGENT_SCHEDULES_TABLE}"
                    SET schedule = '0 10 * * *', enabled = 1
                    WHERE schedule_key = 'primary';
                    """
                )
                conn.commit()
            finally:
                conn.close()
            updated = apply_sqlite_agent_config_updates(self.agent, snapshot)

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.schedule, "0 10 * * *")
        self.assertIn("schedules", updated.updated_fields)

        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    f'DELETE FROM "{AGENT_SCHEDULES_TABLE}" WHERE schedule_key = \'primary\';'
                )
                conn.commit()
            finally:
                conn.close()
            deleted = apply_sqlite_agent_config_updates(self.agent, snapshot)

        self.agent.refresh_from_db()
        self.assertIsNone(self.agent.schedule)
        self.assertIn("schedules", deleted.updated_fields)

    @override_settings(PERSISTENT_AGENT_SCHEDULE_MIN_ONCE_LEAD_SECONDS=0)
    def test_invalid_timer_rolls_back_primary_and_additional_schedule_changes(self):
        preserved = self._create_recurring("preserved")
        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    f'UPDATE "{AGENT_CONFIG_TABLE}" SET schedule = ? WHERE id = 1;',
                    ("0 8 * * *",),
                )
                conn.execute(
                    f"""
                    INSERT INTO "{AGENT_SCHEDULES_TABLE}" (
                        schedule_key, name, kind, timezone, run_at, instruction
                    ) VALUES (?, ?, 'once', 'UTC', ?, ?);
                    """,
                    ("bad_timer", "Bad timer", "not-an-iso-datetime", "Do the thing"),
                )
                conn.commit()
            finally:
                conn.close()
            result = apply_sqlite_agent_config_updates(self.agent, snapshot)

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.schedule, "0 9 * * *")
        self.assertEqual(
            list(self.agent.additional_schedules.values_list("schedule_key", flat=True)),
            [preserved.schedule_key],
        )
        self.assertIn("ISO-8601", result.errors["schedules"])
        self.assertFalse(result.updated_fields)

    @override_settings(PERSISTENT_AGENT_SCHEDULE_MAX_ACTIVE=2)
    def test_active_schedule_cap_rejects_entire_sqlite_schedule_set(self):
        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                conn.executemany(
                    f"""
                    INSERT INTO "{AGENT_SCHEDULES_TABLE}" (
                        schedule_key, name, kind, schedule, timezone, instruction
                    ) VALUES (?, ?, 'recurring', ?, 'UTC', ?);
                    """,
                    [
                        ("first_extra", "First extra", "0 10 * * *", "First task"),
                        ("second_extra", "Second extra", "0 11 * * *", "Second task"),
                    ],
                )
                conn.commit()
            finally:
                conn.close()
            result = apply_sqlite_agent_config_updates(self.agent, snapshot)

        self.assertFalse(PersistentAgentSchedule.objects.filter(agent=self.agent).exists())
        self.assertIn("at most 2 active schedules", result.errors["schedules"])

    @override_settings(PERSISTENT_AGENT_SCHEDULE_MAX_ACTIVE=2)
    def test_enabling_primary_cannot_bypass_combined_active_cap(self):
        self.agent.schedule = None
        self.agent.save(update_fields=["schedule"])
        first = self._create_recurring("first_extra", "0 10 * * *")
        second = self._create_recurring("second_extra", "0 11 * * *")

        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    f'UPDATE "{AGENT_CONFIG_TABLE}" SET schedule = ? WHERE id = 1;',
                    ("0 9 * * *",),
                )
                conn.commit()
            finally:
                conn.close()
            result = apply_sqlite_agent_config_updates(self.agent, snapshot)

        self.agent.refresh_from_db()
        self.assertIsNone(self.agent.schedule)
        self.assertEqual(
            set(self.agent.additional_schedules.values_list("id", flat=True)),
            {first.id, second.id},
        )
        self.assertIn("at most 2 active schedules", result.errors["schedule"])

    def test_planning_mode_rejects_additional_schedule_mutation(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])

        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    f"""
                    INSERT INTO "{AGENT_SCHEDULES_TABLE}" (
                        schedule_key, name, kind, schedule, timezone, instruction
                    ) VALUES ('planning_cadence', 'Planning cadence', 'recurring',
                              '0 10 * * *', 'UTC', 'Should not persist');
                    """
                )
                conn.commit()
            finally:
                conn.close()
            result = apply_sqlite_agent_config_updates(self.agent, snapshot)

        self.assertFalse(PersistentAgentSchedule.objects.filter(agent=self.agent).exists())
        self.assertIn("planning mode", result.errors["schedules"].lower())

    def test_derived_timing_fields_are_read_only(self):
        schedule = self._create_recurring("protected")
        with self._sqlite_state() as db_path:
            snapshot = seed_sqlite_agent_config(self.agent)
            conn = sqlite3.connect(db_path)
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "read-only"):
                    conn.execute(
                        f"""
                        UPDATE "{AGENT_SCHEDULES_TABLE}"
                        SET next_run_at = NULL
                        WHERE schedule_key = 'protected';
                        """
                    )
                conn.rollback()
            finally:
                conn.close()
            result = apply_sqlite_agent_config_updates(self.agent, snapshot)

        schedule.refresh_from_db()
        self.assertIsNotNone(schedule.next_run_at)
        self.assertFalse(result.errors)

    def test_schedule_mutation_classifier_is_strict(self):
        self.assertTrue(
            sqlite_statement_mutates_agent_schedules(
                "WITH desired AS (SELECT 1) UPDATE __agent_schedules SET enabled=0"
            )
        )
        self.assertTrue(
            sqlite_statement_mutates_agent_schedules(
                'DELETE FROM "__agent_schedules" WHERE schedule_key = \'timer\''
            )
        )
        self.assertTrue(
            sqlite_statement_mutates_agent_schedules(
                "DROP TABLE IF EXISTS __agent_schedules"
            )
        )
        self.assertFalse(
            sqlite_statement_mutates_agent_schedules(
                "SELECT * FROM __agent_schedules WHERE enabled = 1"
            )
        )
        self.assertFalse(
            sqlite_statement_mutates_agent_schedules(
                "UPDATE notes SET body = '__agent_schedules'"
            )
        )
        self.assertFalse(
            sqlite_statement_mutates_agent_schedules(
                "INSERT INTO audit(note) VALUES ('DELETE FROM __agent_schedules')"
            )
        )
