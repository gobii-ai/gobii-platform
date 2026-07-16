from __future__ import annotations

from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.db import connection
from django.test import TransactionTestCase, tag
from django.contrib.auth import get_user_model

from api.models import (
    PersistentAgent,
    BrowserUseAgent,
    CommsAllowlistEntry,
    CommsChannel,
    UserPhoneNumber,
)
from api.agent.tools.email_sender import execute_send_email
from api.agent.tools.sms_sender import execute_send_sms
from config import settings


User = get_user_model()

def create_browser_agent_without_proxy(user, name):
    """Helper to create BrowserUseAgent without triggering proxy selection."""
    with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
        return BrowserUseAgent.objects.create(user=user, name=name)


@patch('django.db.close_old_connections')  # Mock at class level to prevent connection closing
@tag("batch_outbound_email")
class OutboundWhitelistGatingTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="u@example.com", email="u@example.com", password="pw"
        )
        # Email verification is required for outbound email sending
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        self.browser = create_browser_agent_without_proxy(self.user, "BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Agent",
            charter="c",
            browser_use_agent=self.browser,
        )
        # Provide from endpoints for tools
        from api.models import PersistentAgentCommsEndpoint
        default_domain = settings.DEFAULT_AGENT_EMAIL_DOMAIN
        self.email_from = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=f"agent@{default_domain}",
            is_primary=True,
        )
        self.sms_from = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent, channel=CommsChannel.SMS, address="+15550007777", is_primary=True
        )

    @patch("api.agent.tools.email_sender.deliver_agent_email")  # Mock where it's imported in email_sender
    @tag("batch_outbound_email")
    def test_email_execute_respects_manual_allowlist(self, mock_deliver_email, mock_close_old_connections):
        # Switch agent to manual and allow only a specific recipient
        self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
        self.agent.save(update_fields=["whitelist_policy"])
        from api.models import CommsAllowlistEntry
        CommsAllowlistEntry.objects.create(
            agent=self.agent, channel=CommsChannel.EMAIL, address="allowed@example.com"
        )

        ok = execute_send_email(self.agent, {
            "to_address": "allowed@example.com",
            "subject": "s",
            "mobile_first_html": "<p>hi</p>",
        })
        self.assertEqual(ok.get("status"), "ok")

        blocked = execute_send_email(self.agent, {
            "to_address": "blocked@example.com",
            "subject": "s",
            "mobile_first_html": "<p>hi</p>",
        })
        self.assertEqual(blocked.get("status"), "error")
        self.assertIn("not allowed", blocked.get("message", ""))

    @patch("api.agent.tools.email_sender.deliver_agent_email")  # Mock where it's imported in email_sender  
    @tag("batch_outbound_email")
    def test_email_execute_default_owner_only_user_owned(self, mock_deliver_email, mock_close_old_connections):
        # Default policy: user-owned agents may send only to owner by default
        ok = execute_send_email(self.agent, {
            "to_address": self.user.email,
            "subject": "s",
            "mobile_first_html": "<p>hi</p>",
        })
        self.assertEqual(ok.get("status"), "ok")

        blocked = execute_send_email(self.agent, {
            "to_address": "friend@example.com",
            "subject": "s",
            "mobile_first_html": "<p>hi</p>",
        })
        self.assertEqual(blocked.get("status"), "error")

    @patch("api.agent.tools.email_sender.deliver_agent_email")
    @tag("batch_outbound_email")
    def test_email_auto_approval_adds_to_and_cc_contacts(self, mock_deliver_email, mock_close_old_connections):
        self.agent.contact_approval_mode = PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL
        self.agent.save(update_fields=["contact_approval_mode"])

        result = execute_send_email(self.agent, {
            "to_address": "New.Person@Example.com",
            "cc_addresses": ["copy@example.com", "COPY@example.com"],
            "subject": "Automatic contacts",
            "mobile_first_html": "<p>Hello</p>",
        })

        self.assertEqual(result.get("status"), "ok")
        entries = CommsAllowlistEntry.objects.filter(agent=self.agent).order_by("address")
        self.assertEqual(list(entries.values_list("address", flat=True)), [
            "copy@example.com",
            "new.person@example.com",
        ])
        self.assertTrue(all(entry.allow_inbound and entry.allow_outbound for entry in entries))
        self.assertTrue(all(not entry.can_configure for entry in entries))
        mock_deliver_email.assert_called_once()

    @patch("api.agent.tools.email_sender.deliver_agent_email")
    @tag("batch_outbound_email")
    def test_email_auto_approval_activates_manual_policy(self, mock_deliver_email, mock_close_old_connections):
        self.agent.contact_approval_mode = PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL
        self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.DEFAULT
        self.agent.save(update_fields=["contact_approval_mode", "whitelist_policy"])

        result = execute_send_email(self.agent, {
            "to_address": "new-policy-contact@example.com",
            "subject": "Automatic contacts",
            "mobile_first_html": "<p>Hello</p>",
        })

        self.assertEqual(result.get("status"), "ok")
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.whitelist_policy, PersistentAgent.WhitelistPolicy.MANUAL)
        mock_deliver_email.assert_called_once()

    @patch("api.agent.tools.email_sender.deliver_agent_email")
    @tag("batch_outbound_email")
    def test_email_auto_approval_honors_mode_change_before_lock(self, mock_deliver_email, mock_close_old_connections):
        self.agent.contact_approval_mode = PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL

        result = execute_send_email(self.agent, {
            "to_address": "needs-review@example.com",
            "subject": "Automatic contacts",
            "mobile_first_html": "<p>Hello</p>",
        })

        self.assertEqual(result.get("status"), "error")
        self.assertIn("requires approval", result.get("message", ""))
        self.assertFalse(CommsAllowlistEntry.objects.filter(agent=self.agent).exists())
        mock_deliver_email.assert_not_called()

    @patch("api.agent.tools.email_sender.deliver_agent_email")
    @tag("batch_outbound_email")
    def test_email_auto_approval_reactivates_inactive_contact(self, mock_deliver_email, mock_close_old_connections):
        self.agent.contact_approval_mode = PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL
        self.agent.save(update_fields=["contact_approval_mode"])
        entry = CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="returning@example.com",
            is_active=False,
            allow_inbound=False,
            allow_outbound=False,
            can_configure=True,
        )

        result = execute_send_email(self.agent, {
            "to_address": "returning@example.com",
            "subject": "Welcome back",
            "mobile_first_html": "<p>Hello</p>",
        })

        self.assertEqual(result.get("status"), "ok")
        entry.refresh_from_db()
        self.assertTrue(entry.is_active)
        self.assertTrue(entry.allow_inbound)
        self.assertTrue(entry.allow_outbound)
        self.assertFalse(entry.can_configure)
        mock_deliver_email.assert_called_once()

    @patch("api.agent.tools.email_sender.deliver_agent_email")
    @tag("batch_outbound_email")
    def test_email_auto_approval_preserves_disabled_outbound(self, mock_deliver_email, mock_close_old_connections):
        self.agent.contact_approval_mode = PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL
        self.agent.save(update_fields=["contact_approval_mode"])
        entry = CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="inbound-only@example.com",
            allow_inbound=True,
            allow_outbound=False,
        )

        result = execute_send_email(self.agent, {
            "to_address": entry.address,
            "subject": "Should remain blocked",
            "mobile_first_html": "<p>Hello</p>",
        })

        self.assertEqual(result.get("status"), "error")
        self.assertIn("Outbound email is disabled", result.get("message", ""))
        entry.refresh_from_db()
        self.assertFalse(entry.allow_outbound)
        mock_deliver_email.assert_not_called()

    @patch("api.services.contact_authorization.get_user_max_contacts_per_agent", return_value=1)
    @patch("api.agent.tools.email_sender.deliver_agent_email")
    @tag("batch_outbound_email")
    def test_email_auto_approval_contact_cap_is_atomic(
        self,
        mock_deliver_email,
        _mock_contact_cap,
        mock_close_old_connections,
    ):
        self.agent.contact_approval_mode = PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL
        self.agent.save(update_fields=["contact_approval_mode"])

        result = execute_send_email(self.agent, {
            "to_address": "first@example.com",
            "cc_addresses": ["second@example.com"],
            "subject": "Too many contacts",
            "mobile_first_html": "<p>Hello</p>",
        })

        self.assertEqual(result.get("status"), "error")
        self.assertIn("1 of 1 contact slots available", result.get("message", ""))
        self.assertFalse(CommsAllowlistEntry.objects.filter(agent=self.agent).exists())
        mock_deliver_email.assert_not_called()

    @patch(
        "api.agent.tools.email_sender.deliver_agent_email",
        side_effect=RuntimeError("Delivery provider unavailable"),
    )
    @tag("batch_outbound_email")
    def test_email_auto_approved_contact_remains_when_delivery_fails(
        self,
        _mock_deliver_email,
        mock_close_old_connections,
    ):
        self.agent.contact_approval_mode = PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL
        self.agent.save(update_fields=["contact_approval_mode"])

        result = execute_send_email(self.agent, {
            "to_address": "retry-later@example.com",
            "subject": "Delivery retry",
            "mobile_first_html": "<p>Hello</p>",
        })

        self.assertEqual(result.get("status"), "error")
        self.assertTrue(
            CommsAllowlistEntry.objects.filter(
                agent=self.agent,
                address="retry-later@example.com",
                is_active=True,
                allow_outbound=True,
            ).exists()
        )

    @patch("api.agent.tools.email_sender.deliver_agent_email")
    @tag("batch_outbound_email")
    def test_email_continue_flag_disables_auto_sleep(self, mock_deliver_email, mock_close_old_connections):
        ok = execute_send_email(self.agent, {
            "to_address": self.user.email,
            "subject": "Continuing",
            "mobile_first_html": "<p>Still working</p>",
            "will_continue_work": True,
        })
        self.assertEqual(ok.get("status"), "ok")
        self.assertFalse(ok.get("auto_sleep_ok"))

        followup = execute_send_email(self.agent, {
            "to_address": self.user.email,
            "subject": "Done",
            "mobile_first_html": "<p>All set</p>",
        })
        self.assertTrue(followup.get("auto_sleep_ok"))
    # NOTE: Temporarily disabling SMS tests until SMS sending is re-enabled in multi-player mode
    @patch("api.agent.tools.sms_sender.deliver_agent_sms")  # Mock where it's imported in sms_sender
    def test_sms_execute_respects_default_and_manual(self, mock_deliver_sms, mock_close_old_connections):
        return
        # Mock successful delivery
        mock_deliver_sms.return_value = None  # deliver_agent_sms doesn't return anything

        # Default policy: require verified owner number
        res = execute_send_sms(self.agent, {"to_number": "+15551110000", "body": "hello"})
        self.assertEqual(res.get("status"), "error")
        mock_deliver_sms.assert_not_called()  # Should not deliver if not whitelisted

        UserPhoneNumber.objects.create(user=self.user, phone_number="+15551110000", is_verified=True)
        res = execute_send_sms(self.agent, {"to_number": "+15551110000", "body": "hello"})
        self.assertEqual(res.get("status"), "ok")
        mock_deliver_sms.assert_called_once()  # Should deliver when whitelisted

        # Manual policy only allows listed numbers
        mock_deliver_sms.reset_mock()  # Reset mock call count
        self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
        self.agent.save(update_fields=["whitelist_policy"])
        from api.models import CommsAllowlistEntry
        CommsAllowlistEntry.objects.create(agent=self.agent, channel=CommsChannel.SMS, address="+15557770000")

        ok = execute_send_sms(self.agent, {"to_number": "+15557770000", "body": "yo"})
        self.assertEqual(ok.get("status"), "ok")
        self.assertEqual(mock_deliver_sms.call_count, 1)  # Should have been called for allowed number

        mock_deliver_sms.reset_mock()
        blocked = execute_send_sms(self.agent, {"to_number": "+15557779999", "body": "yo"})
        self.assertEqual(blocked.get("status"), "error")
        mock_deliver_sms.assert_not_called()  # Should not deliver to blocked number

    @patch("api.agent.tools.sms_sender.deliver_agent_sms")
    @tag("batch_sms")
    def test_sms_execute_respects_agent_sms_disabled(self, mock_deliver_sms, mock_close_old_connections):
        self.agent.sms_disabled = True
        self.agent.save(update_fields=["sms_disabled"])

        result = execute_send_sms(self.agent, {
            "to_number": "+15551110000",
            "body": "hello",
            "will_continue_work": False,
        })

        self.assertEqual(result.get("status"), "error")
        self.assertIn("SMS sending is disabled", result.get("message", ""))
        mock_deliver_sms.assert_not_called()
