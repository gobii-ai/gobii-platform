"""
Tests for contact limit enforcement in allowlist based on user's plan.
"""
from unittest.mock import patch, Mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from api.models import (
    PersistentAgent,
    BrowserUseAgent,
    CommsAllowlistEntry,
    CommsChannel,
    AgentAllowlistInvite,
    Organization,
    OrganizationMembership,
    UserBilling,
)
from constants.plans import PlanNames
from config.plans import PLAN_CONFIG
from util.subscription_helper import get_user_max_contacts_per_agent

User = get_user_model()


@tag("batch_allowlist_rules")
@override_settings(GOBII_PROPRIETARY_MODE=True)
class ContactLimitEnforcementTests(TestCase):
    """Test that contact limits are enforced based on user's plan."""
    
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com"
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Test Browser Agent"
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Test Agent",
            charter="Test charter",
            browser_use_agent=self.browser_agent,
            whitelist_policy=PersistentAgent.WhitelistPolicy.MANUAL
        )

    def _set_user_billing_contact_limit(self, limit: int):
        billing, _ = UserBilling.objects.get_or_create(user=self.user)
        billing.max_contacts_per_agent = limit
        billing.save(update_fields=["max_contacts_per_agent"])

    @patch('util.subscription_helper.get_user_plan')
    def test_user_quota_override_takes_precedence(self, mock_get_user_plan):
        """When UserQuota.max_agent_contacts is set, it overrides plan limits."""
        # Plan would normally allow 20 contacts, but override to 1
        mock_get_user_plan.return_value = PLAN_CONFIG['startup']
        from api.models import UserQuota
        quota, _ = UserQuota.objects.get_or_create(user=self.user)
        quota.max_agent_contacts = 1
        quota.save(update_fields=["max_agent_contacts"]) 

        # Verify helper returns override
        limit = get_user_max_contacts_per_agent(self.user)
        self.assertEqual(limit, 1)

        # Add 1 contact – should pass
        entry1 = CommsAllowlistEntry(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="only@example.com",
            is_active=True
        )
        entry1.full_clean()
        entry1.save()

        # Second contact should fail due to override limit=1
        entry2 = CommsAllowlistEntry(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="too-many@example.com",
            is_active=True
        )
        with self.assertRaises(ValidationError) as ctx:
            entry2.full_clean()
        self.assertIn("Maximum 1 contacts", str(ctx.exception))

    @patch('util.subscription_helper.get_user_plan')
    def test_user_billing_override_takes_precedence(self, mock_get_user_plan):
        """Billing overrides should apply before other per-user overrides."""
        mock_get_user_plan.return_value = PLAN_CONFIG['startup']

        billing, _ = UserBilling.objects.get_or_create(user=self.user)
        billing.max_contacts_per_agent = 2
        billing.save(update_fields=["max_contacts_per_agent"])

        from api.models import UserQuota
        quota, _ = UserQuota.objects.get_or_create(user=self.user)
        quota.max_agent_contacts = 5
        quota.save(update_fields=["max_agent_contacts"]) 

        limit = get_user_max_contacts_per_agent(self.user)
        self.assertEqual(limit, 2)

        for address in ["one@example.com", "two@example.com"]:
            entry = CommsAllowlistEntry(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address=address,
                is_active=True,
            )
            entry.full_clean()
            entry.save()

        entry3 = CommsAllowlistEntry(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="three@example.com",
            is_active=True,
        )
        with self.assertRaises(ValidationError) as ctx:
            entry3.full_clean()
        self.assertIn("Maximum 2 contacts", str(ctx.exception))

    def test_direct_creation_enforces_limit_from_billing_override(self):
        """Creating entries via .objects.create should respect billing overrides."""
        self._set_user_billing_contact_limit(2)

        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="direct1@example.com",
            is_active=True,
        )
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="direct2@example.com",
            is_active=True,
        )

        with self.assertRaises(ValidationError):
            CommsAllowlistEntry.objects.create(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address="direct3@example.com",
                is_active=True,
            )

    def test_reactivating_inactive_entry_checks_limit(self):
        """Re-activating an entry should also enforce the contact cap."""
        self._set_user_billing_contact_limit(2)

        entry1 = CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="reactivate1@example.com",
            is_active=True,
        )
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="reactivate2@example.com",
            is_active=True,
        )

        # Free a slot by disabling the first entry
        entry1.is_active = False
        entry1.save(update_fields=["is_active"])

        # Fill available slot with another entry
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="reactivate3@example.com",
            is_active=True,
        )

        entry1.is_active = True
        with self.assertRaises(ValidationError):
            entry1.save(update_fields=["is_active"])

        entry1.refresh_from_db()
        self.assertFalse(entry1.is_active)

    
    @patch('util.subscription_helper.get_user_plan')
    def test_free_plan_limit_3_contacts(self, mock_get_user_plan):
        """Test that free plan users are limited to 3 contacts per agent."""
        # Mock free plan
        mock_get_user_plan.return_value = PLAN_CONFIG['free']
        
        # Verify the limit is 3
        limit = get_user_max_contacts_per_agent(self.user)
        self.assertEqual(limit, 3)
        
        # Add 3 contacts - should work
        for i in range(3):
            entry = CommsAllowlistEntry.objects.create(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address=f"contact{i}@example.com",
                is_active=True
            )
            entry.full_clean()  # Should pass
        
        # Try to add 4th contact - should fail
        entry4 = CommsAllowlistEntry(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="contact3@example.com",
            is_active=True
        )
        
        with self.assertRaises(ValidationError) as ctx:
            entry4.full_clean()
        
        self.assertIn("Maximum 3 contacts", str(ctx.exception))
        self.assertIn("allowed per agent for your plan", str(ctx.exception))
    
    @patch('util.subscription_helper.get_user_plan')
    def test_pro_plan_limit_20_contacts(self, mock_get_user_plan):
        """Test that pro/startup plan users are limited to 20 contacts per agent."""
        # Mock pro/startup plan
        mock_get_user_plan.return_value = PLAN_CONFIG['startup']
        
        # Verify the limit is 20
        limit = get_user_max_contacts_per_agent(self.user)
        self.assertEqual(limit, 20)
        
        # Add 20 contacts - should work
        for i in range(20):
            entry = CommsAllowlistEntry.objects.create(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address=f"contact{i}@example.com",
                is_active=True
            )
        
        # Try to add 21st contact - should fail
        entry21 = CommsAllowlistEntry(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="contact20@example.com",
            is_active=True
        )
        
        with self.assertRaises(ValidationError) as ctx:
            entry21.full_clean()
        
        self.assertIn("Maximum 20 contacts", str(ctx.exception))
        self.assertIn("allowed per agent for your plan", str(ctx.exception))
    
    @patch('util.subscription_helper.get_user_plan')
    def test_inactive_entries_dont_count_toward_limit(self, mock_get_user_plan):
        """Test that inactive entries don't count toward the limit."""
        # Mock free plan (3 contact limit)
        mock_get_user_plan.return_value = PLAN_CONFIG['free']
        
        # Add 2 active and 2 inactive entries
        for i in range(2):
            CommsAllowlistEntry.objects.create(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address=f"active{i}@example.com",
                is_active=True
            )
        
        for i in range(2):
            CommsAllowlistEntry.objects.create(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address=f"inactive{i}@example.com",
                is_active=False
            )
        
        # Should be able to add 1 more active entry (totaling 3 active)
        entry3 = CommsAllowlistEntry(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="active2@example.com",
            is_active=True
        )
        entry3.full_clean()  # Should pass
        entry3.save()
        
        # But not a 4th active entry
        entry4 = CommsAllowlistEntry(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="active3@example.com",
            is_active=True
        )
        
        with self.assertRaises(ValidationError):
            entry4.full_clean()



    @patch('util.subscription_helper.get_user_plan')
    def test_editing_existing_entry_doesnt_count(self, mock_get_user_plan):
        """Test that updating an existing entry doesn't trigger the limit check."""
        # Mock free plan (3 contact limit)
        mock_get_user_plan.return_value = PLAN_CONFIG['free']
        
        # Add 3 contacts (at limit)
        entries = []
        for i in range(3):
            entry = CommsAllowlistEntry.objects.create(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address=f"contact{i}@example.com",
                is_active=True
            )
            entries.append(entry)
        
        # Edit one of them - should work
        entries[0].address = "updated@example.com"
        entries[0].full_clean()  # Should pass since we're not adding
        entries[0].save()
    
    @patch('util.subscription_helper.get_user_plan')
    def test_different_agents_have_separate_limits(self, mock_get_user_plan):
        """Test that each agent has its own separate contact limit."""
        # Mock free plan (3 contact limit)
        mock_get_user_plan.return_value = PLAN_CONFIG['free']
        
        # Create second agent with its own browser agent
        browser_agent2 = BrowserUseAgent.objects.create(
            user=self.user,
            name="Second Browser Agent"
        )
        agent2 = PersistentAgent.objects.create(
            user=self.user,
            name="Second Agent",
            charter="Test charter 2",
            browser_use_agent=browser_agent2,
            whitelist_policy=PersistentAgent.WhitelistPolicy.MANUAL
        )
        
        # Add 3 contacts to first agent
        for i in range(3):
            CommsAllowlistEntry.objects.create(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address=f"agent1_contact{i}@example.com",
                is_active=True
            )
        
        # Should still be able to add 3 to second agent
        for i in range(3):
            entry = CommsAllowlistEntry(
                agent=agent2,
                channel=CommsChannel.EMAIL,
                address=f"agent2_contact{i}@example.com",
                is_active=True
            )
            entry.full_clean()  # Should pass
            entry.save()
    
    @patch('util.subscription_helper.get_user_plan')
    def test_pending_invitations_count_toward_limit(self, mock_get_user_plan):
        """Test that pending invitations count toward the contact limit."""
        from datetime import timedelta
        import uuid
        
        # Mock free plan (3 contact limit)
        mock_get_user_plan.return_value = PLAN_CONFIG['free']
        
        # Add 1 active contact
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="active@example.com",
            is_active=True
        )
        
        # Add 1 pending invitation
        invite = AgentAllowlistInvite(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="pending@example.com",
            token=uuid.uuid4().hex,
            invited_by=self.user,
            expires_at=timezone.now() + timedelta(days=7)
        )
        invite.full_clean()  # Should pass
        invite.save()
        
        # Should be able to add 1 more (totaling 3: 1 active + 1 pending + 1 new)
        entry3 = CommsAllowlistEntry(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="third@example.com",
            is_active=True
        )
        entry3.full_clean()  # Should pass
        entry3.save()
        
        # But not a 4th
        entry4 = CommsAllowlistEntry(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="fourth@example.com",
            is_active=True
        )
        
        with self.assertRaises(ValidationError) as ctx:
            entry4.full_clean()
        
        self.assertIn("Maximum 3 contacts", str(ctx.exception))
        self.assertIn("including 1 pending invitation", str(ctx.exception))
    
    @patch('util.subscription_helper.get_user_plan')
    def test_cant_send_more_invitations_than_limit(self, mock_get_user_plan):
        """Test that you can't send more invitations than the plan limit."""
        from datetime import timedelta
        import uuid
        
        # Mock free plan (3 contact limit)
        mock_get_user_plan.return_value = PLAN_CONFIG['free']
        
        # Add 2 active contacts
        for i in range(2):
            CommsAllowlistEntry.objects.create(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address=f"active{i}@example.com",
                is_active=True
            )
        
        # Add 1 pending invitation - should work (totaling 3)
        invite1 = AgentAllowlistInvite(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="pending1@example.com",
            token=uuid.uuid4().hex,
            invited_by=self.user,
            expires_at=timezone.now() + timedelta(days=7)
        )
        invite1.full_clean()  # Should pass
        invite1.save()
        
        # Try to add another invitation - should fail
        invite2 = AgentAllowlistInvite(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="pending2@example.com",
            token=uuid.uuid4().hex,
            invited_by=self.user,
            expires_at=timezone.now() + timedelta(days=7)
        )
        
        with self.assertRaises(ValidationError) as ctx:
            invite2.full_clean()
        
        self.assertIn("Cannot send more invitations", str(ctx.exception))
        self.assertIn("Maximum 3 contacts", str(ctx.exception))
        self.assertIn("currently 2 active, 1 pending", str(ctx.exception))
    
    @patch('util.subscription_helper.get_user_plan')
    def test_accepted_invitations_dont_double_count(self, mock_get_user_plan):
        """Test that accepted invitations don't count twice (as both invitation and entry)."""
        from datetime import timedelta
        import uuid
        
        # Mock free plan (3 contact limit)
        mock_get_user_plan.return_value = PLAN_CONFIG['free']
        
        # Add 2 active contacts
        for i in range(2):
            CommsAllowlistEntry.objects.create(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address=f"active{i}@example.com",
                is_active=True
            )
        
        # Add an accepted invitation (it should have created an allowlist entry)
        invite = AgentAllowlistInvite.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="accepted@example.com",
            token=uuid.uuid4().hex,
            invited_by=self.user,
            expires_at=timezone.now() + timedelta(days=7),
            status=AgentAllowlistInvite.InviteStatus.ACCEPTED
        )
        
        # Create the corresponding allowlist entry (as would happen when accepting)
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="accepted@example.com",
            is_active=True
        )
        
        # Should NOT be able to add another entry (we have 3 active entries)
        entry4 = CommsAllowlistEntry(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="fourth@example.com",
            is_active=True
        )
        
        with self.assertRaises(ValidationError) as ctx:
            entry4.full_clean()
        
        # Should show 0 pending since the invitation is accepted
        self.assertIn("Maximum 3 contacts", str(ctx.exception))
        self.assertIn("including 0 pending invitations", str(ctx.exception))


@tag("batch_allowlist_rules")
@override_settings(GOBII_PROPRIETARY_MODE=True)
class OrganizationContactLimitTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="org-owner",
            email="owner@example.com",
        )
        self.org = Organization.objects.create(
            name="OrgCo",
            slug="orgco",
            created_by=self.owner,
        )
        billing = self.org.billing
        billing.subscription = PlanNames.ORG_TEAM
        billing.purchased_seats = 2
        billing.save(update_fields=["subscription", "purchased_seats"])

        OrganizationMembership.objects.create(
            org=self.org,
            user=self.owner,
            role=OrganizationMembership.OrgRole.OWNER,
        )

        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.owner,
            name="Org Browser Agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            organization=self.org,
            name="Org Agent",
            charter="Org charter",
            browser_use_agent=self.browser_agent,
            whitelist_policy=PersistentAgent.WhitelistPolicy.MANUAL,
        )

    def test_helper_uses_org_plan_limit(self):
        limit = get_user_max_contacts_per_agent(self.owner, organization=self.org)
        self.assertEqual(limit, PLAN_CONFIG["org_team"]["max_contacts_per_agent"])

    def test_allowlist_enforces_org_plan_limit(self):
        limit = PLAN_CONFIG["org_team"]["max_contacts_per_agent"]
        for i in range(limit):
            CommsAllowlistEntry.objects.create(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address=f"org{i}@example.com",
                is_active=True,
            )

        overflow_entry = CommsAllowlistEntry(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="overflow@example.com",
            is_active=True,
        )

        with self.assertRaises(ValidationError):
            overflow_entry.full_clean()


@tag("batch_allowlist_rules")
@override_settings(GOBII_PROPRIETARY_MODE=True)
class ContactLimitContextProcessorTests(TestCase):
    """Test that contact limits are properly exposed in template context."""
    
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com"
        )
        
    @patch('util.subscription_helper.get_user_plan')
    def test_free_plan_context_shows_limit(self, mock_get_user_plan):
        """Test that free plan shows 3 contact limit in context."""
        mock_get_user_plan.return_value = PLAN_CONFIG['free']
        
        from pages.context_processors import account_info
        from django.test import RequestFactory
        
        factory = RequestFactory()
        request = factory.get('/')
        request.user = self.user
        
        context = account_info(request)
        self.assertEqual(context['account']['usage']['max_contacts_per_agent'], 3)
    
    @patch('util.subscription_helper.get_user_plan')
    def test_pro_plan_context_shows_limit(self, mock_get_user_plan):
        """Test that pro plan shows 20 contact limit in context."""
        mock_get_user_plan.return_value = PLAN_CONFIG['startup']
        
        from pages.context_processors import account_info
        from django.test import RequestFactory
        
        factory = RequestFactory()
        request = factory.get('/')
        request.user = self.user
        
        context = account_info(request)
        self.assertEqual(context['account']['usage']['max_contacts_per_agent'], 20)

    @patch('util.subscription_helper.get_user_plan')
    def test_context_uses_user_quota_override(self, mock_get_user_plan):
        """Test that context reflects per-user override when set."""
        mock_get_user_plan.return_value = PLAN_CONFIG['startup']
        from pages.context_processors import account_info
        from django.test import RequestFactory
        from api.models import UserQuota

        quota, _ = UserQuota.objects.get_or_create(user=self.user)
        quota.max_agent_contacts = 7
        quota.save(update_fields=["max_agent_contacts"]) 

        factory = RequestFactory()
        request = factory.get('/')
        request.user = self.user

        context = account_info(request)
        self.assertEqual(context['account']['usage']['max_contacts_per_agent'], 7)
