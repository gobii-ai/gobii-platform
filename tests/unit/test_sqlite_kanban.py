import os
import sqlite3
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.tools.sqlite_kanban import apply_sqlite_kanban_updates, seed_sqlite_kanban
from api.agent.tools.sqlite_state import KANBAN_CARDS_TABLE, reset_sqlite_db_path, set_sqlite_db_path
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentKanbanCard


@tag("batch_sqlite")
class SqliteKanbanTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="sqlite-kanban@example.com",
            email="sqlite-kanban@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="SQLite Kanban Browser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="SQLite Kanban Agent",
            charter="Kanban charter",
            browser_use_agent=self.browser_agent,
        )

    def test_sqlite_kanban_applies_updates_and_drops_table(self):
        card = PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Existing task",
            description="Original description",
            status=PersistentAgentKanbanCard.Status.TODO,
            priority=1,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "state.db")
            token = set_sqlite_db_path(db_path)
            try:
                snapshot = seed_sqlite_kanban(self.agent)
                conn = sqlite3.connect(db_path)
                try:
                    conn.execute(
                        f"""
                        UPDATE "{KANBAN_CARDS_TABLE}"
                        SET status = ?, title = ?, priority = ?
                        WHERE id = ?;
                        """,
                        (
                            PersistentAgentKanbanCard.Status.DONE,
                            "Updated task",
                            5,
                            str(card.id),
                        ),
                    )
                    conn.execute(
                        f"""
                        INSERT INTO "{KANBAN_CARDS_TABLE}" (title, description, status, priority)
                        VALUES (?, ?, ?, ?);
                        """,
                        ("New task", "New description", "todo", 2),
                    )
                    conn.commit()
                finally:
                    conn.close()

                result = apply_sqlite_kanban_updates(self.agent, snapshot)
                conn = sqlite3.connect(db_path)
                try:
                    cur = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?;",
                        (KANBAN_CARDS_TABLE,),
                    )
                    self.assertIsNone(cur.fetchone())
                finally:
                    conn.close()
            finally:
                reset_sqlite_db_path(token)

        card.refresh_from_db()
        self.assertEqual(card.title, "Updated task")
        self.assertEqual(card.priority, 5)
        self.assertEqual(card.status, PersistentAgentKanbanCard.Status.DONE)
        self.assertIsNotNone(card.completed_at)
        self.assertFalse(result.errors)
        self.assertEqual(len(result.created_ids), 1)
        self.assertTrue(
            PersistentAgentKanbanCard.objects.filter(
                assigned_agent=self.agent,
                title="New task",
            ).exists()
        )

    def test_sqlite_kanban_rejects_unowned_creates(self):
        other_browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="SQLite Kanban Browser 2",
        )
        other_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Other Agent",
            charter="Other charter",
            browser_use_agent=other_browser_agent,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "state.db")
            token = set_sqlite_db_path(db_path)
            try:
                snapshot = seed_sqlite_kanban(self.agent)
                conn = sqlite3.connect(db_path)
                try:
                    conn.execute(
                        f"""
                        INSERT INTO "{KANBAN_CARDS_TABLE}" (title, assigned_agent_id)
                        VALUES (?, ?);
                        """,
                        ("Other agent task", str(other_agent.id)),
                    )
                    conn.commit()
                finally:
                    conn.close()

                result = apply_sqlite_kanban_updates(self.agent, snapshot)
            finally:
                reset_sqlite_db_path(token)

        self.assertFalse(
            PersistentAgentKanbanCard.objects.filter(title="Other agent task").exists()
        )
        self.assertTrue(result.errors)
