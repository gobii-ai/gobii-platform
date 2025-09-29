from contextlib import ExitStack
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.models import UserBilling
from api.tasks.subscription_tasks import grant_monthly_free_credits
from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNamesChoices


User = get_user_model()


@tag("batch_subscription")
class GrantMonthlyFreeCreditsTaskTests(TestCase):
    def test_uses_billing_anchor_for_grant_dates(self):
        with timezone.override("UTC"):
            user = User.objects.create_user(
                username="anchor-user",
                email="anchor@example.com",
                password="password",
            )

            user.task_credits.all().delete()
            UserBilling.objects.update_or_create(
                user=user,
                defaults={"billing_cycle_anchor": 23},
            )

            period_start = date(2025, 9, 23)
            period_end = date(2025, 10, 22)
            expected_grant = timezone.make_aware(
                datetime.combine(period_start, datetime.min.time())
            )
            expected_expiration = timezone.make_aware(
                datetime.combine(period_end + timedelta(days=1), datetime.min.time())
            )

            with ExitStack() as stack:
                grant_mock = stack.enter_context(
                    patch(
                        "api.tasks.subscription_tasks.TaskCreditService.grant_subscription_credits"
                    )
                )
                stack.enter_context(
                    patch(
                        "api.tasks.subscription_tasks.get_users_due_for_monthly_grant",
                        return_value=[user],
                    )
                )
                stack.enter_context(
                    patch(
                        "api.tasks.subscription_tasks.filter_users_without_active_subscription",
                        return_value=[user],
                    )
                )
                stack.enter_context(
                    patch(
                        "api.tasks.subscription_tasks.BillingService.get_current_billing_period_from_day",
                        return_value=(period_start, period_end),
                    )
                )

                grant_monthly_free_credits()

        grant_mock.assert_called_once()
        call_kwargs = grant_mock.call_args.kwargs

        self.assertEqual(call_kwargs["grant_date"], expected_grant)
        self.assertEqual(call_kwargs["expiration_date"], expected_expiration)
        self.assertEqual(call_kwargs["plan"]["id"], PlanNamesChoices.FREE)

    def test_fallback_ignores_compensation_grants(self):
        with timezone.override("UTC"):
            user = User.objects.create_user(
                username="fallback-user",
                email="fallback@example.com",
                password="password",
            )

            user.task_credits.all().delete()
            UserBilling.objects.filter(user=user).delete()
            user.refresh_from_db()
            self.assertFalse(UserBilling.objects.filter(user=user).exists())

            plan_grant_date = timezone.make_aware(datetime(2025, 8, 23))
            plan_expiration = plan_grant_date + relativedelta(months=1)
            expected_grant = plan_grant_date + relativedelta(months=1)
            expected_expiration = plan_expiration + relativedelta(months=1)

            plan_stub = SimpleNamespace(
                granted_date=plan_grant_date,
                expiration_date=plan_expiration,
                grant_type=GrantTypeChoices.PLAN,
            )

            class StubQuerySet:
                def __init__(self, results):
                    self._results = results

                def order_by(self, *args, **kwargs):
                    return self

                def first(self):
                    return self._results[0] if self._results else None

            class StubManager:
                def __init__(self):
                    self.last_filter_kwargs = None

                def filter(self, **kwargs):
                    self.last_filter_kwargs = kwargs
                    return StubQuerySet([plan_stub])

            stub_manager = StubManager()

            class StubTaskCredit:
                objects = stub_manager

            original_get_model = apps.get_model

            def fake_get_model(app_label, model_name, *args, **kwargs):
                if app_label == "api" and model_name == "TaskCredit":
                    return StubTaskCredit
                return original_get_model(app_label, model_name, *args, **kwargs)

            with ExitStack() as stack:
                grant_mock = stack.enter_context(
                    patch(
                        "api.tasks.subscription_tasks.TaskCreditService.grant_subscription_credits"
                    )
                )
                stack.enter_context(
                    patch(
                        "api.tasks.subscription_tasks.apps.get_model",
                        side_effect=fake_get_model,
                    )
                )
                stack.enter_context(
                    patch(
                        "api.tasks.subscription_tasks.get_users_due_for_monthly_grant",
                        return_value=[user],
                    )
                )
                stack.enter_context(
                    patch(
                        "api.tasks.subscription_tasks.filter_users_without_active_subscription",
                        return_value=[user],
                    )
                )

                grant_monthly_free_credits()

        grant_mock.assert_called_once()
        call_kwargs = grant_mock.call_args.kwargs

        self.assertEqual(
            stub_manager.last_filter_kwargs,
            {
                "user": user,
                "grant_type": GrantTypeChoices.PLAN,
                "additional_task": False,
                "voided": False,
            },
        )

        self.assertEqual(call_kwargs["grant_date"], expected_grant)
        self.assertEqual(call_kwargs["expiration_date"], expected_expiration)
        self.assertEqual(call_kwargs["plan"]["id"], PlanNamesChoices.FREE)
