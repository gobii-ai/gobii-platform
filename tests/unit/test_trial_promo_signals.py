from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.models import (
    TrialPromo,
    TrialPromoRedemption,
    TrialPromoRedemptionStatusChoices,
)
from api.services.trial_promos import TRIAL_PROMO_META_CREDIT_AMOUNT
from constants.plans import PlanNames
from pages.signals import handle_checkout_session_event, handle_subscription_event


User = get_user_model()


def _create_promo(code: str = "SIGNAL-PROMO") -> TrialPromo:
    promo = TrialPromo(
        name="Signal promo",
        plan=PlanNames.STARTUP,
        trial_days=14,
    )
    promo.set_code(code)
    promo.save()
    return promo


def _event(payload, event_type: str, event_id: str = "evt_trial_promo"):
    return SimpleNamespace(data={"object": payload}, type=event_type, id=event_id)


@tag("batch_pages_signals")
class TrialPromoCheckoutSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="trial-promo-signal",
            email="trial-promo-signal@example.com",
            password="pw",
        )
        self.promo = _create_promo()

    def test_checkout_session_expired_marks_redemption_expired(self):
        redemption = TrialPromoRedemption.objects.create(
            promo=self.promo,
            user=self.user,
            status=TrialPromoRedemptionStatusChoices.CHECKOUT_STARTED,
            event_id="trial-promo-expired",
            stripe_customer_id="cus_expired",
            stripe_checkout_session_id="cs_expired",
        )
        payload = {
            "object": "checkout.session",
            "id": "cs_expired",
            "customer": "cus_expired",
            "subscription": "sub_expired",
            "metadata": {"gobii_event_id": "trial-promo-expired", "flow_type": "trial"},
        }

        with patch("pages.signals.stripe_status", return_value=SimpleNamespace(enabled=True)), \
            patch("pages.signals.PaymentsHelper.get_stripe_key", return_value="sk_test"), \
            patch("pages.signals._clear_customer_checkout_context_if_matches", return_value=True):
            handle_checkout_session_event(_event(payload, "checkout.session.expired"))

        redemption.refresh_from_db()
        self.assertEqual(redemption.status, TrialPromoRedemptionStatusChoices.CHECKOUT_EXPIRED)
        self.assertEqual(redemption.stripe_subscription_id, "sub_expired")
        self.assertIsNotNone(redemption.checkout_expired_at)


@tag("batch_pages_signals")
class TrialPromoSubscriptionSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="trial-promo-subscription",
            email="trial-promo-subscription@example.com",
            password="pw",
        )
        self.promo = _create_promo(code="SUBSCRIPTION-PROMO")

    def test_subscription_trial_start_applies_promo_credit_override_and_links_redemption(self):
        TrialPromoRedemption.objects.create(
            promo=self.promo,
            user=self.user,
            status=TrialPromoRedemptionStatusChoices.CHECKOUT_COMPLETED,
            event_id="trial-promo-subscription-event",
            stripe_customer_id="cus_subscription",
            stripe_checkout_session_id="cs_subscription",
        )
        trial_start = timezone.make_aware(datetime(2026, 4, 27, 10, 0, 0), timezone=dt_timezone.utc)
        trial_end = timezone.make_aware(datetime(2026, 5, 11, 10, 0, 0), timezone=dt_timezone.utc)
        payload = {
            "object": "subscription",
            "id": "sub_subscription",
            "status": "trialing",
            "latest_invoice": None,
            "billing_reason": "subscription_create",
            "trial_start": trial_start,
            "trial_end": trial_end,
            "current_period_start": trial_start,
            "current_period_end": trial_end,
            "cancel_at": None,
            "cancel_at_period_end": False,
            "metadata": {
                "gobii_event_id": "trial-promo-subscription-event",
                TRIAL_PROMO_META_CREDIT_AMOUNT: "222.000",
            },
            "items": {
                "data": [
                    {
                        "plan": {"usage_type": "licensed"},
                        "price": {"id": "price_startup", "product": "prod_startup"},
                        "quantity": 1,
                    }
                ],
            },
        }
        sub = SimpleNamespace(
            id="sub_subscription",
            status="trialing",
            customer=SimpleNamespace(id="cus_subscription", subscriber=self.user),
            billing_reason="subscription_create",
            latest_invoice=None,
            stripe_data=payload,
            metadata=payload["metadata"],
        )
        plan = {"id": PlanNames.STARTUP, "monthly_task_credits": 1000}
        licensed_item = {"price": {"id": "price_startup", "product": "prod_startup"}}

        with patch("pages.signals.stripe_status", return_value=SimpleNamespace(enabled=True)), \
            patch("pages.signals.PaymentsHelper.get_stripe_key", return_value="sk_test"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.ensure_single_individual_subscription"), \
            patch("pages.signals.resolve_plan_from_subscription_data", return_value=(plan, None, licensed_item)), \
            patch("pages.signals.get_stripe_settings", return_value=SimpleNamespace()), \
            patch("pages.signals.AddonEntitlementService.sync_subscription_entitlements"), \
            patch("pages.signals.resume_owner_execution"), \
            patch("pages.signals.resume_signup_preview_agents_for_user_if_eligible"), \
            patch("pages.signals.TaskCreditService.grant_subscription_credits") as mock_grant, \
            patch("pages.signals.Analytics.identify"), \
            patch("pages.signals.Analytics.track_event"), \
            patch("pages.signals.capi"):
            handle_subscription_event(_event(payload, "customer.subscription.created"))

        mock_grant.assert_called_once()
        self.assertEqual(mock_grant.call_args.kwargs["credit_override"], Decimal("222.000"))
        self.assertTrue(mock_grant.call_args.kwargs["free_trial_start"])
        redemption = TrialPromoRedemption.objects.get(event_id="trial-promo-subscription-event")
        self.assertEqual(redemption.stripe_subscription_id, "sub_subscription")
