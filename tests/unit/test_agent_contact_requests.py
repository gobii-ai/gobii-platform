from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.contrib.auth import get_user_model
from api.models import PersistentAgent, BrowserUseAgent, CommsAllowlistRequest, CommsAllowlistEntry
import uuid


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
@tag("batch_contact_requests")
class AgentContactRequestsFriendlyErrorsTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(email="owner@example.com", password="pw", username="owner")
        self.other = User.objects.create_user(email="other@example.com", password="pw", username="other")

        self.browser = BrowserUseAgent.objects.create(user=self.owner, name="BA")
        self.agent = PersistentAgent.objects.create(user=self.owner, name="Test Agent", charter="c", browser_use_agent=self.browser)

    def test_wrong_account_shows_friendly_page(self):
        self.client.force_login(self.other)
        url = reverse("agent_contact_requests", kwargs={"pk": self.agent.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("not associated with", resp.content.decode().lower())

    def test_invalid_agent_shows_friendly_page(self):
        self.client.force_login(self.owner)
        bad_id = uuid.uuid4()
        url = reverse("agent_contact_requests", kwargs={"pk": bad_id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("invalid", resp.content.decode().lower())


@tag("batch_contact_requests")
class ContactRequestTokenApproveTest(TestCase):
    """Tests for the one-click token-based approve/deny views."""

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(email="owner@example.com", password="pw", username="owner")
        self.browser = BrowserUseAgent.objects.create(user=self.owner, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.owner, name="Test Agent", charter="c", browser_use_agent=self.browser
        )
        self.contact_request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel="email",
            address="contact@example.com",
            name="Test Contact",
            reason="Need to discuss project",
            purpose="Schedule meeting",
        )

    def test_approve_get_shows_confirmation(self):
        url = reverse("contact_request_approve", kwargs={"token": self.contact_request.approval_token})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Approve", resp.content.decode())
        self.assertIn("Test Contact", resp.content.decode())

    def test_deny_get_shows_confirmation(self):
        url = reverse("contact_request_deny", kwargs={"token": self.contact_request.denial_token})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Deny", resp.content.decode())

    def test_approve_post_creates_allowlist_entry(self):
        url = reverse("contact_request_approve", kwargs={"token": self.contact_request.approval_token})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 200)
        self.contact_request.refresh_from_db()
        self.assertEqual(self.contact_request.status, CommsAllowlistRequest.RequestStatus.APPROVED)
        self.assertTrue(
            CommsAllowlistEntry.objects.filter(agent=self.agent, address="contact@example.com").exists()
        )

    def test_deny_post_rejects_request(self):
        url = reverse("contact_request_deny", kwargs={"token": self.contact_request.denial_token})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 200)
        self.contact_request.refresh_from_db()
        self.assertEqual(self.contact_request.status, CommsAllowlistRequest.RequestStatus.REJECTED)

    def test_invalid_approve_token_shows_error(self):
        url = reverse("contact_request_approve", kwargs={"token": "bad-token"})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Invalid", resp.content.decode())

    def test_invalid_deny_token_shows_error(self):
        url = reverse("contact_request_deny", kwargs={"token": "bad-token"})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Invalid", resp.content.decode())

    def test_already_approved_shows_already_responded(self):
        self.contact_request.approve(invited_by=self.owner, skip_invitation=True)
        url = reverse("contact_request_approve", kwargs={"token": self.contact_request.approval_token})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Already Responded", resp.content.decode())

    def test_tokens_auto_generated(self):
        """Tokens must be populated on save."""
        self.assertIsNotNone(self.contact_request.approval_token)
        self.assertIsNotNone(self.contact_request.denial_token)
        self.assertNotEqual(self.contact_request.approval_token, self.contact_request.denial_token)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
@tag("batch_contact_requests")
class ContactRequestBulkTokenTest(TestCase):
    """Tests for the bulk approve-all/deny-all token views."""

    def setUp(self):
        from django.core import signing
        User = get_user_model()
        self.owner = User.objects.create_user(email="owner@example.com", password="pw", username="owner")
        self.browser = BrowserUseAgent.objects.create(user=self.owner, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.owner, name="Test Agent", charter="c", browser_use_agent=self.browser
        )
        self.cr1 = CommsAllowlistRequest.objects.create(
            agent=self.agent, channel="email", address="a@example.com",
            reason="r", purpose="p",
        )
        self.cr2 = CommsAllowlistRequest.objects.create(
            agent=self.agent, channel="email", address="b@example.com",
            reason="r", purpose="p",
        )
        request_ids = [self.cr1.pk, self.cr2.pk]
        self.approve_all_token = signing.dumps(
            {"agent_id": str(self.agent.pk), "request_ids": [str(r) for r in request_ids], "action": "approve_all"},
            salt="contact_request_bulk",
        )
        self.deny_all_token = signing.dumps(
            {"agent_id": str(self.agent.pk), "request_ids": [str(r) for r in request_ids], "action": "deny_all"},
            salt="contact_request_bulk",
        )

    def test_approve_all_creates_entries(self):
        url = reverse("contact_request_approve_all", kwargs={"token": self.approve_all_token})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.cr1.refresh_from_db()
        self.cr2.refresh_from_db()
        self.assertEqual(self.cr1.status, CommsAllowlistRequest.RequestStatus.APPROVED)
        self.assertEqual(self.cr2.status, CommsAllowlistRequest.RequestStatus.APPROVED)

    def test_deny_all_rejects_requests(self):
        url = reverse("contact_request_deny_all", kwargs={"token": self.deny_all_token})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.cr1.refresh_from_db()
        self.cr2.refresh_from_db()
        self.assertEqual(self.cr1.status, CommsAllowlistRequest.RequestStatus.REJECTED)
        self.assertEqual(self.cr2.status, CommsAllowlistRequest.RequestStatus.REJECTED)

    def test_invalid_bulk_token_shows_error(self):
        url = reverse("contact_request_approve_all", kwargs={"token": "tampered"})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Invalid", resp.content.decode())


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
@tag("batch_contact_requests")
class RequestContactPermissionEmailTest(TestCase):
    """Tests that the tool sends a styled notification email on request creation."""

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            email="owner@example.com", password="pw", username="owner"
        )
        self.browser = BrowserUseAgent.objects.create(user=self.owner, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.owner, name="MyAgent", charter="c", browser_use_agent=self.browser
        )

    def test_email_sent_on_new_request(self):
        from django.core import mail
        from api.agent.tools.request_contact_permission import execute_request_contact_permission

        params = {
            "contacts": [
                {
                    "channel": "email",
                    "address": "contact@example.com",
                    "name": "Jane Doe",
                    "reason": "Need help",
                    "purpose": "Schedule meeting",
                }
            ]
        }
        result = execute_request_contact_permission(self.agent, params)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result.get("email_sent"))
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn("owner@example.com", sent.to)
        # Email should contain approve/deny buttons (check HTML alternative)
        html_body = sent.alternatives[0][0]
        self.assertIn("Approve", html_body)
        self.assertIn("Deny", html_body)
        self.assertIn("/approve/", html_body)

    def test_no_email_when_already_allowed(self):
        from django.core import mail
        from api.agent.tools.request_contact_permission import execute_request_contact_permission

        CommsAllowlistEntry.objects.create(
            agent=self.agent, channel="email", address="contact@example.com", is_active=True
        )
        params = {
            "contacts": [
                {
                    "channel": "email",
                    "address": "contact@example.com",
                    "name": "Jane",
                    "reason": "r",
                    "purpose": "p",
                }
            ]
        }
        result = execute_request_contact_permission(self.agent, params)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(mail.outbox), 0)

