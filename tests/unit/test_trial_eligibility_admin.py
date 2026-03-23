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


@tag("batch_pages")
class UserTrialEligibilityAdminTests(TestCase):
    def setUp(self):
        self.admin = UserTrialEligibilityAdmin(UserTrialEligibility, AdminSite())

    @tag("batch_pages")
    def test_effective_status_display_handles_add_form(self):
        self.assertEqual(self.admin.effective_status_display(None), "-")

    @tag("batch_pages")
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
