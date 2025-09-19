from datetime import datetime, date, timezone as dt_timezone, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from django.test import TestCase, tag
from django.contrib.auth import get_user_model
from django.utils import timezone

from api.models import (
    BrowserUseAgent,
    BrowserUseAgentTask,
    PersistentAgent,
    PersistentAgentStep,
    UserBilling,
    TaskCredit,
    Organization,
    MeteringBatch,
)
from api.tasks.billing_rollup import rollup_and_meter_usage_task, _to_aware_dt


User = get_user_model()

@tag("batch_billing_rollup")
class ToAwareDtHelperTests(TestCase):
    def test_returns_none_for_unsupported(self):
        with timezone.override("UTC"):
            self.assertIsNone(_to_aware_dt(None, as_start=True))
            self.assertIsNone(_to_aware_dt("not-a-date", as_start=True))

    def test_preserves_aware_datetime(self):
        aware = timezone.make_aware(datetime(2025, 9, 16, 12, 0), timezone=dt_timezone.utc)
        with timezone.override("America/New_York"):
            result = _to_aware_dt(aware, as_start=True)
        self.assertIs(result, aware)

    def test_converts_naive_datetime_using_current_timezone(self):
        naive = datetime(2025, 9, 16, 9, 30)
        with timezone.override("America/New_York"):
            result = _to_aware_dt(naive, as_start=True)
            self.assertTrue(timezone.is_aware(result))
            self.assertEqual(result.tzinfo, timezone.get_current_timezone())
            self.assertEqual(result.hour, 9)
            self.assertEqual(result.minute, 30)

    def test_converts_date_boundaries(self):
        with timezone.override("UTC"):
            start = _to_aware_dt(date(2025, 9, 1), as_start=True)
            end = _to_aware_dt(date(2025, 9, 1), as_start=False)

        self.assertTrue(timezone.is_aware(start))
        self.assertTrue(timezone.is_aware(end))
        self.assertEqual(start.hour, 0)
        self.assertEqual(start.day, 1)
        self.assertEqual(end.hour, 0)
        self.assertEqual(end.day, 2)

    def test_parses_datetime_and_date_strings(self):
        with timezone.override("UTC"):
            iso_result = _to_aware_dt("2025-09-10T05:15:00Z", as_start=True)
            date_result = _to_aware_dt("2025-09-10", as_start=False)

        self.assertTrue(timezone.is_aware(iso_result))
        self.assertEqual(iso_result.hour, 5)
        self.assertEqual(iso_result.minute, 15)

        self.assertTrue(timezone.is_aware(date_result))
        self.assertEqual(date_result.day, 10)
        self.assertEqual(date_result.hour, 0)


@tag("batch_billing_rollup")
class BillingRollupTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="meter_user", email="meter@example.com")
        # Ensure user has a billing anchor so period calc is deterministic
        ub = UserBilling.objects.get(user=self.user)
        ub.subscription = "startup"
        ub.billing_cycle_anchor = 1
        ub.save(update_fields=["subscription", "billing_cycle_anchor"])

        # Minimal agent setup
        self.agent = BrowserUseAgent.objects.create(user=self.user, name="Agent")
        self.pa = PersistentAgent.objects.create(user=self.user, name="PA", charter="do", browser_use_agent=self.agent)

        now = timezone.now()
        self.additional_credit = TaskCredit.objects.create(
            user=self.user,
            credits=Decimal("1"),
            credits_used=Decimal("1"),
            granted_date=now,
            expiration_date=now + timedelta(days=30),
            additional_task=True,
        )

    @patch("api.tasks.billing_rollup.report_task_usage_to_stripe")
    @patch("api.tasks.billing_rollup.get_active_subscription")
    @patch("api.models.TaskCreditService.check_and_consume_credit_for_owner")
    def test_rollup_sums_and_marks_metered(self, mock_consume, mock_get_sub, mock_report):
        # Simulate active subscription (non-free)
        mock_get_sub.return_value = MagicMock()
        # Prevent credit errors on object creation
        mock_consume.return_value = {"success": True, "credit": self.additional_credit, "error_message": None}

        # Create unmetered usage in current period
        BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="x",
            credits_cost=Decimal("0.3"),
            task_credit=self.additional_credit,
        )
        BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="y",
            credits_cost=Decimal("0.6"),
            task_credit=self.additional_credit,
        )

        PersistentAgentStep.objects.create(
            agent=self.pa,
            description="z",
            credits_cost=Decimal("0.4"),
            task_credit=self.additional_credit,
        )

        # Total = 1.3 -> rounded (half-up) = 1
        processed = rollup_and_meter_usage_task()

        self.assertEqual(processed, 1)
        mock_report.assert_called_once()
        args, kwargs = mock_report.call_args
        qty = kwargs.get("quantity", args[1] if len(args) > 1 else None)
        self.assertEqual(qty, 1)

        # Verify rows are marked metered
        self.assertEqual(BrowserUseAgentTask.objects.filter(user=self.user, metered=True).count(), 2)
        self.assertTrue(PersistentAgentStep.objects.filter(agent=self.pa, metered=True).exists())

    @patch("api.tasks.billing_rollup.report_task_usage_to_stripe")
    @patch("api.tasks.billing_rollup.get_active_subscription")
    @patch("api.tasks.billing_rollup.BillingService.get_current_billing_period_for_user")
    @patch("api.models.TaskCreditService.check_and_consume_credit_for_owner")
    def test_carry_forward_zero_not_last_day(self, mock_consume, mock_period, mock_get_sub, mock_report):
        from datetime import timedelta
        # Simulate active subscription (non-free)
        mock_get_sub.return_value = MagicMock()
        mock_consume.return_value = {"success": True, "credit": self.additional_credit, "error_message": None}

        # Force a period where today is NOT the last day
        today = timezone.now().date()
        mock_period.return_value = (today - timedelta(days=5), today + timedelta(days=5))

        # Create unmetered usage totaling < 0.5 (rounds to 0)
        BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="x",
            credits_cost=Decimal("0.2"),
            task_credit=self.additional_credit,
        )
        PersistentAgentStep.objects.create(
            agent=self.pa,
            description="z",
            credits_cost=Decimal("0.2"),
            task_credit=self.additional_credit,
        )

        rollup_and_meter_usage_task()

        # No Stripe call and no marking metered yet (carry-forward)
        mock_report.assert_not_called()
        self.assertEqual(BrowserUseAgentTask.objects.filter(user=self.user, metered=True).count(), 0)
        self.assertEqual(PersistentAgentStep.objects.filter(agent=self.pa, metered=True).count(), 0)

    @patch("api.tasks.billing_rollup.report_task_usage_to_stripe")
    @patch("api.tasks.billing_rollup.get_active_subscription")
    @patch("api.tasks.billing_rollup.BillingService.get_current_billing_period_for_user")
    @patch("api.models.TaskCreditService.check_and_consume_credit_for_owner")
    def test_finalize_zero_on_last_day_marks_metered(self, mock_consume, mock_period, mock_get_sub, mock_report):
        from datetime import timedelta
        # Simulate active subscription (non-free)
        mock_get_sub.return_value = MagicMock()
        mock_consume.return_value = {"success": True, "credit": self.additional_credit, "error_message": None}

        # Force a period where today IS the last day
        today = timezone.now().date()
        mock_period.return_value = (today - timedelta(days=5), today)

        # Create unmetered usage totaling < 0.5 (rounds to 0)
        BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="x",
            credits_cost=Decimal("0.1"),
            task_credit=self.additional_credit,
        )
        PersistentAgentStep.objects.create(
            agent=self.pa,
            description="z",
            credits_cost=Decimal("0.2"),
            task_credit=self.additional_credit,
        )

        rollup_and_meter_usage_task()

        # No Stripe call, but rows should be marked metered at period end
        mock_report.assert_not_called()
        self.assertEqual(BrowserUseAgentTask.objects.filter(user=self.user, metered=True).count(), 1)
        self.assertTrue(PersistentAgentStep.objects.filter(agent=self.pa, metered=True).exists())

    @patch("api.tasks.billing_rollup.report_organization_task_usage_to_stripe")
    @patch("api.models.TaskCreditService.check_and_consume_credit_for_owner")
    def test_rollup_handles_organization_overage(self, mock_consume, mock_report):
        mock_consume.return_value = {"success": True, "credit": self.additional_credit, "error_message": None}

        org = Organization.objects.create(name="Org", slug="org", created_by=self.user)
        billing = org.billing
        billing.stripe_customer_id = "cus_test"
        billing.save(update_fields=["stripe_customer_id"])

        org_credit = TaskCredit.objects.create(
            organization=org,
            credits=Decimal("1.0"),
            credits_used=Decimal("1.0"),
            granted_date=timezone.now(),
            expiration_date=timezone.now() + timedelta(days=30),
            additional_task=True,
        )

        BrowserUseAgentTask.objects.create(
            agent=None,
            user=None,
            prompt="org",
            credits_cost=Decimal("1.0"),
            task_credit=org_credit,
        )

        processed = rollup_and_meter_usage_task()

        mock_report.assert_called_once()
        self.assertEqual(processed, 1)
        self.assertEqual(
            BrowserUseAgentTask.objects.filter(task_credit=org_credit, metered=True).count(),
            1,
        )
        batch = MeteringBatch.objects.get(organization=org)
        self.assertEqual(batch.rounded_quantity, 1)

    @patch("api.tasks.billing_rollup.report_task_usage_to_stripe")
    @patch("api.tasks.billing_rollup.get_active_subscription")
    @patch("api.tasks.billing_rollup.BillingService.get_current_billing_period_for_user")
    @patch("api.models.TaskCreditService.check_and_consume_credit_for_owner")
    def test_accumulate_and_bill_when_rounds_up(self, mock_consume, mock_period, mock_get_sub, mock_report):
        from datetime import timedelta
        # Simulate active subscription (non-free)
        mock_get_sub.return_value = MagicMock()
        mock_consume.return_value = {"success": True, "credit": self.additional_credit, "error_message": None}

        # Always not the last day of the period for both runs
        today = timezone.now().date()
        mock_period.return_value = (today - timedelta(days=5), today + timedelta(days=5))

        # First: create partial usage that rounds to 0 (carry-forward)
        BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="x",
            credits_cost=Decimal("0.2"),
            task_credit=self.additional_credit,
        )
        PersistentAgentStep.objects.create(
            agent=self.pa,
            description="z",
            credits_cost=Decimal("0.2"),
            task_credit=self.additional_credit,
        )

        rollup_and_meter_usage_task()
        mock_report.assert_not_called()
        self.assertEqual(BrowserUseAgentTask.objects.filter(user=self.user, metered=True).count(), 0)
        self.assertEqual(PersistentAgentStep.objects.filter(agent=self.pa, metered=True).count(), 0)

        # Second: add more usage so cumulative rounds up to 1
        BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="y",
            credits_cost=Decimal("0.3"),
            task_credit=self.additional_credit,
        )

        rollup_and_meter_usage_task()

        # Stripe should be called once with quantity 1 (0.2 + 0.2 + 0.3 = 0.7 -> 1)
        mock_report.assert_called_once()
        args, kwargs = mock_report.call_args
        qty = kwargs.get("quantity", args[1] if len(args) > 1 else None)
        self.assertEqual(qty, 1)

        # All included rows now marked metered
        self.assertEqual(BrowserUseAgentTask.objects.filter(user=self.user, metered=True).count(), 2)
        self.assertEqual(PersistentAgentStep.objects.filter(agent=self.pa, metered=True).count(), 1)

    def test_stripe_bounds_finalize_zero_usage(self):
        with patch("api.models.TaskCreditService.check_and_consume_credit_for_owner") as mock_consume, \
            patch("api.tasks.billing_rollup.get_active_subscription") as mock_get_sub, \
            patch("api.tasks.billing_rollup.BillingService.get_current_billing_period_for_user") as mock_period, \
            patch("api.tasks.billing_rollup.timezone.now") as mock_now, \
            patch("api.tasks.billing_rollup.report_task_usage_to_stripe") as mock_report, \
            patch("api.tasks.billing_rollup.logger.exception") as mock_log_exception:

            mock_consume.return_value = {"success": True, "credit": self.additional_credit, "error_message": None}

            naive_start = datetime(2025, 9, 1, 0, 0, 0)
            naive_end = datetime(2025, 9, 30, 23, 59, 59)
            sub = SimpleNamespace(current_period_start=naive_start, current_period_end=naive_end, status="active")
            mock_get_sub.return_value = sub

            with timezone.override("UTC"):
                stripe_start = _to_aware_dt(naive_start, as_start=True)
                stripe_end = _to_aware_dt(naive_end, as_start=False)
                self.assertIsNotNone(stripe_start)
                self.assertIsNotNone(stripe_end)
                mock_now.return_value = timezone.make_aware(datetime(2025, 9, 15, 12, 0, 0), timezone=dt_timezone.utc)

                BrowserUseAgentTask.objects.create(
                    agent=self.agent,
                    user=self.user,
                    prompt="x",
                    credits_cost=Decimal("0.2"),
                    task_credit=self.additional_credit,
                )
                PersistentAgentStep.objects.create(
                    agent=self.pa,
                    description="z",
                    credits_cost=Decimal("0.2"),
                    task_credit=self.additional_credit,
                )

                mock_now.return_value = timezone.make_aware(datetime(2025, 10, 1, 0, 0, 0), timezone=dt_timezone.utc)

                self.assertEqual(
                    BrowserUseAgentTask.objects.filter(user=self.user, metered=False).count(),
                    1,
                )
                self.assertEqual(
                    PersistentAgentStep.objects.filter(agent=self.pa, metered=False).count(),
                    1,
                )

                task = BrowserUseAgentTask.objects.get(user=self.user)
                step = PersistentAgentStep.objects.get(agent=self.pa)
                self.assertIsNone(task.meter_batch_key)
                self.assertIsNone(step.meter_batch_key)
                task_window_count = BrowserUseAgentTask.objects.filter(
                    user=self.user,
                    created_at__gte=stripe_start,
                    created_at__lt=stripe_end,
                    meter_batch_key__isnull=True,
                    metered=False,
                ).count()
                step_window_count = PersistentAgentStep.objects.filter(
                    agent__user=self.user,
                    created_at__gte=stripe_start,
                    created_at__lt=stripe_end,
                    meter_batch_key__isnull=True,
                    metered=False,
                ).count()
                self.assertGreater(
                    task_window_count + step_window_count,
                    0,
                    f"stripe_start={stripe_start}, stripe_end={stripe_end}, task_created={task.created_at}, step_created={step.created_at}"
                )

                processed = rollup_and_meter_usage_task()

            remaining_tasks = BrowserUseAgentTask.objects.filter(user=self.user, metered=False).count()
            remaining_steps = PersistentAgentStep.objects.filter(agent=self.pa, metered=False).count()
            self.assertEqual(
                processed,
                1,
                f"mock_calls={mock_log_exception.call_args_list}, sub_calls={mock_get_sub.call_count}, remaining_tasks={remaining_tasks}, remaining_steps={remaining_steps}"
            )
            mock_report.assert_not_called()
            mock_period.assert_not_called()
            mock_log_exception.assert_not_called()
            mock_get_sub.assert_called_once()

            self.assertEqual(BrowserUseAgentTask.objects.filter(user=self.user, metered=True).count(), 1)
            self.assertTrue(PersistentAgentStep.objects.filter(agent=self.pa, metered=True).exists())
