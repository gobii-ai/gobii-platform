from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.utils import timezone

from waffle.models import Flag

from api.models import (
    Organization,
    OrganizationMembership,
    OrganizationInvite,
    PersistentAgent,
    BrowserUseAgent,
)
from datetime import timedelta
from unittest.mock import patch, MagicMock

from config.stripe_config import get_stripe_settings


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
@tag("batch_organizations")
class OrganizationInvitesTest(TestCase):
    def setUp(self):
        # Enable organizations feature flag
        Flag.objects.update_or_create(name="organizations", defaults={"everyone": True})

        User = get_user_model()
        self.inviter = User.objects.create_user(email="owner@example.com", password="pw", username="owner")
        self.invitee_email = "invitee@example.com"
        self.invitee = User.objects.create_user(email=self.invitee_email, password="pw", username="invitee")

        # Create org and add inviter as owner
        self.org = Organization.objects.create(name="Acme", slug="acme", created_by=self.inviter)
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.inviter,
            role=OrganizationMembership.OrgRole.OWNER,
        )
        billing = self.org.billing
        billing.purchased_seats = 2
        billing.save(update_fields=["purchased_seats"])

    @tag("batch_organizations")
    def test_invite_email_and_accept_flow(self):
        # Inviter sends invite
        self.client.force_login(self.inviter)
        detail_url = reverse("organization_detail", kwargs={"org_id": self.org.id})

        resp = self.client.post(detail_url, {"email": self.invitee_email, "role": OrganizationMembership.OrgRole.MEMBER})
        self.assertEqual(resp.status_code, 302)

        # Email sent
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn(self.invitee_email, message.to)
        self.assertIn(self.org.name, message.subject)

        invite = OrganizationInvite.objects.get(org=self.org, email__iexact=self.invitee_email)

        # Accept link present in email body
        accept_url = reverse("org_invite_accept", kwargs={"token": invite.token})
        self.assertIn(accept_url, message.body)  # plain text body contains URL

        # Pending invites should be visible on organizations list for invitee
        self.client.force_login(self.invitee)
        orgs_url = reverse("organizations")
        resp = self.client.get(orgs_url)
        self.assertEqual(resp.status_code, 200)
        # Context var should include the invite
        pending = resp.context.get("pending_invites")
        self.assertIsNotNone(pending)
        self.assertEqual(list(pending), [invite])

        # Invitee accepts (GET supported for email link)
        resp = self.client.get(accept_url)
        self.assertEqual(resp.status_code, 302)

        # Membership created and invite marked accepted
        membership = OrganizationMembership.objects.get(org=self.org, user=self.invitee)
        self.assertEqual(membership.status, OrganizationMembership.OrgStatus.ACTIVE)
        self.assertEqual(membership.role, OrganizationMembership.OrgRole.MEMBER)

        invite.refresh_from_db()
        self.assertIsNotNone(invite.accepted_at)

    @tag("batch_organizations")
    def test_invite_blocked_when_no_seats_available(self):
        billing = self.org.billing
        billing.purchased_seats = 0
        billing.save(update_fields=["purchased_seats"])

        self.client.force_login(self.inviter)
        detail_url = reverse("organization_detail", kwargs={"org_id": self.org.id})
        resp = self.client.post(detail_url, {"email": self.invitee_email, "role": OrganizationMembership.OrgRole.MEMBER})

        self.assertEqual(resp.status_code, 200)
        form = resp.context.get("invite_form")
        self.assertIsNotNone(form)
        self.assertIn("No seats available", " ".join(form.non_field_errors()))
        self.assertFalse(OrganizationInvite.objects.filter(org=self.org, email__iexact=self.invitee_email).exists())

    @tag("batch_organizations")
    def test_invite_blocked_when_pending_invite_exists(self):
        self.client.force_login(self.inviter)
        detail_url = reverse("organization_detail", kwargs={"org_id": self.org.id})
        # First invite succeeds (seat reserved)
        self.client.post(detail_url, {"email": self.invitee_email, "role": OrganizationMembership.OrgRole.MEMBER})
        self.assertTrue(OrganizationInvite.objects.filter(org=self.org, email__iexact=self.invitee_email).exists())

        resp = self.client.post(detail_url, {"email": self.invitee_email, "role": OrganizationMembership.OrgRole.MEMBER})
        self.assertEqual(resp.status_code, 200)
        form = resp.context.get("invite_form")
        self.assertIn("already has a pending invitation", " ".join(form.errors.get("email", [])))
        self.assertEqual(OrganizationInvite.objects.filter(org=self.org, email__iexact=self.invitee_email).count(), 1)

    @patch("console.views.stripe.checkout.Session.create")
    @patch("console.views.get_or_create_stripe_customer")
    def test_seat_checkout_redirects_to_stripe(self, mock_customer, mock_session):
        mock_customer.return_value = MagicMock(id="cus_test")
        mock_session.return_value = MagicMock(url="https://stripe.test/checkout")

        self.client.force_login(self.inviter)
        billing = self.org.billing
        billing.purchased_seats = 0
        billing.save(update_fields=["purchased_seats"])

        url = reverse("organization_seat_checkout", kwargs={"org_id": self.org.id})
        resp = self.client.post(url, {"seats": 1})

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout")
        mock_session.assert_called_once()
        _, kwargs = mock_session.call_args
        line_items = kwargs.get("line_items")
        self.assertIsNotNone(line_items)
        stripe_settings = get_stripe_settings()
        self.assertEqual(line_items[0]["price"], stripe_settings.org_team_price_id)
        self.assertEqual(line_items[0]["quantity"], 1)
        self.assertEqual(
            line_items[1]["price"],
            stripe_settings.org_team_additional_task_price_id,
        )

    def test_seat_checkout_requires_membership(self):
        stranger = get_user_model().objects.create_user(email="stranger@example.com", password="pw", username="stranger")
        self.client.force_login(stranger)
        url = reverse("organization_seat_checkout", kwargs={"org_id": self.org.id})
        resp = self.client.post(url, {"seats": 1})
        self.assertEqual(resp.status_code, 403)

    @patch("console.views.stripe.checkout.Session.create")
    @patch("console.views.get_or_create_stripe_customer")
    def test_seat_checkout_blocks_existing_subscription(self, mock_customer, mock_session):
        mock_customer.return_value = MagicMock(id="cus_test")
        mock_session.return_value = MagicMock(url="https://stripe.test/checkout")

        billing = self.org.billing
        billing.purchased_seats = 2
        billing.stripe_subscription_id = "sub_123"
        billing.save(update_fields=["purchased_seats", "stripe_subscription_id"])

        self.client.force_login(self.inviter)
        url = reverse("organization_seat_checkout", kwargs={"org_id": self.org.id})
        resp = self.client.post(url, {"seats": 1}, follow=True)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Use the Stripe management button to adjust seats", status_code=200)
        mock_session.assert_not_called()

    @patch("console.views.stripe.billing_portal.Session.create")
    def test_seat_portal_redirects(self, mock_portal):
        mock_portal.return_value = MagicMock(url="https://stripe.test/portal")
        billing = self.org.billing
        billing.purchased_seats = 2
        billing.stripe_customer_id = "cus_portal"
        billing.stripe_subscription_id = "sub_portal"
        billing.save(update_fields=["purchased_seats", "stripe_customer_id", "stripe_subscription_id"])

        self.client.force_login(self.inviter)
        url = reverse("organization_seat_portal", kwargs={"org_id": self.org.id})
        resp = self.client.post(url)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/portal")
        mock_portal.assert_called_once()

    def test_seat_portal_requires_membership(self):
        stranger = get_user_model().objects.create_user(email="another@example.com", password="pw", username="another")
        self.client.force_login(stranger)
        url = reverse("organization_seat_portal", kwargs={"org_id": self.org.id})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 403)

    @tag("batch_organizations")
    def test_reject_flow(self):
        # Create another invite
        self.client.force_login(self.inviter)
        detail_url = reverse("organization_detail", kwargs={"org_id": self.org.id})
        resp = self.client.post(detail_url, {"email": self.invitee_email, "role": OrganizationMembership.OrgRole.VIEWER})
        self.assertEqual(resp.status_code, 302)
        invite = OrganizationInvite.objects.filter(org=self.org, email__iexact=self.invitee_email).latest("sent_at")

        # Invitee rejects
        self.client.force_login(self.invitee)
        reject_url = reverse("org_invite_reject", kwargs={"token": invite.token})
        resp = self.client.get(reject_url)
        self.assertEqual(resp.status_code, 302)

        invite.refresh_from_db()
        self.assertIsNotNone(invite.revoked_at)

        # No membership should be created/modified by rejection
        self.assertFalse(OrganizationMembership.objects.filter(org=self.org, user=self.invitee, role=OrganizationMembership.OrgRole.VIEWER).exists())

    @tag("batch_organizations")
    def test_org_detail_shows_pending_invites(self):
        # Owner creates an invite
        self.client.force_login(self.inviter)
        detail_url = reverse("organization_detail", kwargs={"org_id": self.org.id})
        resp = self.client.post(detail_url, {"email": self.invitee_email, "role": OrganizationMembership.OrgRole.MEMBER})
        self.assertEqual(resp.status_code, 302)

        invite = OrganizationInvite.objects.get(org=self.org, email__iexact=self.invitee_email)

        # Owner views org detail; pending invite should be present in context
        resp = self.client.get(detail_url)
        self.assertEqual(resp.status_code, 200)
        pending = resp.context.get("pending_invites")
        self.assertIsNotNone(pending)
        self.assertIn(invite, list(pending))

    @tag("batch_organizations")
    def test_revoke_and_resend_from_org_detail(self):
        # Owner creates invite
        self.client.force_login(self.inviter)
        detail_url = reverse("organization_detail", kwargs={"org_id": self.org.id})
        self.client.post(detail_url, {"email": self.invitee_email, "role": OrganizationMembership.OrgRole.MEMBER})
        invite = OrganizationInvite.objects.get(org=self.org, email__iexact=self.invitee_email)

        # Resend
        mail.outbox.clear()
        resend_url = reverse("org_invite_resend_org", kwargs={"org_id": self.org.id, "token": invite.token})
        resp = self.client.post(resend_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.invitee_email, mail.outbox[0].to)

        # Revoke
        revoke_url = reverse("org_invite_revoke_org", kwargs={"org_id": self.org.id, "token": invite.token})
        resp = self.client.post(revoke_url)
        self.assertEqual(resp.status_code, 302)
        invite.refresh_from_db()
        self.assertIsNotNone(invite.revoked_at)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
@tag("batch_organizations")
class OrganizationPermissionsAndGuardsTest(TestCase):
    def setUp(self):
        # Enable organizations feature flag
        Flag.objects.update_or_create(name="organizations", defaults={"everyone": True})

        User = get_user_model()
        self.owner = User.objects.create_user(email="owner2@example.com", password="pw", username="owner2")
        self.admin = User.objects.create_user(email="admin@example.com", password="pw", username="admin")
        self.viewer = User.objects.create_user(email="viewer@example.com", password="pw", username="viewer")
        self.removed_user = User.objects.create_user(email="removed@example.com", password="pw", username="removed")
        self.outsider = User.objects.create_user(email="outsider@example.com", password="pw", username="outsider")

        self.org = Organization.objects.create(name="Org", slug="org", created_by=self.owner)
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.owner,
            role=OrganizationMembership.OrgRole.OWNER,
        )
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.admin,
            role=OrganizationMembership.OrgRole.ADMIN,
        )
        billing = self.org.billing
        billing.purchased_seats = 5
        billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.viewer,
            role=OrganizationMembership.OrgRole.VIEWER,
        )
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.removed_user,
            role=OrganizationMembership.OrgRole.MEMBER,
            status=OrganizationMembership.OrgStatus.REMOVED,
        )

    @tag("batch_organizations")
    def test_org_detail_requires_active_membership(self):
        detail_url = reverse("organization_detail", kwargs={"org_id": self.org.id})

        # Non-member forbidden
        self.client.force_login(self.outsider)
        resp = self.client.get(detail_url)
        self.assertEqual(resp.status_code, 403)

        # Removed member forbidden
        self.client.force_login(self.removed_user)
        resp = self.client.get(detail_url)
        self.assertEqual(resp.status_code, 403)

    @tag("batch_organizations")
    def test_only_admin_or_owner_can_manage_invites(self):
        # Create a valid pending invite
        invite = OrganizationInvite.objects.create(
            org=self.org,
            email="invitee2@example.com",
            role=OrganizationMembership.OrgRole.MEMBER,
            token="tok-resend",
            expires_at=timezone.now() + timedelta(days=7),
            invited_by=self.owner,
        )

        resend_url = reverse("org_invite_resend_org", kwargs={"org_id": self.org.id, "token": invite.token})
        revoke_url = reverse("org_invite_revoke_org", kwargs={"org_id": self.org.id, "token": invite.token})

        # Viewer cannot manage invites
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(resend_url).status_code, 403)
        self.assertEqual(self.client.post(revoke_url).status_code, 403)

        # Non-member cannot manage invites
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(resend_url).status_code, 403)
        self.assertEqual(self.client.post(revoke_url).status_code, 403)

    @tag("batch_organizations")
    def test_only_admin_or_owner_can_remove_or_change_roles(self):
        remove_url = reverse("org_member_remove_org", kwargs={"org_id": self.org.id, "user_id": self.viewer.id})
        role_url = reverse("org_member_role_update_org", kwargs={"org_id": self.org.id, "user_id": self.viewer.id})

        # Non-member cannot act
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(remove_url).status_code, 403)
        self.assertEqual(self.client.post(role_url, {"role": OrganizationMembership.OrgRole.ADMIN}).status_code, 403)

        # Viewer cannot act
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(remove_url).status_code, 403)
        self.assertEqual(self.client.post(role_url, {"role": OrganizationMembership.OrgRole.ADMIN}).status_code, 403)

    @tag("batch_organizations")
    def test_admin_cannot_remove_owner(self):
        remove_owner_url = reverse("org_member_remove_org", kwargs={"org_id": self.org.id, "user_id": self.owner.id})
        self.client.force_login(self.admin)
        resp = self.client.post(remove_owner_url)
        self.assertEqual(resp.status_code, 403)

    @tag("batch_organizations")
    def test_last_owner_cannot_leave(self):
        leave_url = reverse("org_leave_org", kwargs={"org_id": self.org.id})
        self.client.force_login(self.owner)
        resp = self.client.post(leave_url)
        self.assertEqual(resp.status_code, 302)
        # Still active owner
        m = OrganizationMembership.objects.get(org=self.org, user=self.owner)
        self.assertEqual(m.status, OrganizationMembership.OrgStatus.ACTIVE)
        self.assertEqual(m.role, OrganizationMembership.OrgRole.OWNER)

    @tag("batch_organizations")
    def test_admin_cannot_assign_owner_or_modify_owner(self):
        # Admin cannot promote viewer to owner
        role_viewer_url = reverse("org_member_role_update_org", kwargs={"org_id": self.org.id, "user_id": self.viewer.id})
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.post(role_viewer_url, {"role": OrganizationMembership.OrgRole.OWNER}).status_code,
            403,
        )

        # Admin cannot modify owner's role
        role_owner_url = reverse("org_member_role_update_org", kwargs={"org_id": self.org.id, "user_id": self.owner.id})
        self.assertEqual(
            self.client.post(role_owner_url, {"role": OrganizationMembership.OrgRole.MEMBER}).status_code,
            403,
        )

    def test_prevent_demoting_last_owner(self):
        # Owner attempts to demote self when they are the only owner
        role_self_url = reverse("org_member_role_update_org", kwargs={"org_id": self.org.id, "user_id": self.owner.id})
        self.client.force_login(self.owner)
        resp = self.client.post(role_self_url, {"role": OrganizationMembership.OrgRole.ADMIN})
        self.assertEqual(resp.status_code, 302)
        # Role unchanged
        m = OrganizationMembership.objects.get(org=self.org, user=self.owner)
        self.assertEqual(m.role, OrganizationMembership.OrgRole.OWNER)

    def test_valid_role_update_succeeds(self):
        # Owner promotes viewer to admin
        role_url = reverse("org_member_role_update_org", kwargs={"org_id": self.org.id, "user_id": self.viewer.id})
        self.client.force_login(self.owner)
        resp = self.client.post(role_url, {"role": OrganizationMembership.OrgRole.ADMIN})
        self.assertEqual(resp.status_code, 302)
        m = OrganizationMembership.objects.get(org=self.org, user=self.viewer)
        self.assertEqual(m.role, OrganizationMembership.OrgRole.ADMIN)

    def test_org_owned_agent_requires_paid_seat(self):
        owner = self.owner
        seatless_org = Organization.objects.create(name="Seatless", slug="seatless", created_by=owner)
        OrganizationMembership.objects.create(
            org=seatless_org,
            user=owner,
            role=OrganizationMembership.OrgRole.OWNER,
        )

        browser = BrowserUseAgent.objects.create(user=owner, name="Seatless Browser")
        agent = PersistentAgent(
            user=owner,
            organization=seatless_org,
            name="Seatless Agent",
            charter="do things",
            browser_use_agent=browser,
        )

        with self.assertRaises(ValidationError):
            agent.full_clean()


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
@tag("batch_organizations")
class OrganizationInviteAcceptEdgeCasesTest(TestCase):
    def setUp(self):
        Flag.objects.update_or_create(name="organizations", defaults={"everyone": True})

        User = get_user_model()
        self.owner = User.objects.create_user(email="own@example.com", password="pw", username="own")
        self.invitee = User.objects.create_user(email="edge@example.com", password="pw", username="edge")
        self.other_user = User.objects.create_user(email="other@example.com", password="pw", username="other")

        self.org = Organization.objects.create(name="Edges", slug="edges", created_by=self.owner)
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.owner,
            role=OrganizationMembership.OrgRole.OWNER,
        )
        billing = self.org.billing
        billing.purchased_seats = 4
        billing.save(update_fields=["purchased_seats"])

    def _create_invite(self, email, role, expires_at=None, token="tok-accept"):
        return OrganizationInvite.objects.create(
            org=self.org,
            email=email,
            role=role,
            token=token,
            expires_at=expires_at or (timezone.now() + timedelta(days=7)),
            invited_by=self.owner,
        )

    def test_accept_reactivates_removed_membership_and_sets_role(self):
        # Create removed membership for invitee
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.invitee,
            role=OrganizationMembership.OrgRole.VIEWER,
            status=OrganizationMembership.OrgStatus.REMOVED,
        )
        invite = self._create_invite(self.invitee.email, OrganizationMembership.OrgRole.ADMIN, token="tok-reactivate")

        self.client.force_login(self.invitee)
        url = reverse("org_invite_accept", kwargs={"token": invite.token})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

        m = OrganizationMembership.objects.get(org=self.org, user=self.invitee)
        self.assertEqual(m.status, OrganizationMembership.OrgStatus.ACTIVE)
        self.assertEqual(m.role, OrganizationMembership.OrgRole.ADMIN)

    def test_accept_updates_existing_active_membership_role(self):
        # Existing active membership as VIEWER
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.invitee,
            role=OrganizationMembership.OrgRole.VIEWER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        invite = self._create_invite(self.invitee.email, OrganizationMembership.OrgRole.ADMIN, token="tok-update")

        self.client.force_login(self.invitee)
        url = reverse("org_invite_accept", kwargs={"token": invite.token})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

        m = OrganizationMembership.objects.get(org=self.org, user=self.invitee)
        self.assertEqual(m.role, OrganizationMembership.OrgRole.ADMIN)

    def test_accept_wrong_email_forbidden(self):
        invite = self._create_invite(self.invitee.email, OrganizationMembership.OrgRole.MEMBER, token="tok-wrong")

        self.client.force_login(self.other_user)
        url = reverse("org_invite_accept", kwargs={"token": invite.token})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("not associated", resp.content.decode().lower())

    def test_accept_expired_invite_shows_friendly_page_and_no_membership_created(self):
        expired_invite = self._create_invite(
            self.other_user.email,
            OrganizationMembership.OrgRole.MEMBER,
            expires_at=timezone.now() - timedelta(days=1),
            token="tok-expired",
        )

        self.client.force_login(self.other_user)
        url = reverse("org_invite_accept", kwargs={"token": expired_invite.token})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("expired", resp.content.decode().lower())
        self.assertFalse(
            OrganizationMembership.objects.filter(org=self.org, user=self.other_user).exists()
        )

    def test_accept_via_post_creates_membership(self):
        invite = self._create_invite(self.invitee.email, OrganizationMembership.OrgRole.MEMBER, token="tok-post")

        self.client.force_login(self.invitee)
        url = reverse("org_invite_accept", kwargs={"token": invite.token})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            OrganizationMembership.objects.filter(
                org=self.org, user=self.invitee, role=OrganizationMembership.OrgRole.MEMBER, status=OrganizationMembership.OrgStatus.ACTIVE
            ).exists()
        )

    def test_accept_invalid_token_shows_friendly_page(self):
        self.client.force_login(self.invitee)
        url = reverse("org_invite_accept", kwargs={"token": "nonexistent-token"})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("invalid", resp.content.decode().lower())
