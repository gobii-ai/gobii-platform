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
from dataclasses import replace
from datetime import timedelta, datetime, timezone as datetime_timezone
from unittest.mock import patch, MagicMock

from config.stripe_config import get_stripe_settings
import stripe
from constants.stripe import (
    ORG_OVERAGE_STATE_META_KEY,
    ORG_OVERAGE_STATE_DETACHED_PENDING,
)


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

    @tag("batch_organizations")
    def test_second_invite_blocked_when_only_one_seat_available(self):
        """With 1 purchased seat (beyond founder), only one pending invite should be allowed."""
        # Setup: 1 seat purchased beyond founder allowance
        billing = self.org.billing
        billing.purchased_seats = 1
        billing.save(update_fields=["purchased_seats"])

        self.client.force_login(self.inviter)
        detail_url = reverse("organization_detail", kwargs={"org_id": self.org.id})

        # First invite should succeed
        email1 = "first@example.com"
        resp1 = self.client.post(detail_url, {"email": email1, "role": OrganizationMembership.OrgRole.MEMBER})
        self.assertEqual(resp1.status_code, 302)
        self.assertTrue(OrganizationInvite.objects.filter(org=self.org, email__iexact=email1).exists())

        # Second invite (different email) should be blocked due to seats_reserved including pending invite
        email2 = "second@example.com"
        resp2 = self.client.post(detail_url, {"email": email2, "role": OrganizationMembership.OrgRole.MEMBER})
        self.assertEqual(resp2.status_code, 200)
        form = resp2.context.get("invite_form")
        self.assertIsNotNone(form)
        # Non-field error should indicate no seats available
        self.assertTrue(any("No seats available" in e for e in form.non_field_errors()))
        self.assertFalse(OrganizationInvite.objects.filter(org=self.org, email__iexact=email2).exists())

    @tag("batch_organizations")
    def test_service_partner_invite_does_not_require_seat(self):
        service_partner = get_user_model().objects.create_user(
            email="sp-inviter@example.com",
            password="pw",
            username="sp-inviter",
        )
        OrganizationMembership.objects.create(
            org=self.org,
            user=service_partner,
            role=OrganizationMembership.OrgRole.SERVICE_PARTNER,
        )
        billing = self.org.billing
        billing.purchased_seats = 0
        billing.save(update_fields=["purchased_seats"])

        self.client.force_login(service_partner)
        detail_url = reverse("organization_detail", kwargs={"org_id": self.org.id})
        resp = self.client.post(
            detail_url,
            {"email": "sp-invitee@example.com", "role": OrganizationMembership.OrgRole.SERVICE_PARTNER},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            OrganizationInvite.objects.filter(
                org=self.org,
                email__iexact="sp-invitee@example.com",
                role=OrganizationMembership.OrgRole.SERVICE_PARTNER,
            ).exists()
        )

    @patch("config.stripe_config._load_from_database", return_value=None)
    @patch("console.views.stripe.checkout.Session.create")
    @patch("console.views.get_or_create_stripe_customer")
    def test_seat_checkout_redirects_to_stripe(self, mock_customer, mock_session, _load_from_db):
        mock_customer.return_value = MagicMock(id="cus_test")
        mock_session.return_value = MagicMock(url="https://stripe.test/checkout")

        self.client.force_login(self.inviter)
        billing = self.org.billing
        billing.purchased_seats = 0
        billing.save(update_fields=["purchased_seats"])

        url = reverse("organization_seat_checkout", kwargs={"org_id": self.org.id})
        stripe_settings = get_stripe_settings(force_reload=True)
        resp = self.client.post(url, {"seats": 1})

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout")
        mock_session.assert_called_once()
        _, kwargs = mock_session.call_args
        line_items = kwargs.get("line_items")
        self.assertIsNotNone(line_items)
        self.assertEqual(line_items[0]["price"], stripe_settings.org_team_price_id)
        self.assertEqual(line_items[0]["quantity"], 1)
        overage_price = stripe_settings.org_team_additional_task_price_id
        self.assertEqual(len(line_items), 1)

    def test_seat_checkout_requires_membership(self):
        stranger = get_user_model().objects.create_user(email="stranger@example.com", password="pw", username="stranger")
        self.client.force_login(stranger)
        url = reverse("organization_seat_checkout", kwargs={"org_id": self.org.id})
        resp = self.client.post(url, {"seats": 1})
        self.assertEqual(resp.status_code, 403)

    @tag("batch_organizations")
    @patch("console.views.stripe.billing_portal.Session.create")
    @patch("console.views.stripe.Subscription.retrieve")
    def test_seat_checkout_adds_to_existing_subscription(self, mock_retrieve, mock_portal_create):
        mock_retrieve.return_value = {
            "id": "sub_123",
            "items": {
                "data": [
                    {
                        "id": "si_123",
                        "quantity": 3,
                        "price": {
                            "id": "price_org_team",
                            "recurring": {"usage_type": "licensed"},
                        },
                    }
                ]
            },
            "metadata": {"foo": "bar"},
            "customer": "cus_123",
        }

        billing = self.org.billing
        billing.purchased_seats = 3
        billing.stripe_subscription_id = "sub_123"
        billing.save(update_fields=["purchased_seats", "stripe_subscription_id"])

        self.client.force_login(self.inviter)
        url = reverse("organization_seat_checkout", kwargs={"org_id": self.org.id})
        mock_portal_create.return_value = MagicMock(url="https://stripe.test/portal-update")

        resp = self.client.post(url, {"seats": 2})

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/portal-update")

        mock_retrieve.assert_called_once_with("sub_123", expand=["items.data.price"])
        mock_portal_create.assert_called_once()
        _, kwargs = mock_portal_create.call_args
        self.assertEqual(kwargs.get("customer"), "cus_123")
        flow_data = kwargs.get("flow_data")
        self.assertIsNotNone(flow_data)
        self.assertEqual(flow_data.get("type"), "subscription_update_confirm")
        sub_update = flow_data.get("subscription_update_confirm")
        self.assertIsNotNone(sub_update)
        self.assertEqual(sub_update.get("subscription"), "sub_123")
        items = sub_update.get("items")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "si_123")
        self.assertEqual(items[0]["quantity"], 5)

        session_data = self.client.session.get("org_seat_portal_target")
        self.assertIsNotNone(session_data)
        self.assertEqual(session_data.get("requested"), 5)

    @tag("batch_organizations")
    @tag("batch_organizations")
    @patch("console.views.stripe.billing_portal.Session.create")
    @patch("console.views.stripe.Subscription.retrieve")
    def test_seat_checkout_handles_missing_licensed_item(self, mock_retrieve, mock_portal_create):
        mock_retrieve.return_value = {
            "id": "sub_123",
            "items": {"data": [{"price": {"usage_type": "metered"}}]},
        }

        billing = self.org.billing
        billing.purchased_seats = 1
        billing.stripe_subscription_id = "sub_123"
        billing.save(update_fields=["purchased_seats", "stripe_subscription_id"])

        self.client.force_login(self.inviter)
        url = reverse("organization_seat_checkout", kwargs={"org_id": self.org.id})
        resp = self.client.post(url, {"seats": 1}, follow=True)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp,
            "We couldn&#x27;t find a seat item on the active subscription.",
            status_code=200,
        )
        mock_retrieve.assert_called_once()
        mock_portal_create.assert_not_called()

    @tag("batch_organizations")
    @tag("batch_organizations")
    @patch("console.views.stripe.billing_portal.Session.create")
    @patch("console.views.stripe.Subscription.retrieve")
    def test_seat_checkout_matches_price_id_when_usage_type_missing(self, mock_retrieve, mock_portal_create):
        base_settings = get_stripe_settings()
        custom_settings = replace(base_settings, org_team_price_id="price_org_team")

        mock_retrieve.return_value = {
            "id": "sub_456",
            "items": {
                "data": [
                    {
                        "id": "si_456",
                        "quantity": 1,
                        "price": {"id": "price_org_team"},
                    }
                ]
            },
            "metadata": {},
            "customer": "cus_456",
        }

        billing = self.org.billing
        billing.purchased_seats = 1
        billing.stripe_subscription_id = "sub_456"
        billing.save(update_fields=["purchased_seats", "stripe_subscription_id"])

        self.client.force_login(self.inviter)
        url = reverse("organization_seat_checkout", kwargs={"org_id": self.org.id})

        mock_portal_create.return_value = MagicMock(url="https://stripe.test/portal-update")

        with patch("console.views.get_stripe_settings", return_value=custom_settings):
            resp = self.client.post(url, {"seats": 2})

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/portal-update")

        mock_retrieve.assert_called_once_with("sub_456", expand=["items.data.price"])
        mock_portal_create.assert_called_once()
        _, kwargs = mock_portal_create.call_args
        flow_data = kwargs.get("flow_data")
        self.assertEqual(flow_data.get("type"), "subscription_update_confirm")
        sub_update = flow_data.get("subscription_update_confirm")
        self.assertEqual(sub_update.get("subscription"), "sub_456")
        items = sub_update.get("items")
        self.assertEqual(items[0]["id"], "si_456")
        self.assertEqual(items[0]["quantity"], 3)

    @tag("batch_organizations")
    @patch("console.views.stripe.Subscription.retrieve")
    @patch("console.views.stripe.billing_portal.Session.create")
    @patch("console.views.stripe.SubscriptionItem.delete")
    @patch("console.views.stripe.Subscription.modify")
    @patch("console.views.get_stripe_settings")
    def test_seat_checkout_portal_detach_allows_update(
        self,
        mock_get_settings,
        mock_modify,
        mock_delete,
        mock_portal_create,
        mock_retrieve,
    ):
        base_settings = get_stripe_settings()
        custom_settings = replace(
            base_settings,
            org_team_price_id="price_org_team",
            org_team_additional_task_price_id="price_overage",
        )
        mock_get_settings.return_value = custom_settings

        mock_portal_create.return_value = MagicMock(url="https://stripe.test/portal-update")

        mock_retrieve.return_value = {
            "id": "sub_999",
            "items": {
                "data": [
                    {
                        "id": "si_seats",
                        "quantity": 4,
                        "price": {
                            "id": "price_org_team",
                            "recurring": {"usage_type": "licensed"},
                        },
                    },
                    {
                        "id": "si_overage",
                        "price": {
                            "id": "price_overage",
                            "recurring": {"usage_type": "metered"},
                        },
                    },
                ]
            },
            "metadata": {"foo": "bar"},
            "customer": "cus_999",
        }

        billing = self.org.billing
        billing.purchased_seats = 4
        billing.stripe_subscription_id = "sub_999"
        billing.save(update_fields=["purchased_seats", "stripe_subscription_id"])

        self.client.force_login(self.inviter)
        url = reverse("organization_seat_checkout", kwargs={"org_id": self.org.id})
        resp = self.client.post(url, {"seats": 3})

        mock_retrieve.assert_called_once_with("sub_999", expand=["items.data.price"])
        self.assertEqual(mock_portal_create.call_count, 1)
        mock_delete.assert_called_once_with("si_overage")
        mock_modify.assert_called_once()

        _, metadata_kwargs = mock_modify.call_args
        self.assertEqual(
            metadata_kwargs.get("metadata", {}).get(ORG_OVERAGE_STATE_META_KEY),
            ORG_OVERAGE_STATE_DETACHED_PENDING,
        )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/portal-update")

        session_data = self.client.session.get("org_seat_portal_target")
        self.assertIsNotNone(session_data)
        self.assertEqual(session_data.get("requested"), 7)

        detach_state = self.client.session.get("org_overage_detach", {}).get(str(self.org.id))
        self.assertIsNotNone(detach_state)

    @patch("console.views.stripe.Subscription.retrieve")
    @patch("console.views.stripe.billing_portal.Session.create")
    @patch("console.views.stripe.SubscriptionItem.create")
    @patch("console.views.stripe.SubscriptionItem.delete")
    @patch("console.views.stripe.Subscription.modify")
    @patch("console.views.get_stripe_settings")
    def test_seat_checkout_portal_failure_modifies(
        self,
        mock_get_settings,
        mock_modify,
        mock_delete,
        mock_item_create,
        mock_portal_create,
        mock_retrieve,
    ):
        base_settings = get_stripe_settings()
        custom_settings = replace(
            base_settings,
            org_team_price_id="price_org_team",
            org_team_additional_task_price_id="price_overage",
        )
        mock_get_settings.return_value = custom_settings

        mock_portal_create.side_effect = [
            stripe.error.InvalidRequestError(message="multiple items", param=None),
        ]

        initial_subscription = {
            "id": "sub_888",
            "items": {
                "data": [
                    {
                        "id": "si_seats",
                        "quantity": 2,
                        "price": {
                            "id": "price_org_team",
                            "recurring": {"usage_type": "licensed"},
                        },
                    },
                    {
                        "id": "si_overage",
                        "price": {
                            "id": "price_overage",
                            "recurring": {"usage_type": "metered"},
                        },
                    },
                ]
            },
            "metadata": {"foo": "bar"},
            "customer": "cus_888",
        }

        subscription_after_detach = {
            "id": "sub_888",
            "items": {
                "data": [
                    {
                        "id": "si_seats",
                        "quantity": 4,
                        "price": {
                            "id": "price_org_team",
                            "recurring": {"usage_type": "licensed"},
                        },
                    }
                ]
            },
            "metadata": {ORG_OVERAGE_STATE_META_KEY: ORG_OVERAGE_STATE_DETACHED_PENDING},
            "customer": "cus_888",
        }

        mock_retrieve.side_effect = [initial_subscription, subscription_after_detach]

        billing = self.org.billing
        billing.purchased_seats = 2
        billing.stripe_subscription_id = "sub_888"
        billing.save(update_fields=["purchased_seats", "stripe_subscription_id"])

        self.client.force_login(self.inviter)
        url = reverse("organization_seat_checkout", kwargs={"org_id": self.org.id})
        resp = self.client.post(url, {"seats": 2}, follow=True)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp,
            "Stripe portal seat updates are disabled, so we applied the seat change immediately.",
            status_code=200,
        )

        self.assertEqual(mock_retrieve.call_count, 2)
        mock_portal_create.assert_called_once()
        mock_delete.assert_called_once_with("si_overage")
        mock_item_create.assert_called_once_with(subscription="sub_888", price="price_overage")

        metadata_calls = [call.kwargs.get("metadata") for call in mock_modify.call_args_list if "metadata" in call.kwargs]
        self.assertTrue(any(meta and meta.get(ORG_OVERAGE_STATE_META_KEY) == ORG_OVERAGE_STATE_DETACHED_PENDING for meta in metadata_calls))
        self.assertTrue(any(meta is not None and meta.get(ORG_OVERAGE_STATE_META_KEY, "") == "" for meta in metadata_calls))

        session_data = self.client.session.get("org_seat_portal_target")
        self.assertIsNone(session_data)

    @patch("console.views._reattach_overage_from_session")
    @patch("console.views.PaymentsHelper.get_stripe_key")
    def test_billing_success_reattaches_overage(self, mock_get_key, mock_reattach):
        mock_get_key.return_value = "sk_test"
        mock_reattach.return_value = True

        self.client.force_login(self.inviter)
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(self.org.id)
        session["context_name"] = self.org.name
        session["org_seat_portal_target"] = {
            "org_id": str(self.org.id),
            "requested": 5,
        }
        session["org_overage_detach"] = {
            str(self.org.id): {
                "subscription_id": "sub_123",
                "price_id": "price_overage",
            }
        }
        session.save()

        resp = self.client.get(reverse("billing") + "?seats_success=1")

        self.assertEqual(resp.status_code, 200)
        mock_get_key.assert_called_once()
        mock_reattach.assert_called_once()
        self.assertEqual(mock_reattach.call_args[0][1], str(self.org.id))

        updated_session = self.client.session
        self.assertNotIn("org_seat_portal_target", updated_session)

    @patch("console.views._reattach_overage_from_session")
    @patch("console.views.PaymentsHelper.get_stripe_key")
    def test_billing_cancel_reattaches_overage(self, mock_get_key, mock_reattach):
        mock_get_key.return_value = "sk_test"
        mock_reattach.return_value = True

        self.client.force_login(self.inviter)
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(self.org.id)
        session["context_name"] = self.org.name
        session["org_seat_portal_target"] = {
            "org_id": str(self.org.id),
            "requested": 5,
        }
        session["org_overage_detach"] = {
            str(self.org.id): {
                "subscription_id": "sub_123",
                "price_id": "price_overage",
            }
        }
        session.save()

        resp = self.client.get(reverse("billing") + "?seats_cancelled=1")

        self.assertEqual(resp.status_code, 200)
        mock_get_key.assert_called_once()
        mock_reattach.assert_called_once()
        self.assertEqual(mock_reattach.call_args[0][1], str(self.org.id))

        updated_session = self.client.session
        self.assertNotIn("org_seat_portal_target", updated_session)

    @tag("batch_organizations")
    @patch("console.views.stripe.SubscriptionSchedule.modify")
    @patch("console.views.stripe.SubscriptionSchedule.release")
    @patch("console.views.stripe.SubscriptionSchedule.create")
    @patch("console.views.stripe.Subscription.retrieve")
    def test_seat_reduction_schedules_next_cycle(self, mock_retrieve, mock_schedule_create, mock_schedule_release, mock_schedule_modify):
        period_end = int((timezone.now() + timedelta(days=10)).timestamp())
        current_period_start = int((timezone.now() - timedelta(days=20)).timestamp())
        mock_retrieve.return_value = {
            "id": "sub_789",
            "current_period_end": period_end,
            "current_period_start": current_period_start,
            "items": {
                "data": [
                    {
                        "id": "si_seat",
                        "quantity": 5,
                        "price": {"id": "price_org_team", "usage_type": "licensed"},
                    },
                    {
                        "id": "si_tasks",
                        "price": {"id": "price_overage", "usage_type": "metered"},
                    },
                ]
            },
            "metadata": {},
            "schedule": None,
        }
        mock_schedule_create.return_value = MagicMock(id="ssch_new")

        billing = self.org.billing
        billing.purchased_seats = 5
        billing.stripe_subscription_id = "sub_789"
        billing.save(update_fields=["purchased_seats", "stripe_subscription_id"])

        custom_settings = replace(get_stripe_settings(), org_team_price_id="price_org_team")

        self.client.force_login(self.inviter)
        url = reverse("organization_seat_schedule", kwargs={"org_id": self.org.id})
        with patch("console.views.get_stripe_settings", return_value=custom_settings):
            resp = self.client.post(url, {"future_seats": 3}, follow=True)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Seat reduction scheduled", status_code=200)

        mock_schedule_release.assert_not_called()
        mock_schedule_create.assert_called_once()
        mock_schedule_modify.assert_called_once()

        _, create_kwargs = mock_schedule_create.call_args
        _, modify_kwargs = mock_schedule_modify.call_args
        self.assertEqual(create_kwargs.get("from_subscription"), "sub_789")
        self.assertEqual(modify_kwargs.get("end_behavior"), "release")
        phases = modify_kwargs.get("phases")
        self.assertIsNotNone(phases)
        self.assertEqual(phases[0]["items"][0]["quantity"], 5)
        self.assertEqual(phases[1]["items"][0]["quantity"], 3)
        self.assertEqual(phases[0]["start_date"], current_period_start)
        self.assertEqual(phases[0]["end_date"], period_end)
        self.assertEqual(phases[1]["start_date"], period_end)

        billing.refresh_from_db()
        self.assertEqual(billing.pending_seat_quantity, 3)
        self.assertEqual(billing.pending_seat_schedule_id, "ssch_new")
        expected_effective = datetime.fromtimestamp(period_end, tz=datetime_timezone.utc)
        self.assertEqual(billing.pending_seat_effective_at, expected_effective)

    @tag("batch_organizations")
    @patch("console.views.stripe.SubscriptionSchedule.modify")
    @patch("console.views.stripe.SubscriptionSchedule.release")
    @patch("console.views.stripe.SubscriptionSchedule.create")
    @patch("console.views.stripe.Subscription.retrieve")
    def test_seat_reduction_replaces_existing_schedule(self, mock_retrieve, mock_schedule_create, mock_schedule_release, mock_schedule_modify):
        period_end = int((timezone.now() + timedelta(days=5)).timestamp())
        current_period_start = int((timezone.now() - timedelta(days=10)).timestamp())
        mock_retrieve.return_value = {
            "id": "sub_sched",
            "current_period_end": period_end,
            "current_period_start": current_period_start,
            "items": {
                "data": [
                    {
                        "id": "si_seat",
                        "quantity": 4,
                        "price": {"id": "price_org_team", "usage_type": "licensed"},
                    }
                ]
            },
            "metadata": {},
            "schedule": "ssch_old",
        }
        mock_schedule_create.return_value = MagicMock(id="ssch_new")

        billing = self.org.billing
        billing.purchased_seats = 4
        billing.stripe_subscription_id = "sub_sched"
        billing.pending_seat_quantity = 2
        billing.pending_seat_effective_at = timezone.now()
        billing.pending_seat_schedule_id = "ssch_old"
        billing.save(
            update_fields=[
                "purchased_seats",
                "stripe_subscription_id",
                "pending_seat_quantity",
                "pending_seat_effective_at",
                "pending_seat_schedule_id",
            ]
        )

        custom_settings = replace(get_stripe_settings(), org_team_price_id="price_org_team")

        self.client.force_login(self.inviter)
        url = reverse("organization_seat_schedule", kwargs={"org_id": self.org.id})
        with patch("console.views.get_stripe_settings", return_value=custom_settings):
            resp = self.client.post(url, {"future_seats": 3}, follow=True)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Seat reduction scheduled", status_code=200)

        mock_schedule_release.assert_any_call("ssch_old")
        mock_schedule_create.assert_called_once()
        mock_schedule_modify.assert_called_once()

        billing.refresh_from_db()
        self.assertEqual(billing.pending_seat_quantity, 3)
        self.assertEqual(billing.pending_seat_schedule_id, "ssch_new")

    @tag("batch_organizations")
    @patch("console.views.stripe.SubscriptionSchedule.modify")
    @patch("console.views.stripe.SubscriptionSchedule.release")
    def test_cancel_pending_seat_reduction_releases_schedule(self, mock_schedule_release, mock_schedule_modify):
        billing = self.org.billing
        billing.pending_seat_quantity = 2
        billing.pending_seat_effective_at = timezone.now()
        billing.pending_seat_schedule_id = "ssch_cancel"
        billing.save(update_fields=[
            "pending_seat_quantity",
            "pending_seat_effective_at",
            "pending_seat_schedule_id",
        ])

        self.client.force_login(self.inviter)
        url = reverse("organization_seat_schedule_cancel", kwargs={"org_id": self.org.id})
        resp = self.client.post(url, follow=True)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Scheduled seat changes were cancelled", status_code=200)

        mock_schedule_release.assert_called_once_with("ssch_cancel")
        mock_schedule_modify.assert_not_called()

        billing.refresh_from_db()
        self.assertIsNone(billing.pending_seat_quantity)
        self.assertIsNone(billing.pending_seat_effective_at)
        self.assertEqual(billing.pending_seat_schedule_id, "")

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
        self.service_partner = User.objects.create_user(
            email="servicepartner@example.com",
            password="pw",
            username="servicepartner",
        )
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
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.service_partner,
            role=OrganizationMembership.OrgRole.SERVICE_PARTNER,
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

    @tag("batch_organizations")
    def test_service_partner_can_invite_members(self):
        detail_url = reverse("organization_detail", kwargs={"org_id": self.org.id})
        self.client.force_login(self.service_partner)
        resp = self.client.post(
            detail_url,
            {"email": "member-invite@example.com", "role": OrganizationMembership.OrgRole.MEMBER},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            OrganizationInvite.objects.filter(
                org=self.org,
                email__iexact="member-invite@example.com",
                role=OrganizationMembership.OrgRole.MEMBER,
            ).exists()
        )

    @tag("batch_organizations")
    @patch("console.views.stripe.billing_portal.Session.create")
    def test_service_partner_can_manage_billing(self, mock_portal_create):
        billing = self.org.billing
        billing.stripe_customer_id = "cus_test"
        billing.save(update_fields=["stripe_customer_id"])
        mock_portal_create.return_value = MagicMock(url="https://stripe.test/portal")

        self.client.force_login(self.service_partner)
        portal_url = reverse("organization_seat_portal", kwargs={"org_id": self.org.id})
        resp = self.client.post(portal_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/portal")

    @tag("batch_organizations")
    def test_admin_cannot_modify_or_assign_service_partner(self):
        role_service_partner_url = reverse(
            "org_member_role_update_org",
            kwargs={"org_id": self.org.id, "user_id": self.service_partner.id},
        )
        role_viewer_url = reverse(
            "org_member_role_update_org",
            kwargs={"org_id": self.org.id, "user_id": self.viewer.id},
        )

        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.post(
                role_service_partner_url,
                {"role": OrganizationMembership.OrgRole.MEMBER},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                role_viewer_url,
                {"role": OrganizationMembership.OrgRole.SERVICE_PARTNER},
            ).status_code,
            403,
        )
        membership = OrganizationMembership.objects.get(org=self.org, user=self.service_partner)
        self.assertEqual(membership.role, OrganizationMembership.OrgRole.SERVICE_PARTNER)

    @tag("batch_organizations")
    def test_seats_reserved_excludes_service_partners(self):
        billing = self.org.billing
        billing.refresh_from_db()
        # Active non-service-partner members are owner, admin, and viewer; founder allowance reduces by one.
        self.assertEqual(billing.seats_reserved, 2)

    def test_org_owned_agent_requires_paid_seat(self):
        owner = self.owner
        seatless_org = Organization.objects.create(name="Seatless", slug="seatless", created_by=owner)
        OrganizationMembership.objects.create(
            org=seatless_org,
            user=owner,
            role=OrganizationMembership.OrgRole.OWNER,
        )

        browser = BrowserUseAgent.objects.create(user=owner, name="Seatless Browser")

        self.assertEqual(seatless_org.billing.purchased_seats, 0)

        with self.assertRaises(ValidationError):
            PersistentAgent.objects.create(
                user=owner,
                organization=seatless_org,
                name="Seatless Agent",
                charter="do things",
                browser_use_agent=browser,
            )

        agent = PersistentAgent(
            user=owner,
            organization=seatless_org,
            name="Seatless Agent 2",
            charter="do things",
            browser_use_agent=BrowserUseAgent.objects.create(user=owner, name="Seatless Browser 2"),
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
