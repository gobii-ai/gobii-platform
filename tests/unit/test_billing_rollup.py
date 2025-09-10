from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase, tag
from django.contrib.auth import get_user_model
from django.utils import timezone

from api.models import BrowserUseAgent, BrowserUseAgentTask, PersistentAgent, PersistentAgentStep, UserBilling
from api.tasks.billing_rollup import rollup_and_meter_usage_task


User = get_user_model()


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

    @patch("api.tasks.billing_rollup.report_task_usage_to_stripe")
    @patch("api.tasks.billing_rollup.get_active_subscription")
    @patch("api.models.TaskCreditService.check_and_consume_credit_for_owner")
    def test_rollup_sums_and_marks_metered(self, mock_consume, mock_get_sub, mock_report):
        # Simulate active subscription (non-free)
        mock_get_sub.return_value = MagicMock()
        # Prevent credit errors on object creation
        mock_consume.return_value = {"success": True, "credit": None, "error_message": None}

        # Create unmetered usage in current period
        BrowserUseAgentTask.objects.create(agent=self.agent, user=self.user, prompt="x", credits_cost=Decimal("0.3"))
        BrowserUseAgentTask.objects.create(agent=self.agent, user=self.user, prompt="y", credits_cost=Decimal("0.6"))

        PersistentAgentStep.objects.create(agent=self.pa, description="z", credits_cost=Decimal("0.4"))

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
        mock_consume.return_value = {"success": True, "credit": None, "error_message": None}

        # Force a period where today is NOT the last day
        today = timezone.now().date()
        mock_period.return_value = (today - timedelta(days=5), today + timedelta(days=5))

        # Create unmetered usage totaling < 0.5 (rounds to 0)
        BrowserUseAgentTask.objects.create(agent=self.agent, user=self.user, prompt="x", credits_cost=Decimal("0.2"))
        PersistentAgentStep.objects.create(agent=self.pa, description="z", credits_cost=Decimal("0.2"))

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
        mock_consume.return_value = {"success": True, "credit": None, "error_message": None}

        # Force a period where today IS the last day
        today = timezone.now().date()
        mock_period.return_value = (today - timedelta(days=5), today)

        # Create unmetered usage totaling < 0.5 (rounds to 0)
        BrowserUseAgentTask.objects.create(agent=self.agent, user=self.user, prompt="x", credits_cost=Decimal("0.1"))
        PersistentAgentStep.objects.create(agent=self.pa, description="z", credits_cost=Decimal("0.2"))

        rollup_and_meter_usage_task()

        # No Stripe call, but rows should be marked metered at period end
        mock_report.assert_not_called()
        self.assertEqual(BrowserUseAgentTask.objects.filter(user=self.user, metered=True).count(), 1)
        self.assertTrue(PersistentAgentStep.objects.filter(agent=self.pa, metered=True).exists())

    @patch("api.tasks.billing_rollup.report_task_usage_to_stripe")
    @patch("api.tasks.billing_rollup.get_active_subscription")
    @patch("api.tasks.billing_rollup.BillingService.get_current_billing_period_for_user")
    @patch("api.models.TaskCreditService.check_and_consume_credit_for_owner")
    def test_accumulate_and_bill_when_rounds_up(self, mock_consume, mock_period, mock_get_sub, mock_report):
        from datetime import timedelta
        # Simulate active subscription (non-free)
        mock_get_sub.return_value = MagicMock()
        mock_consume.return_value = {"success": True, "credit": None, "error_message": None}

        # Always not the last day of the period for both runs
        today = timezone.now().date()
        mock_period.return_value = (today - timedelta(days=5), today + timedelta(days=5))

        # First: create partial usage that rounds to 0 (carry-forward)
        BrowserUseAgentTask.objects.create(agent=self.agent, user=self.user, prompt="x", credits_cost=Decimal("0.2"))
        PersistentAgentStep.objects.create(agent=self.pa, description="z", credits_cost=Decimal("0.2"))

        rollup_and_meter_usage_task()
        mock_report.assert_not_called()
        self.assertEqual(BrowserUseAgentTask.objects.filter(user=self.user, metered=True).count(), 0)
        self.assertEqual(PersistentAgentStep.objects.filter(agent=self.pa, metered=True).count(), 0)

        # Second: add more usage so cumulative rounds up to 1
        BrowserUseAgentTask.objects.create(agent=self.agent, user=self.user, prompt="y", credits_cost=Decimal("0.3"))

        rollup_and_meter_usage_task()

        # Stripe should be called once with quantity 1 (0.2 + 0.2 + 0.3 = 0.7 -> 1)
        mock_report.assert_called_once()
        args, kwargs = mock_report.call_args
        qty = kwargs.get("quantity", args[1] if len(args) > 1 else None)
        self.assertEqual(qty, 1)

        # All included rows now marked metered
        self.assertEqual(BrowserUseAgentTask.objects.filter(user=self.user, metered=True).count(), 2)
        self.assertEqual(PersistentAgentStep.objects.filter(agent=self.pa, metered=True).count(), 1)
