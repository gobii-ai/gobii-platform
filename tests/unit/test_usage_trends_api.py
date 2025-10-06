from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from api.models import BrowserUseAgent, BrowserUseAgentTask


class UsageTrendAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="trend@example.com",
            email="trend@example.com",
            password="password123",
        )
        self.client.force_login(self.user)
        self.agent_primary = BrowserUseAgent.objects.create(user=self.user, name="Primary")
        self.agent_secondary = BrowserUseAgent.objects.create(user=self.user, name="Secondary")

    def _create_task_at(self, dt: datetime, count: int = 1, agent: BrowserUseAgent | None = None):
        for _ in range(count):
            task = BrowserUseAgentTask.objects.create(
                user=self.user,
                agent=agent,
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

    def test_agent_filter_limits_results(self):
        tz = timezone.get_current_timezone()
        current_day = timezone.make_aware(datetime(2024, 2, 1, 0, 0, 0), tz)

        self._create_task_at(current_day + timedelta(hours=3), count=5, agent=self.agent_primary)
        self._create_task_at(current_day + timedelta(hours=6), count=7, agent=self.agent_secondary)

        response = self.client.get(
            reverse("console_usage_trends"),
            {
                "mode": "day",
                "from": current_day.date().isoformat(),
                "to": current_day.date().isoformat(),
                "agent": [str(self.agent_primary.id)],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        buckets = payload["buckets"]
        self.assertTrue(any(bucket["current"] == 5 for bucket in buckets))
        self.assertTrue(all(bucket["current"] != 7 for bucket in buckets))


class UsageAgentsAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="agents@example.com",
            email="agents@example.com",
            password="password123",
        )
        self.client.force_login(self.user)
        BrowserUseAgent.objects.create(user=self.user, name="Agent A")
        BrowserUseAgent.objects.create(user=self.user, name="Agent B")

    def test_agent_list_returns_agents(self):
        response = self.client.get(reverse("console_usage_agents"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        agent_names = {agent["name"] for agent in payload.get("agents", [])}
        self.assertIn("Agent A", agent_names)
        self.assertIn("Agent B", agent_names)


class UsageSummaryAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
          username="summary@example.com",
          email="summary@example.com",
          password="password123",
        )
        self.client.force_login(self.user)
        self.agent_primary = BrowserUseAgent.objects.create(user=self.user, name="Primary")
        self.agent_secondary = BrowserUseAgent.objects.create(user=self.user, name="Secondary")

    def test_agent_filter_limits_summary(self):
        now = timezone.now()
        BrowserUseAgentTask.objects.create(
          user=self.user,
          agent=self.agent_primary,
          status=BrowserUseAgentTask.StatusChoices.COMPLETED,
          credits_cost=Decimal("1"),
        )
        BrowserUseAgentTask.objects.create(
          user=self.user,
          agent=self.agent_secondary,
          status=BrowserUseAgentTask.StatusChoices.COMPLETED,
          credits_cost=Decimal("1"),
        )

        response = self.client.get(
          reverse("console_usage_summary"),
          {
            "from": (now - timedelta(days=1)).date().isoformat(),
            "to": now.date().isoformat(),
            "agent": str(self.agent_primary.id),
          },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["metrics"]["tasks"]["count"], 1)
