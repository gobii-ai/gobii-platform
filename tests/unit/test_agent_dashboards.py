import sqlite3
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import TestCase, tag
from django.urls import reverse
from waffle.testutils import override_flag

from api.agent.tools.dashboards import DASHBOARD_TOOL_NAME, execute_create_or_update_dashboard
from api.agent.tools.tool_manager import get_available_builtin_tool_entries
from api.agent.tools.sqlite_state import agent_sqlite_db, sqlite_storage_key
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentDashboard,
    PersistentAgentDashboardWidget,
)
from api.services.agent_dashboards import create_or_update_dashboard
from constants.feature_flags import AGENT_DASHBOARDS


@tag("batch_sqlite")
class AgentDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="agent-dashboard@example.com",
            email="agent-dashboard@example.com",
            password="secret",
        )
        cls.other_user = User.objects.create_user(
            username="agent-dashboard-other@example.com",
            email="agent-dashboard-other@example.com",
            password="secret",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Dashboard Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Dashboard Agent",
            charter="Track stores",
            browser_use_agent=cls.browser_agent,
        )

    def tearDown(self):
        storage_key = sqlite_storage_key(str(self.agent.id))
        if default_storage.exists(storage_key):
            default_storage.delete(storage_key)
        PersistentAgentDashboard.objects.filter(agent=self.agent).delete()

    @staticmethod
    def _seed_store_table(db_path: str) -> None:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stores (
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    rating REAL NOT NULL
                )
                """
            )
            conn.execute("DELETE FROM stores")
            conn.executemany(
                "INSERT INTO stores(name, category, rating) VALUES (?, ?, ?)",
                [
                    ("North Market", "grocery", 4.7),
                    ("Paper Place", "office", 4.2),
                    ("South Market", "grocery", 4.5),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def test_dashboard_tool_creates_validated_widgets(self):
        with agent_sqlite_db(str(self.agent.id)) as db_path:
            self._seed_store_table(db_path)

            result = execute_create_or_update_dashboard(
                self.agent,
                {
                    "title": "Local Store Research",
                    "description": "Tracks nearby stores.",
                    "widgets": [
                        {
                            "type": "metric",
                            "title": "Stores Found",
                            "sql": "select count(*) as value from stores",
                        },
                        {
                            "type": "bar",
                            "title": "Stores By Category",
                            "sql": (
                                "select category, count(*) as stores "
                                "from stores group by category order by stores desc"
                            ),
                            "display_config": {"x": "category", "y": "stores"},
                        },
                        {
                            "type": "table",
                            "title": "Top Stores",
                            "sql": "select name, category, rating from stores order by rating desc",
                        },
                    ],
                },
            )

        self.assertEqual(result["status"], "ok")
        self.assertIn(f"/console/agents/{self.agent.id}/dashboards/", result["dashboard_url"])
        dashboard = PersistentAgentDashboard.objects.get(agent=self.agent)
        self.assertEqual(dashboard.title, "Local Store Research")
        self.assertEqual(dashboard.widgets.count(), 3)
        self.assertEqual(
            list(dashboard.widgets.order_by("position").values_list("widget_type", flat=True)),
            [
                PersistentAgentDashboardWidget.WidgetType.METRIC,
                PersistentAgentDashboardWidget.WidgetType.BAR,
                PersistentAgentDashboardWidget.WidgetType.TABLE,
            ],
        )

    def test_dashboard_tool_rejects_mutating_sql_and_internal_tables(self):
        with agent_sqlite_db(str(self.agent.id)) as db_path:
            self._seed_store_table(db_path)

            mutation_result = execute_create_or_update_dashboard(
                self.agent,
                {
                    "title": "Bad Dashboard",
                    "widgets": [
                        {
                            "type": "table",
                            "title": "Mutation",
                            "sql": "delete from stores returning name",
                        }
                    ],
                },
            )
            internal_result = execute_create_or_update_dashboard(
                self.agent,
                {
                    "title": "Bad Dashboard",
                    "widgets": [
                        {
                            "type": "table",
                            "title": "Internal",
                            "sql": 'select * from "__tool_results"',
                        }
                    ],
                },
            )

        self.assertEqual(mutation_result["status"], "error")
        self.assertIn("SELECT or WITH", mutation_result["message"])
        self.assertEqual(internal_result["status"], "error")
        self.assertIn("internal or ephemeral", internal_result["message"])
        self.assertFalse(PersistentAgentDashboard.objects.filter(agent=self.agent).exists())

    def test_dashboard_api_renders_from_persisted_sqlite_snapshot(self):
        with agent_sqlite_db(str(self.agent.id)) as db_path:
            self._seed_store_table(db_path)
            create_or_update_dashboard(
                self.agent,
                title="Local Store Research",
                description="Tracks nearby stores.",
                widgets=[
                    {
                        "type": "metric",
                        "title": "Stores Found",
                        "sql": "select count(*) as value from stores",
                    },
                    {
                        "type": "table",
                        "title": "Top Stores",
                        "sql": "select name, rating from stores order by rating desc",
                    },
                ],
                db_path=db_path,
            )

        self.client.force_login(self.user)
        with override_flag(AGENT_DASHBOARDS, active=True):
            response = self.client.get(reverse("console_agent_dashboards_api", kwargs={"agent_id": self.agent.id}))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["dashboard"]["title"], "Local Store Research")
        widgets = payload["dashboard"]["widgets"]
        self.assertEqual(widgets[0]["result"]["status"], "ok")
        self.assertEqual(widgets[0]["result"]["value"], 3)
        self.assertEqual(widgets[1]["result"]["rows"][0]["name"], "North Market")

    def test_dashboard_api_uses_agent_access_rules(self):
        self.client.force_login(self.other_user)

        with override_flag(AGENT_DASHBOARDS, active=True):
            response = self.client.get(reverse("console_agent_dashboards_api", kwargs={"agent_id": self.agent.id}))

        self.assertEqual(response.status_code, 403)

    def test_dashboard_api_requires_feature_flag(self):
        self.client.force_login(self.user)

        with override_flag(AGENT_DASHBOARDS, active=False):
            response = self.client.get(reverse("console_agent_dashboards_api", kwargs={"agent_id": self.agent.id}))

        self.assertEqual(response.status_code, 404)

    def test_dashboard_page_requires_feature_flag(self):
        self.client.force_login(self.user)

        with override_flag(AGENT_DASHBOARDS, active=False):
            response = self.client.get(reverse("agent_dashboards", kwargs={"pk": self.agent.id}))

        self.assertEqual(response.status_code, 404)

    def test_dashboard_builtin_tool_requires_feature_flag(self):
        with patch("api.agent.tools.tool_manager.sandbox_compute_enabled_for_agent", return_value=False):
            with override_flag(AGENT_DASHBOARDS, active=False):
                disabled_entries = get_available_builtin_tool_entries(self.agent)
            with override_flag(AGENT_DASHBOARDS, active=True):
                enabled_entries = get_available_builtin_tool_entries(self.agent)

        self.assertNotIn(DASHBOARD_TOOL_NAME, disabled_entries)
        self.assertIn(DASHBOARD_TOOL_NAME, enabled_entries)
