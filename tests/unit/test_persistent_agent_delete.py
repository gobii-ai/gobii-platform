from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, tag

from api.models import BrowserUseAgent, PersistentAgent


User = get_user_model()


@tag('batch_api_persistent_agents')
class PersistentAgentDeleteTests(TestCase):
    def test_delete_tolerates_missing_browser_use_agent(self):
        user = User.objects.create_user(
            username="orphan-owner",
            email="orphan-owner@example.com",
            password="pw",
        )

        browser_agent = BrowserUseAgent.objects.create(user=user, name="Orphan Browser")
        agent = PersistentAgent.objects.create(
            user=user,
            name="Orphan Persistent Agent",
            charter="Help despite missing browser agent",
            browser_use_agent=browser_agent,
        )

        # Simulate historical data corruption where the BrowserUseAgent row vanished while
        # the PersistentAgent row remained.
        with connection.cursor() as cursor:
            if connection.vendor == "postgresql":
                cursor.execute("SET session_replication_role = replica;")
                delete_sql = "DELETE FROM api_browseruseagent WHERE id = %s"
                try:
                    cursor.execute(delete_sql, [str(browser_agent.id)])
                finally:
                    cursor.execute("SET session_replication_role = DEFAULT;")
            else:
                cursor.execute("PRAGMA foreign_keys = OFF;")
                placeholder = "?"
                delete_sql = f"DELETE FROM api_browseruseagent WHERE id = {placeholder}"
                try:
                    cursor.execute(delete_sql, [str(browser_agent.id)])
                finally:
                    cursor.execute("PRAGMA foreign_keys = ON;")

        agent.refresh_from_db()
        self.assertTrue(PersistentAgent.objects.filter(pk=agent.pk).exists())

        # Should not raise when the browser agent is already gone.
        agent.delete()

        self.assertFalse(PersistentAgent.objects.filter(pk=agent.pk).exists())
