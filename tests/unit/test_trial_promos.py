import re
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import stripe
from allauth.account.models import EmailAddress
from agents.services import PretrainedWorkerTemplateService
from django.contrib.auth import get_user_model
from django.core import mail
from django.http import HttpResponseRedirect
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from api.models import (
    PersistentAgentTemplate,
    TrialPromo,
    TrialPromoActivationModeChoices,
    TrialPromoAllowedEmail,
    TrialPromoDiscountStateChoices,
    TrialPromoNoPaymentMethodEndBehaviorChoices,
    TrialPromoRedemption,
    TrialPromoRedemptionStatusChoices,
    UserTrialEligibility,
    UserTrialEligibilityManualActionChoices,
)
from api.admin_forms import TrialPromoAdminForm
from api.services.direct_trial_promos import (
    DirectTrialCoupon,
    _create_or_retrieve_schedule,
    activate_direct_trial_promo,
)
from api.services.persistent_agents import (
    PersistentAgentProvisioningError,
    PersistentAgentProvisioningService,
)
from api.services.trial_promos import (
    TRIAL_PROMO_META_CREDIT_AMOUNT,
    TRIAL_PROMO_META_ACTIVATION_MODE,
    TRIAL_PROMO_META_DISCOUNT_MONTHS,
    TRIAL_PROMO_META_ID,
    TRIAL_PROMO_META_PAYMENT_REQUIRED,
    TRIAL_PROMO_META_PLAN,
    TRIAL_PROMO_META_REDEMPTION_ID,
    TRIAL_PROMO_REDEMPTION_COUPON_ID_KEY,
    TRIAL_PROMO_REDEMPTION_DISCOUNT_MONTHS_KEY,
    TRIAL_PROMO_REASON_EMAIL_NOT_ALLOWLISTED,
    TRIAL_PROMO_REASON_EMAIL_NOT_VERIFIED,
    TrialPromoError,
    can_user_start_trial_promo,
    find_active_trial_promo_by_code,
    get_eligible_late_conversion_for_user,
    get_eligible_late_conversion_redemption,
    mark_trial_promo_redemption_from_checkout_session,
    mark_trial_promo_redemption_subscription,
    parse_trial_promo_credit_amount,
    reserve_direct_trial_promo_redemption,
    reserve_trial_promo_redemption,
)
from constants.plans import PlanNames
from constants.stripe import PERSONAL_CHECKOUT_PAYMENT_METHOD_TYPES


User = get_user_model()


def _create_promo(code: str = "CONF-ACCESS", **overrides) -> TrialPromo:
    promo = TrialPromo(
        name=overrides.pop("name", "Conference special"),
        plan=overrides.pop("plan", PlanNames.STARTUP),
        trial_days=overrides.pop("trial_days", 14),
        **overrides,
    )
    promo.set_code(code)
    promo.save()
    return promo


def _trial_promo_form_data(code: str, **overrides) -> dict[str, str]:
    data = {
        "name": "Conference special",
        "code": code,
        "plan": PlanNames.STARTUP,
        "activation_mode": TrialPromoActivationModeChoices.HOSTED_CHECKOUT,
        "trial_days": "14",
        "payment_method_required": "on",
        "no_payment_method_end_behavior": TrialPromoNoPaymentMethodEndBehaviorChoices.CREATE_INVOICE,
        "trial_abuse_filtering_enabled": "on",
        "discount_months": "3",
        "late_conversion_grace_days": "30",
        "is_active": "on",
        "headline": "",
        "description": "",
    }
    data.update(overrides)
    return data


@tag("batch_pages")
class TrialPromoServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="trial-promo-user",
            email="trial-promo@example.com",
            password="pw",
        )

    def test_find_active_trial_promo_by_code_normalizes_and_hides_digest(self):
        promo = _create_promo(code="GobiiConf")

        found = find_active_trial_promo_by_code("  gobiiconf  ")

        self.assertEqual(found, promo)
        self.assertEqual(promo.code_label, "GOBIICONF")
        self.assertNotEqual(promo.code_digest, "GOBIICONF")

    @patch("api.services.trial_promos.user_has_prior_individual_history", return_value=True)
    def test_repeat_trials_allowed_skips_same_user_prior_history(self, mock_prior_history):
        promo = _create_promo(
            code="REPEAT-OK",
            repeat_trials_allowed=True,
            trial_abuse_filtering_enabled=False,
        )

        decision = can_user_start_trial_promo(user=self.user, promo=promo)

        self.assertTrue(decision.allowed)
        mock_prior_history.assert_not_called()

    @patch("api.services.trial_promos.user_has_prior_individual_history", return_value=True)
    def test_repeat_trials_disabled_blocks_same_user_prior_history(self, mock_prior_history):
        promo = _create_promo(
            code="NO-REPEAT",
            repeat_trials_allowed=False,
            trial_abuse_filtering_enabled=False,
        )

        decision = can_user_start_trial_promo(user=self.user, promo=promo)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "prior_trial_or_subscription")
        mock_prior_history.assert_called_once_with(self.user)

    @patch("api.services.trial_promos.user_has_prior_individual_history", return_value=False)
    def test_failed_activation_subscription_can_be_excluded_from_prior_history(
        self,
        mock_prior_history,
    ):
        promo = _create_promo(
            code="FAILED-DIRECT-RETRY",
            repeat_trials_allowed=False,
            trial_abuse_filtering_enabled=False,
        )

        decision = can_user_start_trial_promo(
            user=self.user,
            promo=promo,
            excluded_subscription_ids={"sub_failed_partial"},
        )

        self.assertTrue(decision.allowed)
        mock_prior_history.assert_called_once_with(
            self.user,
            excluded_subscription_ids={"sub_failed_partial"},
        )

    def test_email_allowlist_blocks_user_not_on_campaign(self):
        promo = _create_promo(
            code="EMAIL-GATED",
            email_allowlist_enabled=True,
            repeat_trials_allowed=True,
            trial_abuse_filtering_enabled=False,
        )
        TrialPromoAllowedEmail.objects.create(
            promo=promo,
            normalized_email="someone-else@example.com",
        )

        decision = can_user_start_trial_promo(user=self.user, promo=promo)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, TRIAL_PROMO_REASON_EMAIL_NOT_ALLOWLISTED)

    def test_email_allowlist_allows_normalized_user_email(self):
        promo = _create_promo(
            code="EMAIL-OK",
            email_allowlist_enabled=True,
            repeat_trials_allowed=True,
            trial_abuse_filtering_enabled=False,
        )
        TrialPromoAllowedEmail.objects.create(
            promo=promo,
            normalized_email="TRIAL-PROMO@EXAMPLE.COM",
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )

        decision = can_user_start_trial_promo(user=self.user, promo=promo)

        self.assertTrue(decision.allowed)

    def test_email_allowlist_requires_matching_verified_email(self):
        promo = _create_promo(
            code="EMAIL-UNVERIFIED",
            email_allowlist_enabled=True,
            repeat_trials_allowed=True,
            trial_abuse_filtering_enabled=False,
        )
        TrialPromoAllowedEmail.objects.create(
            promo=promo,
            normalized_email=self.user.email,
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=False,
            primary=True,
        )

        decision = can_user_start_trial_promo(user=self.user, promo=promo)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, TRIAL_PROMO_REASON_EMAIL_NOT_VERIFIED)

    def test_email_allowlist_is_scoped_per_campaign(self):
        first_promo = _create_promo(
            code="FIRST-LIST",
            email_allowlist_enabled=True,
            repeat_trials_allowed=True,
            trial_abuse_filtering_enabled=False,
        )
        second_promo = _create_promo(
            code="SECOND-LIST",
            email_allowlist_enabled=True,
            repeat_trials_allowed=True,
            trial_abuse_filtering_enabled=False,
        )

        TrialPromoAllowedEmail.objects.create(
            promo=first_promo,
            normalized_email=self.user.email,
        )
        TrialPromoAllowedEmail.objects.create(
            promo=second_promo,
            normalized_email=self.user.email,
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )

        self.assertTrue(can_user_start_trial_promo(user=self.user, promo=first_promo).allowed)
        self.assertTrue(can_user_start_trial_promo(user=self.user, promo=second_promo).allowed)

    def test_manual_allow_does_not_bypass_email_allowlist(self):
        promo = _create_promo(
            code="MANUAL-NOT-LISTED",
            email_allowlist_enabled=True,
            repeat_trials_allowed=True,
            trial_abuse_filtering_enabled=False,
        )
        UserTrialEligibility.objects.create(
            user=self.user,
            manual_action=UserTrialEligibilityManualActionChoices.ALLOW_TRIAL,
        )

        decision = can_user_start_trial_promo(user=self.user, promo=promo)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, TRIAL_PROMO_REASON_EMAIL_NOT_ALLOWLISTED)

    def test_redemption_capacity_counts_completed_only(self):
        promo = _create_promo(code="CAP-ONE", max_redemptions=1)
        started_redemption = reserve_trial_promo_redemption(
            promo=promo,
            user=self.user,
            event_id="trial-promo-one",
            stripe_customer_id="cus_one",
        )
        second_user = User.objects.create_user(
            username="trial-promo-second",
            email="trial-promo-second@example.com",
            password="pw",
        )

        second_started_redemption = reserve_trial_promo_redemption(
            promo=promo,
            user=second_user,
            event_id="trial-promo-two",
            stripe_customer_id="cus_two",
        )

        self.assertEqual(started_redemption.status, TrialPromoRedemptionStatusChoices.CHECKOUT_STARTED)
        self.assertEqual(second_started_redemption.status, TrialPromoRedemptionStatusChoices.CHECKOUT_STARTED)

        started_redemption.status = TrialPromoRedemptionStatusChoices.CHECKOUT_COMPLETED
        started_redemption.save(update_fields=["status", "updated_at"])

        with self.assertRaises(TrialPromoError) as raised:
            reserve_trial_promo_redemption(
                promo=promo,
                user=second_user,
                event_id="trial-promo-three",
                stripe_customer_id="cus_three",
            )

        self.assertEqual(raised.exception.code, "capacity_reached")

    def test_user_can_retry_after_checkout_started_without_completion(self):
        promo = _create_promo(code="RETRY-STARTED", max_redemptions=1)
        reserve_trial_promo_redemption(
            promo=promo,
            user=self.user,
            event_id="trial-promo-started",
            stripe_customer_id="cus_started",
        )

        retry_redemption = reserve_trial_promo_redemption(
            promo=promo,
            user=self.user,
            event_id="trial-promo-retry",
            stripe_customer_id="cus_retry",
        )

        self.assertEqual(retry_redemption.status, TrialPromoRedemptionStatusChoices.CHECKOUT_STARTED)

    def test_pending_direct_activation_can_be_reserved_after_campaign_expires(self):
        promo = _create_promo(
            code="DIRECT-EXPIRED-RETRY",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
        )
        redemption = TrialPromoRedemption.objects.create(
            promo=promo,
            user=self.user,
            status=TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_PENDING,
            event_id="trial-promo-direct-expired-retry",
            stripe_customer_id="cus_expired_retry",
        )
        promo.is_active = False
        promo.active_until = timezone.now() - timedelta(days=1)
        promo.save()

        reserved, created = reserve_direct_trial_promo_redemption(
            promo=promo,
            user=self.user,
            stripe_customer_id="cus_new",
        )

        self.assertFalse(created)
        self.assertEqual(reserved.pk, redemption.pk)
        self.assertEqual(reserved.stripe_customer_id, "cus_expired_retry")

    def test_mark_redemption_from_checkout_session_sets_failed_timestamp(self):
        promo = _create_promo(code="FAILED-SESSION")
        redemption = reserve_trial_promo_redemption(
            promo=promo,
            user=self.user,
            event_id="trial-promo-failed",
            stripe_customer_id="cus_failed",
        )
        redemption.stripe_checkout_session_id = "cs_failed"
        redemption.save(update_fields=["stripe_checkout_session_id", "updated_at"])

        updated = mark_trial_promo_redemption_from_checkout_session(
            checkout_session_id="cs_failed",
            status=TrialPromoRedemptionStatusChoices.CHECKOUT_FAILED,
        )

        self.assertTrue(updated)
        redemption.refresh_from_db()
        self.assertEqual(redemption.status, TrialPromoRedemptionStatusChoices.CHECKOUT_FAILED)
        self.assertIsNotNone(redemption.checkout_failed_at)

    def test_parse_trial_promo_credit_amount_ignores_invalid_values(self):
        self.assertEqual(
            parse_trial_promo_credit_amount({TRIAL_PROMO_META_CREDIT_AMOUNT: "123.456"}),
            Decimal("123.456"),
        )
        self.assertIsNone(parse_trial_promo_credit_amount({TRIAL_PROMO_META_CREDIT_AMOUNT: "0"}))
        self.assertIsNone(parse_trial_promo_credit_amount({TRIAL_PROMO_META_CREDIT_AMOUNT: "not-a-number"}))

    def test_discount_phase_webhook_marks_campaign_discount_applied(self):
        promo = _create_promo(
            code="DISCOUNT-PHASE",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
        )
        redemption = TrialPromoRedemption.objects.create(
            promo=promo,
            user=self.user,
            status=TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_COMPLETED,
            event_id="direct-discount-phase",
            stripe_subscription_id="sub_discount_phase",
        )

        updated = mark_trial_promo_redemption_subscription(
            event_id=redemption.event_id,
            stripe_subscription_id="sub_discount_phase",
            discount_active=True,
        )

        self.assertTrue(updated)
        redemption.refresh_from_db()
        self.assertIsNotNone(redemption.discount_applied_at)
        self.assertEqual(
            redemption.discount_state,
            TrialPromoDiscountStateChoices.REDEEMED,
        )

    def test_inactive_campaign_preserves_earned_late_conversion(self):
        promo = _create_promo(
            code="INACTIVE-EARNED-CONVERSION",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
        )
        redemption = TrialPromoRedemption.objects.create(
            promo=promo,
            user=self.user,
            status=TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_COMPLETED,
            event_id="inactive-earned-conversion",
            discount_state=TrialPromoDiscountStateChoices.AVAILABLE,
            activated_at=timezone.now() - timedelta(days=15),
            late_conversion_expires_at=timezone.now() + timedelta(days=15),
        )
        promo.is_active = False
        promo.save(update_fields=["is_active", "updated_at"])

        self.assertEqual(
            get_eligible_late_conversion_redemption(
                promo=promo,
                user=self.user,
            ),
            redemption,
        )
        self.assertEqual(
            get_eligible_late_conversion_for_user(user=self.user),
            redemption,
        )


@tag("batch_pages")
@override_settings(TRIAL_PROMO_DIRECT_ACTIVATION_ENABLED=True)
class DirectTrialPromoServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="direct-trial-user",
            email="direct-trial@example.com",
            password="pw",
        )

    @patch("api.services.direct_trial_promos.TaskCreditService.grant_subscription_credits")
    @patch(
        "api.services.direct_trial_promos.reconcile_user_plan_from_stripe",
        return_value={
            "id": PlanNames.STARTUP,
            "monthly_task_credits": 1000,
        },
    )
    @patch("api.services.direct_trial_promos.Subscription.sync_from_stripe_data")
    @patch("api.services.direct_trial_promos.stripe.SubscriptionSchedule.modify")
    @patch("api.services.direct_trial_promos.stripe.SubscriptionSchedule.create")
    @patch("api.services.direct_trial_promos.stripe.Subscription.create")
    @patch("api.services.direct_trial_promos.stripe.Coupon.retrieve")
    @patch("api.services.direct_trial_promos.get_or_create_stripe_customer")
    def test_activation_creates_no_card_trial_and_delayed_discount_schedule(
        self,
        mock_customer,
        mock_coupon_retrieve,
        mock_subscription_create,
        mock_schedule_create,
        mock_schedule_modify,
        _mock_subscription_sync,
        _mock_reconcile,
        mock_grant_credits,
    ):
        promo = _create_promo(
            code="DIRECT-TRIAL",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
            payment_method_required=False,
            no_payment_method_end_behavior=TrialPromoNoPaymentMethodEndBehaviorChoices.CANCEL,
            conversion_coupon_id="coupon_three_months",
            discount_months=3,
            repeat_trials_allowed=True,
            trial_abuse_filtering_enabled=False,
        )
        mock_customer.return_value = SimpleNamespace(id="cus_direct")
        mock_coupon_retrieve.return_value = {
            "id": "coupon_three_months",
            "duration": "repeating",
            "duration_in_months": 3,
            "percent_off": 40,
            "valid": True,
        }
        mock_subscription_create.return_value = {
            "id": "sub_direct",
            "status": "trialing",
            "latest_invoice": "in_direct_trial",
            "trial_start": 1_800_000_000,
            "trial_end": 1_801_209_600,
            "current_period_start": 1_800_000_000,
            "trial_settings": {
                "end_behavior": {
                    "missing_payment_method": "cancel",
                },
            },
        }
        mock_schedule_create.return_value = {"id": "sub_sched_direct"}
        mock_schedule_modify.return_value = {
            "id": "sub_sched_direct",
            "end_behavior": "release",
            "phases": [
                {
                    "start_date": 1_800_000_000,
                    "end_date": 1_801_209_600,
                    "discounts": [],
                },
                {
                    "start_date": 1_801_209_600,
                    "end_date": 1_808_985_600,
                    "discounts": [{"coupon": "coupon_three_months"}],
                },
            ],
        }

        result = activate_direct_trial_promo(
            promo=promo,
            user=self.user,
            price_object=SimpleNamespace(
                recurring={"interval": "month", "interval_count": 1},
            ),
            price_id="price_pro_monthly",
        )

        subscription_kwargs = mock_subscription_create.call_args.kwargs
        self.assertEqual(subscription_kwargs["trial_period_days"], promo.trial_days)
        self.assertEqual(
            subscription_kwargs["trial_settings"]["end_behavior"]["missing_payment_method"],
            "cancel",
        )
        self.assertEqual(
            subscription_kwargs["payment_settings"]["save_default_payment_method"],
            "on_subscription",
        )
        self.assertNotIn("discounts", subscription_kwargs)

        schedule_kwargs = mock_schedule_modify.call_args.kwargs
        self.assertEqual(schedule_kwargs["end_behavior"], "release")
        self.assertEqual(schedule_kwargs["phases"][0]["discounts"], "")
        self.assertEqual(schedule_kwargs["phases"][1]["iterations"], 3)
        self.assertEqual(
            schedule_kwargs["phases"][1]["discounts"],
            [{"coupon": "coupon_three_months"}],
        )

        redemption = TrialPromoRedemption.objects.get(pk=result.redemption.pk)
        self.assertEqual(
            redemption.status,
            TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_COMPLETED,
        )
        self.assertEqual(redemption.stripe_subscription_id, "sub_direct")
        self.assertEqual(
            redemption.stripe_subscription_schedule_id,
            "sub_sched_direct",
        )
        self.assertIsNotNone(redemption.activated_at)
        self.assertIsNotNone(redemption.late_conversion_expires_at)
        self.assertEqual(
            redemption.discount_state,
            TrialPromoDiscountStateChoices.AVAILABLE,
        )
        mock_grant_credits.assert_called_once()
        self.assertEqual(
            mock_grant_credits.call_args.kwargs["invoice_id"],
            "in_direct_trial",
        )

        retry_result = activate_direct_trial_promo(
            promo=promo,
            user=self.user,
            price_object=SimpleNamespace(
                recurring={"interval": "month", "interval_count": 1},
            ),
            price_id="price_pro_monthly",
        )

        self.assertEqual(retry_result.redemption.pk, result.redemption.pk)
        mock_subscription_create.assert_called_once()
        mock_schedule_create.assert_called_once()
        mock_schedule_modify.assert_called_once()
        mock_grant_credits.assert_called_once()

    @patch("api.services.direct_trial_promos.stripe.SubscriptionSchedule.modify")
    @patch("api.services.direct_trial_promos.stripe.SubscriptionSchedule.retrieve")
    def test_interrupted_schedule_creation_finishes_phase_configuration(
        self,
        mock_schedule_retrieve,
        mock_schedule_modify,
    ):
        promo = _create_promo(
            code="DIRECT-SCHEDULE-REPAIR",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
        )
        redemption = TrialPromoRedemption.objects.create(
            promo=promo,
            user=self.user,
            status=TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_PENDING,
            event_id="direct-schedule-repair",
            stripe_customer_id="cus_schedule_repair",
            stripe_subscription_id="sub_schedule_repair",
            stripe_subscription_schedule_id="sub_sched_schedule_repair",
        )
        mock_schedule_retrieve.return_value = {
            "id": "sub_sched_schedule_repair",
            "end_behavior": "release",
            "phases": [
                {
                    "start_date": 1_800_000_000,
                    "end_date": 1_801_209_600,
                    "discounts": [],
                },
            ],
        }
        mock_schedule_modify.return_value = {
            "id": "sub_sched_schedule_repair",
            "end_behavior": "release",
        }
        coupon = DirectTrialCoupon(
            coupon_id="coupon_three_months",
            duration_in_months=3,
            percent_off=Decimal("40"),
            amount_off=None,
            currency="",
        )

        result = _create_or_retrieve_schedule(
            redemption=redemption,
            subscription={
                "id": "sub_schedule_repair",
                "trial_start": 1_800_000_000,
                "trial_end": 1_801_209_600,
            },
            items=[{"price": "price_pro_monthly", "quantity": 1}],
            coupon=coupon,
            metadata={"gobii_event_id": redemption.event_id},
        )

        self.assertEqual(result, mock_schedule_modify.return_value)
        mock_schedule_retrieve.assert_called_once_with(
            "sub_sched_schedule_repair",
            api_key=stripe.api_key,
        )
        modify_kwargs = mock_schedule_modify.call_args.kwargs
        self.assertEqual(modify_kwargs["phases"][1]["iterations"], 3)
        self.assertEqual(
            modify_kwargs["idempotency_key"],
            f"direct-trial-schedule-phases-{redemption.pk}",
        )

    @patch("api.services.direct_trial_promos.stripe.Subscription.delete")
    @patch("api.services.direct_trial_promos.stripe.SubscriptionSchedule.cancel")
    @patch(
        "api.services.direct_trial_promos.stripe.SubscriptionSchedule.modify",
        side_effect=stripe.error.APIError("schedule failed"),
    )
    @patch("api.services.direct_trial_promos.stripe.SubscriptionSchedule.create")
    @patch("api.services.direct_trial_promos.stripe.Subscription.create")
    @patch("api.services.direct_trial_promos.stripe.Coupon.retrieve")
    @patch("api.services.direct_trial_promos.get_or_create_stripe_customer")
    def test_schedule_failure_cancels_partial_activation(
        self,
        mock_customer,
        mock_coupon_retrieve,
        mock_subscription_create,
        mock_schedule_create,
        _mock_schedule_modify,
        mock_schedule_cancel,
        mock_subscription_delete,
    ):
        promo = _create_promo(
            code="DIRECT-ROLLBACK",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
            payment_method_required=False,
            no_payment_method_end_behavior=TrialPromoNoPaymentMethodEndBehaviorChoices.CANCEL,
            conversion_coupon_id="coupon_three_months",
            discount_months=3,
        )
        mock_customer.return_value = SimpleNamespace(id="cus_rollback")
        mock_coupon_retrieve.return_value = {
            "id": "coupon_three_months",
            "duration": "repeating",
            "duration_in_months": 3,
            "percent_off": 40,
            "valid": True,
        }
        mock_subscription_create.return_value = {
            "id": "sub_rollback",
            "status": "trialing",
            "trial_start": 1_800_000_000,
            "trial_end": 1_801_209_600,
            "current_period_start": 1_800_000_000,
            "trial_settings": {
                "end_behavior": {
                    "missing_payment_method": "cancel",
                },
            },
        }
        mock_schedule_create.return_value = {"id": "sub_sched_rollback"}

        with self.assertRaises(TrialPromoError) as raised:
            activate_direct_trial_promo(
                promo=promo,
                user=self.user,
                price_object=SimpleNamespace(
                    recurring={"interval": "month", "interval_count": 1},
                ),
                price_id="price_pro_monthly",
            )

        self.assertEqual(raised.exception.code, "stripe_activation_failed")
        mock_schedule_cancel.assert_called_once_with(
            "sub_sched_rollback",
            invoice_now=False,
            prorate=False,
            api_key=stripe.api_key,
        )
        mock_subscription_delete.assert_not_called()
        redemption = TrialPromoRedemption.objects.get(promo=promo, user=self.user)
        self.assertEqual(
            redemption.status,
            TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_FAILED,
        )

    @patch("api.services.direct_trial_promos._sync_direct_trial_entitlements")
    @patch("api.services.direct_trial_promos.stripe.SubscriptionSchedule.retrieve")
    @patch("api.services.direct_trial_promos.stripe.Subscription.retrieve")
    @patch("api.services.direct_trial_promos.stripe.SubscriptionSchedule.modify")
    @patch("api.services.direct_trial_promos.stripe.SubscriptionSchedule.create")
    @patch("api.services.direct_trial_promos.stripe.Subscription.create")
    @patch("api.services.direct_trial_promos.stripe.Coupon.retrieve")
    @patch("api.services.direct_trial_promos.get_or_create_stripe_customer")
    def test_interrupted_entitlement_sync_reuses_subscription_and_schedule(
        self,
        mock_customer,
        mock_coupon_retrieve,
        mock_subscription_create,
        mock_schedule_create,
        mock_schedule_modify,
        mock_subscription_retrieve,
        mock_schedule_retrieve,
        mock_sync_entitlements,
    ):
        promo = _create_promo(
            code="DIRECT-SYNC-RETRY",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
            payment_method_required=False,
            no_payment_method_end_behavior=TrialPromoNoPaymentMethodEndBehaviorChoices.CANCEL,
            conversion_coupon_id="coupon_three_months",
            discount_months=3,
        )
        subscription = {
            "id": "sub_sync_retry",
            "status": "trialing",
            "trial_start": 1_800_000_000,
            "trial_end": 1_801_209_600,
            "current_period_start": 1_800_000_000,
            "trial_settings": {
                "end_behavior": {
                    "missing_payment_method": "cancel",
                },
            },
        }
        schedule = {
            "id": "sub_sched_sync_retry",
            "end_behavior": "release",
            "phases": [
                {
                    "start_date": 1_800_000_000,
                    "end_date": 1_801_209_600,
                    "discounts": [],
                },
                {
                    "start_date": 1_801_209_600,
                    "end_date": 1_808_985_600,
                    "discounts": [{"coupon": "coupon_three_months"}],
                },
            ],
        }
        mock_customer.return_value = SimpleNamespace(id="cus_sync_retry")
        mock_coupon_retrieve.return_value = {
            "id": "coupon_three_months",
            "duration": "repeating",
            "duration_in_months": 3,
            "percent_off": 40,
            "valid": True,
        }
        mock_subscription_create.return_value = subscription
        mock_subscription_retrieve.return_value = subscription
        mock_schedule_create.return_value = {"id": "sub_sched_sync_retry"}
        mock_schedule_modify.return_value = schedule
        mock_schedule_retrieve.return_value = schedule
        mock_sync_entitlements.side_effect = [
            TrialPromoError("entitlement_sync_failed", "sync interrupted"),
            None,
        ]

        with self.assertRaisesMessage(TrialPromoError, "sync interrupted"):
            activate_direct_trial_promo(
                promo=promo,
                user=self.user,
                price_object=SimpleNamespace(
                    recurring={"interval": "month", "interval_count": 1},
                ),
                price_id="price_pro_monthly",
            )

        redemption = TrialPromoRedemption.objects.get(promo=promo, user=self.user)
        self.assertEqual(
            redemption.status,
            TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_PENDING,
        )

        promo.plan = PlanNames.SCALE
        promo.activation_mode = TrialPromoActivationModeChoices.HOSTED_CHECKOUT
        promo.trial_days = 45
        promo.trial_credit_amount = Decimal("999")
        promo.conversion_coupon_id = "coupon_six_months"
        promo.discount_months = 6
        promo.late_conversion_grace_days = 90
        promo.active_until = timezone.now() - timedelta(days=1)
        promo.is_active = False
        promo.save()

        result = activate_direct_trial_promo(
            promo=promo,
            user=self.user,
        )

        self.assertEqual(result.redemption.pk, redemption.pk)
        self.assertEqual(
            result.redemption.status,
            TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_COMPLETED,
        )
        mock_subscription_create.assert_called_once()
        mock_subscription_retrieve.assert_called_once()
        mock_schedule_create.assert_called_once()
        mock_schedule_modify.assert_called_once()
        mock_schedule_retrieve.assert_called_once()
        mock_customer.assert_called_once()
        mock_coupon_retrieve.assert_called_once_with(
            "coupon_three_months",
            api_key=stripe.api_key,
        )
        resumed_terms = mock_sync_entitlements.call_args_list[1].kwargs[
            "activation_terms"
        ]
        self.assertEqual(resumed_terms.plan, PlanNames.STARTUP)
        self.assertEqual(resumed_terms.price_id, "price_pro_monthly")
        self.assertEqual(resumed_terms.trial_days, 14)
        self.assertEqual(resumed_terms.coupon.coupon_id, "coupon_three_months")
        self.assertEqual(resumed_terms.coupon.duration_in_months, 3)
        completed_redemption = TrialPromoRedemption.objects.get(pk=redemption.pk)
        self.assertEqual(
            completed_redemption.late_conversion_expires_at,
            datetime.fromtimestamp(
                subscription["trial_end"],
                tz=datetime_timezone.utc,
            )
            + timedelta(days=30),
        )
        self.assertEqual(
            TrialPromoRedemption.objects.filter(promo=promo, user=self.user).count(),
            1,
        )

    @patch("api.services.direct_trial_promos.stripe.Coupon.retrieve")
    def test_activation_rejects_coupon_duration_mismatch(self, mock_coupon_retrieve):
        promo = _create_promo(
            code="DIRECT-MISMATCH",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
            payment_method_required=False,
            no_payment_method_end_behavior=TrialPromoNoPaymentMethodEndBehaviorChoices.CANCEL,
            conversion_coupon_id="coupon_six_months",
            discount_months=3,
        )
        mock_coupon_retrieve.return_value = {
            "id": "coupon_six_months",
            "duration": "repeating",
            "duration_in_months": 6,
            "valid": True,
        }

        with self.assertRaisesMessage(
            TrialPromoError,
            "does not match its configured discount duration",
        ):
            activate_direct_trial_promo(
                promo=promo,
                user=self.user,
                price_object=SimpleNamespace(
                    recurring={"interval": "month", "interval_count": 1},
                ),
                price_id="price_pro_monthly",
            )

    @patch("api.services.direct_trial_promos.stripe.Coupon.retrieve")
    def test_activation_rejects_coupon_restricted_to_another_product(
        self,
        mock_coupon_retrieve,
    ):
        promo = _create_promo(
            code="DIRECT-WRONG-PRODUCT",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
            payment_method_required=False,
            no_payment_method_end_behavior=TrialPromoNoPaymentMethodEndBehaviorChoices.CANCEL,
            conversion_coupon_id="coupon_other_product",
            discount_months=3,
        )
        mock_coupon_retrieve.return_value = {
            "id": "coupon_other_product",
            "duration": "repeating",
            "duration_in_months": 3,
            "percent_off": 40,
            "valid": True,
            "applies_to": {"products": ["prod_other"]},
        }

        with self.assertRaisesMessage(
            TrialPromoError,
            "not valid for its plan price",
        ):
            activate_direct_trial_promo(
                promo=promo,
                user=self.user,
                price_object=SimpleNamespace(
                    recurring={"interval": "month", "interval_count": 1},
                    product_id="prod_campaign",
                    currency="usd",
                ),
                price_id="price_pro_monthly",
            )

        self.assertFalse(
            TrialPromoRedemption.objects.filter(promo=promo, user=self.user).exists(),
        )


@tag("batch_pages")
class TrialPromoAdminFormTests(TestCase):
    def test_duplicate_code_is_form_error_before_save(self):
        _create_promo(code="DUPE-CODE")

        form = TrialPromoAdminForm(data=_trial_promo_form_data(" dupe-code "))

        self.assertFalse(form.is_valid())
        self.assertIn("code", form.errors)
        self.assertIn("already exists", form.errors["code"][0])

    def test_editing_promo_can_keep_same_code(self):
        promo = _create_promo(code="SAME-CODE")

        form = TrialPromoAdminForm(
            instance=promo,
            data=_trial_promo_form_data("same-code", name="Updated conference special"),
        )

        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save()
        self.assertEqual(updated.pk, promo.pk)
        self.assertEqual(updated.code_label, "SAME-CODE")
        self.assertEqual(updated.name, "Updated conference special")

    def test_bulk_allowed_emails_are_normalized_and_deduped(self):
        form = TrialPromoAdminForm(
            data=_trial_promo_form_data(
                "ALLOWLIST-CODE",
                email_allowlist_enabled="on",
                allowed_emails_bulk=" One@Example.com, two@example.com\none@example.com ",
            )
        )

        self.assertTrue(form.is_valid(), form.errors)
        promo = form.save()

        self.assertTrue(promo.email_allowlist_enabled)
        self.assertEqual(
            list(promo.allowed_emails.values_list("normalized_email", flat=True)),
            ["one@example.com", "two@example.com"],
        )

    def test_bulk_allowed_emails_save_after_admin_commit_false_lifecycle(self):
        promo = _create_promo(code="ALLOWLIST-EDIT")
        form = TrialPromoAdminForm(
            instance=promo,
            data=_trial_promo_form_data(
                "",
                email_allowlist_enabled="on",
                allowed_emails_bulk=" AdminUser@Example.com ",
            ),
        )

        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save(commit=False)
        updated.save()
        form.save_m2m()

        self.assertEqual(updated.code_label, "ALLOWLIST-EDIT")
        self.assertEqual(
            list(updated.allowed_emails.values_list("normalized_email", flat=True)),
            ["adminuser@example.com"],
        )

    def test_bulk_allowed_emails_reject_invalid_email(self):
        form = TrialPromoAdminForm(
            data=_trial_promo_form_data(
                "ALLOWLIST-BAD",
                email_allowlist_enabled="on",
                allowed_emails_bulk="valid@example.com not-an-email",
            )
        )

        self.assertFalse(form.is_valid())
        self.assertIn("allowed_emails_bulk", form.errors)

    def test_direct_trial_requires_safe_billing_and_unlisted_template(self):
        listed_template = PersistentAgentTemplate.objects.create(
            code="listed-campaign-template",
            display_name="Listed campaign template",
            tagline="Listed",
            description="Listed template",
            charter="Do the work.",
            is_listed=True,
        )
        form = TrialPromoAdminForm(
            data=_trial_promo_form_data(
                "DIRECT-INVALID",
                activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
                payment_method_required="on",
                no_payment_method_end_behavior=TrialPromoNoPaymentMethodEndBehaviorChoices.CREATE_INVOICE,
                conversion_coupon_id="",
                linked_template=str(listed_template.pk),
            )
        )

        self.assertFalse(form.is_valid())
        self.assertIn("payment_method_required", form.errors)
        self.assertIn("no_payment_method_end_behavior", form.errors)
        self.assertIn("conversion_coupon_id", form.errors)
        self.assertIn("linked_template", form.errors)

    def test_generated_campaign_link_opens_terms_page(self):
        from django.contrib.admin.sites import AdminSite

        from api.admin import TrialPromoAdmin

        promo = _create_promo(code="CAMPAIGN-LINK")

        rendered_url = str(TrialPromoAdmin(TrialPromo, AdminSite()).campaign_url(promo))

        self.assertIn(
            f"{reverse('pages:special_access')}?code={promo.code_label}",
            rendered_url,
        )
        self.assertNotIn(reverse("pages:special_access_start"), rendered_url)


@tag("batch_pages")
@override_settings(GOBII_PROPRIETARY_MODE=True)
class SpecialAccessCheckoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="special-access-user",
            email="special-access@example.com",
            password="pw",
        )

    def test_special_access_uses_model_plan_display_label(self):
        promo = _create_promo(code="SCALE-DISPLAY", plan=PlanNames.SCALE)

        response = self.client.post(reverse("pages:special_access"), {"code": "scale-display"}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["plan_label"], promo.get_plan_display())
        self.assertContains(response, "Scale")

    def test_anonymous_campaign_link_shows_terms_and_preserves_code_through_auth(self):
        promo = _create_promo(
            code="DIRECT-ANONYMOUS",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
            payment_method_required=False,
            no_payment_method_end_behavior=TrialPromoNoPaymentMethodEndBehaviorChoices.CANCEL,
            conversion_coupon_id="coupon_three_months",
        )

        response = self.client.get(
            reverse("pages:special_access"),
            {"code": promo.code_label},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("pages:special_access"))
        terms_response = self.client.get(response["Location"])
        self.assertEqual(terms_response.status_code, 200)
        self.assertContains(terms_response, f"{promo.discount_months} paid months")

        start_response = self.client.post(reverse("pages:special_access_start"))

        self.assertEqual(start_response.status_code, 302)
        login_url = urlsplit(start_response["Location"])
        next_url = parse_qs(login_url.query)["next"][0]
        self.assertEqual(next_url, reverse("pages:special_access"))
        self.assertEqual(
            self.client.session["special_access_trial_promo_id"],
            str(promo.pk),
        )

    @patch("pages.views._start_direct_trial_promo")
    def test_authenticated_campaign_get_cannot_activate_subscription(
        self,
        mock_start_direct_trial,
    ):
        promo = _create_promo(
            code="DIRECT-GET-SAFE",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
            payment_method_required=False,
            no_payment_method_end_behavior=TrialPromoNoPaymentMethodEndBehaviorChoices.CANCEL,
            conversion_coupon_id="coupon_three_months",
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("pages:special_access_start"),
            {"code": promo.code_label},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("pages:special_access"))
        mock_start_direct_trial.assert_not_called()

    @override_settings(TRIAL_PROMO_DIRECT_ACTIVATION_ENABLED=False)
    @patch("pages.views.activate_direct_trial_promo")
    @patch(
        "pages.views.reconcile_user_plan_from_stripe",
        return_value={"id": PlanNames.FREE},
    )
    def test_direct_activation_switch_still_blocks_new_trials(
        self,
        _mock_reconcile,
        mock_activate,
    ):
        promo = _create_promo(
            code="DIRECT-DISABLED-NEW",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
            payment_method_required=False,
            no_payment_method_end_behavior=TrialPromoNoPaymentMethodEndBehaviorChoices.CANCEL,
            conversion_coupon_id="coupon_three_months",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("pages:special_access_start"),
            {"code": promo.code_label},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("pages:special_access"))
        mock_activate.assert_not_called()

    @override_settings(TRIAL_PROMO_DIRECT_ACTIVATION_ENABLED=True)
    @patch("pages.views.activate_direct_trial_promo")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_stripe_settings")
    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views.reconcile_user_plan_from_stripe", return_value={"id": PlanNames.FREE})
    def test_direct_trial_skips_checkout_and_opens_unlisted_template(
        self,
        _mock_reconcile,
        _mock_prepare,
        mock_stripe_settings,
        mock_price_get,
        mock_activate,
    ):
        template = PersistentAgentTemplate.objects.create(
            code="private-campaign-worker",
            display_name="Private campaign worker",
            tagline="Private",
            description="Private campaign template",
            charter="Run the private campaign workflow.",
            is_listed=False,
        )
        promo = _create_promo(
            code="DIRECT-SPAWN",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
            payment_method_required=False,
            no_payment_method_end_behavior=TrialPromoNoPaymentMethodEndBehaviorChoices.CANCEL,
            conversion_coupon_id="coupon_three_months",
            discount_months=3,
            linked_template=template,
            repeat_trials_allowed=True,
            trial_abuse_filtering_enabled=False,
        )
        mock_stripe_settings.return_value = SimpleNamespace(
            startup_price_id="price_startup",
            startup_additional_task_price_id="",
        )
        mock_price_get.return_value = SimpleNamespace(
            id="price_startup",
            recurring={"interval": "month", "interval_count": 1},
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = "existing-org"
        session["context_name"] = "Existing org"
        session.save()

        response = self.client.post(
            reverse("pages:special_access_start"),
            {"code": promo.code_label},
        )

        self.assertEqual(response.status_code, 302)
        redirect_url = urlsplit(response["Location"])
        self.assertEqual(redirect_url.path, "/app/agents/new")
        self.assertEqual(
            parse_qs(redirect_url.query),
            {
                "spawn": ["1"],
                "context_type": ["personal"],
                "context_id": [str(self.user.pk)],
            },
        )
        mock_activate.assert_called_once()
        session = self.client.session
        self.assertEqual(session["agent_charter"], template.charter)
        self.assertEqual(session["agent_template_source"], "trial_promo")
        self.assertEqual(session["context_type"], "personal")
        self.assertEqual(session["context_id"], str(self.user.pk))

    @override_settings(TRIAL_PROMO_DIRECT_ACTIVATION_ENABLED=False)
    @patch("pages.views.activate_direct_trial_promo")
    @patch(
        "pages.views.reconcile_user_plan_from_stripe",
        return_value={"id": PlanNames.STARTUP},
    )
    def test_existing_paid_user_gets_template_without_subscription_mutation(
        self,
        _mock_reconcile,
        mock_activate,
    ):
        template = PersistentAgentTemplate.objects.create(
            code="paid-user-private-worker",
            display_name="Paid user private worker",
            tagline="Private",
            description="Private campaign template",
            charter="Run the paid-user workflow.",
            is_listed=False,
        )
        promo = _create_promo(
            code="PAID-DIRECT-SPAWN",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
            payment_method_required=False,
            no_payment_method_end_behavior=TrialPromoNoPaymentMethodEndBehaviorChoices.CANCEL,
            conversion_coupon_id="coupon_three_months",
            linked_template=template,
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("pages:special_access_start"),
            {"code": promo.code_label},
        )

        self.assertEqual(response.status_code, 302)
        redirect_url = urlsplit(response["Location"])
        self.assertEqual(redirect_url.path, "/app/agents/new")
        self.assertEqual(parse_qs(redirect_url.query)["spawn"], ["1"])
        self.assertEqual(
            parse_qs(redirect_url.query)["context_type"],
            ["personal"],
        )
        self.assertEqual(
            parse_qs(redirect_url.query)["context_id"],
            [str(self.user.pk)],
        )
        mock_activate.assert_not_called()

    @override_settings(TRIAL_PROMO_DIRECT_ACTIVATION_ENABLED=False)
    @patch(
        "pages.views._start_trial_promo_conversion_checkout",
        return_value=HttpResponseRedirect("https://stripe.test/discounted"),
    )
    @patch(
        "pages.views.reconcile_user_plan_from_stripe",
        return_value={"id": PlanNames.FREE},
    )
    def test_completed_redemption_can_convert_with_activation_disabled(
        self,
        _mock_reconcile,
        mock_start_conversion,
    ):
        promo = _create_promo(
            code="DISABLED-ACTIVATION-CONVERSION",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
            payment_method_required=False,
            no_payment_method_end_behavior=TrialPromoNoPaymentMethodEndBehaviorChoices.CANCEL,
            conversion_coupon_id="coupon_three_months",
        )
        redemption = TrialPromoRedemption.objects.create(
            promo=promo,
            user=self.user,
            status=TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_COMPLETED,
            event_id="disabled-activation-conversion",
            discount_state=TrialPromoDiscountStateChoices.AVAILABLE,
            activated_at=timezone.now() - timedelta(days=15),
            late_conversion_expires_at=timezone.now() + timedelta(days=15),
        )
        promo.activation_mode = TrialPromoActivationModeChoices.HOSTED_CHECKOUT
        promo.save(update_fields=["activation_mode", "updated_at"])
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("pages:special_access_start"),
            {"code": promo.code_label},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://stripe.test/discounted")
        self.assertEqual(mock_start_conversion.call_args.args[1:], (promo, redemption))

    @override_settings(TRIAL_PROMO_DIRECT_ACTIVATION_ENABLED=False)
    @patch("pages.views.activate_direct_trial_promo")
    @patch(
        "pages.views.reconcile_user_plan_from_stripe",
        return_value={"id": PlanNames.STARTUP},
    )
    def test_completed_activation_finishes_template_handoff_after_campaign_expires(
        self,
        _mock_reconcile,
        mock_activate,
    ):
        template = PersistentAgentTemplate.objects.create(
            code="completed-expired-private-worker",
            display_name="Completed expired private worker",
            tagline="Private",
            description="Private campaign template",
            charter="Run the completed activation workflow.",
            is_listed=False,
        )
        promo = _create_promo(
            code="COMPLETED-EXPIRED-HANDOFF",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
            payment_method_required=False,
            no_payment_method_end_behavior=TrialPromoNoPaymentMethodEndBehaviorChoices.CANCEL,
            conversion_coupon_id="coupon_three_months",
            linked_template=template,
        )
        TrialPromoRedemption.objects.create(
            promo=promo,
            user=self.user,
            status=TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_COMPLETED,
            event_id="completed-expired-handoff",
            discount_state=TrialPromoDiscountStateChoices.AVAILABLE,
            activated_at=timezone.now() - timedelta(minutes=1),
            late_conversion_expires_at=timezone.now() + timedelta(days=30),
        )
        promo.activation_mode = TrialPromoActivationModeChoices.HOSTED_CHECKOUT
        promo.email_allowlist_enabled = True
        promo.is_active = False
        promo.active_until = timezone.now() - timedelta(seconds=1)
        promo.save()
        self.client.force_login(self.user)
        session = self.client.session
        session["special_access_trial_promo_id"] = str(promo.pk)
        session.save()

        response = self.client.post(reverse("pages:special_access_start"))

        self.assertEqual(response.status_code, 302)
        redirect_url = urlsplit(response["Location"])
        self.assertEqual(redirect_url.path, "/app/agents/new")
        self.assertEqual(parse_qs(redirect_url.query)["spawn"], ["1"])
        self.assertEqual(
            self.client.session["agent_template_source"],
            "trial_promo",
        )
        mock_activate.assert_not_called()

    @override_settings(TRIAL_PROMO_DIRECT_ACTIVATION_ENABLED=False)
    @patch("pages.views.activate_direct_trial_promo")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_stripe_settings")
    @patch("pages.views._prepare_stripe_or_404")
    @patch(
        "pages.views.reconcile_user_plan_from_stripe",
        return_value={"id": PlanNames.STARTUP},
    )
    def test_pending_activation_is_repaired_before_paid_plan_handoff(
        self,
        _mock_reconcile,
        _mock_prepare,
        mock_stripe_settings,
        mock_price_get,
        mock_activate,
    ):
        promo = _create_promo(
            code="DIRECT-REPAIR",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
            payment_method_required=False,
            no_payment_method_end_behavior=TrialPromoNoPaymentMethodEndBehaviorChoices.CANCEL,
            conversion_coupon_id="coupon_three_months",
        )
        redemption = TrialPromoRedemption.objects.create(
            promo=promo,
            user=self.user,
            status=TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_PENDING,
            event_id="trial-promo-direct-repair",
            stripe_customer_id="cus_repair",
            stripe_subscription_id="sub_repair",
            stripe_subscription_schedule_id="sub_sched_repair",
        )
        mock_stripe_settings.return_value = SimpleNamespace(
            startup_price_id="price_startup",
            startup_additional_task_price_id="",
        )
        mock_price_get.return_value = SimpleNamespace(
            id="price_startup",
            recurring={"interval": "month", "interval_count": 1},
        )
        promo.activation_mode = TrialPromoActivationModeChoices.HOSTED_CHECKOUT
        promo.is_active = False
        promo.active_until = timezone.now() - timedelta(days=1)
        promo.save()
        self.client.force_login(self.user)
        session = self.client.session
        session["special_access_trial_promo_id"] = str(promo.pk)
        session.save()

        response = self.client.post(reverse("pages:special_access_start"))

        self.assertEqual(response.status_code, 302)
        redirect_url = urlsplit(response["Location"])
        self.assertEqual(redirect_url.path, "/app/agents/new")
        self.assertEqual(
            parse_qs(redirect_url.query),
            {
                "context_type": ["personal"],
                "context_id": [str(self.user.pk)],
            },
        )
        mock_activate.assert_called_once()
        self.assertEqual(
            mock_activate.call_args.kwargs["promo"],
            redemption.promo,
        )
        self.assertEqual(
            set(mock_activate.call_args.kwargs),
            {"promo", "user"},
        )
        mock_stripe_settings.assert_not_called()
        mock_price_get.assert_not_called()

    @override_settings(TRIAL_PROMO_DIRECT_ACTIVATION_ENABLED=True)
    @patch("pages.views.activate_direct_trial_promo")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_stripe_settings")
    @patch("pages.views._prepare_stripe_or_404")
    @patch(
        "pages.views.reconcile_user_plan_from_stripe",
        return_value={"id": PlanNames.FREE},
    )
    @patch(
        "pages.views.can_user_start_trial_promo",
        return_value=SimpleNamespace(allowed=True, reason=""),
    )
    def test_failed_direct_activation_excludes_its_partial_subscription_on_retry(
        self,
        mock_can_start,
        _mock_reconcile,
        _mock_prepare,
        mock_stripe_settings,
        mock_price_get,
        mock_activate,
    ):
        promo = _create_promo(
            code="DIRECT-FAILED-RETRY",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
            payment_method_required=False,
            no_payment_method_end_behavior=TrialPromoNoPaymentMethodEndBehaviorChoices.CANCEL,
            conversion_coupon_id="coupon_three_months",
            repeat_trials_allowed=False,
            trial_abuse_filtering_enabled=False,
        )
        TrialPromoRedemption.objects.create(
            promo=promo,
            user=self.user,
            status=TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_FAILED,
            event_id="trial-promo-direct-failed-retry",
            stripe_customer_id="cus_failed_retry",
            stripe_subscription_id="sub_failed_retry",
        )
        mock_stripe_settings.return_value = SimpleNamespace(
            startup_price_id="price_startup",
            startup_additional_task_price_id="",
        )
        mock_price_get.return_value = SimpleNamespace(
            id="price_startup",
            recurring={"interval": "month", "interval_count": 1},
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("pages:special_access_start"),
            {"code": promo.code_label},
        )

        self.assertEqual(response.status_code, 302)
        mock_can_start.assert_called_once_with(
            user=self.user,
            promo=promo,
            request=mock_can_start.call_args.kwargs["request"],
            excluded_subscription_ids={"sub_failed_retry"},
        )
        mock_activate.assert_called_once()

    def test_unlisted_campaign_template_is_not_publicly_resolvable(self):
        template = PersistentAgentTemplate.objects.create(
            # This deliberately shadows a legacy in-code template definition.
            # The database visibility decision must take precedence over fallback.
            code="sales-pipeline-whisperer",
            display_name="Unlisted private worker",
            tagline="Private",
            description="Private campaign template",
            charter="Run the unlisted workflow.",
            is_listed=False,
        )

        self.assertIsNone(
            PretrainedWorkerTemplateService.get_template_by_code(template.code),
        )
        self.assertIsNotNone(
            PretrainedWorkerTemplateService.get_template_by_code(
                template.code,
                include_unlisted=True,
            ),
        )
        self.assertNotIn(
            template.code,
            [
                item.code
                for item in PretrainedWorkerTemplateService.get_active_templates()
            ],
        )
        response = self.client.get(
            reverse(
                "pages:pretrained_worker_detail",
                kwargs={"slug": template.code},
            ),
        )
        self.assertEqual(response.status_code, 404)
        launch_response = self.client.get(
            reverse(
                "pages:pretrained_worker_launch",
                kwargs={"slug": template.code},
            ),
        )
        self.assertEqual(launch_response.status_code, 404)

        template.is_active = False
        template.save(update_fields=["is_active", "updated_at"])
        self.assertNotIn(
            template.code,
            [
                item.code
                for item in PretrainedWorkerTemplateService.get_active_templates()
            ],
        )

    def test_campaign_authorization_reaches_agent_provisioning(self):
        template = PersistentAgentTemplate.objects.create(
            code="provision-private-campaign-worker",
            display_name="Provision private campaign worker",
            tagline="Private",
            description="Private campaign template",
            charter="Run the private provisioning workflow.",
            base_schedule="0 9 * * *",
            is_listed=False,
        )

        with self.assertRaisesMessage(
            PersistentAgentProvisioningError,
            f"Unknown template code '{template.code}'.",
        ):
            PersistentAgentProvisioningService.provision(
                user=self.user,
                name="Unauthorized private campaign worker",
                template_code=template.code,
            )

        result = PersistentAgentProvisioningService.provision(
            user=self.user,
            name="Authorized private campaign worker",
            template_code=template.code,
            allow_unlisted_template=True,
        )

        self.assertEqual(result.applied_template_code, template.code)
        self.assertEqual(result.agent.charter, template.charter)
        self.assertEqual(result.agent.schedule_snapshot, template.base_schedule)

    @override_settings(TRIAL_PROMO_DIRECT_ACTIVATION_ENABLED=False)
    @patch("pages.views._create_checkout_session_with_customer_context")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.validate_direct_trial_conversion_configuration")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_stripe_settings")
    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views.reconcile_user_plan_from_stripe", return_value={"id": PlanNames.FREE})
    def test_late_conversion_uses_campaign_only_discounted_checkout(
        self,
        _mock_reconcile,
        _mock_prepare,
        mock_stripe_settings,
        mock_price_get,
        mock_validate,
        mock_customer,
        mock_create_checkout,
    ):
        promo = _create_promo(
            code="LATE-CONVERSION",
            activation_mode=TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
            payment_method_required=False,
            no_payment_method_end_behavior=TrialPromoNoPaymentMethodEndBehaviorChoices.CANCEL,
            conversion_coupon_id="coupon_three_months",
            discount_months=3,
        )
        redemption = TrialPromoRedemption.objects.create(
            promo=promo,
            user=self.user,
            status=TrialPromoRedemptionStatusChoices.DIRECT_ACTIVATION_COMPLETED,
            event_id="direct-late-conversion",
            stripe_customer_id="cus_late",
            stripe_subscription_id="sub_expired",
            discount_state=TrialPromoDiscountStateChoices.AVAILABLE,
            activated_at=timezone.now() - timedelta(days=15),
            late_conversion_expires_at=timezone.now() + timedelta(days=15),
            metadata={
                TRIAL_PROMO_META_PLAN: PlanNames.STARTUP,
                TRIAL_PROMO_REDEMPTION_COUPON_ID_KEY: "coupon_three_months",
                TRIAL_PROMO_REDEMPTION_DISCOUNT_MONTHS_KEY: 3,
            },
        )
        promo.plan = PlanNames.SCALE
        promo.activation_mode = TrialPromoActivationModeChoices.HOSTED_CHECKOUT
        promo.payment_method_required = True
        promo.no_payment_method_end_behavior = (
            TrialPromoNoPaymentMethodEndBehaviorChoices.CREATE_INVOICE
        )
        promo.conversion_coupon_id = "coupon_six_months"
        promo.discount_months = 6
        promo.is_active = False
        promo.save(
            update_fields=[
                "plan",
                "activation_mode",
                "payment_method_required",
                "no_payment_method_end_behavior",
                "conversion_coupon_id",
                "discount_months",
                "is_active",
            ],
        )
        mock_stripe_settings.return_value = SimpleNamespace(
            startup_price_id="price_startup",
            startup_additional_task_price_id="",
            scale_price_id="price_scale",
            scale_additional_task_price_id="",
        )
        mock_price_get.return_value = SimpleNamespace(
            id="price_startup",
            unit_amount=12000,
            currency="usd",
            recurring={"interval": "month", "interval_count": 1},
        )
        mock_validate.return_value = SimpleNamespace(coupon_id="coupon_three_months")
        mock_customer.return_value = SimpleNamespace(id="cus_late")
        mock_create_checkout.return_value = SimpleNamespace(
            id="cs_late",
            url="https://stripe.test/discounted",
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse(
                "pages:special_access_convert",
                kwargs={"redemption_id": redemption.pk},
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://stripe.test/discounted")
        mock_price_get.assert_called_once_with(id="price_startup")
        self.assertEqual(
            mock_validate.call_args.kwargs,
            {
                "price_object": mock_price_get.return_value,
                "coupon_id": "coupon_three_months",
                "discount_months": 3,
            },
        )
        checkout_kwargs = mock_create_checkout.call_args.kwargs["checkout_kwargs"]
        self.assertEqual(
            checkout_kwargs["line_items"],
            [{"price": "price_startup", "quantity": 1}],
        )
        self.assertEqual(
            checkout_kwargs["discounts"],
            [{"coupon": "coupon_three_months"}],
        )
        self.assertEqual(
            checkout_kwargs["subscription_data"]["metadata"][TRIAL_PROMO_META_PLAN],
            PlanNames.STARTUP,
        )
        self.assertEqual(
            checkout_kwargs["subscription_data"]["metadata"][TRIAL_PROMO_META_DISCOUNT_MONTHS],
            "3",
        )
        self.assertEqual(
            checkout_kwargs["subscription_data"]["metadata"][TRIAL_PROMO_META_ACTIVATION_MODE],
            TrialPromoActivationModeChoices.DIRECT_STRIPE_TRIAL,
        )
        self.assertNotIn("trial_period_days", checkout_kwargs["subscription_data"])
        self.assertEqual(
            checkout_kwargs["idempotency_key"],
            f"trial-promo-conversion-{redemption.pk}-1",
        )
        redemption.refresh_from_db()
        self.assertEqual(redemption.conversion_checkout_session_id, "cs_late")
        self.assertFalse(redemption.metadata["conversion_checkout_pending"])

        with patch(
            "pages.views.stripe.checkout.Session.retrieve",
            return_value={
                "id": "cs_late",
                "status": "open",
                "url": "https://stripe.test/discounted",
            },
        ) as mock_retrieve:
            retry_response = self.client.get(
                reverse(
                    "pages:special_access_convert",
                    kwargs={"redemption_id": redemption.pk},
                ),
            )

        self.assertEqual(retry_response.status_code, 302)
        self.assertEqual(
            retry_response["Location"],
            "https://stripe.test/discounted",
        )
        mock_create_checkout.assert_called_once()
        mock_retrieve.assert_called_once_with("cs_late", api_key=stripe.api_key)

    @patch(
        "pages.views._start_trial_promo_checkout",
        return_value=HttpResponseRedirect("https://stripe.test/special"),
    )
    def test_special_access_get_stages_terms_before_post_starts_checkout(
        self,
        mock_start_checkout,
    ):
        promo = _create_promo(
            code="DIRECT-START",
            email_allowlist_enabled=True,
            repeat_trials_allowed=True,
            trial_abuse_filtering_enabled=False,
        )
        TrialPromoAllowedEmail.objects.create(
            promo=promo,
            normalized_email=self.user.email,
        )
        self.client.force_login(self.user)

        terms_redirect = self.client.get(
            reverse("pages:special_access_start"),
            {"code": "direct-start"},
        )

        self.assertEqual(terms_redirect.status_code, 302)
        self.assertEqual(
            terms_redirect["Location"],
            reverse("pages:special_access"),
        )
        mock_start_checkout.assert_not_called()

        response = self.client.post(reverse("pages:special_access_start"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://stripe.test/special")
        mock_start_checkout.assert_called_once()
        _request, resolved_promo = mock_start_checkout.call_args.args
        self.assertEqual(resolved_promo, promo)

    def test_email_allowlist_checkout_prompts_unverified_user_to_resend(self):
        promo = _create_promo(
            code="VERIFY-FIRST",
            email_allowlist_enabled=True,
            repeat_trials_allowed=True,
            trial_abuse_filtering_enabled=False,
        )
        TrialPromoAllowedEmail.objects.create(
            promo=promo,
            normalized_email=self.user.email,
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=False,
            primary=True,
        )
        self.client.force_login(self.user)
        self.client.post(reverse("pages:special_access"), {"code": "verify-first"})

        response = self.client.post(reverse("pages:special_access_start"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please verify your email address to start this special trial.")
        self.assertContains(response, "Send verification email")
        self.assertContains(response, self.user.email)

    def test_special_access_resend_verification_sends_email_for_allowlisted_user(self):
        promo = _create_promo(
            code="VERIFY-RESEND",
            email_allowlist_enabled=True,
            repeat_trials_allowed=True,
            trial_abuse_filtering_enabled=False,
        )
        TrialPromoAllowedEmail.objects.create(
            promo=promo,
            normalized_email=self.user.email,
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=False,
            primary=True,
        )
        self.client.force_login(self.user)
        self.client.post(reverse("pages:special_access"), {"code": "verify-resend"})

        response = self.client.post(
            reverse("pages:special_access_start"),
            {"action": "resend_email_verification"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Verification email sent to {self.user.email}.")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertIn("next=%2Fspecial-access%2F", mail.outbox[0].body)

    def test_special_access_verification_link_redirects_back_to_access_page(self):
        self.user.email = "special-access-return@example.com"
        self.user.save(update_fields=["email"])
        promo = _create_promo(
            code="VERIFY-RETURN",
            email_allowlist_enabled=True,
            repeat_trials_allowed=True,
            trial_abuse_filtering_enabled=False,
        )
        TrialPromoAllowedEmail.objects.create(
            promo=promo,
            normalized_email=self.user.email,
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=False,
            primary=True,
        )
        self.client.force_login(self.user)
        self.client.post(reverse("pages:special_access"), {"code": "verify-return"})
        self.client.post(
            reverse("pages:special_access_start"),
            {"action": "resend_email_verification"},
        )
        match = re.search(r"https?://\S+/accounts/confirm-email/\S+", mail.outbox[0].body)

        self.assertIsNotNone(match)
        url_parts = urlsplit(match.group(0))
        response = self.client.get(f"{url_parts.path}?{url_parts.query}")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("pages:special_access"))

    def test_special_access_resend_verification_targets_current_account_email(self):
        self.user.email = "special-access-current@example.com"
        self.user.save(update_fields=["email"])
        promo = _create_promo(
            code="VERIFY-CURRENT",
            email_allowlist_enabled=True,
            repeat_trials_allowed=True,
            trial_abuse_filtering_enabled=False,
        )
        TrialPromoAllowedEmail.objects.create(
            promo=promo,
            normalized_email=self.user.email,
        )
        EmailAddress.objects.create(
            user=self.user,
            email="old-primary@example.com",
            verified=True,
            primary=True,
        )
        self.client.force_login(self.user)
        self.client.post(reverse("pages:special_access"), {"code": "verify-current"})

        response = self.client.post(
            reverse("pages:special_access_start"),
            {"action": "resend_email_verification"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertTrue(
            EmailAddress.objects.filter(
                user=self.user,
                email=self.user.email,
                verified=False,
            ).exists()
        )

    @patch("pages.views._track_web_event_for_request")
    @patch("pages.views._emit_checkout_initiated_event")
    @patch("pages.views.stripe.Customer.modify")
    @patch("pages.views.stripe.checkout.Session.create")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.get_stripe_settings")
    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views.reconcile_user_plan_from_stripe", return_value={"id": PlanNames.FREE})
    def test_optional_payment_method_promo_starts_stripe_trial_checkout(
        self,
        _mock_reconcile,
        _mock_prepare,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        mock_customer_modify,
        _mock_emit_checkout,
        _mock_track_event,
    ):
        promo = _create_promo(
            code="SPECIAL-ACCESS",
            payment_method_required=False,
            trial_abuse_filtering_enabled=False,
            repeat_trials_allowed=True,
            trial_days=21,
            trial_credit_amount=Decimal("1234.000"),
            max_redemptions=5,
        )
        self.client.force_login(self.user)
        self.client.post(reverse("pages:special_access"), {"code": "special-access"})
        mock_stripe_settings.return_value = SimpleNamespace(
            startup_price_id="price_startup",
            startup_additional_task_price_id="",
        )
        mock_customer.return_value = SimpleNamespace(id="cus_special")
        mock_price_get.return_value = MagicMock(unit_amount=12000, currency="usd")
        mock_session_create.return_value = SimpleNamespace(
            id="cs_special",
            created=1_700_000_000,
            url="https://stripe.test/special",
        )

        response = self.client.post(reverse("pages:special_access_start"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://stripe.test/special")
        kwargs = mock_session_create.call_args.kwargs
        self.assertEqual(kwargs["payment_method_types"], PERSONAL_CHECKOUT_PAYMENT_METHOD_TYPES)
        self.assertEqual(kwargs["payment_method_collection"], "if_required")
        self.assertEqual(kwargs["subscription_data"]["trial_period_days"], 21)
        self.assertEqual(
            kwargs["subscription_data"]["trial_settings"]["end_behavior"]["missing_payment_method"],
            "create_invoice",
        )
        self.assertEqual(kwargs["metadata"][TRIAL_PROMO_META_ID], str(promo.pk))
        self.assertEqual(kwargs["metadata"][TRIAL_PROMO_META_PAYMENT_REQUIRED], "false")
        self.assertNotIn(TRIAL_PROMO_META_ACTIVATION_MODE, kwargs["metadata"])
        self.assertNotIn(TRIAL_PROMO_META_DISCOUNT_MONTHS, kwargs["metadata"])
        self.assertEqual(kwargs["metadata"][TRIAL_PROMO_META_CREDIT_AMOUNT], "1234.000")
        redemption_id = kwargs["metadata"][TRIAL_PROMO_META_REDEMPTION_ID]
        self.assertEqual(
            kwargs["subscription_data"]["metadata"][TRIAL_PROMO_META_REDEMPTION_ID],
            redemption_id,
        )

        redemption = TrialPromoRedemption.objects.get(pk=redemption_id)
        self.assertEqual(redemption.status, TrialPromoRedemptionStatusChoices.CHECKOUT_STARTED)
        self.assertEqual(redemption.stripe_customer_id, "cus_special")
        self.assertEqual(redemption.stripe_checkout_session_id, "cs_special")
        customer_metadata = mock_customer_modify.call_args.kwargs["metadata"]
        self.assertEqual(customer_metadata[TRIAL_PROMO_META_ID], str(promo.pk))
