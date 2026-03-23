from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, tag
from django.utils import timezone

from api.models import (
    TaskCredit,
    UserIdentitySignal,
    UserIdentitySignalTypeChoices,
    UserTrialEligibility,
    UserTrialEligibilityAutoStatusChoices,
    UserTrialEligibilityManualActionChoices,
)
from api.services.trial_abuse import (
    SIGNAL_SOURCE_SIGNUP,
    capture_request_identity_signals_and_attribution,
    evaluate_user_trial_eligibility,
    extract_request_identity_signal_values,
)
from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNames
from util.analytics import AnalyticsEvent, AnalyticsSource


User = get_user_model()


@tag("batch_pages")
class TrialAbuseServiceTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _create_user(self, email: str) -> User:
        return User.objects.create_user(
            username=email,
            email=email,
            password="pw",
        )

    def _grant_trial_history(self, user):
        now = timezone.now()
        TaskCredit.objects.create(
            user=user,
            credits=Decimal("100"),
            credits_used=Decimal("0"),
            granted_date=now,
            expiration_date=now + timedelta(days=30),
            plan=PlanNames.FREE,
            additional_task=False,
            free_trial_start=True,
            grant_type=GrantTypeChoices.PROMO,
        )

    @tag("batch_pages")
    def test_capture_request_identity_signals_and_attribution_stores_raw_values(self):
        user = self._create_user("capture-signals@example.com")
        request = self.factory.post(
            "/signup",
            {
                "ufp": "visitor-123",
                "ufpr": "request-456",
                "uga": "GA1.2.111.222",
            },
        )
        request.META["REMOTE_ADDR"] = "198.51.100.24"
        request.COOKIES = {
            "_fbp": "fb.1.123.abcdef",
        }

        captured = capture_request_identity_signals_and_attribution(
            user,
            request,
            source=SIGNAL_SOURCE_SIGNUP,
            include_fpjs=True,
        )

        self.assertEqual(
            captured,
            {
                UserIdentitySignalTypeChoices.GA_CLIENT_ID: "111.222",
                UserIdentitySignalTypeChoices.FBP: "fb.1.123.abcdef",
                UserIdentitySignalTypeChoices.IP_EXACT: "198.51.100.24",
                UserIdentitySignalTypeChoices.IP_PREFIX: "198.51.100.0/24",
                UserIdentitySignalTypeChoices.FPJS_VISITOR_ID: "visitor-123",
                UserIdentitySignalTypeChoices.FPJS_REQUEST_ID: "request-456",
            },
        )
        self.assertSetEqual(
            set(UserIdentitySignal.objects.filter(user=user).values_list("signal_type", "signal_value")),
            {
                (UserIdentitySignalTypeChoices.GA_CLIENT_ID, "111.222"),
                (UserIdentitySignalTypeChoices.FBP, "fb.1.123.abcdef"),
                (UserIdentitySignalTypeChoices.IP_EXACT, "198.51.100.24"),
                (UserIdentitySignalTypeChoices.IP_PREFIX, "198.51.100.0/24"),
                (UserIdentitySignalTypeChoices.FPJS_VISITOR_ID, "visitor-123"),
                (UserIdentitySignalTypeChoices.FPJS_REQUEST_ID, "request-456"),
            },
        )

    @tag("batch_pages")
    def test_capture_request_identity_signals_and_attribution_normalizes_ga_cookie_value(self):
        user = self._create_user("capture-ga-cookie@example.com")
        request = self.factory.post("/signup")
        request.META["REMOTE_ADDR"] = "198.51.100.24"
        request.COOKIES = {
            "_ga": "GA1.2.333.444",
        }

        captured = capture_request_identity_signals_and_attribution(
            user,
            request,
            source=SIGNAL_SOURCE_SIGNUP,
            include_fpjs=False,
        )

        self.assertEqual(
            captured[UserIdentitySignalTypeChoices.GA_CLIENT_ID],
            "333.444",
        )

    @tag("batch_pages")
    @patch("api.services.trial_abuse.get_stripe_customer", return_value=None)
    def test_fpjs_match_blocks_trial(self, _mock_get_stripe_customer):
        historical_user = self._create_user("historical-fpjs@example.com")
        current_user = self._create_user("current-fpjs@example.com")
        self._grant_trial_history(historical_user)

        UserIdentitySignal.objects.create(
            user=historical_user,
            signal_type=UserIdentitySignalTypeChoices.FPJS_VISITOR_ID,
            signal_value="visitor-shared",
        )
        UserIdentitySignal.objects.create(
            user=current_user,
            signal_type=UserIdentitySignalTypeChoices.FPJS_VISITOR_ID,
            signal_value="visitor-shared",
        )

        result = evaluate_user_trial_eligibility(current_user)

        self.assertFalse(result.eligible)
        self.assertEqual(result.decision, UserTrialEligibilityAutoStatusChoices.NO_TRIAL)
        self.assertIn("fpjs_history_match", result.reason_codes)
        self.assertEqual(
            result.evidence_summary["matched_signal_types"],
            [UserIdentitySignalTypeChoices.FPJS_VISITOR_ID],
        )

    @tag("batch_pages")
    @patch("api.services.trial_abuse.get_stripe_customer", return_value=None)
    def test_multi_signal_match_sends_user_to_review(self, _mock_get_stripe_customer):
        historical_user = self._create_user("historical-multi@example.com")
        current_user = self._create_user("current-multi@example.com")
        self._grant_trial_history(historical_user)

        for user in (historical_user, current_user):
            UserIdentitySignal.objects.create(
                user=user,
                signal_type=UserIdentitySignalTypeChoices.FBP,
                signal_value="fb.1.123.shared",
            )
            UserIdentitySignal.objects.create(
                user=user,
                signal_type=UserIdentitySignalTypeChoices.GA_CLIENT_ID,
                signal_value="GA1.2.shared",
            )

        result = evaluate_user_trial_eligibility(current_user)

        self.assertFalse(result.eligible)
        self.assertEqual(result.decision, UserTrialEligibilityAutoStatusChoices.REVIEW)
        self.assertIn("multi_signal_history_match", result.reason_codes)
        self.assertEqual(
            result.evidence_summary["matched_signal_types"],
            [
                UserIdentitySignalTypeChoices.FBP,
                UserIdentitySignalTypeChoices.GA_CLIENT_ID,
            ],
        )

    @tag("batch_pages")
    @patch("api.services.trial_abuse.get_stripe_customer", return_value=None)
    def test_ip_only_match_does_not_send_user_to_review(self, _mock_get_stripe_customer):
        historical_user = self._create_user("historical-ip@example.com")
        current_user = self._create_user("current-ip@example.com")
        self._grant_trial_history(historical_user)

        for user in (historical_user, current_user):
            UserIdentitySignal.objects.create(
                user=user,
                signal_type=UserIdentitySignalTypeChoices.IP_EXACT,
                signal_value="198.51.100.24",
            )
            UserIdentitySignal.objects.create(
                user=user,
                signal_type=UserIdentitySignalTypeChoices.IP_PREFIX,
                signal_value="198.51.100.0/24",
            )

        result = evaluate_user_trial_eligibility(current_user)

        self.assertTrue(result.eligible)
        self.assertEqual(result.decision, UserTrialEligibilityAutoStatusChoices.ELIGIBLE)
        self.assertEqual(result.reason_codes, [])
        self.assertEqual(
            result.evidence_summary["matched_signal_types"],
            [
                UserIdentitySignalTypeChoices.IP_EXACT,
                UserIdentitySignalTypeChoices.IP_PREFIX,
            ],
        )

    @tag("batch_pages")
    @patch("api.services.trial_abuse.get_stripe_customer", return_value=None)
    def test_fbp_and_ip_match_sends_user_to_review(self, _mock_get_stripe_customer):
        historical_user = self._create_user("historical-fbp-ip@example.com")
        current_user = self._create_user("current-fbp-ip@example.com")
        self._grant_trial_history(historical_user)

        for user in (historical_user, current_user):
            UserIdentitySignal.objects.create(
                user=user,
                signal_type=UserIdentitySignalTypeChoices.FBP,
                signal_value="fb.1.123.shared",
            )
            UserIdentitySignal.objects.create(
                user=user,
                signal_type=UserIdentitySignalTypeChoices.IP_EXACT,
                signal_value="198.51.100.24",
            )
            UserIdentitySignal.objects.create(
                user=user,
                signal_type=UserIdentitySignalTypeChoices.IP_PREFIX,
                signal_value="198.51.100.0/24",
            )

        result = evaluate_user_trial_eligibility(current_user)

        self.assertFalse(result.eligible)
        self.assertEqual(result.decision, UserTrialEligibilityAutoStatusChoices.REVIEW)
        self.assertIn("multi_signal_history_match", result.reason_codes)

    @tag("batch_pages")
    def test_password_signup_post_does_not_reuse_staged_fpjs_cookies(self):
        request = self.factory.post(
            "/signup",
            {
                "email": "user@example.com",
                "password1": "pw",
            },
        )
        request.COOKIES = {
            "gobii_signup_fpjs_visitor_id": "visitor-stale",
            "gobii_signup_fpjs_request_id": "request-stale",
        }

        extracted = extract_request_identity_signal_values(request, include_fpjs=True)

        self.assertNotIn(UserIdentitySignalTypeChoices.FPJS_VISITOR_ID, extracted)
        self.assertNotIn(UserIdentitySignalTypeChoices.FPJS_REQUEST_ID, extracted)

    @tag("batch_pages")
    def test_non_post_signup_flow_can_use_staged_fpjs_cookies(self):
        request = self.factory.get("/accounts/google/login/callback/")
        request.COOKIES = {
            "gobii_signup_fpjs_visitor_id": "visitor-current",
            "gobii_signup_fpjs_request_id": "request-current",
        }

        extracted = extract_request_identity_signal_values(request, include_fpjs=True)

        self.assertEqual(
            extracted[UserIdentitySignalTypeChoices.FPJS_VISITOR_ID],
            "visitor-current",
        )
        self.assertEqual(
            extracted[UserIdentitySignalTypeChoices.FPJS_REQUEST_ID],
            "request-current",
        )

    @tag("batch_pages")
    @patch("api.services.trial_abuse.Analytics.track_event")
    @patch("api.services.trial_abuse.get_stripe_customer", return_value=None)
    def test_tracks_trial_assessment_event_for_explicit_source(
        self,
        _mock_get_stripe_customer,
        mock_track_event,
    ):
        historical_user = self._create_user("historical-event@example.com")
        current_user = self._create_user("current-event@example.com")
        self._grant_trial_history(historical_user)

        UserIdentitySignal.objects.create(
            user=historical_user,
            signal_type=UserIdentitySignalTypeChoices.FPJS_VISITOR_ID,
            signal_value="visitor-shared-event",
        )
        UserIdentitySignal.objects.create(
            user=current_user,
            signal_type=UserIdentitySignalTypeChoices.FPJS_VISITOR_ID,
            signal_value="visitor-shared-event",
        )

        result = evaluate_user_trial_eligibility(
            current_user,
            assessment_source=SIGNAL_SOURCE_SIGNUP,
        )

        self.assertFalse(result.eligible)
        mock_track_event.assert_called_once()
        call_kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(call_kwargs["user_id"], current_user.id)
        self.assertEqual(call_kwargs["event"], AnalyticsEvent.PERSONAL_TRIAL_ELIGIBILITY_ASSESSED)
        self.assertEqual(call_kwargs["source"], AnalyticsSource.WEB)
        self.assertEqual(
            call_kwargs["properties"],
            {
                "assessment_source": SIGNAL_SOURCE_SIGNUP,
                "auto_status": UserTrialEligibilityAutoStatusChoices.NO_TRIAL,
                "decision": UserTrialEligibilityAutoStatusChoices.NO_TRIAL,
                "eligible": False,
                "manual_action": UserTrialEligibilityManualActionChoices.INHERIT,
                "has_manual_override": False,
                "reason_codes": ["fpjs_history_match"],
                "matched_signal_types": [UserIdentitySignalTypeChoices.FPJS_VISITOR_ID],
                "matched_signal_user_count": 1,
            },
        )

    @tag("batch_pages")
    @patch("api.services.trial_abuse.Analytics.track_event")
    @patch("api.services.trial_abuse.get_stripe_customer", return_value=None)
    def test_does_not_track_trial_assessment_event_without_explicit_source(
        self,
        _mock_get_stripe_customer,
        mock_track_event,
    ):
        current_user = self._create_user("current-no-event@example.com")

        result = evaluate_user_trial_eligibility(current_user)

        self.assertTrue(result.eligible)
        mock_track_event.assert_not_called()

    @tag("batch_pages")
    @patch("api.services.trial_abuse.get_stripe_customer", return_value=None)
    def test_manual_allow_override_keeps_trial_enabled(self, _mock_get_stripe_customer):
        historical_user = self._create_user("historical-override@example.com")
        current_user = self._create_user("current-override@example.com")
        self._grant_trial_history(historical_user)

        UserIdentitySignal.objects.create(
            user=historical_user,
            signal_type=UserIdentitySignalTypeChoices.FPJS_VISITOR_ID,
            signal_value="visitor-shared-override",
        )
        UserIdentitySignal.objects.create(
            user=current_user,
            signal_type=UserIdentitySignalTypeChoices.FPJS_VISITOR_ID,
            signal_value="visitor-shared-override",
        )
        UserTrialEligibility.objects.create(
            user=current_user,
            manual_action=UserTrialEligibilityManualActionChoices.ALLOW_TRIAL,
        )

        result = evaluate_user_trial_eligibility(current_user)

        self.assertTrue(result.eligible)
        self.assertEqual(result.decision, UserTrialEligibilityAutoStatusChoices.ELIGIBLE)
        self.assertEqual(result.manual_action, UserTrialEligibilityManualActionChoices.ALLOW_TRIAL)

    @tag("batch_pages")
    @patch("api.services.trial_abuse.customer_has_any_individual_subscription", return_value=False)
    @patch("api.services.trial_abuse.get_stripe_customer")
    def test_signal_matching_does_not_fan_out_to_candidate_stripe_lookups(
        self,
        mock_get_stripe_customer,
        mock_customer_has_any_individual_subscription,
    ):
        current_user = self._create_user("current-fanout@example.com")
        matching_users = [
            self._create_user(f"candidate-{index}@example.com")
            for index in range(5)
        ]

        UserIdentitySignal.objects.create(
            user=current_user,
            signal_type=UserIdentitySignalTypeChoices.IP_PREFIX,
            signal_value="198.51.100.0/24",
        )
        for user in matching_users:
            UserIdentitySignal.objects.create(
                user=user,
                signal_type=UserIdentitySignalTypeChoices.IP_PREFIX,
                signal_value="198.51.100.0/24",
            )

        def _customer_for_user(user):
            return SimpleNamespace(id=f"cus_{user.id}")

        mock_get_stripe_customer.side_effect = _customer_for_user

        result = evaluate_user_trial_eligibility(current_user)

        self.assertTrue(result.eligible)
        self.assertEqual(mock_get_stripe_customer.call_count, 1)
        self.assertEqual(mock_get_stripe_customer.call_args.args[0], current_user)
        mock_customer_has_any_individual_subscription.assert_called_once_with(
            f"cus_{current_user.id}"
        )
