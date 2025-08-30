from datetime import datetime

from django.test import TestCase
from django.contrib.auth import get_user_model
from unittest.mock import MagicMock, patch

from tasks.services import TaskCreditService
from util.constants.task_constants import TASKS_UNLIMITED


User = get_user_model()


class TaskCreditServiceCalculateAvailableTasksTests(TestCase):
    @patch("tasks.services.TaskCreditService.get_user_task_credits_used")
    @patch("tasks.services.TaskCreditService.get_tasks_entitled")
    def test_calculate_available_tasks_regular(self, mock_entitled, mock_used):
        user = User.objects.create(username="user1")
        mock_entitled.return_value = 10
        mock_used.return_value = 4

        available = TaskCreditService.calculate_available_tasks(user)

        self.assertEqual(available, 6)
        mock_entitled.assert_called_once_with(user)
        mock_used.assert_called_once_with(user, None)

    @patch("tasks.services.TaskCreditService.get_user_task_credits_used")
    @patch("tasks.services.TaskCreditService.get_tasks_entitled")
    def test_calculate_available_tasks_unlimited(self, mock_entitled, mock_used):
        user = User.objects.create(username="user2")
        mock_entitled.return_value = TASKS_UNLIMITED

        available = TaskCreditService.calculate_available_tasks(user)

        self.assertEqual(available, TASKS_UNLIMITED)
        mock_used.assert_not_called()


class TaskCreditServiceGrantSubscriptionCreditsTests(TestCase):
    @patch("tasks.services.apps.get_model")
    def test_grant_subscription_credits_avoids_duplicate_invoice(self, mock_get_model):
        user = User.objects.create(username="user3")
        TaskCredit = MagicMock()
        mock_get_model.return_value = TaskCredit
        TaskCredit.objects.filter.return_value.first.return_value = MagicMock()

        granted = TaskCreditService.grant_subscription_credits(user, invoice_id="inv-1")

        self.assertEqual(granted, 0)
        TaskCredit.objects.create.assert_not_called()

    @patch("tasks.services.timezone")
    @patch("tasks.services.apps.get_model")
    @patch("tasks.services.get_active_subscription")
    @patch("tasks.services.get_user_plan")
    def test_grant_subscription_credits_sets_expiration_to_subscription_end(self, mock_plan, mock_subscription, mock_get_model, mock_timezone):
        user = User.objects.create(username="user4")
        TaskCredit = MagicMock()
        mock_get_model.return_value = TaskCredit
        TaskCredit.objects.filter.return_value.first.return_value = None

        plan = {"id": "startup", "monthly_task_credits": 5}
        mock_plan.return_value = plan

        sub = MagicMock()
        sub.current_period_end = datetime(2024, 1, 31)
        mock_subscription.return_value = sub

        mock_timezone.now.return_value = datetime(2024, 1, 1)

        TaskCreditService.grant_subscription_credits(user, invoice_id="inv-2")

        TaskCredit.objects.create.assert_called_once()
        args, kwargs = TaskCredit.objects.create.call_args
        self.assertEqual(kwargs["expiration_date"], sub.current_period_end)
        self.assertEqual(kwargs["credits"], plan["monthly_task_credits"])


class TaskCreditServiceConsumeCreditTests(TestCase):
    @patch("tasks.services.TaskCreditService.handle_task_threshold")
    @patch("tasks.services.report_task_usage_to_stripe")
    @patch("tasks.services.apps.get_model")
    def test_consume_credit_without_additional_task(self, mock_get_model, mock_report, mock_handle):
        user = User.objects.create(username="user5")
        TaskCredit = MagicMock()
        mock_get_model.return_value = TaskCredit
        credit = MagicMock(credits_used=0)
        select = TaskCredit.objects.select_for_update.return_value
        filt = select.filter.return_value
        ordered = filt.order_by.return_value
        ordered.first.return_value = credit

        def refresh():
            credit.credits_used = 1
        credit.refresh_from_db.side_effect = refresh

        result = TaskCreditService.consume_credit(user)

        self.assertIs(result, credit)
        credit.save.assert_called_once()
        credit.refresh_from_db.assert_called_once()
        mock_report.assert_called_once_with(user)
        mock_handle.assert_called_once_with(user)

    @patch("tasks.services.apps.get_model")
    @patch("tasks.services.get_user_plan")
    @patch("tasks.services.BillingService.get_current_billing_period_for_user")
    @patch("tasks.services.report_task_usage_to_stripe")
    @patch("tasks.services.TaskCreditService.handle_task_threshold")
    def test_consume_credit_with_additional_task(self, mock_handle, mock_report, mock_period, mock_plan, mock_get_model):
        user = User.objects.create(username="user6")
        TaskCredit = MagicMock()
        mock_get_model.return_value = TaskCredit
        credit = MagicMock(credits_used=0)
        TaskCredit.objects.create.return_value = credit
        mock_plan.return_value = {"id": "startup"}
        mock_period.return_value = (datetime(2024, 1, 1), datetime(2024, 2, 1))

        def refresh():
            credit.credits_used = 1
        credit.refresh_from_db.side_effect = refresh

        result = TaskCreditService.consume_credit(user, additional_task=True)

        TaskCredit.objects.create.assert_called_once()
        self.assertIs(result, credit)
        mock_report.assert_called_once_with(user)
        mock_handle.assert_called_once_with(user)


class TaskCreditServiceGetTasksEntitledTests(TestCase):
    @patch("tasks.services.get_user_extra_task_limit")
    @patch("tasks.services.apps.get_model")
    @patch("tasks.services.get_user_plan")
    def test_get_tasks_entitled_sums_granted_and_extra(self, mock_plan, mock_get_model, mock_extra):
        user = User.objects.create(username="user7")
        mock_plan.return_value = {"id": "PRO", "monthly_task_credits": 5}
        mock_extra.return_value = 2
        TaskCredit = MagicMock()
        mock_get_model.return_value = TaskCredit
        TaskCredit.objects.filter.return_value.aggregate.return_value = {"total_granted": 10}

        result = TaskCreditService.get_tasks_entitled(user)

        self.assertEqual(result, 12)

    @patch("tasks.services.get_user_extra_task_limit")
    @patch("tasks.services.get_user_plan")
    def test_get_tasks_entitled_unlimited_extra(self, mock_plan, mock_extra):
        user = User.objects.create(username="user8")
        mock_plan.return_value = {"id": "startup"}
        mock_extra.return_value = TASKS_UNLIMITED

        result = TaskCreditService.get_tasks_entitled(user)

        self.assertEqual(result, TASKS_UNLIMITED)


class TaskCreditServiceCalculateUsedPctTests(TestCase):
    @patch("tasks.services.TaskCreditService.get_tasks_entitled")
    @patch("tasks.services.TaskCreditService.get_user_total_tasks_used")
    def test_calculate_used_pct_normal_and_capped(self, mock_used, mock_entitled):
        user = User.objects.create(username="user9")
        mock_used.return_value = 5
        mock_entitled.return_value = 10
        self.assertEqual(TaskCreditService.calculate_used_pct(user), 50.0)

    @patch("tasks.services.TaskCreditService.get_user_total_tasks_used")
    @patch("tasks.services.TaskCreditService.get_tasks_entitled")
    def test_calculate_used_pct_capped_at_100(self, mock_used, mock_entitled):
        user = User.objects.create(username="user9")
        mock_entitled.return_value = 20
        mock_used.return_value = 20
        self.assertEqual(TaskCreditService.calculate_used_pct(user), 100.0)

    @patch("tasks.services.TaskCreditService.get_user_total_tasks_used")
    @patch("tasks.services.TaskCreditService.get_tasks_entitled")
    def test_calculate_used_pct_with_zero_entitled(self, mock_used, mock_entitled):
        user = User.objects.create(username="user9")
        mock_entitled.return_value = 0
        mock_used.return_value = 5
        self.assertEqual(TaskCreditService.calculate_used_pct(user), 0.0)


class TaskCreditServiceHandleThresholdTests(TestCase):
    @patch("tasks.services.apps.get_model")
    @patch("tasks.services.Analytics.publish_threshold_event")
    def test_handle_task_threshold_triggers_notifications(self, mock_publish, mock_get_model):
        user = User.objects.create(username="user10")
        Usage = MagicMock()
        mock_get_model.return_value = Usage

        # First call crossing 75%
        Usage.objects.get_or_create.return_value = (MagicMock(), True)
        with patch.object(TaskCreditService, "get_tasks_entitled", return_value=100), \
             patch.object(TaskCreditService, "get_user_total_tasks_used", return_value=80):
            TaskCreditService.handle_task_threshold(user)
        mock_publish.assert_called_once()

        # Second call crossing 90%, 75% already sent
        mock_publish.reset_mock()
        Usage.objects.get_or_create.side_effect = [
            (MagicMock(), False),  # 75% already exists
            (MagicMock(), True),   # 90% newly crossed
        ]
        with patch.object(TaskCreditService, "get_tasks_entitled", return_value=100), \
             patch.object(TaskCreditService, "get_user_total_tasks_used", return_value=95):
            TaskCreditService.handle_task_threshold(user)
        mock_publish.assert_called_once()
