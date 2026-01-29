from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, tag, override_settings
from django.urls import reverse
from django.utils import timezone

from api.models import BrowserUseAgent, BrowserUseAgentTask, TaskCredit
from api.services.burn_rate_snapshots import refresh_burn_rate_snapshots
from constants.grant_types import GrantTypeChoices


def _grant_task_credits(*, user, credits: Decimal = Decimal("24")) -> None:
    now = timezone.now()
    TaskCredit.objects.create(
        user=user,
        credits=credits,
        credits_used=Decimal("0"),
        granted_date=now - timedelta(days=1),
        expiration_date=now + timedelta(days=30),
        grant_type=GrantTypeChoices.COMPENSATION,
    )


@tag("batch_usage_api")
@override_settings(FIRST_RUN_SETUP_ENABLED=False, LLM_BOOTSTRAP_OPTIONAL=True)
class UsageBurnRateSnapshotAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="burnrate@example.com",
            email="burnrate@example.com",
            password="password123",
        )
        self.client.force_login(self.user)
        _grant_task_credits(user=self.user)
        self.agent = BrowserUseAgent.objects.create(user=self.user, name="Burn Rate Agent")

    def test_projection_returns_days_remaining(self):
        now = timezone.now()
        task = BrowserUseAgentTask.objects.create(
            user=self.user,
            agent=self.agent,
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            credits_cost=Decimal("1.0"),
        )
        BrowserUseAgentTask.objects.filter(pk=task.pk).update(created_at=now - timedelta(minutes=30))

        refresh_burn_rate_snapshots(windows_minutes=[60], now=now)

        response = self.client.get(reverse("console_usage_burn_rate"), {"tier": "standard", "window": 60})
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertIsNotNone(payload["snapshot"])
        self.assertEqual(payload["snapshot"]["window_minutes"], 60)
        self.assertIsNotNone(payload["projection"])
        expected_days = payload["projection"]["available"] / payload["snapshot"]["burn_rate_per_day"]
        self.assertAlmostEqual(payload["projection"]["projected_days_remaining"], expected_days, places=2)
