from datetime import timedelta
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone
from waffle.testutils import override_flag

from api.agent.comms.outbound_delivery import deliver_agent_email
from api.agent.tools.email_sender import execute_send_email
from api.models import (
    AgentCollaborator,
    BrowserUseAgent,
    CommsAllowlistEntry,
    CommsChannel,
    DeliveryStatus,
    OutboundEmailReview,
    OutboundMessageAttempt,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    PersistentAgentUserActionEvent,
    UserPreference,
)
from api.services.outbound_email_policy import (
    classify_email_recipients,
    get_effective_email_sending_mode,
    set_workspace_email_sending_policy,
)
from api.services.outbound_email_review import (
    OutboundEmailReviewError,
    approve_review,
    compute_message_content_hash,
    expire_review_if_needed,
    queue_message_for_review,
    retry_review,
    update_pending_review_message,
)
from api.tasks.outbox import reconcile_approved_outbox_emails
from console.outbox_api_views import serialize_outbox_review
from constants.feature_flags import EMAIL_REVIEW_OUTBOX


User = get_user_model()


@tag("batch_outbox")
class EmailReviewOutboxTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="outbox-owner@example.com",
            email="outbox-owner@example.com",
            password="pw",
        )
        EmailAddress.objects.create(
            user=self.owner,
            email=self.owner.email,
            verified=True,
            primary=True,
        )
        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            browser_agent = BrowserUseAgent.objects.create(user=self.owner, name="Outbox browser")
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="Outbox agent",
            charter="Test review before send.",
            browser_use_agent=browser_agent,
            email_sending_mode=PersistentAgent.EmailSendingMode.REVIEW_ALL_EXTERNAL,
        )
        self.from_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="outbox-agent@example.test",
            is_primary=True,
        )

    def _message(self, recipient="external@example.com"):
        endpoint = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.EMAIL,
            address=recipient,
            defaults={"owner_agent": None},
        )[0]
        return PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.from_endpoint,
            to_endpoint=endpoint,
            is_outbound=True,
            body="<p>Hello</p>",
            raw_payload={"subject": "Review me"},
        )

    @override_flag(EMAIL_REVIEW_OUTBOX, active=True)
    @patch("django.db.close_old_connections")
    @patch("api.agent.tools.email_sender.deliver_agent_email")
    def test_external_send_is_queued_without_provider_or_attempt(self, deliver_mock, close_mock):
        result = execute_send_email(
            self.agent,
            {
                "to_address": "External.Person@Example.com",
                "subject": "Approval required",
                "mobile_first_html": "<p>Hello</p>",
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "pending_approval")
        self.assertEqual(result["delivery_status"], "not_sent")
        self.assertFalse(result["auto_sleep_ok"])
        message = PersistentAgentMessage.objects.get(pk=result["message_id"])
        review = OutboundEmailReview.objects.get(pk=result["outbox_item_id"])
        self.assertEqual(message.latest_status, DeliveryStatus.PENDING_APPROVAL)
        self.assertEqual(review.content_hash, compute_message_content_hash(message))
        self.assertFalse(OutboundMessageAttempt.objects.filter(message=message).exists())
        deliver_mock.assert_not_called()

    @override_flag(EMAIL_REVIEW_OUTBOX, active=True)
    @patch("django.db.close_old_connections")
    @patch("api.agent.tools.email_sender.deliver_agent_email")
    def test_verified_owner_sends_without_review(self, deliver_mock, close_mock):
        result = execute_send_email(
            self.agent,
            {
                "to_address": self.owner.email.upper(),
                "subject": "Internal",
                "mobile_first_html": "<p>Hello owner</p>",
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertFalse(OutboundEmailReview.objects.exists())
        deliver_mock.assert_called_once()

    @override_flag(EMAIL_REVIEW_OUTBOX, active=True)
    def test_collaborator_is_external_for_review_policy(self):
        collaborator = User.objects.create_user(
            username="collaborator@example.com",
            email="collaborator@example.com",
            password="pw",
        )
        EmailAddress.objects.create(user=collaborator, email=collaborator.email, verified=True)
        AgentCollaborator.objects.create(agent=self.agent, user=collaborator, invited_by=self.owner)

        decision = classify_email_recipients(self.agent, [collaborator.email])

        self.assertEqual(decision.external_recipients, (collaborator.email,))
        self.assertTrue(decision.requires_review)

    @override_flag(EMAIL_REVIEW_OUTBOX, active=True)
    def test_verified_org_member_is_internal_but_external_cc_requires_review(self):
        organization = Organization.objects.create(
            name="Outbox recipient org",
            slug="outbox-recipient-org",
            created_by=self.owner,
        )
        organization.billing.purchased_seats = 2
        organization.billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=organization,
            user=self.owner,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        member = User.objects.create_user(
            username="outbox-member@example.com",
            email="outbox-member@example.com",
            password="pw",
        )
        EmailAddress.objects.create(user=member, email=member.email, verified=True)
        OrganizationMembership.objects.create(
            org=organization,
            user=member,
            role=OrganizationMembership.OrgRole.MEMBER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self.agent.organization = organization
        self.agent.save(update_fields=["organization"])

        decision = classify_email_recipients(
            self.agent,
            [member.email.upper(), "outside@example.com"],
        )

        self.assertEqual(decision.internal_recipients, (member.email,))
        self.assertEqual(decision.external_recipients, ("outside@example.com",))
        self.assertTrue(decision.requires_review)

    @override_flag(EMAIL_REVIEW_OUTBOX, active=True)
    @patch("django.db.close_old_connections")
    @patch("api.agent.tools.email_sender.deliver_agent_email")
    def test_external_cc_queues_the_entire_email(self, deliver_mock, close_mock):
        result = execute_send_email(
            self.agent,
            {
                "to_address": self.owner.email,
                "cc_addresses": ["external-cc@example.com"],
                "subject": "Mixed recipients",
                "mobile_first_html": "<p>Hello everyone</p>",
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "pending_approval")
        self.assertEqual(result["delivery_status"], "not_sent")
        deliver_mock.assert_not_called()

    @override_flag(EMAIL_REVIEW_OUTBOX, active=True)
    def test_low_level_delivery_denies_unreviewed_external_message(self):
        message = self._message()

        deliver_agent_email(message)

        message.refresh_from_db()
        self.assertEqual(message.latest_status, DeliveryStatus.QUEUED)
        self.assertEqual(message.latest_error_code, "outbox_review_required")
        self.assertFalse(OutboundMessageAttempt.objects.filter(message=message).exists())

    @override_flag(EMAIL_REVIEW_OUTBOX, active=True)
    def test_low_level_delivery_denies_tampered_approved_message(self):
        message = self._message()
        review = queue_message_for_review(message)
        review.status = OutboundEmailReview.Status.APPROVED
        review.approved_version = review.content_version
        review.approved_content_hash = review.content_hash
        review.save(update_fields=["status", "approved_version", "approved_content_hash"])
        message.latest_status = DeliveryStatus.QUEUED
        message.body = "<p>Tampered</p>"
        message.save(update_fields=["latest_status", "body"])

        deliver_agent_email(message)

        message.refresh_from_db()
        review.refresh_from_db()
        self.assertEqual(message.latest_status, DeliveryStatus.FAILED)
        self.assertEqual(message.latest_error_code, "outbox_approval_invalid")
        self.assertFalse(OutboundMessageAttempt.objects.filter(message=message).exists())
        self.assertFalse(serialize_outbox_review(review)["allowedActions"]["retry"])

        with patch("api.tasks.outbox.dispatch_approved_outbox_email.delay") as delay_mock:
            self.assertEqual(reconcile_approved_outbox_emails(), 0)
        delay_mock.assert_not_called()

    @override_flag(EMAIL_REVIEW_OUTBOX, active=True)
    def test_low_level_delivery_denies_contact_revoked_after_approval(self):
        contact = CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="revoked@example.com",
            is_active=True,
            allow_outbound=True,
        )
        message = self._message(contact.address)
        review = queue_message_for_review(message)
        review.status = OutboundEmailReview.Status.APPROVED
        review.approved_version = review.content_version
        review.approved_content_hash = review.content_hash
        review.save(update_fields=["status", "approved_version", "approved_content_hash"])
        message.latest_status = DeliveryStatus.QUEUED
        message.save(update_fields=["latest_status"])
        contact.is_active = False
        contact.save(update_fields=["is_active"])

        deliver_agent_email(message)

        message.refresh_from_db()
        review.refresh_from_db()
        self.assertEqual(message.latest_status, DeliveryStatus.FAILED)
        self.assertEqual(message.latest_error_code, "outbox_contact_revoked")
        self.assertFalse(OutboundMessageAttempt.objects.filter(message=message).exists())
        self.assertTrue(serialize_outbox_review(review)["allowedActions"]["retry"])

        with patch("api.tasks.outbox.dispatch_approved_outbox_email.delay") as delay_mock:
            self.assertEqual(reconcile_approved_outbox_emails(), 0)
        delay_mock.assert_not_called()

        contact.is_active = True
        contact.save(update_fields=["is_active"])
        with patch("api.tasks.outbox.dispatch_approved_outbox_email.delay") as delay_mock:
            with self.captureOnCommitCallbacks(execute=True):
                retry_review(review, actor=self.owner)
        message.refresh_from_db()
        self.assertEqual(message.latest_status, DeliveryStatus.QUEUED)
        delay_mock.assert_called_once_with(str(review.id))

    def test_editing_primary_recipient_rethreads_and_clears_reply_parent(self):
        message = self._message("first@example.com")
        message.parent = self._message("first@example.com")
        message.save(update_fields=["parent"])
        review = queue_message_for_review(message)

        update_pending_review_message(
            review,
            actor=self.owner,
            expected_version=1,
            changes={"to": "second@example.com"},
        )

        message.refresh_from_db()
        self.assertIsNone(message.to_endpoint_id)
        self.assertEqual(message.conversation.address, "second@example.com")
        self.assertIsNone(message.parent_id)

    def test_expiry_records_manager_visible_action(self):
        review = queue_message_for_review(self._message())
        review.expires_at = timezone.now() - timedelta(seconds=1)
        review.save(update_fields=["expires_at"])

        self.assertTrue(expire_review_if_needed(review))

        self.assertTrue(
            PersistentAgentUserActionEvent.objects.filter(
                agent=self.agent,
                action_type=PersistentAgentUserActionEvent.ActionType.OUTBOX_EXPIRED,
                metadata__outboxItemId=str(review.id),
            ).exists()
        )

    @patch("api.tasks.outbox.dispatch_approved_outbox_email.delay")
    def test_approval_revalidates_recipient_address(self, delay_mock):
        review = queue_message_for_review(self._message("not-an-email"))

        with self.assertRaisesRegex(OutboundEmailReviewError, "not a valid email"):
            approve_review(review, actor=self.owner, expected_version=1)

        delay_mock.assert_not_called()

    def test_personal_default_apply_to_existing_and_effective_mode(self):
        set_workspace_email_sending_policy(
            user=self.owner,
            organization=None,
            default_mode=PersistentAgent.EmailSendingMode.REVIEW_NEW_CONTACTS,
            apply_to_existing=True,
        )

        self.agent.refresh_from_db()
        preferences = UserPreference.resolve_known_preferences(self.owner)
        self.assertEqual(
            preferences[UserPreference.KEY_DEFAULT_EMAIL_SENDING_MODE],
            PersistentAgent.EmailSendingMode.REVIEW_NEW_CONTACTS,
        )
        self.assertEqual(
            get_effective_email_sending_mode(self.agent),
            PersistentAgent.EmailSendingMode.REVIEW_NEW_CONTACTS,
        )

    @override_flag(EMAIL_REVIEW_OUTBOX, active=True)
    def test_review_new_contacts_and_automatic_modes_classify_deterministically(self):
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="known@example.com",
            is_active=True,
            allow_outbound=True,
        )
        self.agent.email_sending_mode = PersistentAgent.EmailSendingMode.REVIEW_NEW_CONTACTS
        self.agent.save(update_fields=["email_sending_mode"])

        self.assertFalse(classify_email_recipients(self.agent, ["known@example.com"]).requires_review)
        self.assertTrue(classify_email_recipients(self.agent, ["unknown@example.com"]).requires_review)

        self.agent.email_sending_mode = PersistentAgent.EmailSendingMode.SEND_AUTOMATICALLY
        self.agent.save(update_fields=["email_sending_mode"])
        self.assertFalse(classify_email_recipients(self.agent, ["unknown@example.com"]).requires_review)

    @patch("api.services.outbox_notifications.send_mail", return_value=1)
    def test_notification_cycle_sends_only_on_zero_to_one_transition(self, send_mail_mock):
        with self.captureOnCommitCallbacks(execute=True):
            queue_message_for_review(self._message("first-pending@example.com"))
        with self.captureOnCommitCallbacks(execute=True):
            queue_message_for_review(self._message("second-pending@example.com"))

        self.assertEqual(send_mail_mock.call_count, 1)

    def test_updating_org_default_does_not_clear_minimum(self):
        organization = Organization.objects.create(
            name="Outbox policy org",
            slug="outbox-policy-org",
            created_by=self.owner,
            org_settings={
                "default_email_sending_mode": PersistentAgent.EmailSendingMode.REVIEW_NEW_CONTACTS,
                "minimum_email_sending_mode": PersistentAgent.EmailSendingMode.REVIEW_ALL_EXTERNAL,
            },
        )
        OrganizationMembership.objects.create(
            org=organization,
            user=self.owner,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(organization.id)
        session.save()
        self.client.force_login(self.owner)

        response = self.client.patch(
            reverse("console_email_sending_policy"),
            data={"defaultMode": PersistentAgent.EmailSendingMode.SEND_AUTOMATICALLY},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content.decode())
        organization.refresh_from_db()
        self.assertEqual(
            organization.org_settings["minimum_email_sending_mode"],
            PersistentAgent.EmailSendingMode.REVIEW_ALL_EXTERNAL,
        )

    @override_flag(EMAIL_REVIEW_OUTBOX, active=True)
    @patch("api.tasks.outbox.dispatch_approved_outbox_email.delay")
    def test_outbox_api_uses_versions_and_creates_outbound_only_contact(self, delay_mock):
        message = self._message("new-contact@example.com")
        review = queue_message_for_review(message)
        self.client.force_login(self.owner)
        detail_url = reverse("console_outbox_detail", kwargs={"outbox_id": review.id})

        edit_response = self.client.patch(
            detail_url,
            data={"expectedVersion": 1, "subject": "Edited subject"},
            content_type="application/json",
        )
        self.assertEqual(edit_response.status_code, 200, edit_response.content.decode())
        self.assertEqual(edit_response.json()["item"]["version"], 2)

        stale_response = self.client.post(
            reverse("console_outbox_approve", kwargs={"outbox_id": review.id}),
            data={"expectedVersion": 1},
            content_type="application/json",
        )
        self.assertEqual(stale_response.status_code, 409, stale_response.content.decode())
        self.assertEqual(stale_response.json()["error"], "stale_version")

        approve_response = self.client.post(
            reverse("console_outbox_approve", kwargs={"outbox_id": review.id}),
            data={"expectedVersion": 2},
            content_type="application/json",
        )
        self.assertEqual(approve_response.status_code, 200, approve_response.content.decode())
        review.refresh_from_db()
        message.refresh_from_db()
        self.assertEqual(review.status, OutboundEmailReview.Status.APPROVED)
        self.assertEqual(message.latest_status, DeliveryStatus.QUEUED)
        contact = CommsAllowlistEntry.objects.get(agent=self.agent, address="new-contact@example.com")
        self.assertFalse(contact.allow_inbound)
        self.assertTrue(contact.allow_outbound)

    @override_flag(EMAIL_REVIEW_OUTBOX, active=True)
    def test_collaborator_cannot_access_outbox_api(self):
        collaborator = User.objects.create_user(
            username="outbox-collab@example.com",
            email="outbox-collab@example.com",
            password="pw",
        )
        AgentCollaborator.objects.create(agent=self.agent, user=collaborator, invited_by=self.owner)
        review = queue_message_for_review(self._message())
        self.client.force_login(collaborator)

        response = self.client.get(reverse("console_outbox_detail", kwargs={"outbox_id": review.id}))

        self.assertIn(response.status_code, {403, 404})
