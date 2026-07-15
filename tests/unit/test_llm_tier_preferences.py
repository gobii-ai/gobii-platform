from decimal import Decimal
from datetime import timedelta
import importlib
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib import admin
from django.test import TestCase, tag, override_settings
from django.utils import timezone
from django.core.cache import cache
from django.db import DatabaseError, IntegrityError, transaction

from api.agent.core.llm_config import (
    AgentLLMTier,
    _plan_supports_paid_tiers,
    apply_tier_credit_multiplier,
    clear_runtime_tier_override,
    get_agent_baseline_llm_tier,
    get_agent_llm_tier,
    get_credit_multiplier_for_tier,
    get_next_lower_configured_tier,
    get_system_default_tier,
    get_trial_default_tier,
    resolve_preferred_tier_for_owner,
    set_runtime_tier_override,
)
from api.admin import IntelligenceTierAdmin
from api.models import (
    BrowserUseAgent,
    BrowserUseAgentTask,
    IntelligenceTier,
    PersistentAgent,
    PersistentAgentTemplate,
    TaskCredit,
    TaskCreditConfig,
    UserQuota,
)
from api.services.persistent_agents import PersistentAgentProvisioningService
from api.services.tool_blacklist import get_agent_tool_blacklist, get_tier_tool_blacklist
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

    def test_free_user_first_loop_uses_standard(self):
        """Brand new free-plan agents use normal plan-clamped tier resolution."""
        tier = get_agent_llm_tier(self.agent, is_first_loop=True)
        self.assertEqual(tier, AgentLLMTier.STANDARD)

    def test_free_user_standard_tier_pays_standard_multiplier(self):
        self.assertEqual(get_agent_llm_tier(self.agent), AgentLLMTier.STANDARD)
        amount = Decimal("1.000")
        discounted = apply_tier_credit_multiplier(self.agent, amount)
        self.assertEqual(discounted, Decimal("1.000"))

    def test_user_quota_standard_keeps_free_user_standard(self):
        self.user.quota.max_intelligence_tier = AgentLLMTier.STANDARD.value
        self.user.quota.save(update_fields=["max_intelligence_tier"])
        self.assertEqual(get_agent_llm_tier(self.agent), AgentLLMTier.STANDARD)

    def test_user_quota_standard_keeps_first_loop_standard(self):
        self.user.quota.max_intelligence_tier = AgentLLMTier.STANDARD.value
        self.user.quota.save(update_fields=["max_intelligence_tier"])
        self.assertEqual(get_agent_llm_tier(self.agent, is_first_loop=True), AgentLLMTier.STANDARD)

    def test_next_lower_configured_tier_follows_live_ladder(self):
        self.assertEqual(get_next_lower_configured_tier(AgentLLMTier.ULTRA_MAX), AgentLLMTier.ULTRA)
        self.assertEqual(get_next_lower_configured_tier(AgentLLMTier.ULTRA), AgentLLMTier.MAX)
        self.assertEqual(get_next_lower_configured_tier(AgentLLMTier.MAX), AgentLLMTier.PREMIUM)
        self.assertEqual(get_next_lower_configured_tier(AgentLLMTier.PREMIUM), AgentLLMTier.STANDARD)
        self.assertEqual(get_next_lower_configured_tier(AgentLLMTier.STANDARD), AgentLLMTier.STANDARD)

    def test_tool_blacklist_cache_invalidates_when_tier_changes(self):
        cache.clear()
        tier = get_intelligence_tier("standard")
        tier.blacklisted_tools = []
        tier.save(update_fields=["blacklisted_tools"])
        self.assertEqual(get_tier_tool_blacklist("standard"), set())

        tier.blacklisted_tools = ["http_request"]
        tier.save(update_fields=["blacklisted_tools"])

        self.assertEqual(get_tier_tool_blacklist("standard"), {"http_request"})

    def test_tool_blacklist_uses_current_agent_tier_when_instance_is_stale(self):
        self.user.quota.max_intelligence_tier = AgentLLMTier.PREMIUM.value
        self.user.quota.save(update_fields=["max_intelligence_tier"])
        standard = get_intelligence_tier("standard")
        premium = get_intelligence_tier("premium")
        standard.blacklisted_tools = ["http_request"]
        standard.save(update_fields=["blacklisted_tools"])
        premium.blacklisted_tools = []
        premium.save(update_fields=["blacklisted_tools"])
        self.agent.preferred_llm_tier = standard
        self.agent.save(update_fields=["preferred_llm_tier"])
        self.assertEqual(getattr(self.agent.preferred_llm_tier, "key", None), "standard")
        self.assertEqual(get_agent_tool_blacklist(self.agent), {"http_request"})

        PersistentAgent.objects.filter(id=self.agent.id).update(preferred_llm_tier=premium)

        self.assertEqual(getattr(self.agent.preferred_llm_tier, "key", None), "standard")
        self.assertEqual(get_agent_tool_blacklist(self.agent), set())
        self.assertEqual(self.agent.preferred_llm_tier_id, premium.id)

    def test_runtime_override_changes_runtime_tier_and_billing_but_not_baseline(self):
        self.agent.preferred_llm_tier = get_intelligence_tier("ultra_max")
        self.agent.save(update_fields=["preferred_llm_tier"])

        try:
            with patch("api.agent.core.llm_config.get_owner_plan", return_value={"id": "pro"}):
                baseline_tier = get_agent_baseline_llm_tier(self.agent)
                self.assertEqual(baseline_tier, AgentLLMTier.ULTRA_MAX)

                runtime_tier = set_runtime_tier_override(self.agent, AgentLLMTier.ULTRA)
                self.assertEqual(runtime_tier, AgentLLMTier.ULTRA)
                self.assertEqual(get_agent_baseline_llm_tier(self.agent), AgentLLMTier.ULTRA_MAX)
                self.assertEqual(get_agent_llm_tier(self.agent), AgentLLMTier.ULTRA)

                baseline_cost = apply_tier_credit_multiplier(
                    self.agent,
                    Decimal("1.000"),
                    use_runtime_override=False,
                )
                runtime_cost = apply_tier_credit_multiplier(self.agent, Decimal("1.000"))
                self.assertEqual(
                    baseline_cost,
                    (Decimal("1.000") * get_credit_multiplier_for_tier(AgentLLMTier.ULTRA_MAX)).quantize(
                        Decimal("0.001")
                    ),
                )
                self.assertEqual(
                    runtime_cost,
                    (Decimal("1.000") * get_credit_multiplier_for_tier(AgentLLMTier.ULTRA)).quantize(
                        Decimal("0.001")
                    ),
                )
        finally:
            clear_runtime_tier_override(self.agent)

        with patch("api.agent.core.llm_config.get_owner_plan", return_value={"id": "pro"}):
            self.assertEqual(get_agent_llm_tier(self.agent), AgentLLMTier.ULTRA_MAX)


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
        self.premium = get_intelligence_tier("premium")
        self.max_tier = get_intelligence_tier("max")
        self.standard.__class__.objects.update(is_default=False, is_trial_default=False)
        self.max_tier.is_default = True
        self.max_tier.is_trial_default = True
        self.max_tier.save(update_fields=["is_default", "is_trial_default"])
        cache.clear()

    def test_system_default_tier_used_when_owner_unknown(self):
        self.assertEqual(get_system_default_tier(force_refresh=True), AgentLLMTier.MAX)
        self.assertEqual(resolve_preferred_tier_for_owner(None, None), AgentLLMTier.MAX)

    def test_system_default_tier_is_clamped_for_free_users(self):
        # Free plan users are limited to STANDARD; preferences/defaults should be clamped.
        self.assertEqual(resolve_preferred_tier_for_owner(self.user, None), AgentLLMTier.STANDARD)
        self.assertEqual(resolve_preferred_tier_for_owner(self.user, "max"), AgentLLMTier.STANDARD)

    def test_user_quota_can_cap_paid_plan_tier(self):
        self.user.quota.max_intelligence_tier = AgentLLMTier.PREMIUM.value
        self.user.quota.save(update_fields=["max_intelligence_tier"])
        with patch("api.agent.core.llm_config.get_owner_plan", return_value={"id": "pro"}):
            resolved = resolve_preferred_tier_for_owner(self.user, AgentLLMTier.ULTRA_MAX.value)
        self.assertEqual(resolved, AgentLLMTier.PREMIUM)

    def test_user_quota_can_override_free_plan_limit(self):
        self.user.quota.max_intelligence_tier = AgentLLMTier.MAX.value
        self.user.quota.save(update_fields=["max_intelligence_tier"])
        resolved = resolve_preferred_tier_for_owner(self.user, AgentLLMTier.ULTRA_MAX.value)
        self.assertEqual(resolved, AgentLLMTier.MAX)

    def test_name_only_plans_are_treated_as_paid(self):
        for plan_name in (PlanNames.STARTUP, PlanNames.ORG_TEAM):
            with self.subTest(plan_name=plan_name):
                self.assertTrue(_plan_supports_paid_tiers({"name": plan_name}))

    def test_name_only_paid_plans_do_not_clamp_requested_tier_to_standard(self):
        for plan_name in (PlanNames.STARTUP, PlanNames.ORG_TEAM):
            with self.subTest(plan_name=plan_name):
                with patch("api.agent.core.llm_config.get_owner_plan", return_value={"name": plan_name}):
                    resolved = resolve_preferred_tier_for_owner(self.user, AgentLLMTier.ULTRA_MAX.value)
                self.assertEqual(resolved, AgentLLMTier.ULTRA_MAX)

    def test_max_is_seeded_as_trial_default(self):
        IntelligenceTier.objects.update(is_trial_default=False)
        migration = importlib.import_module(
            "api.migrations.0422_intelligencetier_trial_default"
        )
        migration.seed_trial_default_intelligence_tier(apps, None)

        self.assertTrue(
            IntelligenceTier.objects.filter(key="max", is_trial_default=True).exists()
        )
        self.assertEqual(get_trial_default_tier(force_refresh=True), AgentLLMTier.MAX)

    def test_trial_default_is_unique(self):
        self.standard.is_trial_default = True
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.standard.save(update_fields=["is_trial_default"])

    def test_admin_switches_trial_default_without_changing_system_default(self):
        get_trial_default_tier(force_refresh=True)
        self.standard.is_trial_default = True

        IntelligenceTierAdmin(IntelligenceTier, admin.site).save_model(
            request=None,
            obj=self.standard,
            form=None,
            change=True,
        )

        self.standard.refresh_from_db()
        self.max_tier.refresh_from_db()
        self.assertTrue(self.standard.is_trial_default)
        self.assertFalse(self.max_tier.is_trial_default)
        self.assertTrue(self.max_tier.is_default)
        self.assertEqual(get_trial_default_tier(), AgentLLMTier.STANDARD)

    def test_active_trial_uses_trial_default(self):
        with (
            patch("util.user_behavior.is_owner_currently_in_trial", return_value=True),
            patch("api.agent.core.llm_config.get_owner_plan", return_value={"id": "pro"}),
        ):
            resolved = resolve_preferred_tier_for_owner(self.user, None)

        self.assertEqual(resolved, AgentLLMTier.MAX)

    def test_explicit_selection_overrides_trial_default(self):
        with (
            patch("util.user_behavior.is_owner_currently_in_trial", return_value=True),
            patch("api.agent.core.llm_config.get_owner_plan", return_value={"id": "pro"}),
        ):
            resolved = resolve_preferred_tier_for_owner(self.user, "premium")

        self.assertEqual(resolved, AgentLLMTier.PREMIUM)

    def test_non_trial_owner_uses_system_default(self):
        IntelligenceTier.objects.update(is_default=False)
        self.standard.is_default = True
        self.standard.save(update_fields=["is_default"])

        with (
            patch("util.user_behavior.is_owner_currently_in_trial", return_value=False),
            patch("api.agent.core.llm_config.get_owner_plan", return_value={"id": "pro"}),
        ):
            resolved = resolve_preferred_tier_for_owner(self.user, None)

        self.assertEqual(resolved, AgentLLMTier.STANDARD)

    def test_trial_default_is_clamped_by_user_quota(self):
        self.user.quota.max_intelligence_tier = AgentLLMTier.PREMIUM.value
        self.user.quota.save(update_fields=["max_intelligence_tier"])

        with (
            patch("util.user_behavior.is_owner_currently_in_trial", return_value=True),
            patch("api.agent.core.llm_config.get_owner_plan", return_value={"id": "pro"}),
        ):
            resolved = resolve_preferred_tier_for_owner(self.user, None)

        self.assertEqual(resolved, AgentLLMTier.PREMIUM)

    def test_missing_trial_default_falls_back_to_system_default(self):
        IntelligenceTier.objects.update(is_trial_default=False)
        cache.clear()

        with (
            patch("util.user_behavior.is_owner_currently_in_trial", return_value=True),
            patch("api.agent.core.llm_config.get_owner_plan", return_value={"id": "pro"}),
        ):
            resolved = resolve_preferred_tier_for_owner(self.user, None)

        self.assertEqual(resolved, AgentLLMTier.MAX)

    def test_trial_lookup_failure_falls_back_to_system_default(self):
        IntelligenceTier.objects.update(is_default=False)
        self.standard.is_default = True
        self.standard.save(update_fields=["is_default"])

        with (
            patch(
                "util.user_behavior.is_owner_currently_in_trial",
                side_effect=DatabaseError("subscription lookup failed"),
            ),
            patch("api.agent.core.llm_config.get_owner_plan", return_value={"id": "pro"}),
        ):
            resolved = resolve_preferred_tier_for_owner(self.user, None)

        self.assertEqual(resolved, AgentLLMTier.STANDARD)


@tag("batch_llm_intelligence")
@override_settings(GOBII_PROPRIETARY_MODE=True)
class TrialTemplateTierProvisioningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="trial-template@example.com",
            email="trial-template@example.com",
            password="test123",
        )
        self.user.quota.agent_limit = 5
        self.user.quota.save(update_fields=["agent_limit"])
        self.standard = get_intelligence_tier("standard")
        self.premium = get_intelligence_tier("premium")
        self.template = PersistentAgentTemplate.objects.create(
            code="trial-template-precedence",
            display_name="Trial template",
            tagline="Use the configured template tier",
            description="Template precedence coverage",
            charter="Run the premium template workflow",
            preferred_llm_tier=self.premium,
        )

    def test_template_tier_overrides_trial_default(self):
        with (
            patch("util.user_behavior.is_owner_currently_in_trial", return_value=True),
            patch("api.agent.core.llm_config.get_owner_plan", return_value={"id": "pro"}),
        ):
            result = PersistentAgentProvisioningService.provision(
                user=self.user,
                name="Template Tier Agent",
                template_code=self.template.code,
            )

        self.assertEqual(result.agent.preferred_llm_tier, self.premium)

    def test_explicit_tier_overrides_template_and_trial_defaults(self):
        with (
            patch("util.user_behavior.is_owner_currently_in_trial", return_value=True),
            patch("api.agent.core.llm_config.get_owner_plan", return_value={"id": "pro"}),
        ):
            result = PersistentAgentProvisioningService.provision(
                user=self.user,
                name="Explicit Tier Agent",
                template_code=self.template.code,
                preferred_llm_tier=self.standard,
            )

        self.assertEqual(result.agent.preferred_llm_tier, self.standard)


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
