import os
import sqlite3
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.tools.create_csv import execute_create_csv
from api.agent.tools.sqlite_state import set_sqlite_db_path, reset_sqlite_db_path
from api.models import AgentFsNode, BrowserUseAgent, PersistentAgent


@tag("batch_sqlite")
class CreateCsvQueryExportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="sqlite-export@example.com",
            email="sqlite-export@example.com",
            password="secret",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="BA")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="SQLiteExportAgent",
            charter="test sqlite export",
            browser_use_agent=cls.browser_agent,
            created_at=timezone.now(),
        )

    def _with_temp_db(self):
        """Context manager to set/reset the sqlite DB path."""
        tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(tmp.name, "state.db")
        token_state = set_sqlite_db_path(db_path)

        class _Cxt:
            def __enter__(self_inner):
                return (db_path, token_state, tmp)

            def __exit__(self_inner, exc_type, exc, tb):
                try:
                    reset_sqlite_db_path(token_state)
                finally:
                    tmp.cleanup()

        return _Cxt()

    def test_exports_select_results_to_csv(self):
        with self._with_temp_db() as (db_path, token, tmp):
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE t(a INTEGER, b TEXT);")
                conn.execute("INSERT INTO t(a, b) VALUES (1, 'x'), (2, 'y');")
                conn.commit()
            finally:
                conn.close()

            result = execute_create_csv(
                self.agent,
                {
                    "query": "SELECT a, b FROM t ORDER BY a",
                    "file_path": "/exports/report.csv",
                },
            )

            self.assertEqual(result.get("status"), "ok")
            self.assertEqual(result.get("file"), "$[/exports/report.csv]")
            node = AgentFsNode.objects.get(created_by_agent=self.agent, path="/exports/report.csv")
            with node.content.open("rb") as handle:
                csv_bytes = handle.read()
            self.assertEqual(csv_bytes, b"a,b\r\n1,x\r\n2,y\r\n")

    def test_rejects_non_select(self):
        with self._with_temp_db():
            result = execute_create_csv(
                self.agent,
                {"query": "UPDATE foo SET a=1", "file_path": "/exports/report.csv"},
            )
            self.assertEqual(result.get("status"), "error")
            self.assertIn("SELECT", result.get("message", "").upper())

