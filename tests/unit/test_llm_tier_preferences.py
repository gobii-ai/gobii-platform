from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag, override_settings
from django.utils import timezone
from django.core.cache import cache

from api.agent.core.llm_config import (
    AgentLLMTier,
    apply_tier_credit_multiplier,
    get_agent_llm_tier,
    get_system_default_tier,
    resolve_preferred_tier_for_owner,
)
from api.models import (
    BrowserUseAgent,
    BrowserUseAgentTask,
    PersistentAgent,
    TaskCredit,
    TaskCreditConfig,
)
from constants.plans import PlanNames
from tests.utils.llm_seed import get_intelligence_tier
from util.tool_costs import clear_tool_credit_cost_cache


User = get_user_model()


@tag("batch_llm_intelligence")
@override_settings(GOBII_PROPRIETARY_MODE=True)
class AgentTierPreferenceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="tier-tests@example.com",
            email="tier-tests@example.com",
            password="test123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Tier-BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Tier Tester",
            charter="Validate tier defaults",
            browser_use_agent=self.browser_agent,
            preferred_llm_tier=get_intelligence_tier("standard"),
        )

    def test_first_loop_always_uses_premium(self):
        """Brand new agents should be routed through premium tier on their first loop."""
        tier = get_agent_llm_tier(self.agent, is_first_loop=True)
        self.assertEqual(tier, AgentLLMTier.PREMIUM)

    def test_trial_accounts_pay_standard_multiplier(self):
        """Premium trial routing should still charge 1× credits."""
        self.assertEqual(get_agent_llm_tier(self.agent), AgentLLMTier.PREMIUM)
        amount = Decimal("1.000")
        discounted = apply_tier_credit_multiplier(self.agent, amount)
        self.assertEqual(discounted, Decimal("1.000"))


@tag("batch_llm_intelligence")
@override_settings(GOBII_PROPRIETARY_MODE=True)
class SystemDefaultTierTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="default-tier@example.com",
            email="default-tier@example.com",
            password="test123",
        )
        self.standard = get_intelligence_tier("standard")
        self.max_tier = get_intelligence_tier("max")
        self.standard.__class__.objects.update(is_default=False)
        self.max_tier.is_default = True
        self.max_tier.save(update_fields=["is_default"])
        cache.clear()

    def test_system_default_tier_used_when_owner_unknown(self):
        self.assertEqual(get_system_default_tier(force_refresh=True), AgentLLMTier.MAX)
        self.assertEqual(resolve_preferred_tier_for_owner(None, None), AgentLLMTier.MAX)

    def test_system_default_tier_is_clamped_for_free_users(self):
        # Free plan users are limited to STANDARD; preferences/defaults should be clamped.
        self.assertEqual(resolve_preferred_tier_for_owner(self.user, None), AgentLLMTier.STANDARD)
        self.assertEqual(resolve_preferred_tier_for_owner(self.user, "max"), AgentLLMTier.STANDARD)


@tag("batch_llm_intelligence")
class BrowserUseTaskTierMultiplierTests(TestCase):
    def setUp(self):
        clear_tool_credit_cost_cache()
        TaskCreditConfig.objects.update_or_create(
            singleton_id=1,
            defaults={"default_task_cost": Decimal("0.50")},
        )
        self.user = User.objects.create_user(
            username="browser-tier@example.com",
            email="browser-tier@example.com",
            password="secret123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Browser BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Browser Agent",
            charter="Use browser",
            browser_use_agent=self.browser_agent,
            preferred_llm_tier=get_intelligence_tier("premium"),
        )
        self.credit = TaskCredit.objects.create(
            user=self.user,
            credits=Decimal("10.000"),
            credits_used=Decimal("0.000"),
            granted_date=timezone.now(),
            expiration_date=timezone.now() + timedelta(days=30),
            plan=PlanNames.STARTUP,
            voided=False,
        )

    def test_browser_use_task_applies_persistent_agent_multiplier(self):
        captured = {}

        def fake_consume(owner, amount=None):
            captured["amount"] = amount
            return {"success": True, "credit": self.credit, "error_message": None}

        multiplier_value = Decimal("1.250")
        with patch("api.models._apply_tier_multiplier", return_value=multiplier_value) as mock_multiplier, patch(
            "api.models.TaskCreditService.check_and_consume_credit_for_owner",
            side_effect=fake_consume,
        ):
            task = BrowserUseAgentTask.objects.create(user=self.user, agent=self.browser_agent)

        task.refresh_from_db()
        self.assertEqual(captured["amount"], multiplier_value)
        self.assertEqual(task.credits_cost, multiplier_value)
        mock_multiplier.assert_called_once()
