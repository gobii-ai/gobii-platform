import os

from django.test import TestCase, tag, override_settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from unittest.mock import patch, MagicMock

from api.models import BrowserUseAgent, PersistentAgent, UserQuota
from agents.services import AgentService
from config.plans import MAX_AGENT_LIMIT, AGENTS_UNLIMITED, PLAN_CONFIG
from constants.plans import PlanNames
from util.subscription_helper import has_unlimited_agents, is_community_unlimited_mode


User = get_user_model()


def create_persistent_agent(user, name: str, *, bypass_validation: bool = False) -> PersistentAgent:
    """Create a PersistentAgent (and backing BrowserUseAgent) for tests."""
    browser_agent = BrowserUseAgent(user=user, name=name)
    if bypass_validation:
        super(BrowserUseAgent, browser_agent).save(force_insert=True)
    else:
        browser_agent.save()

    persistent_agent = PersistentAgent(
        user=user,
        name=name,
        charter="",
        browser_use_agent=browser_agent,
    )
    if bypass_validation:
        super(PersistentAgent, persistent_agent).save(force_insert=True)
    else:
        persistent_agent.full_clean()
        persistent_agent.save()
    return persistent_agent


@tag("batch_agent_limits")
class AgentLimitTests(TestCase):
    """Test suite for agent limit enforcement including the MAX_AGENT_LIMIT safety cap."""

    def setUp(self):
        """Set up test users and quotas."""
        self.free_user = User.objects.create_user(
            username='freeuser@example.com',
            email='freeuser@example.com',
            password='password123'
        )
        self.unlimited_user = User.objects.create_user(
            username='unlimiteduser@example.com',
            email='unlimiteduser@example.com',
            password='password123'
        )
        self.high_quota_user = User.objects.create_user(
            username='highquotauser@example.com',
            email='highquotauser@example.com',
            password='password123'
        )
        self.no_quota_user = User.objects.create_user(
            username='noquotauser@example.com',
            email='noquotauser@example.com',
            password='password123'
        )

        # Set up quotas - use get_or_create since signals may have already created them
        quota, _ = UserQuota.objects.get_or_create(user=self.free_user, defaults={'agent_limit': 5})
        quota.agent_limit = 5
        quota.save()
        
        # For unlimited user, we'll mock has_unlimited_agents rather than storing AGENTS_UNLIMITED
        # since UserQuota.agent_limit is a PositiveIntegerField and can't store negative values
        quota, _ = UserQuota.objects.get_or_create(user=self.unlimited_user, defaults={'agent_limit': 10000})
        quota.agent_limit = 10000  # Large value, but we'll mock has_unlimited_agents() instead
        quota.save()
        
        quota, _ = UserQuota.objects.get_or_create(user=self.high_quota_user, defaults={'agent_limit': 2000})
        quota.agent_limit = 2000  # Above safety cap
        quota.save()
        
        # Ensure no_quota_user has no UserQuota record for testing
        UserQuota.objects.filter(user=self.no_quota_user).delete()

    def test_get_agents_in_use_counts_correctly(self):
        """Test that get_agents_in_use returns correct count."""
        # Create some agents
        create_persistent_agent(self.free_user, 'agent1')
        create_persistent_agent(self.free_user, 'agent2')
        create_persistent_agent(self.unlimited_user, 'agent3')

        self.assertEqual(AgentService.get_agents_in_use(self.free_user), 2)
        self.assertEqual(AgentService.get_agents_in_use(self.unlimited_user), 1)
        self.assertEqual(AgentService.get_agents_in_use(self.no_quota_user), 0)

    def test_free_user_agent_limit(self):
        """Test that free users respect their quota limit."""
        # Create 3 agents for free user (limit is 5)
        for i in range(3):
            create_persistent_agent(self.free_user, f'agent{i}')

        available = AgentService.get_agents_available(self.free_user)
        self.assertEqual(available, 2)  # 5 - 3 = 2

    def test_free_user_at_limit(self):
        """Test that free users get 0 available when at limit."""
        # Create 5 agents for free user (at limit)
        for i in range(5):
            create_persistent_agent(self.free_user, f'agent{i}')

        available = AgentService.get_agents_available(self.free_user)
        self.assertEqual(available, 0)

    def test_free_user_over_limit_returns_zero(self):
        """Test that if user somehow has more agents than quota, available returns 0."""
        # Create agents up to the limit normally
        for i in range(5):
            create_persistent_agent(self.free_user, f'agent{i}')
        
        # Create additional agents bypassing validation (simulates edge case like data migration)
        # We do this by calling save() with force_insert=True and not calling full_clean()
        for i in range(5, 7):
            create_persistent_agent(self.free_user, f'agent{i}', bypass_validation=True)

        available = AgentService.get_agents_available(self.free_user)
        self.assertEqual(available, 0)  # max(5 - 7, 0) = 0

    @patch('util.subscription_helper.has_unlimited_agents')
    def test_unlimited_user_capped_at_max_limit(self, mock_has_unlimited):
        """Test that unlimited users are capped at MAX_AGENT_LIMIT."""
        mock_has_unlimited.return_value = True

        # Create some agents (well below the cap)
        for i in range(10):
            create_persistent_agent(self.unlimited_user, f'agent{i}')

        available = AgentService.get_agents_available(self.unlimited_user)
        self.assertEqual(available, MAX_AGENT_LIMIT - 10)

    @patch('util.subscription_helper.has_unlimited_agents')
    def test_unlimited_user_at_max_limit(self, mock_has_unlimited):
        """Test that unlimited users get 0 available when at MAX_AGENT_LIMIT."""
        mock_has_unlimited.return_value = True

        # Mock the get_agents_in_use to return MAX_AGENT_LIMIT
        with patch.object(AgentService, 'get_agents_in_use', return_value=MAX_AGENT_LIMIT):
            available = AgentService.get_agents_available(self.unlimited_user)
            self.assertEqual(available, 0)

    @patch('util.subscription_helper.has_unlimited_agents')
    def test_unlimited_user_over_max_limit_returns_zero(self, mock_has_unlimited):
        """Test that unlimited users over MAX_AGENT_LIMIT get 0 available."""
        mock_has_unlimited.return_value = True

        # Mock the get_agents_in_use to return more than MAX_AGENT_LIMIT
        with patch.object(AgentService, 'get_agents_in_use', return_value=MAX_AGENT_LIMIT + 5):
            available = AgentService.get_agents_available(self.unlimited_user)
            self.assertEqual(available, 0)

    def test_high_quota_user_capped_at_max_limit(self):
        """Test that users with quota above MAX_AGENT_LIMIT are capped."""
        # high_quota_user has agent_limit=2000, should be capped to MAX_AGENT_LIMIT=1000
        
        # Create some agents
        for i in range(50):
            create_persistent_agent(self.high_quota_user, f'agent{i}')

        available = AgentService.get_agents_available(self.high_quota_user)
        self.assertEqual(available, MAX_AGENT_LIMIT - 50)  # 1000 - 50 = 950

    def test_no_quota_user_returns_zero(self):
        """Test that users without UserQuota record get 0 available."""
        available = AgentService.get_agents_available(self.no_quota_user)
        self.assertEqual(available, 0)

    def test_agent_creation_validation_allows_under_limit(self):
        """Test that agent creation succeeds when under limit."""
        # Create 2 agents for free user (limit is 5)
        for i in range(2):
            create_persistent_agent(self.free_user, f'agent{i}')

        # Creating another should succeed
        agent = BrowserUseAgent(user=self.free_user, name='new_agent')
        try:
            agent.clean()  # Should not raise ValidationError
        except ValidationError:
            self.fail("Agent creation should succeed when under limit")

    def test_agent_creation_validation_blocks_at_limit(self):
        """Test that agent creation fails when at limit."""
        # Create 5 agents for free user (at limit)
        for i in range(5):
            create_persistent_agent(self.free_user, f'agent{i}')

        # Creating another should fail
        agent = BrowserUseAgent(user=self.free_user, name='new_agent')
        with self.assertRaises(ValidationError) as cm:
            agent.clean()
        
        self.assertIn("Agent limit reached", str(cm.exception))

    @patch('util.subscription_helper.has_unlimited_agents')
    def test_unlimited_user_blocked_at_max_limit(self, mock_has_unlimited):
        """Test that even unlimited users are blocked at MAX_AGENT_LIMIT."""
        mock_has_unlimited.return_value = True

        # Mock to simulate user at MAX_AGENT_LIMIT
        with patch.object(AgentService, 'get_agents_in_use', return_value=MAX_AGENT_LIMIT):
            agent = BrowserUseAgent(user=self.unlimited_user, name='new_agent')
            with self.assertRaises(ValidationError) as cm:
                agent.clean()
            
            self.assertIn("Agent limit reached", str(cm.exception))

    def test_max_agent_limit_constant_sanity(self):
        """Test that MAX_AGENT_LIMIT is set to expected value."""
        self.assertEqual(MAX_AGENT_LIMIT, 1000)
        self.assertGreater(MAX_AGENT_LIMIT, AGENTS_UNLIMITED)  # Sanity check for min() comparisons

    def test_free_plan_limit_below_max(self):
        """Test that free plan limit is well below the safety cap."""
        free_limit = PLAN_CONFIG[PlanNames.FREE]["agent_limit"]
        self.assertLess(free_limit, MAX_AGENT_LIMIT)
        self.assertEqual(free_limit, 5)  # Explicit check for current value

    @override_settings(GOBII_PROPRIETARY_MODE=False, GOBII_ENABLE_COMMUNITY_UNLIMITED=True)
    def test_community_unlimited_mode_ignores_quota(self):
        """Community Edition unlimited mode should bypass per-user quota caps."""
        quota = UserQuota.objects.get(user=self.free_user)
        quota.agent_limit = 2
        quota.save()

        for i in range(2):
            create_persistent_agent(self.free_user, f'community-agent-{i}')

        # Baseline: with default test settings (community unlimited disabled) we enforce quota.
        self.assertEqual(AgentService.get_agents_available(self.free_user), 0)

        with patch.dict(os.environ, {"DJANGO_SETTINGS_MODULE": "config.settings"}, clear=False):
            available = AgentService.get_agents_available(self.free_user)
            self.assertEqual(available, MAX_AGENT_LIMIT - 2)
            self.assertTrue(has_unlimited_agents(self.free_user))
            self.assertTrue(is_community_unlimited_mode())


@tag("batch_agent_limits")
class AgentLimitIntegrationTests(TestCase):
    """Integration tests that test the full agent creation flow with limits."""

    def setUp(self):
        """Set up test user."""
        self.user = User.objects.create_user(
            username='integrationuser@example.com',
            email='integrationuser@example.com',
            password='password123'
        )
        # Set up quota - use get_or_create since signals may have already created it
        quota, _ = UserQuota.objects.get_or_create(user=self.user, defaults={'agent_limit': 3})
        quota.agent_limit = 3  # Low limit for testing
        quota.save()

    def test_full_agent_creation_flow_respects_limits(self):
        """Test that the full agent creation flow (not just validation) respects limits."""
        # Create agents up to the limit
        agents = []
        for i in range(3):
            persistent = create_persistent_agent(self.user, f'agent{i}')
            agents.append(persistent.browser_use_agent)

        # Verify we created 3 agents
        self.assertEqual(BrowserUseAgent.objects.filter(user=self.user).count(), 3)
        self.assertEqual(PersistentAgent.objects.filter(user=self.user).count(), 3)
        self.assertEqual(AgentService.get_agents_available(self.user), 0)

        # Try to create one more - should fail validation
        with self.assertRaises(ValidationError):
            agent = BrowserUseAgent(user=self.user, name='excess_agent')
            agent.clean()

        # Verify we still have only 3 agents
        self.assertEqual(BrowserUseAgent.objects.filter(user=self.user).count(), 3)

    def test_deleting_agent_frees_up_quota(self):
        """Test that deleting an agent allows creating a new one."""
        # Create agents up to the limit
        agents = []
        for i in range(3):
            persistent = create_persistent_agent(self.user, f'agent{i}')
            agents.append(persistent.browser_use_agent)

        # Delete one agent
        agents[0].delete()

        # Now we should be able to create another
        self.assertEqual(AgentService.get_agents_available(self.user), 1)
        
        # Creating should succeed
        agent = BrowserUseAgent(user=self.user, name='replacement_agent')
        agent.clean()  # Should not raise ValidationError
        agent.save()
        replacement_persistent = PersistentAgent(
            user=self.user,
            name='replacement_agent',
            charter="",
            browser_use_agent=agent,
        )
        replacement_persistent.full_clean()
        replacement_persistent.save()

        # Verify final state
        self.assertEqual(BrowserUseAgent.objects.filter(user=self.user).count(), 3)
        self.assertEqual(PersistentAgent.objects.filter(user=self.user).count(), 3)
        self.assertEqual(AgentService.get_agents_available(self.user), 0) 
