"""
Tests for email verification gating.

Ensures that external communications (email, SMS, webhooks) are blocked
for users who have not verified their email address.
"""

from unittest.mock import patch, MagicMock

from django.test import TransactionTestCase, TestCase, tag, override_settings
from django.contrib.auth import get_user_model

from allauth.account.models import EmailAddress

from api.models import (
    PersistentAgent,
    BrowserUseAgent,
    CommsChannel,
    PersistentAgentCommsEndpoint,
    PersistentAgentWebhook,
    UserPhoneNumber,
)
from api.services.email_verification import (
    has_verified_email,
    require_verified_email,
    EmailVerificationError,
)
from api.agent.tools.email_sender import execute_send_email
from api.agent.tools.sms_sender import execute_send_sms
from api.agent.tools.webhook_sender import execute_send_webhook_event
from api.permissions import IsEmailVerified
from config import settings


User = get_user_model()


def create_browser_agent_without_proxy(user, name):
    """Helper to create BrowserUseAgent without triggering proxy selection."""
    with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
        return BrowserUseAgent.objects.create(user=user, name=name)


@tag("batch_email_verification")
class EmailVerificationServiceTests(TestCase):
    """Tests for the core email verification service."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="test@example.com",
            email="test@example.com",
            password="password123",
        )

    def test_has_verified_email_returns_false_for_none_user(self):
        self.assertFalse(has_verified_email(None))

    def test_has_verified_email_returns_false_for_anonymous_user(self):
        anonymous_user = MagicMock()
        anonymous_user.is_authenticated = False
        self.assertFalse(has_verified_email(anonymous_user))

    def test_has_verified_email_returns_false_for_unverified_user(self):
        """User without any verified email addresses should return False."""
        self.assertFalse(has_verified_email(self.user))

    def test_has_verified_email_returns_false_with_unverified_email_address(self):
        """User with only unverified EmailAddress records should return False."""
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=False,
            primary=True,
        )
        self.assertFalse(has_verified_email(self.user))

    def test_has_verified_email_returns_true_for_verified_user(self):
        """User with a verified email should return True."""
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        self.assertTrue(has_verified_email(self.user))

    def test_superuser_bypasses_verification(self):
        """Superusers should bypass email verification check."""
        self.user.is_superuser = True
        self.user.save()
        self.assertTrue(has_verified_email(self.user))

    def test_require_verified_email_raises_for_unverified(self):
        """require_verified_email should raise EmailVerificationError for unverified users."""
        with self.assertRaises(EmailVerificationError) as ctx:
            require_verified_email(self.user, action_description="test action")
        self.assertIn("test action", str(ctx.exception))
        self.assertIn("Email verification required", str(ctx.exception))

    def test_require_verified_email_does_not_raise_for_verified(self):
        """require_verified_email should not raise for verified users."""
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        require_verified_email(self.user, action_description="test action")

    def test_email_verification_error_to_tool_response(self):
        """EmailVerificationError should have correct tool response format."""
        error = EmailVerificationError("Custom message")
        response = error.to_tool_response()
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error_code"], "EMAIL_VERIFICATION_REQUIRED")
        self.assertEqual(response["message"], "Custom message")


@patch('django.db.close_old_connections')
@tag("batch_email_verification")
class EmailVerificationToolGatingTests(TransactionTestCase):
    """Tests for email verification gating in agent tools."""

    def setUp(self):
        self.verified_user = User.objects.create_user(
            username="verified@example.com",
            email="verified@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=self.verified_user,
            email=self.verified_user.email,
            verified=True,
            primary=True,
        )

        self.unverified_user = User.objects.create_user(
            username="unverified@example.com",
            email="unverified@example.com",
            password="password123",
        )

        self.verified_browser = create_browser_agent_without_proxy(self.verified_user, "VerifiedBrowser")
        self.verified_agent = PersistentAgent.objects.create(
            user=self.verified_user,
            name="VerifiedAgent",
            charter="Test charter",
            browser_use_agent=self.verified_browser,
        )

        self.unverified_browser = create_browser_agent_without_proxy(self.unverified_user, "UnverifiedBrowser")
        self.unverified_agent = PersistentAgent.objects.create(
            user=self.unverified_user,
            name="UnverifiedAgent",
            charter="Test charter",
            browser_use_agent=self.unverified_browser,
        )

        default_domain = settings.DEFAULT_AGENT_EMAIL_DOMAIN
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.verified_agent,
            channel=CommsChannel.EMAIL,
            address=f"verified-agent@{default_domain}",
            is_primary=True,
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.verified_agent,
            channel=CommsChannel.SMS,
            address="+15550001111",
            is_primary=True,
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.unverified_agent,
            channel=CommsChannel.EMAIL,
            address=f"unverified-agent@{default_domain}",
            is_primary=True,
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.unverified_agent,
            channel=CommsChannel.SMS,
            address="+15550002222",
            is_primary=True,
        )

    @patch("api.agent.tools.email_sender.deliver_agent_email")
    @tag("batch_email_verification")
    def test_send_email_blocked_for_unverified_user(self, mock_deliver, mock_close):
        """Email sending should be blocked for users without verified email."""
        result = execute_send_email(self.unverified_agent, {
            "to_address": self.unverified_user.email,
            "subject": "Test",
            "mobile_first_html": "<p>Hello</p>",
        })
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "EMAIL_VERIFICATION_REQUIRED")
        mock_deliver.assert_not_called()

    @patch("api.agent.tools.email_sender.deliver_agent_email")
    @tag("batch_email_verification")
    def test_send_email_allowed_for_verified_user(self, mock_deliver, mock_close):
        """Email sending should work for users with verified email."""
        result = execute_send_email(self.verified_agent, {
            "to_address": self.verified_user.email,
            "subject": "Test",
            "mobile_first_html": "<p>Hello</p>",
        })
        self.assertEqual(result["status"], "ok")

    @patch("api.agent.tools.sms_sender.deliver_agent_sms")
    @tag("batch_email_verification")
    def test_send_sms_blocked_for_unverified_user(self, mock_deliver, mock_close):
        """SMS sending should be blocked for users without verified email."""
        result = execute_send_sms(self.unverified_agent, {
            "to_number": "+15553331234",
            "body": "Test message",
        })
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "EMAIL_VERIFICATION_REQUIRED")
        mock_deliver.assert_not_called()

    @tag("batch_email_verification")
    def test_send_webhook_blocked_for_unverified_user(self, mock_close):
        """Webhook sending should be blocked for users without verified email."""
        webhook = PersistentAgentWebhook.objects.create(
            agent=self.unverified_agent,
            name="Test Webhook",
            url="https://example.com/webhook",
        )
        result = execute_send_webhook_event(self.unverified_agent, {
            "webhook_id": str(webhook.id),
            "payload": {"test": "data"},
        })
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "EMAIL_VERIFICATION_REQUIRED")

    @patch("api.agent.tools.webhook_sender.select_proxies_for_webhook")
    @patch("api.agent.tools.webhook_sender.requests.post")
    @tag("batch_email_verification")
    def test_send_webhook_allowed_for_verified_user(self, mock_post, mock_select_proxies, mock_close):
        """Webhook sending should work for users with verified email."""
        mock_select_proxies.return_value = ({}, None)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"ok": true}'
        mock_post.return_value = mock_response

        webhook = PersistentAgentWebhook.objects.create(
            agent=self.verified_agent,
            name="Test Webhook",
            url="https://example.com/webhook",
        )
        result = execute_send_webhook_event(self.verified_agent, {
            "webhook_id": str(webhook.id),
            "payload": {"test": "data"},
        })
        self.assertEqual(result["status"], "success")


@tag("batch_email_verification")
class EmailVerificationPermissionTests(TestCase):
    """Tests for the DRF IsEmailVerified permission class."""

    def setUp(self):
        self.permission = IsEmailVerified()
        self.verified_user = User.objects.create_user(
            username="verified@example.com",
            email="verified@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=self.verified_user,
            email=self.verified_user.email,
            verified=True,
            primary=True,
        )

        self.unverified_user = User.objects.create_user(
            username="unverified@example.com",
            email="unverified@example.com",
            password="password123",
        )

    def test_permission_denies_unauthenticated(self):
        """Unauthenticated requests should be denied."""
        request = MagicMock()
        request.user = None
        self.assertFalse(self.permission.has_permission(request, None))

    def test_permission_denies_unverified_user(self):
        """Unverified users should be denied with PermissionDenied exception."""
        from rest_framework.exceptions import PermissionDenied
        request = MagicMock()
        mock_user = MagicMock()
        mock_user.is_authenticated = True
        mock_user.is_superuser = False
        request.user = mock_user
        with patch("api.permissions.has_verified_email", return_value=False):
            with self.assertRaises(PermissionDenied):
                self.permission.has_permission(request, None)

    def test_permission_allows_verified_user(self):
        """Verified users should be allowed."""
        request = MagicMock()
        mock_user = MagicMock()
        mock_user.is_authenticated = True
        mock_user.is_superuser = False
        request.user = mock_user
        with patch("api.permissions.has_verified_email", return_value=True):
            self.assertTrue(self.permission.has_permission(request, None))


@tag("batch_email_verification")
class InboundWebhookVerificationTests(TestCase):
    """Tests for email verification gating in inbound webhooks."""

    def setUp(self):
        from django.test import RequestFactory
        self.factory = RequestFactory()

        self.verified_user = User.objects.create_user(
            username="verified@example.com",
            email="verified@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=self.verified_user,
            email=self.verified_user.email,
            verified=True,
            primary=True,
        )

        self.unverified_user = User.objects.create_user(
            username="unverified@example.com",
            email="unverified@example.com",
            password="password123",
        )

        self.verified_browser = BrowserUseAgent.objects.create(user=self.verified_user, name="VB")
        self.verified_agent = PersistentAgent.objects.create(
            user=self.verified_user,
            name="VerifiedAgent",
            charter="Test",
            browser_use_agent=self.verified_browser,
        )
        self.verified_sms_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.verified_agent,
            channel=CommsChannel.SMS,
            address="+15550001111",
        )

        self.unverified_browser = BrowserUseAgent.objects.create(user=self.unverified_user, name="UB")
        self.unverified_agent = PersistentAgent.objects.create(
            user=self.unverified_user,
            name="UnverifiedAgent",
            charter="Test",
            browser_use_agent=self.unverified_browser,
        )
        self.unverified_sms_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.unverified_agent,
            channel=CommsChannel.SMS,
            address="+15550002222",
        )

    @patch("api.webhooks.ingest_inbound_message")
    @tag("batch_email_verification")
    def test_inbound_sms_dropped_for_unverified_owner(self, mock_ingest):
        """Inbound SMS should be silently dropped if agent owner has unverified email."""
        from api.webhooks import sms_webhook
        from api.models import UserPhoneNumber

        UserPhoneNumber.objects.create(
            user=self.unverified_user,
            phone_number="+15559998888",
            is_verified=True,
        )

        request = self.factory.post(
            f"/api/webhooks/inbound/sms/?t={settings.TWILIO_INCOMING_WEBHOOK_TOKEN}",
            data={
                "From": "+15559998888",
                "To": self.unverified_sms_ep.address,
                "Body": "Hello",
            },
        )
        resp = sms_webhook(request)
        self.assertEqual(resp.status_code, 200)
        mock_ingest.assert_not_called()

    @patch("api.webhooks.ingest_inbound_message")
    @tag("batch_email_verification")
    def test_inbound_sms_processed_for_verified_owner(self, mock_ingest):
        """Inbound SMS should be processed if agent owner has verified email."""
        from api.webhooks import sms_webhook
        from api.models import UserPhoneNumber

        UserPhoneNumber.objects.create(
            user=self.verified_user,
            phone_number="+15559997777",
            is_verified=True,
        )

        request = self.factory.post(
            f"/api/webhooks/inbound/sms/?t={settings.TWILIO_INCOMING_WEBHOOK_TOKEN}",
            data={
                "From": "+15559997777",
                "To": self.verified_sms_ep.address,
                "Body": "Hello",
            },
        )
        resp = sms_webhook(request)
        self.assertEqual(resp.status_code, 200)
        mock_ingest.assert_called_once()
