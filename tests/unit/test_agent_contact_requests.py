import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from api.models import (
    BrowserUseAgent,
    CommsAllowlistEntry,
    CommsAllowlistRequest,
    CommsChannel,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    SmsContactPurpose,
)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
@tag("batch_contact_requests")
class AgentContactRequestsFriendlyErrorsTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(email="owner@example.com", password="pw", username="owner")
        self.other = User.objects.create_user(email="other@example.com", password="pw", username="other")
        self.organization = Organization.objects.create(
            name="Contact Requests Org",
            slug="contact-requests-org",
            created_by=self.owner,
        )
        billing = self.organization.billing
        billing.purchased_seats = 1
        billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=self.organization,
            user=self.owner,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        self.browser = BrowserUseAgent.objects.create(user=self.owner, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            organization=self.organization,
            name="Test Agent",
            charter="c",
            browser_use_agent=self.browser,
        )

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

    def test_legacy_contact_requests_page_redirects_to_immersive_page(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("agent_contact_requests", kwargs={"pk": self.agent.pk}))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"/app/agents/{self.agent.pk}/contact-requests?context_type=organization&context_id={self.organization.id}",
        )

    def test_contact_requests_api_lists_pending_requests(self):
        self.client.force_login(self.owner)
        first_request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="ops@example.com",
            name="Ops",
            reason="Send operational status updates.",
            purpose="Status updates",
            request_inbound=True,
            request_outbound=True,
        )
        second_request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15551234567",
            name="Escalations",
            reason="Text urgent escalations.",
            purpose="Urgent notifications",
            request_inbound=False,
            request_outbound=True,
            sms_contact_purpose=SmsContactPurpose.TEAM_OPERATIONAL,
            sms_contact_purpose_details="Internal operational notifications only.",
        )

        response = self.client.get(
            reverse("console_agent_contact_requests_api", kwargs={"agent_id": self.agent.pk}),
        )

        self.assertEqual(response.status_code, 200, response.content.decode())
        payload = response.json()
        self.assertEqual(payload["count"], 2)
        self.assertEqual(
            payload["resolveApiUrl"],
            reverse("console_agent_contact_requests_resolve", kwargs={"agent_id": self.agent.pk}),
        )
        request_ids = {item["id"] for item in payload["requests"]}
        self.assertEqual(request_ids, {str(first_request.id), str(second_request.id)})
        sms_payload = next(item for item in payload["requests"] if item["id"] == str(second_request.id))
        self.assertEqual(sms_payload["channel"], CommsChannel.SMS)
        self.assertEqual(sms_payload["smsContactPurpose"], SmsContactPurpose.TEAM_OPERATIONAL)

    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    @patch("console.api_views.Analytics.track_event")
    def test_contact_requests_api_resolves_batch(self, _mock_track_event, _mock_delay):
        self.client.force_login(self.owner)
        approved_request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="approve@example.com",
            name="Ops Team",
            reason="Notify the team when action items are assigned.",
            purpose="Action item notifications",
            request_inbound=True,
            request_outbound=True,
        )
        email_request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="decline@example.com",
            name="Decline",
            reason="This one should be denied.",
            purpose="Testing batch decisions",
            request_inbound=True,
            request_outbound=True,
        )

        response = self.client.post(
            reverse("console_agent_contact_requests_resolve", kwargs={"agent_id": self.agent.pk}),
            data=json.dumps({
                "responses": [
                    {
                        "request_id": str(approved_request.id),
                        "decision": "approve",
                        "allow_inbound": True,
                        "allow_outbound": True,
                        "can_configure": False,
                    },
                    {
                        "request_id": str(email_request.id),
                        "decision": "decline",
                        "allow_inbound": True,
                        "allow_outbound": True,
                        "can_configure": False,
                    },
                ],
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content.decode())
        payload = response.json()
        self.assertEqual(payload["approved_count"], 1)
        self.assertEqual(payload["rejected_count"], 1)

        approved_request.refresh_from_db()
        email_request.refresh_from_db()
        self.assertEqual(approved_request.status, CommsAllowlistRequest.RequestStatus.APPROVED)
        self.assertEqual(email_request.status, CommsAllowlistRequest.RequestStatus.REJECTED)
        entry = CommsAllowlistEntry.objects.get(agent=self.agent, channel=CommsChannel.EMAIL)
        self.assertEqual(entry.address, "approve@example.com")
        self.assertEqual(payload["pending_action_requests"], [])
