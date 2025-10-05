from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from api.models import TaskCredit
from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNamesChoices


@tag("batch_task_credits")
class GrantCreditsByUserIdsAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin_user = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password123",
        )
        cls.recipient_user = User.objects.create_user(
            username="recipient",
            email="recipient@example.com",
            password="password123",
        )

    def setUp(self):
        self.client.force_login(self.admin_user)
        TaskCredit.objects.all().delete()

    def _split_datetime(self, value):
        value = value.replace(microsecond=0)
        return value.strftime("%Y-%m-%d"), value.strftime("%H:%M:%S")

    def test_expiration_date_is_required(self):
        url = reverse("admin:api_taskcredit_grant_by_user_ids")
        grant_date = timezone.localtime()
        grant_date_date, grant_date_time = self._split_datetime(grant_date)

        response = self.client.post(
            url,
            data={
                "user_ids": str(self.recipient_user.id),
                "plan": PlanNamesChoices.STARTUP,
                "credits": "5",
                "grant_type": GrantTypeChoices.PROMO,
                "grant_date_0": grant_date_date,
                "grant_date_1": grant_date_time,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        self.assertFalse(TaskCredit.objects.exists())

    def test_grant_uses_submitted_expiration_date(self):
        url = reverse("admin:api_taskcredit_grant_by_user_ids")
        grant_date = timezone.localtime().replace(microsecond=0)
        expiration_date = grant_date + timedelta(days=7)
        grant_date_date, grant_date_time = self._split_datetime(grant_date)
        expiration_date_date, expiration_date_time = self._split_datetime(expiration_date)

        response = self.client.post(
            url,
            data={
                "user_ids": str(self.recipient_user.id),
                "plan": PlanNamesChoices.STARTUP,
                "credits": "5",
                "grant_type": GrantTypeChoices.PROMO,
                "grant_date_0": grant_date_date,
                "grant_date_1": grant_date_time,
                "expiration_date_0": expiration_date_date,
                "expiration_date_1": expiration_date_time,
            },
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        credits = TaskCredit.objects.filter(user=self.recipient_user)
        self.assertEqual(credits.count(), 1)
        credit = credits.get()
        self.assertEqual(credit.expiration_date, expiration_date)


@tag("batch_task_credits")
class TaskCreditAdminSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin_user = User.objects.create_superuser(
            username="admin_search",
            email="admin_search@example.com",
            password="password123",
        )
        cls.recipient_user = User.objects.create_user(
            username="recipient_search",
            email="recipient_search@example.com",
            password="password123",
        )

    def setUp(self):
        self.client.force_login(self.admin_user)
        TaskCredit.objects.all().delete()

    def test_search_finds_task_credit_by_user_id(self):
        now = timezone.localtime().replace(microsecond=0)
        TaskCredit.objects.create(
            user=self.recipient_user,
            credits=Decimal("5"),
            credits_used=Decimal("1"),
            granted_date=now,
            expiration_date=now + timedelta(days=7),
            plan=PlanNamesChoices.STARTUP,
            grant_type=GrantTypeChoices.PLAN,
            additional_task=False,
            voided=False,
        )

        url = reverse("admin:api_taskcredit_changelist")
        response = self.client.get(url, {"q": str(self.recipient_user.id)})

        self.assertEqual(response.status_code, 200)
        change_list = response.context.get("cl")
        self.assertIsNotNone(change_list)
        self.assertEqual(change_list.result_count, 1)
        self.assertContains(
            response,
            f"User: {self.recipient_user.email} ({self.recipient_user.id})",
        )
