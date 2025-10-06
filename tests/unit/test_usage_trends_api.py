from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from api.models import BrowserUseAgentTask


class UsageTrendAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="trend@example.com",
            email="trend@example.com",
            password="password123",
        )
        self.client.force_login(self.user)

    def _create_task_at(self, dt: datetime, count: int = 1):
        for _ in range(count):
            task = BrowserUseAgentTask.objects.create(
                user=self.user,
                status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            )
            BrowserUseAgentTask.objects.filter(pk=task.pk).update(created_at=dt)

    def test_week_mode_returns_current_and_previous_counts(self):
        tz = timezone.get_current_timezone()
        current_period_start = timezone.make_aware(datetime(2024, 1, 8, 0, 0, 0), tz)
        current_period_end = current_period_start + timedelta(days=6)

        for offset in range(7):
            bucket_time = current_period_start + timedelta(days=offset, hours=2)
            self._create_task_at(bucket_time, count=offset + 1)

        previous_period_start = current_period_start - timedelta(days=7)
        for offset in range(7):
            bucket_time = previous_period_start + timedelta(days=offset, hours=3)
            self._create_task_at(bucket_time, count=offset + 2)

        response = self.client.get(
            reverse("console_usage_trends"),
            {
                "mode": "week",
                "from": current_period_start.date().isoformat(),
                "to": current_period_end.date().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["mode"], "week")
        self.assertEqual(payload["resolution"], "day")
        self.assertEqual(len(payload["buckets"]), 7)

        first_bucket = payload["buckets"][0]
        last_bucket = payload["buckets"][-1]

        self.assertEqual(first_bucket["current"], 1)
        self.assertEqual(first_bucket["previous"], 2)
        self.assertEqual(last_bucket["current"], 7)
        self.assertEqual(last_bucket["previous"], 8)

    def test_invalid_mode_returns_error(self):
        response = self.client.get(reverse("console_usage_trends"), {"mode": "year"})
        self.assertEqual(response.status_code, 400)
