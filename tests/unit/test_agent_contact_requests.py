from unittest.mock import patch

from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.contrib.auth import get_user_model
from api.models import (
    BrowserUseAgent,
    CommsAllowlistEntry,
    CommsAllowlistRequest,
    CommsChannel,
    PersistentAgent,
    SmsContactPurpose,
)
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

    @patch("console.views.Analytics.track_event")
    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    def test_legacy_contact_requests_page_approves_sms_request(self, _mock_delay, _mock_track_event):
        self.client.force_login(self.owner)
        contact_request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15551234567",
            name="Ops Team",
            reason="Notify the team when action items are assigned.",
            purpose="Action item notifications",
            request_inbound=True,
            request_outbound=True,
            sms_contact_purpose=SmsContactPurpose.TEAM_OPERATIONAL,
            sms_contact_purpose_details="Internal operational notifications only.",
        )

        response = self.client.post(
            reverse("agent_contact_requests", kwargs={"pk": self.agent.pk}),
            data={
                f"approve_{contact_request.id}": "on",
                f"inbound_{contact_request.id}": "on",
                f"outbound_{contact_request.id}": "on",
                f"sms_permission_attested_{contact_request.id}": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("agent_contact_requests_thanks", kwargs={"pk": self.agent.pk}),
        )
        contact_request.refresh_from_db()
        self.assertEqual(contact_request.status, CommsAllowlistRequest.RequestStatus.APPROVED)
        self.assertTrue(contact_request.sms_contact_permission_attested)
        entry = CommsAllowlistEntry.objects.get(agent=self.agent, channel=CommsChannel.SMS)
        self.assertEqual(entry.address, "+15551234567")
        self.assertTrue(entry.sms_contact_permission_attested)
