from django.test import TestCase, tag
from django.contrib.auth import get_user_model
from django.utils import timezone

from api.models import PersistentAgent, BrowserUseAgent, PersistentAgentEnabledTool, UserBilling, UserFlags
from api.agent.core import event_processing as ep
from api.agent.core.llm_config import AgentLLMTier
from api.agent.tools.database_enabler import execute_enable_database
from api.agent.tools.tool_manager import (
    SQLITE_TOOL_NAME,
    is_sqlite_enabled_for_agent,
    get_enabled_tool_definitions,
    execute_enabled_tool,
)
from constants.plans import PlanNames


@tag("enable_database")
class EnableDatabaseToolTests(TestCase):
    """Tests for enable_database tool with eligible (paid + max intelligence) agents."""

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
            preferred_llm_tier=AgentLLMTier.MAX.value,  # Max intelligence required
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

    def test_enable_database_tool_removed_once_sqlite_enabled(self):
        """get_agent_tools should hide enable_database after sqlite_batch is enabled."""

        tools_before = ep.get_agent_tools(self.agent)
        tool_names_before = [
            entry.get("function", {}).get("name")
            for entry in tools_before
            if isinstance(entry, dict)
        ]
        self.assertIn("enable_database", tool_names_before)

        execute_enable_database(self.agent, {})

        tools_after = ep.get_agent_tools(self.agent)
        tool_names_after = [
            entry.get("function", {}).get("name")
            for entry in tools_after
            if isinstance(entry, dict)
        ]
        self.assertNotIn("enable_database", tool_names_after)


@tag("enable_database")
class SqliteToolRestrictionTests(TestCase):
    """Tests for sqlite tool restrictions based on account type and intelligence tier."""

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

    # --- is_sqlite_enabled_for_agent tests ---

    def test_free_account_not_eligible(self):
        """Free accounts should never be eligible for sqlite."""
        agent = self._create_agent(
            self.free_user, self.free_browser, "FreeAgent", AgentLLMTier.MAX.value
        )
        self.assertFalse(is_sqlite_enabled_for_agent(agent))

    def test_paid_standard_tier_not_eligible(self):
        """Paid accounts with standard intelligence should not be eligible."""
        agent = self._create_agent(
            self.paid_user, self.paid_browser, "PaidStandard", AgentLLMTier.STANDARD.value
        )
        self.assertFalse(is_sqlite_enabled_for_agent(agent))

    def test_paid_premium_tier_not_eligible(self):
        """Paid accounts with premium intelligence should not be eligible."""
        agent = self._create_agent(
            self.paid_user, self.paid_browser, "PaidPremium", AgentLLMTier.PREMIUM.value
        )
        self.assertFalse(is_sqlite_enabled_for_agent(agent))

    def test_paid_max_tier_is_eligible(self):
        """Paid accounts with max intelligence should be eligible."""
        agent = self._create_agent(
            self.paid_user, self.paid_browser, "PaidMax", AgentLLMTier.MAX.value
        )
        self.assertTrue(is_sqlite_enabled_for_agent(agent))

    def test_vip_user_is_always_eligible(self):
        """VIP users are eligible regardless of plan/tier."""
        agent = self._create_agent(
            self.vip_user, self.vip_browser, "VipStandard", AgentLLMTier.STANDARD.value
        )
        self.assertTrue(is_sqlite_enabled_for_agent(agent))

    def test_none_agent_not_eligible(self):
        """None agent should not be eligible."""
        self.assertFalse(is_sqlite_enabled_for_agent(None))

    # --- enable_database execution tests ---

    def test_enable_database_rejected_for_free_account(self):
        """enable_database should reject free accounts."""
        agent = self._create_agent(
            self.free_user, self.free_browser, "FreeAgentReject", AgentLLMTier.MAX.value
        )
        result = execute_enable_database(agent, {})

        self.assertEqual(result["status"], "error")
        self.assertIn("not available", result["message"])
        self.assertFalse(
            PersistentAgentEnabledTool.objects.filter(
                agent=agent, tool_full_name=SQLITE_TOOL_NAME
            ).exists()
        )

    def test_enable_database_rejected_for_paid_non_max(self):
        """enable_database should reject paid accounts without max intelligence."""
        agent = self._create_agent(
            self.paid_user, self.paid_browser, "PaidPremiumReject", AgentLLMTier.PREMIUM.value
        )
        result = execute_enable_database(agent, {})

        self.assertEqual(result["status"], "error")
        self.assertIn("not available", result["message"])

    def test_enable_database_allowed_for_vip_on_free_standard(self):
        """enable_database should allow VIP users even on free + standard tier."""
        agent = self._create_agent(
            self.vip_user, self.vip_browser, "VipFreeStandard", AgentLLMTier.STANDARD.value
        )
        result = execute_enable_database(agent, {})

        self.assertEqual(result["status"], "ok")
        self.assertIn(SQLITE_TOOL_NAME, result["tool_manager"]["enabled"])

    # --- get_agent_tools visibility tests ---

    def test_enable_database_hidden_for_ineligible_agents(self):
        """enable_database tool should not appear for ineligible agents."""
        agent = self._create_agent(
            self.free_user, self.free_browser, "FreeToolsHidden", AgentLLMTier.MAX.value
        )
        tools = ep.get_agent_tools(agent)
        tool_names = [
            entry.get("function", {}).get("name")
            for entry in tools
            if isinstance(entry, dict)
        ]
        self.assertNotIn("enable_database", tool_names)

    def test_enable_database_visible_for_eligible_agents(self):
        """enable_database tool should appear for eligible agents."""
        agent = self._create_agent(
            self.paid_user, self.paid_browser, "PaidToolsVisible", AgentLLMTier.MAX.value
        )
        tools = ep.get_agent_tools(agent)
        tool_names = [
            entry.get("function", {}).get("name")
            for entry in tools
            if isinstance(entry, dict)
        ]
        self.assertIn("enable_database", tool_names)

    # --- Previously enabled sqlite should be hidden ---

    def test_previously_enabled_sqlite_hidden_for_ineligible(self):
        """sqlite_batch should be hidden even if previously enabled when agent becomes ineligible."""
        # Create eligible agent and enable sqlite
        agent = self._create_agent(
            self.paid_user, self.paid_browser, "PreviouslyEnabled", AgentLLMTier.MAX.value
        )
        execute_enable_database(agent, {})

        # Verify sqlite is enabled in DB
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=agent, tool_full_name=SQLITE_TOOL_NAME
            ).exists()
        )

        # Downgrade agent to premium tier
        agent.preferred_llm_tier = AgentLLMTier.PREMIUM.value
        agent.save(update_fields=["preferred_llm_tier"])

        # sqlite_batch should not appear in tool definitions
        definitions = get_enabled_tool_definitions(agent)
        tool_names = [
            d.get("function", {}).get("name")
            for d in definitions
            if isinstance(d, dict)
        ]
        self.assertNotIn("sqlite_batch", tool_names)

    def test_previously_enabled_sqlite_execution_blocked(self):
        """sqlite_batch execution should be blocked for ineligible agents even if previously enabled."""
        # Create eligible agent and enable sqlite
        agent = self._create_agent(
            self.paid_user, self.paid_browser, "ExecutionBlocked", AgentLLMTier.MAX.value
        )
        execute_enable_database(agent, {})

        # Downgrade agent to premium tier
        agent.preferred_llm_tier = AgentLLMTier.PREMIUM.value
        agent.save(update_fields=["preferred_llm_tier"])

        # Execution should be blocked
        result = execute_enabled_tool(agent, SQLITE_TOOL_NAME, {"queries": []})
        self.assertEqual(result["status"], "error")
        self.assertIn("not available", result["message"])
