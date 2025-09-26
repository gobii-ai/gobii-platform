from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.models import UserBilling, Organization
from constants.plans import PlanNamesChoices
from pages.signals import handle_subscription_event
from util.subscription_helper import mark_user_billing_with_plan as real_mark_user_billing_with_plan


User = get_user_model()


def _build_event_payload(
    *,
    status="active",
    invoice_id="in_123",
    usage_type="licensed",
    quantity=1,
    billing_reason="subscription_update",
    product="prod_123",
):
    payload = {
        "object": "subscription",
        "id": "sub_123",
        "latest_invoice": invoice_id,
        "items": {
            "data": [
                {
                    "plan": {"usage_type": usage_type},
                    "price": {"product": product},
                    "quantity": quantity,
                }
            ]
        },
        "status": status,
        "cancel_at": None,
        "cancel_at_period_end": False,
        "current_period_start": None,
        "current_period_end": None,
    }

    if billing_reason is not None:
        payload["billing_reason"] = billing_reason

    return payload


def _build_djstripe_event(payload, event_type="customer.subscription.updated"):
    return SimpleNamespace(data={"object": payload}, type=event_type)


@tag("batch_pages")
class SubscriptionSignalTests(TestCase):
    maxDiff = None

    def setUp(self):
        self.user = User.objects.create_user(username="stripe-user", email="stripe@example.com", password="pw")
        self.billing = UserBilling.objects.get(user=self.user)
        self.billing.billing_cycle_anchor = 1
        self.billing.save(update_fields=["billing_cycle_anchor"])

    def _mock_subscription(self, current_period_day: int, *, subscriber=None):
        aware_start = timezone.make_aware(datetime(2025, 9, current_period_day, 8, 0, 0), timezone=dt_timezone.utc)
        aware_end = timezone.make_aware(datetime(2025, 10, current_period_day, 8, 0, 0), timezone=dt_timezone.utc)
        subscriber = subscriber or self.user
        sub = MagicMock()
        sub.status = "active"
        sub.id = "sub_123"
        sub.customer = SimpleNamespace(subscriber=subscriber)
        sub.billing_reason = None
        sub.stripe_data = _build_event_payload()
        sub.stripe_data['current_period_start'] = str(aware_start)
        sub.stripe_data['current_period_end'] = str(aware_end)
        return sub

    @tag("batch_pages")
    def test_subscription_anchor_updates_from_stripe(self):
        payload = _build_event_payload()
        event = _build_djstripe_event(payload)

        fresh_user = User.objects.get(pk=self.user.pk)
        sub = self._mock_subscription(current_period_day=17, subscriber=fresh_user)

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
            patch("pages.signals.TaskCreditService.grant_subscription_credits"), \
            patch("pages.signals.mark_user_billing_with_plan", wraps=real_mark_user_billing_with_plan) as mock_mark_plan, \
            patch("pages.signals.Analytics.identify") as mock_identify, \
            patch("pages.signals.Analytics.track_event") as mock_track_event, \
            patch("pages.signals.logger.exception") as mock_logger_exception:

            handle_subscription_event(event)

        self.user.refresh_from_db()
        updated_billing = self.user.billing
        self.assertEqual(updated_billing.billing_cycle_anchor, 17)

        mock_mark_plan.assert_called_once()
        _, kwargs = mock_mark_plan.call_args
        call_user = mock_mark_plan.call_args[0][0]
        self.assertEqual(call_user.pk, self.user.pk)
        self.assertFalse(kwargs.get("update_anchor", True))
        mock_identify.assert_called_once()
        mock_track_event.assert_called_once()
        mock_logger_exception.assert_not_called()

    @tag("batch_pages")
    def test_missing_user_billing_logs_exception(self):
        payload = _build_event_payload()
        event = _build_djstripe_event(payload)

        fresh_user = User.objects.get(pk=self.user.pk)
        sub = self._mock_subscription(current_period_day=20, subscriber=fresh_user)

        # Remove billing record to trigger DoesNotExist branch
        UserBilling.objects.filter(user=self.user).delete()
        self.user.__dict__.pop("billing", None)

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
            patch("pages.signals.TaskCreditService.grant_subscription_credits"), \
            patch("pages.signals.mark_user_billing_with_plan"), \
            patch("pages.signals.Analytics.identify"), \
            patch("pages.signals.Analytics.track_event"), \
            patch("pages.signals.logger.exception") as mock_logger:

            handle_subscription_event(event)

        mock_logger.assert_called_once()
        self.assertFalse(UserBilling.objects.filter(user=self.user).exists())


@tag("batch_pages")
class SubscriptionSignalOrganizationTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username="org-owner", email="org@example.com", password="pw")
        self.org = Organization.objects.create(name="Org", slug="org", created_by=owner)
        billing = self.org.billing
        billing.stripe_customer_id = "cus_org"
        billing.subscription = PlanNamesChoices.ORG_TEAM.value
        billing.save(update_fields=["stripe_customer_id", "subscription"])

    def _mock_subscription(self, *, quantity, billing_reason, payload_invoice="in_org"):
        aware_start = timezone.make_aware(datetime(2025, 9, 1, 0, 0, 0), timezone=dt_timezone.utc)
        aware_end = timezone.make_aware(datetime(2025, 10, 1, 0, 0, 0), timezone=dt_timezone.utc)
        sub = MagicMock()
        sub.status = "active"
        sub.id = "sub_org"
        sub.customer = SimpleNamespace(id="cus_org", subscriber=None)
        sub.billing_reason = billing_reason
        payload = _build_event_payload(
            invoice_id=payload_invoice,
            quantity=quantity,
            billing_reason=billing_reason,
            product="prod_org",
        )
        sub.stripe_data = payload
        sub.stripe_data['current_period_start'] = aware_start
        sub.stripe_data['current_period_end'] = aware_end
        sub.stripe_data['cancel_at'] = None
        sub.stripe_data['cancel_at_period_end'] = False

        return sub, payload

    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_create_sets_seats_and_grants(self, mock_sync, mock_plan, mock_grant):
        sub, payload = self._mock_subscription(quantity=2, billing_reason=None)
        mock_sync.return_value = sub
        mock_plan.return_value = {"id": PlanNamesChoices.ORG_TEAM.value, "credits_per_seat": 500}
        event = _build_djstripe_event(payload, event_type="customer.subscription.created")

        invoice_payload = {
            "id": payload["latest_invoice"],
            "object": "invoice",
            "billing_reason": "subscription_create",
        }
        invoice_obj = SimpleNamespace(billing_reason="subscription_create", stripe_data=invoice_payload)

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.stripe.Invoice.retrieve", return_value=invoice_payload) as mock_invoice_retrieve, \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj) as mock_invoice_sync:

            handle_subscription_event(event)

        mock_invoice_retrieve.assert_called_once_with(payload["latest_invoice"])
        mock_invoice_sync.assert_called_once()

        billing = self.org.billing
        billing.refresh_from_db()
        self.assertEqual(billing.purchased_seats, 2)
        mock_grant.assert_called_once()
        _, kwargs = mock_grant.call_args
        self.assertEqual(kwargs.get("seats"), 2)
        self.assertEqual(kwargs.get("invoice_id"), invoice_payload["id"])

    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_create_with_existing_seats_grants_delta(self, mock_sync, mock_plan, mock_grant):
        billing = self.org.billing
        billing.purchased_seats = 3
        billing.save(update_fields=["purchased_seats"])

        sub, payload = self._mock_subscription(quantity=5, billing_reason=None, payload_invoice="in_seat_add")
        mock_sync.return_value = sub
        mock_plan.return_value = {"id": PlanNamesChoices.ORG_TEAM.value, "credits_per_seat": 500}
        event = _build_djstripe_event(payload, event_type="customer.subscription.created")

        invoice_payload = {
            "id": payload["latest_invoice"],
            "object": "invoice",
            "billing_reason": "subscription_create",
        }
        invoice_obj = SimpleNamespace(billing_reason="subscription_create", stripe_data=invoice_payload)

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.stripe.Invoice.retrieve", return_value=invoice_payload), \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj):

            handle_subscription_event(event)

        billing.refresh_from_db()
        self.assertEqual(billing.purchased_seats, 5)
        mock_grant.assert_called_once()
        _, kwargs = mock_grant.call_args
        self.assertEqual(kwargs.get("seats"), 2)
        self.assertEqual(kwargs.get("invoice_id"), "")

    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_update_grants_difference(self, mock_sync, mock_plan, mock_grant):
        billing = self.org.billing
        billing.purchased_seats = 2
        billing.save(update_fields=["purchased_seats"])

        sub, payload = self._mock_subscription(quantity=3, billing_reason=None, payload_invoice="in_upgrade")
        mock_sync.return_value = sub
        mock_plan.return_value = {"id": PlanNamesChoices.ORG_TEAM.value, "credits_per_seat": 500}
        event = _build_djstripe_event(payload)

        invoice_payload = {
            "id": payload["latest_invoice"],
            "object": "invoice",
            "billing_reason": "subscription_update",
        }
        invoice_obj = SimpleNamespace(billing_reason="subscription_update", stripe_data=invoice_payload)

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.stripe.Invoice.retrieve", return_value=invoice_payload) as mock_invoice_retrieve, \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj):

            handle_subscription_event(event)

        mock_invoice_retrieve.assert_called_once()

        billing.refresh_from_db()
        self.assertEqual(billing.purchased_seats, 3)
        mock_grant.assert_called_once()
        _, kwargs = mock_grant.call_args
        self.assertEqual(kwargs.get("seats"), 1)
        self.assertEqual(kwargs.get("invoice_id"), "")

    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_update_decrease_no_grant(self, mock_sync, mock_plan, mock_grant):
        billing = self.org.billing
        billing.purchased_seats = 3
        billing.save(update_fields=["purchased_seats"])

        sub, payload = self._mock_subscription(quantity=1, billing_reason=None, payload_invoice="in_downgrade")
        mock_sync.return_value = sub
        mock_plan.return_value = {"id": PlanNamesChoices.ORG_TEAM.value, "credits_per_seat": 500}
        event = _build_djstripe_event(payload)

        invoice_payload = {
            "id": payload["latest_invoice"],
            "object": "invoice",
            "billing_reason": "subscription_update",
        }
        invoice_obj = SimpleNamespace(billing_reason="subscription_update", stripe_data=invoice_payload)

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.stripe.Invoice.retrieve", return_value=invoice_payload), \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj):

            handle_subscription_event(event)

        billing.refresh_from_db()
        self.assertEqual(billing.purchased_seats, 1)
        mock_grant.assert_not_called()

    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_cycle_renews_with_replace_current(self, mock_sync, mock_plan, mock_grant):
        billing = self.org.billing
        billing.purchased_seats = 3
        billing.billing_cycle_anchor = 17
        billing.save(update_fields=["purchased_seats", "billing_cycle_anchor"])

        sub, payload = self._mock_subscription(quantity=3, billing_reason="subscription_cycle", payload_invoice="in_cycle")
        mock_sync.return_value = sub
        mock_plan.return_value = {"id": PlanNamesChoices.ORG_TEAM.value, "credits_per_seat": 500}
        event = _build_djstripe_event(payload)

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.stripe.Invoice.retrieve") as mock_invoice_retrieve, \
            patch("pages.signals.Invoice.sync_from_stripe_data") as mock_invoice_sync:

            handle_subscription_event(event)

        mock_invoice_retrieve.assert_not_called()
        mock_invoice_sync.assert_not_called()

        billing.refresh_from_db()
        self.assertEqual(billing.purchased_seats, 3)
        self.assertEqual(billing.billing_cycle_anchor, 1)

        mock_plan.assert_called_once()
        mock_grant.assert_called_once()
        call_args, call_kwargs = mock_grant.call_args
        self.assertEqual(call_args[0], self.org)
        self.assertEqual(call_kwargs.get("seats"), 3)
        self.assertEqual(call_kwargs.get("invoice_id"), payload["latest_invoice"])
        self.assertTrue(call_kwargs.get("replace_current"))
        self.assertIs(call_kwargs.get("subscription"), sub)
