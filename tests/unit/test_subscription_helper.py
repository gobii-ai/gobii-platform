from datetime import datetime, timezone as datetime_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone
from unittest.mock import patch

from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    UserBilling,
    Organization,
    OrganizationBilling,
    TaskCredit,
)
from constants.plans import PlanNames
from constants.grant_types import GrantTypeChoices
from util.subscription_helper import (
    mark_user_billing_with_plan,
    mark_organization_billing_with_plan,
    downgrade_organization_to_free_plan,
    get_users_due_for_monthly_grant,
)


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

    @tag("batch_subscription")
    def test_upgrade_clears_agent_daily_credit_limit(self):
        """Daily credit caps are removed from agents when the user upgrades."""
        billing = UserBilling.objects.get(user=self.user)
        billing.subscription = PlanNames.FREE
        billing.save(update_fields=["subscription"])

        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Limitless Agent")

        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Capped Agent",
            charter="Upgrade charter",
            browser_use_agent=browser_agent,
            daily_credit_limit=5,
        )

        mark_user_billing_with_plan(self.user, PlanNames.STARTUP)

        agent.refresh_from_db()
        self.assertIsNone(agent.daily_credit_limit)


@tag("batch_subscription")
class MarkOrganizationBillingWithPlanTests(TestCase):
    """Ensure organization billing records are synced with plan updates."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner@example.com",
            email="owner@example.com",
            password="ownerpass123",
        )
        self.organization = Organization.objects.create(
            name="Acme Corp",
            slug="acme-corp",
            created_by=self.owner,
        )

    def test_creates_and_updates_billing_record(self):
        OrganizationBilling.objects.filter(organization=self.organization).delete()

        with patch("util.subscription_helper.timezone.now") as mock_now:
            mock_now.return_value = datetime(2025, 3, 15, tzinfo=datetime_timezone.utc)
            mark_organization_billing_with_plan(self.organization, PlanNames.ORG_TEAM)

        billing = OrganizationBilling.objects.get(organization=self.organization)
        self.assertEqual(billing.subscription, PlanNames.ORG_TEAM)
        self.assertEqual(billing.billing_cycle_anchor, 15)

        with patch("util.subscription_helper.timezone.now") as mock_now:
            mock_now.return_value = datetime(2025, 4, 2, tzinfo=datetime_timezone.utc)
            mark_organization_billing_with_plan(self.organization, PlanNames.ORG_TEAM, update_anchor=False)

        billing.refresh_from_db()
        self.assertEqual(billing.subscription, PlanNames.ORG_TEAM)
        self.assertEqual(billing.billing_cycle_anchor, 15)

    def test_downgrade_sets_timestamp(self):
        with patch("util.subscription_helper.timezone.now") as mock_now:
            mock_now.return_value = datetime(2025, 5, 5, tzinfo=datetime_timezone.utc)
            mark_organization_billing_with_plan(self.organization, PlanNames.ORG_TEAM)

        with patch("util.subscription_helper.timezone.now") as mock_now:
            mock_now.return_value = datetime(2025, 6, 1, tzinfo=datetime_timezone.utc)
            downgrade_organization_to_free_plan(self.organization)

        billing = OrganizationBilling.objects.get(organization=self.organization)
        self.assertEqual(billing.subscription, PlanNames.FREE)
        self.assertEqual(billing.billing_cycle_anchor, 5)
        self.assertEqual(billing.downgraded_at, datetime(2025, 6, 1, tzinfo=datetime_timezone.utc))


@tag("batch_subscription")
class GetUsersDueForMonthlyGrantTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="due-user@example.com",
            email="due-user@example.com",
            password="password123",
        )
        self.other = User.objects.create_user(
            username="current-user@example.com",
            email="current-user@example.com",
            password="password123",
        )
        self.user.task_credits.all().delete()
        self.other.task_credits.all().delete()
        UserBilling.objects.filter(user__in=[self.user, self.other]).delete()

    @tag("batch_subscription")
    def test_returns_user_when_current_period_missing_grant(self):
        with timezone.override("UTC"):
            UserBilling.objects.update_or_create(
                user=self.user,
                defaults={"billing_cycle_anchor": 6, "subscription": PlanNames.FREE},
            )
            TaskCredit.objects.create(
                user=self.user,
                credits=10,
                credits_used=0,
                granted_date=timezone.make_aware(datetime(2025, 10, 6)),
                expiration_date=timezone.make_aware(datetime(2025, 11, 6)),
                plan=PlanNames.FREE,
                grant_type=GrantTypeChoices.PLAN,
                additional_task=False,
                voided=False,
            )

            with patch("util.subscription_helper.timezone.now") as mock_now:
                mock_now.return_value = datetime(2025, 11, 6, tzinfo=datetime_timezone.utc)
                results = get_users_due_for_monthly_grant()

        self.assertIn(self.user, results)

    @tag("batch_subscription")
    def test_skips_user_with_grant_in_current_period(self):
        with timezone.override("UTC"):
            UserBilling.objects.update_or_create(
                user=self.other,
                defaults={"billing_cycle_anchor": 6, "subscription": PlanNames.FREE},
            )
            TaskCredit.objects.create(
                user=self.other,
                credits=10,
                credits_used=0,
                granted_date=timezone.make_aware(datetime(2025, 11, 6)),
                expiration_date=timezone.make_aware(datetime(2025, 12, 6)),
                plan=PlanNames.FREE,
                grant_type=GrantTypeChoices.PLAN,
                additional_task=False,
                voided=False,
            )

            with patch("util.subscription_helper.timezone.now") as mock_now:
                mock_now.return_value = datetime(2025, 11, 6, tzinfo=datetime_timezone.utc)
                results = get_users_due_for_monthly_grant()

        self.assertNotIn(self.other, results)
