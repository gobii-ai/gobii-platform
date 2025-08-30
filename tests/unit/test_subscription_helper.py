from datetime import datetime

from django.test import TestCase, tag
from django.contrib.auth import get_user_model
from unittest.mock import patch

from api.models import UserBilling
from constants.plans import PlanNames
from util.subscription_helper import mark_user_billing_with_plan


User = get_user_model()


@tag("batch_subscription")
class MarkUserBillingWithPlanTests(TestCase):
    """Tests for the mark_user_billing_with_plan helper."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="planuser@example.com",
            email="planuser@example.com",
            password="testpass123",
        )

    @tag("batch_subscription")
    def test_creates_billing_record_when_missing(self):
        """A billing record is created when one does not exist."""
        UserBilling.objects.filter(user=self.user).delete()

        with patch("util.subscription_helper.timezone.now") as mock_now:
            mock_now.return_value = datetime(2024, 5, 9)
            mark_user_billing_with_plan(self.user, PlanNames.STARTUP)

        billing = UserBilling.objects.get(user=self.user)
        self.assertEqual(billing.subscription, PlanNames.STARTUP)
        self.assertEqual(billing.billing_cycle_anchor, 9)

    @tag("batch_subscription")
    def test_updates_existing_record_without_duplication(self):
        """Existing billing records are updated in place without creating duplicates."""
        with patch("util.subscription_helper.timezone.now") as mock_now:
            mock_now.return_value = datetime(2024, 6, 2)
            mark_user_billing_with_plan(self.user, PlanNames.STARTUP)

        self.assertEqual(UserBilling.objects.filter(user=self.user).count(), 1)
        billing = UserBilling.objects.get(user=self.user)
        self.assertEqual(billing.subscription, PlanNames.STARTUP)
        self.assertEqual(billing.billing_cycle_anchor, 2)

        # Call again with a different plan; anchor should update and still only one record
        with patch("util.subscription_helper.timezone.now") as mock_now:
            mock_now.return_value = datetime(2024, 7, 4)
            mark_user_billing_with_plan(self.user, PlanNames.FREE)

        self.assertEqual(UserBilling.objects.filter(user=self.user).count(), 1)
        billing.refresh_from_db()
        self.assertEqual(billing.subscription, PlanNames.FREE)
        self.assertEqual(billing.billing_cycle_anchor, 4)

    @tag("batch_subscription")
    def test_update_anchor_false_keeps_existing_anchor(self):
        """The billing cycle anchor remains unchanged when update_anchor is False."""
        billing = UserBilling.objects.get(user=self.user)
        billing.billing_cycle_anchor = 5
        billing.subscription = PlanNames.FREE
        billing.save()

        with patch("util.subscription_helper.timezone.now") as mock_now:
            mock_now.return_value = datetime(2024, 8, 30)
            mark_user_billing_with_plan(self.user, PlanNames.STARTUP, update_anchor=False)

        billing.refresh_from_db()
        self.assertEqual(billing.subscription, PlanNames.STARTUP)
        self.assertEqual(billing.billing_cycle_anchor, 5)
