from django.test import TestCase, tag
from django.contrib.auth import get_user_model
from django.utils import timezone

from api.models import PersistentAgent, BrowserUseAgent, PersistentAgentEnabledTool
from api.agent.tools.database_enabler import execute_enable_database
from api.agent.tools.tool_manager import SQLITE_TOOL_NAME


@tag("enable_database")
class EnableDatabaseToolTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="enable-database@example.com",
            email="enable-database@example.com",
            password="secret",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="BrowserAgent")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="EnableDatabaseAgent",
            charter="test enable database",
            browser_use_agent=cls.browser_agent,
            created_at=timezone.now(),
        )

    def test_enable_database_creates_enabled_row(self):
        result = execute_enable_database(self.agent, {})

        self.assertEqual(result["status"], "ok")
        self.assertIn(SQLITE_TOOL_NAME, result["tool_manager"]["enabled"])
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent,
                tool_full_name=SQLITE_TOOL_NAME,
            ).exists()
        )

    def test_enable_database_is_idempotent(self):
        execute_enable_database(self.agent, {})
        result = execute_enable_database(self.agent, {})

        self.assertEqual(result["status"], "ok")
        self.assertIn(
            SQLITE_TOOL_NAME,
            result["tool_manager"]["already_enabled"],
        )
