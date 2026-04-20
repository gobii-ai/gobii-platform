from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.admin import UserTrialEligibilityAdmin
from api.models import (
    UserTrialEligibility,
    UserTrialEligibilityAutoStatusChoices,
    UserTrialEligibilityManualActionChoices,
)


User = get_user_model()


@tag("batch_pages_signals")
class UserTrialEligibilityAdminTests(TestCase):
    def setUp(self):
        self.admin = UserTrialEligibilityAdmin(UserTrialEligibility, AdminSite())

    @tag("batch_pages_signals")
    def test_list_display_includes_user_id_and_reason(self):
        self.assertEqual(
            self.admin.list_display,
            (
                "user",
                "user_id_display",
                "sign_up_date_display",
                "effective_status_display",
                "auto_status",
                "reason_display",
                "manual_action",
                "evaluated_at",
                "reviewed_by",
                "reviewed_at",
            ),
        )

    @tag("batch_pages_signals")
    def test_effective_status_display_handles_add_form(self):
        self.assertEqual(self.admin.effective_status_display(None), "-")

    @tag("batch_pages_signals")
    def test_effective_status_display_returns_effective_status(self):
        user = User.objects.create_user(
            username="trial-admin@example.com",
            email="trial-admin@example.com",
            password="pw",
        )
        eligibility = UserTrialEligibility.objects.create(
            user=user,
            auto_status=UserTrialEligibilityAutoStatusChoices.REVIEW,
            manual_action=UserTrialEligibilityManualActionChoices.ALLOW_TRIAL,
        )

        self.assertEqual(
            self.admin.effective_status_display(eligibility),
            UserTrialEligibilityAutoStatusChoices.ELIGIBLE,
        )

    @tag("batch_pages_signals")
    def test_user_id_display_returns_user_id(self):
        user = User.objects.create_user(
            username="trial-admin-user-id@example.com",
            email="trial-admin-user-id@example.com",
            password="pw",
        )
        eligibility = UserTrialEligibility.objects.create(user=user)

        self.assertEqual(self.admin.user_id_display(eligibility), user.id)

    @tag("batch_pages_signals")
    def test_sign_up_date_display_returns_date_joined(self):
        user = User.objects.create_user(
            username="trial-admin-signup-date@example.com",
            email="trial-admin-signup-date@example.com",
            password="pw",
        )
        eligibility = UserTrialEligibility.objects.create(user=user)

        self.assertEqual(self.admin.sign_up_date_display(eligibility), user.date_joined)

    @tag("batch_pages_signals")
    def test_reason_display_handles_missing_reason_codes(self):
        self.assertEqual(self.admin.reason_display(None), "-")

        user = User.objects.create_user(
            username="trial-admin-no-reason@example.com",
            email="trial-admin-no-reason@example.com",
            password="pw",
        )
        eligibility = UserTrialEligibility.objects.create(user=user, reason_codes=[])

        self.assertEqual(self.admin.reason_display(eligibility), "-")

    @tag("batch_pages_signals")
    def test_reason_display_joins_reason_codes(self):
        user = User.objects.create_user(
            username="trial-admin-reason@example.com",
            email="trial-admin-reason@example.com",
            password="pw",
        )
        eligibility = UserTrialEligibility.objects.create(
            user=user,
            reason_codes=["fpjs_history_match", "multi_signal_history_match"],
        )

        self.assertEqual(
            self.admin.reason_display(eligibility),
            "fpjs_history_match, multi_signal_history_match",
        )
