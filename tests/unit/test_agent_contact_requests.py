import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext
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
from console.agent_chat.pending_actions import (
    CONTACT_REQUEST_PENDING_ACTION_PREVIEW_LIMIT,
    list_pending_action_requests,
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

    @patch("util.subscription_helper.get_user_max_contacts_per_agent", return_value=1)
    @patch("console.api_views.get_user_max_contacts_per_agent", return_value=1)
    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    @patch("console.api_views.Analytics.track_event")
    def test_contact_requests_api_partially_approves_until_contact_limit(
        self,
        _mock_track_event,
        _mock_delay,
        _mock_view_cap,
        _mock_model_cap,
    ):
        self.client.force_login(self.owner)
        first_request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="first@example.com",
            reason="Notify first.",
            purpose="Testing contact limits",
        )
        second_request = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="second@example.com",
            reason="Notify second.",
            purpose="Testing contact limits",
        )

        response = self.client.post(
            reverse("console_agent_contact_requests_resolve", kwargs={"agent_id": self.agent.pk}),
            data=json.dumps({
                "responses": [
                    {
                        "request_id": str(first_request.id),
                        "decision": "approve",
                        "allow_inbound": True,
                        "allow_outbound": True,
                        "can_configure": False,
                    },
                    {
                        "request_id": str(second_request.id),
                        "decision": "approve",
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
        self.assertEqual(payload["rejected_count"], 0)
        self.assertEqual(payload["skipped_count"], 1)
        self.assertEqual(len(payload["pending_action_requests"]), 1)
        self.assertEqual(payload["pending_action_requests"][0]["count"], 1)
        self.assertEqual(
            payload["pending_action_requests"][0]["requests"][0]["address"],
            "second@example.com",
        )

        first_request.refresh_from_db()
        second_request.refresh_from_db()
        self.assertEqual(first_request.status, CommsAllowlistRequest.RequestStatus.APPROVED)
        self.assertEqual(second_request.status, CommsAllowlistRequest.RequestStatus.PENDING)
        self.assertTrue(
            CommsAllowlistEntry.objects.filter(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address="first@example.com",
                is_active=True,
            ).exists()
        )
        self.assertFalse(
            CommsAllowlistEntry.objects.filter(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address="second@example.com",
                is_active=True,
            ).exists()
        )

    @patch("console.api_views.get_user_max_contacts_per_agent", return_value=100)
    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    @patch("console.api_views.Analytics.track_event")
    def test_contact_requests_api_batches_large_approval_work(
        self,
        _mock_track_event,
        _mock_delay,
        _mock_view_cap,
    ):
        self.client.force_login(self.owner)
        requests = [
            CommsAllowlistRequest.objects.create(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address=f"batch-{index}@example.com",
                reason="Notify this contact.",
                purpose="Testing batched approvals",
            )
            for index in range(30)
        ]

        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(
                reverse("console_agent_contact_requests_resolve", kwargs={"agent_id": self.agent.pk}),
                data=json.dumps({
                    "responses": [
                        {
                            "request_id": str(request_obj.id),
                            "decision": "approve",
                            "allow_inbound": True,
                            "allow_outbound": True,
                            "can_configure": False,
                        }
                        for request_obj in requests
                    ],
                }),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200, response.content.decode())
        self.assertLess(len(queries), 80)
        self.assertEqual(response.json()["approved_count"], 30)
        self.assertEqual(
            CommsAllowlistEntry.objects.filter(agent=self.agent, is_active=True).count(),
            30,
        )

    def test_pending_contact_actions_use_bounded_preview_with_full_count(self):
        for index in range(CONTACT_REQUEST_PENDING_ACTION_PREVIEW_LIMIT + 3):
            CommsAllowlistRequest.objects.create(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address=f"pending-{index}@example.com",
                reason="Needs approval.",
                purpose="Testing pending action payload size",
            )

        actions = list_pending_action_requests(self.agent, self.owner)
        contact_action = next(action for action in actions if action["kind"] == "contact_requests")

        self.assertEqual(contact_action["id"], "contact_requests")
        self.assertEqual(contact_action["count"], CONTACT_REQUEST_PENDING_ACTION_PREVIEW_LIMIT + 3)
        self.assertEqual(len(contact_action["requests"]), CONTACT_REQUEST_PENDING_ACTION_PREVIEW_LIMIT)
