"""Tests for directional allowlist functionality (inbound/outbound)."""
import json
from unittest.mock import patch
from django.test import TestCase, tag
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from waffle.testutils import override_switch

from api.models import (
    PersistentAgent,
    CommsAllowlistEntry,
    CommsAllowlistRequest,
    CommsChannel,
    BrowserUseAgent,
    Organization,
    SmsContactPurpose,
)
from api.agent.tools.request_contact_permission import execute_request_contact_permission
from constants.feature_flags import SMS_CONTACT_PURPOSE_REQUIRED

User = get_user_model()


@tag("batch_allowlist_direction")
class AllowlistDirectionTests(TestCase):
    """Test the directional allowlist functionality."""
    
    def setUp(self):
        """Set up test data."""
        # No feature flags; behavior is always-on now
        
        # Create test user and agent
        self.owner = User.objects.create_user(
            username="testowner",
            email="owner@example.com",
            password="testpass"
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.owner,
            name="TestBrowserAgent"
        )
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="TestAgent",
            charter="Test charter",
            browser_use_agent=self.browser_agent,
            whitelist_policy=PersistentAgent.WhitelistPolicy.MANUAL,
        )
    
    def test_allowlist_entry_defaults_to_both_directions(self):
        """Test that new allowlist entries default to allowing both directions."""
        entry = CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="test@example.com"
        )
        
        self.assertTrue(entry.allow_inbound)
        self.assertTrue(entry.allow_outbound)
    
    def test_inbound_only_allowlist(self):
        """Test allowlist entry that only allows inbound communication."""
        entry = CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="inbound@example.com",
            allow_inbound=True,
            allow_outbound=False
        )
        
        # Test inbound is allowed
        self.assertTrue(
            self.agent.is_sender_whitelisted(CommsChannel.EMAIL, "inbound@example.com")
        )
        
        # Test outbound is blocked
        self.assertFalse(
            self.agent.is_recipient_whitelisted(CommsChannel.EMAIL, "inbound@example.com")
        )
    
    def test_outbound_only_allowlist(self):
        """Test allowlist entry that only allows outbound communication."""
        entry = CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="outbound@example.com",
            allow_inbound=False,
            allow_outbound=True
        )
        
        # Test inbound is blocked
        self.assertFalse(
            self.agent.is_sender_whitelisted(CommsChannel.EMAIL, "outbound@example.com")
        )
        
        # Test outbound is allowed
        self.assertTrue(
            self.agent.is_recipient_whitelisted(CommsChannel.EMAIL, "outbound@example.com")
        )
    
    def test_both_directions_allowed(self):
        """Test allowlist entry that allows both directions."""
        entry = CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="both@example.com",
            allow_inbound=True,
            allow_outbound=True
        )
        
        # Test both directions are allowed
        self.assertTrue(
            self.agent.is_sender_whitelisted(CommsChannel.EMAIL, "both@example.com")
        )
        self.assertTrue(
            self.agent.is_recipient_whitelisted(CommsChannel.EMAIL, "both@example.com")
        )
    
    def test_neither_direction_allowed(self):
        """Test allowlist entry with both directions disabled (edge case)."""
        entry = CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="blocked@example.com",
            allow_inbound=False,
            allow_outbound=False
        )
        
        # Test both directions are blocked
        self.assertFalse(
            self.agent.is_sender_whitelisted(CommsChannel.EMAIL, "blocked@example.com")
        )
        self.assertFalse(
            self.agent.is_recipient_whitelisted(CommsChannel.EMAIL, "blocked@example.com")
        )
    
    def test_inactive_entry_blocks_both_directions(self):
        """Test that inactive entries block both directions."""
        entry = CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="inactive@example.com",
            allow_inbound=True,
            allow_outbound=True,
            is_active=False
        )
        
        # Test both directions are blocked when inactive
        self.assertFalse(
            self.agent.is_sender_whitelisted(CommsChannel.EMAIL, "inactive@example.com")
        )
        self.assertFalse(
            self.agent.is_recipient_whitelisted(CommsChannel.EMAIL, "inactive@example.com")
        )
    
    def test_owner_always_allowed_both_directions(self):
        """Test that the owner is always allowed in both directions."""
        # Owner should be allowed even without explicit allowlist entry
        self.assertTrue(
            self.agent.is_sender_whitelisted(CommsChannel.EMAIL, "owner@example.com")
        )
        self.assertTrue(
            self.agent.is_recipient_whitelisted(CommsChannel.EMAIL, "owner@example.com")
        )
    
    def test_contact_request_with_direction_settings(self):
        """Test that contact requests respect direction settings when approved."""
        request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="request@example.com",
            reason="Need to contact for testing",
            purpose="Test purposes",
            request_inbound=True,
            request_outbound=False  # Only requesting inbound
        )
        
        # Approve the request
        entry = request.approve(invited_by=self.owner, skip_invitation=True)
        
        # Check the created entry has correct direction settings
        self.assertTrue(entry.allow_inbound)
        self.assertFalse(entry.allow_outbound)
        
        # Verify in the allowlist checks
        self.assertTrue(
            self.agent.is_sender_whitelisted(CommsChannel.EMAIL, "request@example.com")
        )
        self.assertFalse(
            self.agent.is_recipient_whitelisted(CommsChannel.EMAIL, "request@example.com")
        )
    
    def test_sms_channel_validation(self):
        """Personal agents may allow SMS; organization-owned agents remain blocked."""
        # Personal/manual agent should allow SMS entries
        entry = CommsAllowlistEntry(
            agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15551234567",
            allow_inbound=True,
            allow_outbound=False,
        )
        # No exception expected
        entry.full_clean()

        # Organization-owned agent should still reject SMS allowlist entries
        org = Organization.objects.create(name="Org", slug="org", created_by=self.owner)
        billing = org.billing
        billing.purchased_seats = 1
        billing.save(update_fields=["purchased_seats"])

        org_browser = BrowserUseAgent.objects.create(user=self.owner, name="OrgBrowser")
        org_agent = PersistentAgent.objects.create(
            user=self.owner,
            organization=org,
            name="OrgAgent",
            charter="org",
            browser_use_agent=org_browser,
            whitelist_policy=PersistentAgent.WhitelistPolicy.MANUAL,
        )
        org_entry = CommsAllowlistEntry(
            agent=org_agent,
            channel=CommsChannel.SMS,
            address="+15557654321",
        )
        with self.assertRaises(ValidationError) as context:
            org_entry.full_clean()
        self.assertIn("Organization agents only support email", str(context.exception))

    @override_switch(SMS_CONTACT_PURPOSE_REQUIRED, active=True)
    def test_sms_contact_purpose_required_switch_blocks_new_sms_allowlist_without_purpose(self):
        entry = CommsAllowlistEntry(
            agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15551234567",
            allow_inbound=True,
            allow_outbound=False,
        )

        with self.assertRaises(ValidationError) as context:
            entry.save()

        self.assertIn("SMS contacts require an operational purpose", str(context.exception))
        self.assertIn("Confirm you have permission", str(context.exception))

        entry.sms_contact_purpose = SmsContactPurpose.TEAM_OPERATIONAL
        entry.sms_contact_purpose_details = "Action item notifications for the internal team."
        entry.sms_contact_permission_attested = True
        entry.save()

        entry.refresh_from_db()
        self.assertEqual(entry.sms_contact_purpose, SmsContactPurpose.TEAM_OPERATIONAL)
        self.assertEqual(
            entry.sms_contact_purpose_details,
            "Action item notifications for the internal team.",
        )

    @override_switch(SMS_CONTACT_PURPOSE_REQUIRED, active=True)
    def test_sms_contact_request_propagates_purpose_to_approved_allowlist_entry(self):
        request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15551234567",
            reason="Need to notify the team when action items are assigned.",
            purpose="Action item notifications",
            request_inbound=True,
            request_outbound=True,
            sms_contact_purpose=SmsContactPurpose.TEAM_OPERATIONAL,
            sms_contact_purpose_details="Only operational team notifications.",
            sms_contact_permission_attested=True,
        )

        entry = request.approve(invited_by=self.owner, skip_invitation=True)

        self.assertEqual(entry.sms_contact_purpose, SmsContactPurpose.TEAM_OPERATIONAL)
        self.assertEqual(entry.sms_contact_purpose_details, "Only operational team notifications.")
        self.assertTrue(entry.sms_contact_permission_attested)
        self.assertIsNotNone(entry.sms_contact_permission_attested_at)

    def test_sms_attestation_partial_save_persists_attestation_pair(self):
        entry = CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15551234567",
            sms_contact_purpose=SmsContactPurpose.TEAM_OPERATIONAL,
            sms_contact_permission_attested=True,
        )
        self.assertIsNotNone(entry.sms_contact_permission_attested_at)

        entry.sms_contact_permission_attested = False
        entry.allow_inbound = False
        entry.save(update_fields=["allow_inbound"])

        entry.refresh_from_db()
        self.assertFalse(entry.allow_inbound)
        self.assertFalse(entry.sms_contact_permission_attested)
        self.assertIsNone(entry.sms_contact_permission_attested_at)

    @override_switch(SMS_CONTACT_PURPOSE_REQUIRED, active=True)
    def test_sms_contact_request_approval_requires_permission_attestation(self):
        request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15551234567",
            reason="Need to notify the team when action items are assigned.",
            purpose="Action item notifications",
            sms_contact_purpose=SmsContactPurpose.TEAM_OPERATIONAL,
        )

        with self.assertRaises(ValidationError) as context:
            request.approve(invited_by=self.owner, skip_invitation=True)

        self.assertIn("Confirm you have permission", str(context.exception))

    @override_switch(SMS_CONTACT_PURPOSE_REQUIRED, active=True)
    def test_request_contact_permission_requires_sms_contact_purpose_when_switch_active(self):
        result = execute_request_contact_permission(self.agent, {
            "contacts": [
                {
                    "channel": "sms",
                    "address": "+15551234567",
                    "reason": "Need to notify the team when action items are assigned.",
                    "purpose": "Action item notifications",
                }
            ]
        })

        self.assertEqual(result["status"], "error")
        self.assertIn("requires an operational purpose", result["message"])
        self.assertFalse(CommsAllowlistRequest.objects.exists())

    @override_switch(SMS_CONTACT_PURPOSE_REQUIRED, active=True)
    def test_request_contact_permission_stores_sms_contact_purpose(self):
        result = execute_request_contact_permission(self.agent, {
            "contacts": [
                {
                    "channel": "sms",
                    "address": "+15551234567",
                    "reason": "Need to notify the team when action items are assigned.",
                    "purpose": "Action item notifications",
                    "sms_contact_purpose": SmsContactPurpose.TEAM_OPERATIONAL,
                    "sms_contact_purpose_details": "Internal team action item alerts only.",
                }
            ]
        })

        self.assertEqual(result["status"], "ok")
        request = CommsAllowlistRequest.objects.get()
        self.assertEqual(request.sms_contact_purpose, SmsContactPurpose.TEAM_OPERATIONAL)
        self.assertEqual(
            request.sms_contact_purpose_details,
            "Internal team action item alerts only.",
        )
    
    def test_case_insensitive_email_with_directions(self):
        """Test that email addresses are case-insensitive with direction settings."""
        entry = CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="test@example.com",  # lowercase
            allow_inbound=True,
            allow_outbound=False
        )
        
        # Should match regardless of case
        self.assertTrue(
            self.agent.is_sender_whitelisted(CommsChannel.EMAIL, "TEST@EXAMPLE.COM")
        )
        self.assertFalse(
            self.agent.is_recipient_whitelisted(CommsChannel.EMAIL, "Test@Example.Com")
        )
    
    def test_multiple_entries_same_address(self):
        """Test that only one active entry per address is allowed."""
        # Create first entry
        entry1 = CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="duplicate@example.com",
            allow_inbound=True,
            allow_outbound=False
        )
        
        # Try to create duplicate - should raise integrity error
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            entry2 = CommsAllowlistEntry.objects.create(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address="duplicate@example.com",
                allow_inbound=False,
                allow_outbound=True
            )
    
    def test_invitation_flow_with_directional_settings(self):
        """Test the full invitation flow preserves directional settings.
        
        This test ensures that when a CommsAllowlistRequest is approved with
        skip_invitation=False, the created AgentAllowlistInvite and eventual
        CommsAllowlistEntry have the correct directional permissions.
        """
        from api.models import AgentAllowlistInvite
        
        # Create a request with specific directional settings (inbound only)
        request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="invite@example.com",
            reason="Need inbound only access",
            purpose="Receiving notifications",
            request_inbound=True,
            request_outbound=False  # Only requesting inbound
        )
        
        # Approve the request with skip_invitation=False to trigger invitation flow
        result = request.approve(invited_by=self.owner, skip_invitation=False)
        
        # Verify that an invitation was created (not a direct entry)
        self.assertIsInstance(result, AgentAllowlistInvite)
        invitation = result
        
        # Verify the invitation has the correct directional settings
        self.assertTrue(invitation.allow_inbound)
        self.assertFalse(invitation.allow_outbound)
        self.assertEqual(invitation.channel, CommsChannel.EMAIL)
        self.assertEqual(invitation.address, "invite@example.com")
        self.assertEqual(invitation.status, AgentAllowlistInvite.InviteStatus.PENDING)
        
        # Verify request is marked as approved and linked to invitation
        request.refresh_from_db()
        self.assertEqual(request.status, CommsAllowlistRequest.RequestStatus.APPROVED)
        self.assertEqual(request.allowlist_invitation, invitation)
        
        # Accept the invitation
        entry = invitation.accept()
        
        # Verify the created entry has the correct directional permissions
        self.assertIsInstance(entry, CommsAllowlistEntry)
        self.assertTrue(entry.allow_inbound)
        self.assertFalse(entry.allow_outbound)
        self.assertEqual(entry.channel, CommsChannel.EMAIL)
        self.assertEqual(entry.address, "invite@example.com")
        self.assertTrue(entry.is_active)
        
        # Verify the invitation is now accepted
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, AgentAllowlistInvite.InviteStatus.ACCEPTED)
        
        # Verify the allowlist checks respect the directional settings
        self.assertTrue(
            self.agent.is_sender_whitelisted(CommsChannel.EMAIL, "invite@example.com")
        )
        self.assertFalse(
            self.agent.is_recipient_whitelisted(CommsChannel.EMAIL, "invite@example.com")
        )
    
    def test_invitation_flow_outbound_only(self):
        """Test invitation flow with outbound-only permissions."""
        from api.models import AgentAllowlistInvite
        
        # Create a request for outbound-only access
        request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="outbound-invite@example.com",
            reason="Need to send updates",
            purpose="Sending status updates",
            request_inbound=False,
            request_outbound=True  # Only requesting outbound
        )
        
        # Approve with invitation flow
        invitation = request.approve(invited_by=self.owner, skip_invitation=False)
        
        # Verify invitation has correct settings
        self.assertFalse(invitation.allow_inbound)
        self.assertTrue(invitation.allow_outbound)
        
        # Accept and verify entry
        entry = invitation.accept()
        self.assertFalse(entry.allow_inbound)
        self.assertTrue(entry.allow_outbound)
        
        # Verify allowlist behavior
        self.assertFalse(
            self.agent.is_sender_whitelisted(CommsChannel.EMAIL, "outbound-invite@example.com")
        )
        self.assertTrue(
            self.agent.is_recipient_whitelisted(CommsChannel.EMAIL, "outbound-invite@example.com")
        )
    
    def test_invitation_flow_bidirectional(self):
        """Test invitation flow with bidirectional permissions."""
        from api.models import AgentAllowlistInvite
        
        # Create a request for both directions
        request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="both-invite@example.com",
            reason="Full communication needed",
            purpose="Collaborative work",
            request_inbound=True,
            request_outbound=True  # Requesting both directions
        )
        
        # Approve with invitation flow
        invitation = request.approve(invited_by=self.owner, skip_invitation=False)
        
        # Verify invitation has both directions enabled
        self.assertTrue(invitation.allow_inbound)
        self.assertTrue(invitation.allow_outbound)
        
        # Accept and verify entry
        entry = invitation.accept()
        self.assertTrue(entry.allow_inbound)
        self.assertTrue(entry.allow_outbound)
        
        # Verify both directions work
        self.assertTrue(
            self.agent.is_sender_whitelisted(CommsChannel.EMAIL, "both-invite@example.com")
        )
        self.assertTrue(
            self.agent.is_recipient_whitelisted(CommsChannel.EMAIL, "both-invite@example.com")
        )
