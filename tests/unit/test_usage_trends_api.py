from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

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
    PersistentAgentStep,
    TaskCredit,
    UserPreference,
)
from constants.grant_types import GrantTypeChoices
from console.usage_views import API_AGENT_ID
from billing.services import BillingService
from tasks.services import TaskCreditService


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


def _create_api_task(*, user, created_at: datetime, organization=None, credits_cost: Decimal | None = None) -> BrowserUseAgentTask:
    task_kwargs = {
        "user": user,
        "status": BrowserUseAgentTask.StatusChoices.COMPLETED,
    }
    if organization is not None:
        task_kwargs["organization"] = organization
    task_kwargs["credits_cost"] = credits_cost if credits_cost is not None else Decimal("1.0")

    task = BrowserUseAgentTask.objects.create(**task_kwargs)
    BrowserUseAgentTask.objects.filter(pk=task.pk).update(created_at=created_at)
    return task

@tag("batch_usage_api")
@override_settings(
    FIRST_RUN_SETUP_ENABLED=False,
    LLM_BOOTSTRAP_OPTIONAL=True,
    PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False,
)
class UsageTrendAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="trend@example.com",
            email="trend@example.com",
            password="password123",
        )
        self.client.force_login(self.user)
        _grant_task_credits(user=self.user, credits=Decimal("100"))
        self.agent_primary = BrowserUseAgent.objects.create(user=self.user, name="Primary")
        self.agent_secondary = BrowserUseAgent.objects.create(user=self.user, name="Secondary")

    def _create_task_at(self, dt: datetime, count: int = 1, agent: BrowserUseAgent | None = None):
        for _ in range(count):
            task = BrowserUseAgentTask.objects.create(
                user=self.user,
                agent=agent,
                status=BrowserUseAgentTask.StatusChoices.COMPLETED,
                credits_cost=Decimal("1.0"),
            )
            BrowserUseAgentTask.objects.filter(pk=task.pk).update(created_at=dt)

    def _create_api_task_at(self, dt: datetime, count: int = 1):
        for _ in range(count):
            _create_api_task(user=self.user, created_at=dt)

    def _create_step_at(self, dt: datetime, *, count: int = 1, agent: PersistentAgent):
        for _ in range(count):
            step = PersistentAgentStep.objects.create(
                agent=agent,
                description="Test step",
                credits_cost=Decimal("1.0"),
            )
            PersistentAgentStep.objects.filter(pk=step.pk).update(created_at=dt)

    def test_week_mode_returns_current_counts(self):
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
        self.assertNotIn("current_period", payload)
        self.assertNotIn("previous_period", payload)
        self.assertEqual(len(payload["buckets"]), 7)

        first_bucket = payload["buckets"][0]
        last_bucket = payload["buckets"][-1]

        self.assertEqual(first_bucket["current"], 1)
        self.assertEqual(last_bucket["current"], 7)
        self.assertNotIn("previous", first_bucket)
        self.assertNotIn("previous", last_bucket)

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

    def test_trend_includes_persistent_steps(self):
        tz = timezone.get_current_timezone()
        current_day = timezone.make_aware(datetime(2024, 4, 1, 0, 0, 0), tz)

        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Trend Persistent",
            charter="Trend charter",
            browser_use_agent=self.agent_primary,
        )
        self._create_step_at(current_day + timedelta(hours=2), count=4, agent=persistent_agent)

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
        current_counts = [bucket["current"] for bucket in payload.get("buckets", [])]
        self.assertIn(4, current_counts)

    def test_api_agent_appears_in_trend_data(self):
        tz = timezone.get_current_timezone()
        current_day = timezone.make_aware(datetime(2024, 3, 1, 0, 0, 0), tz)

        self._create_api_task_at(current_day + timedelta(hours=1), count=3)

        response = self.client.get(
            reverse("console_usage_trends"),
            {
                "mode": "day",
                "from": current_day.date().isoformat(),
                "to": current_day.date().isoformat(),
                "agent": [API_AGENT_ID],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        agents = payload.get("agents", [])
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]["id"], API_AGENT_ID)
        current_counts = [bucket["current"] for bucket in payload.get("buckets", [])]
        self.assertIn(3, current_counts)

@tag("batch_usage_api")
@override_settings(
    FIRST_RUN_SETUP_ENABLED=False,
    LLM_BOOTSTRAP_OPTIONAL=True,
    PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False,
)
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
        self.persistent_primary = PersistentAgent.objects.create(
            user=self.user,
            name="Agent Alpha Persistent",
            charter="Primary charter",
            browser_use_agent=self.agent_primary,
        )
        self.persistent_secondary = PersistentAgent.objects.create(
            user=self.user,
            name="Agent Beta Persistent",
            charter="Secondary charter",
            browser_use_agent=self.agent_secondary,
        )

    def _create_task(self, *, dt: datetime, agent: BrowserUseAgent, status: str):
        task = BrowserUseAgentTask.objects.create(
            user=self.user,
            agent=agent,
            status=status,
            credits_cost=Decimal("1.0"),
        )
        BrowserUseAgentTask.objects.filter(pk=task.pk).update(created_at=dt)

    def _create_step(self, *, dt: datetime, agent: PersistentAgent, credits: Decimal = Decimal("1.0")):
        step = PersistentAgentStep.objects.create(
            agent=agent,
            description="Leaderboard step",
            credits_cost=credits,
        )
        PersistentAgentStep.objects.filter(pk=step.pk).update(created_at=dt)

    def test_returns_all_agents_with_zero_counts(self):
        response = self.client.get(reverse("console_usage_agents_leaderboard"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        agents = {entry["id"]: entry for entry in payload.get("agents", [])}
        self.assertIn(str(self.agent_primary.id), agents)
        self.assertIn(str(self.agent_secondary.id), agents)
        self.assertIn(API_AGENT_ID, agents)
        self.assertTrue(all(entry["tasks_total"] == 0 for entry in agents.values()))
        self.assertEqual(agents[str(self.agent_primary.id)]["persistent_id"], str(self.persistent_primary.id))
        self.assertEqual(agents[str(self.agent_secondary.id)]["persistent_id"], str(self.persistent_secondary.id))
        self.assertIsNone(agents[API_AGENT_ID]["persistent_id"])

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
        self.assertIn(API_AGENT_ID, agent_map)
        self.assertAlmostEqual(agent_map[API_AGENT_ID]["tasks_total"], 0.0)
        self.assertEqual(primary["persistent_id"], str(self.persistent_primary.id))
        self.assertEqual(secondary["persistent_id"], str(self.persistent_secondary.id))
        self.assertIsNone(agent_map[API_AGENT_ID]["persistent_id"])

        self.assertAlmostEqual(primary["tasks_total"], 2.0)
        self.assertAlmostEqual(primary["success_count"], 1.0)
        self.assertAlmostEqual(primary["error_count"], 1.0)
        self.assertAlmostEqual(primary["tasks_per_day"], 1.0)

        self.assertAlmostEqual(secondary["tasks_total"], 1.0)
        self.assertAlmostEqual(secondary["success_count"], 1.0)
        self.assertAlmostEqual(secondary["error_count"], 0.0)
        self.assertAlmostEqual(secondary["tasks_per_day"], 0.5)

    def test_persistent_steps_counted_in_leaderboard(self):
        tz = timezone.get_current_timezone()
        step_dt = timezone.make_aware(datetime(2024, 5, 5, 15, 0, 0), tz)

        self._create_step(dt=step_dt, agent=self.persistent_primary)

        response = self.client.get(
            reverse("console_usage_agents_leaderboard"),
            {
                "from": step_dt.date().isoformat(),
                "to": step_dt.date().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        agent_map = {entry["id"]: entry for entry in payload.get("agents", [])}

        primary = agent_map[str(self.agent_primary.id)]
        self.assertAlmostEqual(primary["tasks_total"], 1.0)
        self.assertAlmostEqual(primary["success_count"], 1.0)
        self.assertAlmostEqual(primary["error_count"], 0.0)
        self.assertAlmostEqual(primary["tasks_per_day"], 1.0)

    def test_api_tasks_included_when_selected(self):
        tz = timezone.get_current_timezone()
        start_dt = timezone.make_aware(datetime(2024, 4, 2, 9, 0, 0), tz)
        _create_api_task(user=self.user, created_at=start_dt, credits_cost=Decimal("2.5"))
        _create_api_task(user=self.user, created_at=start_dt + timedelta(hours=1), credits_cost=Decimal("1.0"))

        response = self.client.get(
            reverse("console_usage_agents_leaderboard"),
            {
                "from": start_dt.date().isoformat(),
                "to": start_dt.date().isoformat(),
                "agent": [API_AGENT_ID],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        agents = payload.get("agents", [])
        self.assertEqual(len(agents), 1)
        api_row = agents[0]
        self.assertEqual(api_row["id"], API_AGENT_ID)
        self.assertAlmostEqual(api_row["tasks_total"], 3.5)
        self.assertAlmostEqual(api_row["success_count"], 3.5)
        self.assertAlmostEqual(api_row["error_count"], 0.0)
        self.assertAlmostEqual(api_row["tasks_per_day"], 3.5)
        self.assertIsNone(api_row["persistent_id"])

    def test_soft_deleted_agent_is_flagged_in_leaderboard(self):
        delete_response = self.client.delete(reverse("agent_delete", kwargs={"pk": self.persistent_primary.id}))
        self.assertEqual(delete_response.status_code, 200)

        response = self.client.get(reverse("console_usage_agents_leaderboard"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        agent_map = {entry["id"]: entry for entry in payload.get("agents", [])}

        primary = agent_map[str(self.agent_primary.id)]
        secondary = agent_map[str(self.agent_secondary.id)]
        self.assertTrue(primary.get("is_deleted"))
        self.assertFalse(secondary.get("is_deleted"))

@tag("batch_usage_api")
@override_settings(
    FIRST_RUN_SETUP_ENABLED=False,
    LLM_BOOTSTRAP_OPTIONAL=True,
    PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False,
)
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
        self.assertIn("API", agent_names)
        self.assertIn("Agent A", agent_names)
        self.assertIn("Agent B", agent_names)
        self.assertNotIn("Org Agent", agent_names)

    def test_agent_list_excludes_eval_agents(self):
        eval_browser = BrowserUseAgent.objects.create(user=self.user, name="Eval Browser")
        PersistentAgent.objects.create(
            user=self.user,
            name="Eval Agent",
            charter="Eval charter",
            browser_use_agent=eval_browser,
            execution_environment="eval",
        )

        response = self.client.get(reverse("console_usage_agents"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        agent_names = {agent["name"] for agent in payload.get("agents", [])}
        self.assertNotIn("Eval Browser", agent_names)

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
        self.assertEqual(agent_ids, {str(self.org_agent.id), API_AGENT_ID})

        # Reset session context back to personal to avoid leaking state to other tests.
        reset_session = self.client.session
        reset_session["context_type"] = "personal"
        reset_session["context_id"] = str(self.user.id)
        reset_session["context_name"] = self.user.username
        reset_session.save()

    def test_agent_list_excludes_soft_deleted_agents(self):
        persistent = PersistentAgent.objects.create(
            user=self.user,
            name="Agent A Persistent",
            charter="Delete me",
            browser_use_agent=self.personal_agent,
        )

        delete_response = self.client.delete(reverse("agent_delete", kwargs={"pk": persistent.id}))
        self.assertEqual(delete_response.status_code, 200)

        response = self.client.get(reverse("console_usage_agents"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        agent_ids = {agent["id"] for agent in payload.get("agents", [])}
        self.assertNotIn(str(self.personal_agent.id), agent_ids)
        self.assertIn(str(self.personal_agent_two.id), agent_ids)


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

    def test_summary_includes_reset_date(self):
        period_start, period_end = BillingService.get_current_billing_period_for_owner(self.user)

        response = self.client.get(reverse("console_usage_summary"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["period"]["start"], period_start.isoformat())
        self.assertEqual(payload["period"]["end"], period_end.isoformat())
        self.assertEqual(payload["period"]["resetOn"], (period_end + timedelta(days=1)).isoformat())

    def test_summary_today_credits_respects_persisted_user_timezone(self):
        UserPreference.update_known_preferences(
            self.user,
            {UserPreference.KEY_USER_TIMEZONE: "America/Los_Angeles"},
        )
        fixed_now = datetime(2024, 5, 2, 10, 0, 0, tzinfo=dt_timezone.utc)
        included_task = BrowserUseAgentTask.objects.create(
            user=self.user,
            agent=self.agent_primary,
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            credits_cost=Decimal("1.5"),
        )
        BrowserUseAgentTask.objects.filter(pk=included_task.pk).update(
            created_at=datetime(2024, 5, 2, 8, 0, 0, tzinfo=dt_timezone.utc),
        )
        excluded_task = BrowserUseAgentTask.objects.create(
            user=self.user,
            agent=self.agent_primary,
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            credits_cost=Decimal("7.0"),
        )
        BrowserUseAgentTask.objects.filter(pk=excluded_task.pk).update(
            created_at=datetime(2024, 5, 2, 6, 30, 0, tzinfo=dt_timezone.utc),
        )

        with patch("console.usage_views.timezone.now", return_value=fixed_now):
            response = self.client.get(reverse("console_usage_summary"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["period"]["timezone"], "America/Los_Angeles")
        self.assertAlmostEqual(payload["metrics"]["todayCredits"]["total"], 1.5)

    def test_org_summary_includes_org_quota_and_reset_date(self):
        period_start, period_end = BillingService.get_current_billing_period_for_owner(self.organization)

        response = self.client.get(
            reverse("console_usage_summary"),
            HTTP_X_GOBII_CONTEXT_TYPE="organization",
            HTTP_X_GOBII_CONTEXT_ID=str(self.organization.id),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["context"]["type"], "organization")
        self.assertEqual(payload["period"]["start"], period_start.isoformat())
        self.assertEqual(payload["period"]["end"], period_end.isoformat())
        self.assertEqual(payload["period"]["resetOn"], (period_end + timedelta(days=1)).isoformat())
        self.assertEqual(payload["billing"]["purchasedSeats"], 1)
        self.assertAlmostEqual(payload["metrics"]["quota"]["total"], 25.0)
        self.assertAlmostEqual(payload["metrics"]["quota"]["available"], 25.0)

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
        self.assertNotIn("tasks", payload["metrics"])
        self.assertAlmostEqual(payload["metrics"]["credits"]["total"], 1.0)

    def test_summary_excludes_eval_agents(self):
        now = timezone.now()
        BrowserUseAgentTask.objects.create(
            user=self.user,
            agent=self.agent_primary,
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            credits_cost=Decimal("1"),
        )

        eval_browser = BrowserUseAgent.objects.create(user=self.user, name="Eval Browser")
        eval_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Eval Agent",
            charter="Eval charter",
            browser_use_agent=eval_browser,
            execution_environment="eval",
        )
        PersistentAgentStep.objects.create(
            agent=eval_agent,
            description="Eval step",
            credits_cost=Decimal("2"),
        )

        response = self.client.get(
            reverse("console_usage_summary"),
            {
                "from": (now - timedelta(days=1)).date().isoformat(),
                "to": now.date().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("tasks", payload["metrics"])
        self.assertAlmostEqual(payload["metrics"]["credits"]["total"], 1.0)

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
        self.assertNotIn("tasks", payload["metrics"])
        self.assertAlmostEqual(payload["metrics"]["credits"]["total"], 1.0)

    def test_api_filter_limits_summary(self):
        tz = timezone.get_current_timezone()
        now = timezone.make_aware(datetime(2024, 5, 5, 12, 0, 0), tz)
        _create_api_task(user=self.user, created_at=now, credits_cost=Decimal("4.0"))
        BrowserUseAgentTask.objects.create(
            user=self.user,
            agent=self.agent_primary,
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            credits_cost=Decimal("2.0"),
        )

        response = self.client.get(
            reverse("console_usage_summary"),
            {
                "from": now.date().isoformat(),
                "to": now.date().isoformat(),
                "agent": API_AGENT_ID,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("tasks", payload["metrics"])
        self.assertAlmostEqual(payload["metrics"]["credits"]["total"], 4.0)

    def test_persistent_steps_contribute_to_summary(self):
        tz = timezone.get_current_timezone()
        step_time = timezone.make_aware(datetime(2024, 6, 1, 12, 0, 0), tz)

        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Summary Persistent",
            charter="Summary charter",
            browser_use_agent=self.agent_primary,
        )

        step = PersistentAgentStep.objects.create(
            agent=persistent_agent,
            description="Persistent summary step",
            credits_cost=Decimal("2.5"),
        )
        PersistentAgentStep.objects.filter(pk=step.pk).update(created_at=step_time)

        response = self.client.get(
            reverse("console_usage_summary"),
            {
                "from": step_time.date().isoformat(),
                "to": step_time.date().isoformat(),
                "agent": str(self.agent_primary.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("tasks", payload["metrics"])
        self.assertAlmostEqual(payload["metrics"]["credits"]["total"], 2.5)

    def test_summary_uses_credit_ledger_for_consumed_totals(self):
        BrowserUseAgentTask.objects.create(
            user=self.user,
            agent=self.agent_primary,
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            credits_cost=Decimal("2.0"),
        )
        TaskCredit.objects.filter(user=self.user).update(credits_used=Decimal("5"))
        expected_used = TaskCreditService.get_owner_task_credits_used(self.user)

        response = self.client.get(reverse("console_usage_summary"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertAlmostEqual(payload["metrics"]["credits"]["total"], 2.0)
        self.assertAlmostEqual(payload["metrics"]["quota"]["used"], float(expected_used))
        available = TaskCreditService.calculate_available_tasks(self.user)
        self.assertAlmostEqual(payload["metrics"]["quota"]["available"], float(available))

    def test_summary_respects_requested_date_window_for_credits(self):
        tz = timezone.get_current_timezone()
        past_day = timezone.make_aware(datetime(2023, 1, 10, 12, 0, 0), tz)

        old_task = BrowserUseAgentTask.objects.create(
            user=self.user,
            agent=self.agent_primary,
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            credits_cost=Decimal("3.0"),
        )
        BrowserUseAgentTask.objects.filter(pk=old_task.pk).update(created_at=past_day)

        recent_task = BrowserUseAgentTask.objects.create(
            user=self.user,
            agent=self.agent_primary,
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            credits_cost=Decimal("7.0"),
        )
        BrowserUseAgentTask.objects.filter(pk=recent_task.pk).update(created_at=timezone.now())

        response = self.client.get(
            reverse("console_usage_summary"),
            {
                "from": past_day.date().isoformat(),
                "to": past_day.date().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertAlmostEqual(payload["metrics"]["credits"]["total"], 3.0)

    def test_personal_summary_uses_entitlement_when_no_grants(self):
        User = get_user_model()
        other_user = User.objects.create_user(
            username="nogrants@example.com",
            email="nogrants@example.com",
            password="password123",
        )
        self.client.force_login(other_user)
        TaskCredit.objects.filter(user=other_user).delete()

        response = self.client.get(reverse("console_usage_summary"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        entitled = TaskCreditService.get_tasks_entitled_for_owner(other_user)
        self.assertEqual(payload["metrics"]["quota"]["total"], float(entitled))
        self.assertEqual(payload["metrics"]["quota"]["available"], float(entitled))
        self.assertEqual(payload["metrics"]["credits"]["total"], 0.0)
