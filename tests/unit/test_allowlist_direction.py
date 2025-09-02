"""Tests for directional allowlist functionality (inbound/outbound)."""
import json
from unittest.mock import patch
from django.test import TestCase, tag
from django.contrib.auth import get_user_model

from api.models import (
    PersistentAgent,
    CommsAllowlistEntry,
    CommsAllowlistRequest,
    CommsChannel,
    BrowserUseAgent,
)

User = get_user_model()


@tag("allowlist_direction")
class AllowlistDirectionTests(TestCase):
    """Test the directional allowlist functionality."""
    
    def setUp(self):
        """Set up test data."""
        # Enable feature flags
        self._p_flag = patch("api.models.flag_is_active", return_value=True)
        self._p_switch = patch("api.models.switch_is_active", return_value=True)
        self._p_flag.start()
        self._p_switch.start()
        self.addCleanup(self._p_flag.stop)
        self.addCleanup(self._p_switch.stop)
        
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
        """Test that SMS channel is blocked for manual whitelist policy agents."""
        # SMS is currently blocked for agents with manual whitelist policy
        # This is enforced at the model validation level
        from django.core.exceptions import ValidationError
        
        entry = CommsAllowlistEntry(
            agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15551234567",
            allow_inbound=True,
            allow_outbound=False
        )
        
        # Should raise validation error for SMS with manual whitelist policy
        with self.assertRaises(ValidationError) as context:
            entry.full_clean()
        
        self.assertIn("Multi-player agents only support email", str(context.exception))
    
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