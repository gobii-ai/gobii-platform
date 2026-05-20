from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, tag, override_settings
from django.urls import reverse
from django.utils import timezone

from api.models import (
    BrowserUseAgent,
    BrowserUseAgentTask,
    PersistentAgent,
    PersistentAgentStep,
    PersistentAgentWorkPlan,
    PersistentAgentWorkPlanStep,
    TaskCredit,
)
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
@override_settings(FIRST_RUN_SETUP_ENABLED=False, LLM_BOOTSTRAP_OPTIONAL=True, PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
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

    def test_agent_credit_awareness_returns_plan_daily_quota_and_burn_rate(self):
        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Credit Awareness Agent",
            charter="Credit charter",
            browser_use_agent=self.agent,
        )
        work_plan = PersistentAgentWorkPlan.objects.create(
            agent=persistent_agent,
            title="Research sources",
            status=PersistentAgentWorkPlan.Status.ACTIVE,
        )
        work_plan_step = PersistentAgentWorkPlanStep.objects.create(
            work_plan=work_plan,
            title="Research sources",
            normalized_title="research sources",
            status=PersistentAgentWorkPlanStep.Status.DOING,
            position=0,
        )
        now = timezone.now()
        PersistentAgentStep.objects.create(
            agent=persistent_agent,
            description="Research",
            credits_cost=Decimal("0.5"),
            work_plan=work_plan,
            work_plan_step=work_plan_step,
        )
        task = BrowserUseAgentTask.objects.create(
            user=self.user,
            agent=self.agent,
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            credits_cost=Decimal("1.0"),
        )
        BrowserUseAgentTask.objects.filter(pk=task.pk).update(created_at=now - timedelta(minutes=20))
        refresh_burn_rate_snapshots(windows_minutes=[60], now=now)

        response = self.client.get(reverse("console_agent_credit_awareness", kwargs={"agent_id": persistent_agent.id}))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agentId"], str(persistent_agent.id))
        self.assertEqual(payload["currentPlan"]["id"], str(work_plan.id))
        self.assertAlmostEqual(payload["currentPlan"]["creditsUsed"], 0.5)
        self.assertEqual(payload["currentStep"]["title"], "Research sources")
        self.assertIn("usage", payload["dailyCredits"])
        self.assertIn("available", payload["quota"])
        self.assertIn("resetOn", payload["billingPeriod"])
        self.assertIn("owner", payload["burnRate"])
        self.assertTrue(payload["actions"]["canOpenUsage"])
