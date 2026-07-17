import json
import os
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.tools.sqlite_agent_config import (
    AgentConfigApplyResult,
    apply_sqlite_agent_config_updates,
    seed_sqlite_agent_config,
)
from api.agent.core.event_processing import _persist_agent_config_update_results
from api.agent.tools.sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from api.agent.tools.sqlite_state import AGENT_CONFIG_TABLE, reset_sqlite_db_path, set_sqlite_db_path
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentStep,
    PersistentAgentToolCall,
)


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
        self.assertEqual(result.attempted_fields, ("charter", "schedule"))
        self.assertIn("charter", result.updated_fields)
        self.assertIn("schedule", result.updated_fields)
        self.assertFalse(result.unchanged_fields)
        self.assertNotEqual(result.charter_hash_before, result.charter_hash_after)

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
                    attempted_fields=("charter",),
                )
            finally:
                reset_sqlite_db_path(token)

        self.assertEqual(result.attempted_fields, ("charter",))
        self.assertFalse(result.updated_fields)
        self.assertEqual(result.unchanged_fields, ("charter",))
        self.assertEqual(result.charter_hash_before, result.charter_hash_after)

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
                        attempted_fields=("charter",),
                    )
            finally:
                reset_sqlite_db_path(token)

        update_charter.assert_not_called()
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.charter, "Original charter")
        self.assertFalse(result.updated_fields)
        self.assertEqual(result.unchanged_fields, ("charter",))
        self.assertEqual(result.charter_hash_before, result.charter_hash_after)

    def test_reconciled_config_result_is_persisted_for_next_prompt(self):
        step = PersistentAgentStep.objects.create(agent=self.agent)
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="sqlite_batch",
            tool_params={"sql": "UPDATE __agent_config SET charter = charter WHERE id = 1"},
            result=json.dumps({"status": "ok", "results": [{"message": "Query 0 affected 1 rows."}]}),
        )
        outcome = SimpleNamespace(
            prepared=SimpleNamespace(
                tool_name="sqlite_batch",
                exec_params={"sql": "UPDATE __agent_config SET charter = charter WHERE id = 1"},
                pending_step=step,
            ),
            persisted_step=step,
            result={"status": "ok", "results": [{"message": "Query 0 affected 1 rows."}]},
        )
        config_apply = AgentConfigApplyResult(
            attempted_fields=("charter",),
            updated_fields=(),
            unchanged_fields=("charter",),
            errors=(),
            charter_hash_before="before-hash",
            charter_hash_after="before-hash",
        )

        _persist_agent_config_update_results([outcome], config_apply)

        persisted = json.loads(PersistentAgentToolCall.objects.get(step=step).result)
        self.assertEqual(persisted["status"], "ok")
        self.assertEqual(
            persisted["results"],
            [{"message": "Query 0 affected 1 rows."}],
        )
        self.assertEqual(
            persisted["agent_config_update"],
            {
                "attempted_fields": ["charter"],
                "updated_fields": [],
                "unchanged_fields": ["charter"],
                "charter_hash_before": "before-hash",
                "charter_hash_after": "before-hash",
                "errors": [],
            },
        )
