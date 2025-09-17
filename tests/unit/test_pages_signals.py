from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.models import UserBilling
from constants.plans import PlanNamesChoices
from pages.signals import handle_subscription_event
from util.subscription_helper import mark_user_billing_with_plan as real_mark_user_billing_with_plan


User = get_user_model()


def _build_event_payload(*, status="active", invoice_id="in_123", usage_type="licensed"):
    return {
        "object": "subscription",
        "id": "sub_123",
        "latest_invoice": invoice_id,
        "items": {
            "data": [
                {
                    "plan": {"usage_type": usage_type},
                    "price": {"product": "prod_123"},
                }
            ]
        },
        "status": status,
    }


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
        sub.current_period_start = aware_start
        sub.current_period_end = aware_end
        sub.stripe_data = _build_event_payload()
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
