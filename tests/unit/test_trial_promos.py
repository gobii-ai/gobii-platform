from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from api.models import (
    TrialPromo,
    TrialPromoRedemption,
    TrialPromoRedemptionStatusChoices,
)
from api.services.trial_promos import (
    TRIAL_PROMO_META_CREDIT_AMOUNT,
    TRIAL_PROMO_META_ID,
    TRIAL_PROMO_META_PAYMENT_REQUIRED,
    TRIAL_PROMO_META_REDEMPTION_ID,
    TrialPromoError,
    can_user_start_trial_promo,
    find_active_trial_promo_by_code,
    parse_trial_promo_credit_amount,
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

    def test_parse_trial_promo_credit_amount_ignores_invalid_values(self):
        self.assertEqual(
            parse_trial_promo_credit_amount({TRIAL_PROMO_META_CREDIT_AMOUNT: "123.456"}),
            Decimal("123.456"),
        )
        self.assertIsNone(parse_trial_promo_credit_amount({TRIAL_PROMO_META_CREDIT_AMOUNT: "0"}))
        self.assertIsNone(parse_trial_promo_credit_amount({TRIAL_PROMO_META_CREDIT_AMOUNT: "not-a-number"}))


@tag("batch_pages")
@override_settings(GOBII_PROPRIETARY_MODE=True)
class SpecialAccessCheckoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="special-access-user",
            email="special-access@example.com",
            password="pw",
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
