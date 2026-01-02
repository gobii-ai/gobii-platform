from django.test import TestCase, tag, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from api.models import PersistentAgent, BrowserUseAgent, PersistentAgentEnabledTool, UserBilling, UserFlags
from api.agent.core import event_processing as ep
from api.agent.core.llm_config import AgentLLMTier
from api.agent.tools.database_enabler import execute_enable_database
from api.agent.tools.tool_manager import (
    SQLITE_TOOL_NAME,
    is_sqlite_enabled_for_agent,
)
from constants.plans import PlanNames


@tag("enable_database")
@override_settings(GOBII_PROPRIETARY_MODE=True)
class EnableDatabaseToolTests(TestCase):
    """Tests for enable_database tool with always-on sqlite."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="enable-database@example.com",
            email="enable-database@example.com",
            password="secret",
        )
        # Set up paid account
        billing, _ = UserBilling.objects.get_or_create(user=cls.user)
        billing.subscription = PlanNames.STARTUP
        billing.save(update_fields=["subscription"])

        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="BrowserAgent")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="EnableDatabaseAgent",
            charter="test enable database",
            browser_use_agent=cls.browser_agent,
            created_at=timezone.now(),
            preferred_llm_tier=AgentLLMTier.MAX.value,
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

    def test_enable_database_tool_hidden(self):
        """get_agent_tools should not expose enable_database."""
        tools = ep.get_agent_tools(self.agent)
        tool_names = [
            entry.get("function", {}).get("name")
            for entry in tools
            if isinstance(entry, dict)
        ]
        self.assertNotIn("enable_database", tool_names)


@tag("enable_database")
@override_settings(GOBII_PROPRIETARY_MODE=True)
class SqliteToolAvailabilityTests(TestCase):
    """SQLite is always available for all agents."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()

        # Create free user
        cls.free_user = User.objects.create_user(
            username="free-user@example.com",
            email="free-user@example.com",
            password="secret",
        )
        billing, _ = UserBilling.objects.get_or_create(user=cls.free_user)
        billing.subscription = PlanNames.FREE
        billing.save(update_fields=["subscription"])

        # Create paid user
        cls.paid_user = User.objects.create_user(
            username="paid-user@example.com",
            email="paid-user@example.com",
            password="secret",
        )
        billing, _ = UserBilling.objects.get_or_create(user=cls.paid_user)
        billing.subscription = PlanNames.STARTUP
        billing.save(update_fields=["subscription"])

        # Create VIP user (remains on free plan to validate override)
        cls.vip_user = User.objects.create_user(
            username="vip-user@example.com",
            email="vip-user@example.com",
            password="secret",
        )
        UserFlags.objects.create(user=cls.vip_user, is_vip=True)
        billing, _ = UserBilling.objects.get_or_create(user=cls.vip_user)
        billing.subscription = PlanNames.FREE
        billing.save(update_fields=["subscription"])

        # Browser agents
        cls.free_browser = BrowserUseAgent.objects.create(user=cls.free_user, name="FreeBrowser")
        cls.paid_browser = BrowserUseAgent.objects.create(user=cls.paid_user, name="PaidBrowser")
        cls.vip_browser = BrowserUseAgent.objects.create(user=cls.vip_user, name="VipBrowser")

    def _create_agent(self, user, browser, name, tier):
        return PersistentAgent.objects.create(
            user=user,
            name=name,
            charter="test",
            browser_use_agent=browser,
            created_at=timezone.now(),
            preferred_llm_tier=tier,
        )

    def test_sqlite_enabled_for_all_agents(self):
        agents = [
            self._create_agent(self.free_user, self.free_browser, "FreeAgent", AgentLLMTier.STANDARD.value),
            self._create_agent(self.paid_user, self.paid_browser, "PaidAgent", AgentLLMTier.PREMIUM.value),
            self._create_agent(self.vip_user, self.vip_browser, "VipAgent", AgentLLMTier.MAX.value),
        ]
        for agent in agents:
            self.assertTrue(is_sqlite_enabled_for_agent(agent))
        self.assertFalse(is_sqlite_enabled_for_agent(None))

    def test_enable_database_allowed_for_all_accounts(self):
        agent = self._create_agent(
            self.free_user, self.free_browser, "FreeAgentEnable", AgentLLMTier.STANDARD.value
        )
        result = execute_enable_database(agent, {})
        self.assertEqual(result["status"], "ok")
        self.assertIn(SQLITE_TOOL_NAME, result["tool_manager"]["enabled"])

    def test_sqlite_batch_visible_in_agent_tools(self):
        agent = self._create_agent(
            self.paid_user, self.paid_browser, "ToolsVisible", AgentLLMTier.PREMIUM.value
        )
        tools = ep.get_agent_tools(agent)
        tool_names = [
            entry.get("function", {}).get("name")
            for entry in tools
            if isinstance(entry, dict)
        ]
        self.assertIn("sqlite_batch", tool_names)
        self.assertNotIn("enable_database", tool_names)
