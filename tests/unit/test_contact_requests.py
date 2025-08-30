"""Test contact request system with invitation flow."""
from django.test import TestCase, tag
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta

from api.models import (
    PersistentAgent,
    CommsAllowlistRequest,
    AgentAllowlistInvite,
    CommsAllowlistEntry,
)

User = get_user_model()


@tag("batch_contact_requests")
class ContactRequestTests(TestCase):
    """Test the contact request approval workflow."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123"
        )
        
        # Create a BrowserUseAgent first (required for PersistentAgent)
        from api.models import BrowserUseAgent
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Test Browser Agent"
        )
        
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Test Agent",
            charter="A test agent charter",
            browser_use_agent=browser_agent
        )

    def test_approve_request_switches_to_manual_mode(self):
        """Test that approving a contact request keeps agent in manual mode."""
        # Set agent to DEFAULT mode to test the switch (though new agents start in MANUAL)
        self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.DEFAULT
        self.agent.save()
        self.assertEqual(self.agent.whitelist_policy, PersistentAgent.WhitelistPolicy.DEFAULT)
        
        # Create a contact request
        request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel="email",
            address="switch-test@example.com",
            name="Mode Switch Test",
            reason="Testing mode switch",
            purpose="Test"
        )
        
        # Approve the request
        request.approve(invited_by=self.user)
        
        # Verify agent switched to manual mode
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.whitelist_policy, PersistentAgent.WhitelistPolicy.MANUAL)
    
    @tag("batch_contact_requests")
    def test_approve_request_creates_invitation(self):
        """Test that approving a contact request can create an invitation or direct entry."""
        # Create a contact request
        request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel="email",
            address="contact@example.com",
            name="John Doe",
            reason="Need to discuss project",
            purpose="Schedule meeting"
        )
        
        # Test with skip_invitation=True (new default behavior)
        result = request.approve(invited_by=self.user, skip_invitation=True)
        
        # Should create a direct allowlist entry
        self.assertIsInstance(result, CommsAllowlistEntry)
        self.assertEqual(result.agent, self.agent)
        self.assertEqual(result.channel, "email")
        self.assertEqual(result.address, "contact@example.com")
        self.assertTrue(result.is_active)
        
        # Request should be marked as approved
        request.refresh_from_db()
        self.assertEqual(request.status, CommsAllowlistRequest.RequestStatus.APPROVED)
        self.assertIsNotNone(request.responded_at)
        
        # Test with skip_invitation=False (old behavior)
        request2 = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel="email",
            address="contact2@example.com",
            name="Jane Doe",
            reason="Need to discuss project",
            purpose="Schedule meeting"
        )
        
        result2 = request2.approve(invited_by=self.user, skip_invitation=False)
        
        # Should create an invitation
        self.assertIsInstance(result2, AgentAllowlistInvite)
        self.assertEqual(result2.agent, self.agent)
        self.assertEqual(result2.channel, "email")
        self.assertEqual(result2.address, "contact2@example.com")
        self.assertEqual(result2.invited_by, self.user)
        self.assertIsNotNone(result2.token)
        self.assertIsNotNone(result2.expires_at)
        
        # Request should be marked as approved
        request2.refresh_from_db()
        self.assertEqual(request2.status, CommsAllowlistRequest.RequestStatus.APPROVED)
        self.assertIsNotNone(request2.responded_at)
        self.assertEqual(request2.allowlist_invitation, result2)
        
    @tag("batch_contact_requests")
    def test_approve_existing_contact_skips_invitation(self):
        """Test that approving a request for an existing contact doesn't create a new invitation."""
        # Create existing allowlist entry
        existing_entry = CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel="email",
            address="existing@example.com",
            is_active=True
        )
        
        # Create a contact request for the same address
        request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel="email",
            address="existing@example.com",
            name="Existing User",
            reason="Need to follow up",
            purpose="Check status"
        )
        
        # Approve the request
        result = request.approve(invited_by=self.user)
        
        # Should return existing entry, not create invitation
        self.assertEqual(result, existing_entry)
        
        # Request should be marked as approved
        request.refresh_from_db()
        self.assertEqual(request.status, CommsAllowlistRequest.RequestStatus.APPROVED)
        
        # No invitation should be created
        self.assertIsNone(request.allowlist_invitation)
        
    @tag("batch_contact_requests")
    def test_cannot_approve_expired_request(self):
        """Test that expired requests cannot be approved."""
        # Create an expired request
        request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel="sms",
            address="+1234567890",
            name="Jane Doe",
            reason="Urgent matter",
            purpose="Get approval",
            expires_at=timezone.now() - timedelta(hours=1)
        )
        
        # Should not be able to approve
        self.assertFalse(request.can_be_approved())
        
        with self.assertRaises(ValueError) as context:
            request.approve(invited_by=self.user)
        
        self.assertIn("cannot be approved", str(context.exception))
        
    @tag("batch_contact_requests")
    def test_reject_request(self):
        """Test rejecting a contact request."""
        # Create a contact request
        request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel="email",
            address="rejected@example.com",
            name="Rejected User",
            reason="Not needed",
            purpose="Test"
        )
        
        # Reject the request
        request.reject()
        
        # Request should be marked as rejected
        self.assertEqual(request.status, CommsAllowlistRequest.RequestStatus.REJECTED)
        self.assertIsNotNone(request.responded_at)
        
        # No invitation should be created
        self.assertIsNone(request.allowlist_invitation)
        
    def test_pending_requests_queryset(self):
        """Test the pending_requests queryset method."""
        # Create various requests
        pending1 = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel="email",
            address="pending1@example.com",
            reason="Test",
            purpose="Test"
        )
        
        pending2 = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel="sms",
            address="+9876543210",
            reason="Test",
            purpose="Test"
        )
        
        # Create approved request
        approved = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel="email",
            address="approved@example.com",
            reason="Test",
            purpose="Test"
        )
        approved.approve(invited_by=self.user)
        
        # Create rejected request
        rejected = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel="email",
            address="rejected@example.com",
            reason="Test",
            purpose="Test"
        )
        rejected.reject()
        
        # Check pending requests
        pending = CommsAllowlistRequest.objects.filter(
            status=CommsAllowlistRequest.RequestStatus.PENDING
        )
        self.assertIn(pending1, pending)
        self.assertIn(pending2, pending)
        self.assertNotIn(approved, pending)
        self.assertNotIn(rejected, pending)
    
    def test_accept_invitation_switches_to_manual_mode(self):
        """Test that accepting an invitation switches agent to manual mode."""
        from api.models import AgentAllowlistInvite
        import uuid
        from datetime import timedelta
        from django.utils import timezone
        
        # Set agent to DEFAULT mode to test the switch (though new agents start in MANUAL)
        self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.DEFAULT
        self.agent.save()
        self.assertEqual(self.agent.whitelist_policy, PersistentAgent.WhitelistPolicy.DEFAULT)
        
        # Create an invitation directly
        invitation = AgentAllowlistInvite.objects.create(
            agent=self.agent,
            channel="email",
            address="invite-test@example.com",
            token=uuid.uuid4().hex,
            invited_by=self.user,
            expires_at=timezone.now() + timedelta(days=7)
        )
        
        # Accept the invitation
        invitation.accept()
        
        # Verify agent switched to manual mode
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.whitelist_policy, PersistentAgent.WhitelistPolicy.MANUAL)
        
        # Verify allowlist entry was created
        from api.models import CommsAllowlistEntry
        entry = CommsAllowlistEntry.objects.get(
            agent=self.agent,
            channel="email",
            address="invite-test@example.com"
        )
        self.assertTrue(entry.is_active)
