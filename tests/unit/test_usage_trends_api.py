from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, tag, override_settings
from django.urls import reverse
from django.utils import timezone

from api.models import (
    BrowserUseAgent,
    BrowserUseAgentTask,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    TaskCredit,
)
from constants.grant_types import GrantTypeChoices


def _grant_task_credits(*, user=None, organization=None, credits: Decimal = Decimal("25")) -> None:
    """Provision task credits for tests so quota validation passes."""
    now = timezone.now()
    grant_kwargs = {
        "credits": credits,
        "credits_used": Decimal("0"),
        "granted_date": now - timedelta(days=1),
        "expiration_date": now + timedelta(days=30),
        "grant_type": GrantTypeChoices.COMPENSATION,
    }
    if organization is not None:
        grant_kwargs["organization"] = organization
    else:
        grant_kwargs["user"] = user
    TaskCredit.objects.create(**grant_kwargs)

@tag("batch_usage_api")
@override_settings(FIRST_RUN_SETUP_ENABLED=False, LLM_BOOTSTRAP_OPTIONAL=True)
class UsageTrendAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="trend@example.com",
            email="trend@example.com",
            password="password123",
        )
        self.client.force_login(self.user)
        _grant_task_credits(user=self.user)
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

@tag("batch_usage_api")
@override_settings(FIRST_RUN_SETUP_ENABLED=False, LLM_BOOTSTRAP_OPTIONAL=True)
class UsageAgentLeaderboardAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="leaderboard@example.com",
            email="leaderboard@example.com",
            password="password123",
        )
        self.client.force_login(self.user)
        _grant_task_credits(user=self.user)
        self.agent_primary = BrowserUseAgent.objects.create(user=self.user, name="Agent Alpha")
        self.agent_secondary = BrowserUseAgent.objects.create(user=self.user, name="Agent Beta")

    def _create_task(self, *, dt: datetime, agent: BrowserUseAgent, status: str):
        task = BrowserUseAgentTask.objects.create(
            user=self.user,
            agent=agent,
            status=status,
        )
        BrowserUseAgentTask.objects.filter(pk=task.pk).update(created_at=dt)

    def test_returns_all_agents_with_zero_counts(self):
        response = self.client.get(reverse("console_usage_agents_leaderboard"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        agents = {entry["id"]: entry for entry in payload.get("agents", [])}
        self.assertIn(str(self.agent_primary.id), agents)
        self.assertIn(str(self.agent_secondary.id), agents)
        self.assertTrue(all(entry["tasks_total"] == 0 for entry in agents.values()))

    def test_calculates_totals_and_average_per_day(self):
        tz = timezone.get_current_timezone()
        start_dt = timezone.make_aware(datetime(2024, 1, 10, 12, 0, 0), tz)
        next_day = start_dt + timedelta(days=1)

        self._create_task(dt=start_dt, agent=self.agent_primary, status=BrowserUseAgentTask.StatusChoices.COMPLETED)
        self._create_task(dt=start_dt + timedelta(hours=2), agent=self.agent_primary, status=BrowserUseAgentTask.StatusChoices.FAILED)
        self._create_task(dt=next_day, agent=self.agent_secondary, status=BrowserUseAgentTask.StatusChoices.COMPLETED)

        response = self.client.get(
            reverse("console_usage_agents_leaderboard"),
            {
                "from": start_dt.date().isoformat(),
                "to": next_day.date().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        agent_map = {entry["id"]: entry for entry in payload.get("agents", [])}

        primary = agent_map[str(self.agent_primary.id)]
        secondary = agent_map[str(self.agent_secondary.id)]

        self.assertEqual(primary["tasks_total"], 2)
        self.assertEqual(primary["success_count"], 1)
        self.assertEqual(primary["error_count"], 1)
        self.assertAlmostEqual(primary["tasks_per_day"], 1.0)

        self.assertEqual(secondary["tasks_total"], 1)
        self.assertEqual(secondary["success_count"], 1)
        self.assertEqual(secondary["error_count"], 0)
        self.assertAlmostEqual(secondary["tasks_per_day"], 0.5)

@tag("batch_usage_api")
@override_settings(FIRST_RUN_SETUP_ENABLED=False, LLM_BOOTSTRAP_OPTIONAL=True)
class UsageAgentsAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="agents@example.com",
            email="agents@example.com",
            password="password123",
        )
        self.client.force_login(self.user)
        _grant_task_credits(user=self.user)
        self.personal_agent = BrowserUseAgent.objects.create(user=self.user, name="Agent A")
        self.personal_agent_two = BrowserUseAgent.objects.create(user=self.user, name="Agent B")

        self.organization = Organization.objects.create(
            name="Org Inc",
            slug="org-inc",
            created_by=self.user,
        )
        # Ensure seats are available so org-owned agents can be created.
        billing = self.organization.billing
        billing.purchased_seats = 1
        billing.save()

        _grant_task_credits(organization=self.organization)

        OrganizationMembership.objects.create(
            org=self.organization,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
        )

        org_browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Org Agent")
        PersistentAgent.objects.create(
            user=self.user,
            organization=self.organization,
            name="Org Agent Persistent",
            charter="Test charter",
            browser_use_agent=org_browser_agent,
        )
        self.org_agent = org_browser_agent

    def test_agent_list_returns_agents(self):
        response = self.client.get(reverse("console_usage_agents"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        agent_names = {agent["name"] for agent in payload.get("agents", [])}
        self.assertIn("Agent A", agent_names)
        self.assertIn("Agent B", agent_names)
        self.assertNotIn("Org Agent", agent_names)

    def test_user_context_excludes_org_agents(self):
        response = self.client.get(reverse("console_usage_agents"))
        payload = response.json()
        agent_ids = {agent["id"] for agent in payload.get("agents", [])}
        self.assertNotIn(str(self.org_agent.id), agent_ids)

    def test_org_context_returns_only_org_agents(self):
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(self.organization.id)
        session["context_name"] = self.organization.name
        session.save()

        response = self.client.get(reverse("console_usage_agents"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        agent_ids = {agent["id"] for agent in payload.get("agents", [])}
        self.assertEqual(agent_ids, {str(self.org_agent.id)})

        # Reset session context back to personal to avoid leaking state to other tests.
        reset_session = self.client.session
        reset_session["context_type"] = "personal"
        reset_session["context_id"] = str(self.user.id)
        reset_session["context_name"] = self.user.username
        reset_session.save()

@tag("batch_usage_api")
@override_settings(FIRST_RUN_SETUP_ENABLED=False, LLM_BOOTSTRAP_OPTIONAL=True)
class UsageSummaryAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
          username="summary@example.com",
          email="summary@example.com",
          password="password123",
        )
        self.client.force_login(self.user)
        _grant_task_credits(user=self.user)
        self.agent_primary = BrowserUseAgent.objects.create(user=self.user, name="Primary")
        self.agent_secondary = BrowserUseAgent.objects.create(user=self.user, name="Secondary")

        self.organization = Organization.objects.create(
            name="Summary Org",
            slug="summary-org",
            created_by=self.user,
        )
        billing = self.organization.billing
        billing.purchased_seats = 1
        billing.save()

        OrganizationMembership.objects.create(
            org=self.organization,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
        )

        self.org_agent = BrowserUseAgent.objects.create(user=self.user, name="Org Summary Agent")
        PersistentAgent.objects.create(
            user=self.user,
            organization=self.organization,
            name="Summary Org Agent",
            charter="Org charter",
            browser_use_agent=self.org_agent,
        )
        _grant_task_credits(organization=self.organization)

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

    def test_personal_context_excludes_org_tasks(self):
        now = timezone.now()
        BrowserUseAgentTask.objects.create(
            user=self.user,
            agent=self.agent_primary,
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            credits_cost=Decimal("1"),
        )
        BrowserUseAgentTask.objects.create(
            user=self.user,
            agent=self.org_agent,
            organization=self.organization,
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            credits_cost=Decimal("1"),
        )

        response = self.client.get(
            reverse("console_usage_summary"),
            {
                "from": (now - timedelta(days=1)).date().isoformat(),
                "to": (now + timedelta(days=1)).date().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["metrics"]["tasks"]["count"], 1)
