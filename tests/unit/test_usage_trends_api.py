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
    PersistentAgentStep,
    PersistentAgentToolCall,
    TaskCredit,
)
from constants.grant_types import GrantTypeChoices
from console.usage_views import API_AGENT_ID


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
    if credits_cost is not None:
        task_kwargs["credits_cost"] = credits_cost

    task = BrowserUseAgentTask.objects.create(**task_kwargs)
    BrowserUseAgentTask.objects.filter(pk=task.pk).update(created_at=created_at)
    return task

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
        self.assertEqual(agent_map[API_AGENT_ID]["tasks_total"], 0)
        self.assertEqual(primary["persistent_id"], str(self.persistent_primary.id))
        self.assertEqual(secondary["persistent_id"], str(self.persistent_secondary.id))
        self.assertIsNone(agent_map[API_AGENT_ID]["persistent_id"])

        self.assertEqual(primary["tasks_total"], 2)
        self.assertEqual(primary["success_count"], 1)
        self.assertEqual(primary["error_count"], 1)
        self.assertAlmostEqual(primary["tasks_per_day"], 1.0)

        self.assertEqual(secondary["tasks_total"], 1)
        self.assertEqual(secondary["success_count"], 1)
        self.assertEqual(secondary["error_count"], 0)
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
        self.assertEqual(primary["tasks_total"], 1)
        self.assertEqual(primary["success_count"], 1)
        self.assertEqual(primary["error_count"], 0)
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
        self.assertEqual(api_row["tasks_total"], 2)
        self.assertEqual(api_row["success_count"], 2)
        self.assertEqual(api_row["error_count"], 0)
        self.assertAlmostEqual(api_row["tasks_per_day"], 2.0)
        self.assertIsNone(api_row["persistent_id"])

@tag("batch_usage_api")
@override_settings(FIRST_RUN_SETUP_ENABLED=False, LLM_BOOTSTRAP_OPTIONAL=True)
class UsageToolBreakdownAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="toolbreakdown@example.com",
            email="toolbreakdown@example.com",
            password="password123",
        )
        self.client.force_login(self.user)

        _grant_task_credits(user=self.user)

        self.primary_agent = BrowserUseAgent.objects.create(user=self.user, name="Primary Tool Agent")
        self.secondary_agent = BrowserUseAgent.objects.create(user=self.user, name="Secondary Tool Agent")

        self.primary_persistent = PersistentAgent.objects.create(
            user=self.user,
            name="Primary Persistent",
            charter="Primary charter",
            browser_use_agent=self.primary_agent,
        )
        self.secondary_persistent = PersistentAgent.objects.create(
            user=self.user,
            name="Secondary Persistent",
            charter="Secondary charter",
            browser_use_agent=self.secondary_agent,
        )

    def _create_tool_call(
        self,
        *,
        persistent_agent: PersistentAgent,
        created_at: datetime,
        tool_name: str,
        credits: Decimal,
    ) -> None:
        step = PersistentAgentStep.objects.create(agent=persistent_agent, credits_cost=credits)
        PersistentAgentStep.objects.filter(pk=step.pk).update(created_at=created_at)
        PersistentAgentToolCall.objects.create(step=step, tool_name=tool_name)

    def test_returns_tool_totals_for_selected_range(self):
        tz = timezone.get_current_timezone()
        window_start = timezone.make_aware(datetime(2024, 5, 1, 12, 0, 0), tz)
        later = window_start + timedelta(hours=4)
        much_later = window_start + timedelta(days=1, hours=2)

        self._create_tool_call(
            persistent_agent=self.primary_persistent,
            created_at=window_start,
            tool_name="api_call",
            credits=Decimal("2.5"),
        )
        self._create_tool_call(
            persistent_agent=self.primary_persistent,
            created_at=later,
            tool_name="search_tools",
            credits=Decimal("1.25"),
        )
        self._create_tool_call(
            persistent_agent=self.secondary_persistent,
            created_at=much_later,
            tool_name="api_call",
            credits=Decimal("0.5"),
        )

        response = self.client.get(
            reverse("console_usage_tools"),
            {
                "from": (window_start - timedelta(days=1)).date().isoformat(),
                "to": (much_later + timedelta(days=1)).date().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["total_count"], 3)
        self.assertAlmostEqual(payload["total_credits"], 4.25)
        self.assertEqual(payload["timezone"], timezone.get_current_timezone_name())

        tool_map = {tool["name"]: tool for tool in payload.get("tools", [])}
        self.assertIn("api_call", tool_map)
        self.assertIn("search_tools", tool_map)
        self.assertEqual(tool_map["api_call"]["count"], 2)
        self.assertAlmostEqual(tool_map["api_call"]["credits"], 3.0)
        self.assertEqual(tool_map["search_tools"]["count"], 1)
        self.assertAlmostEqual(tool_map["search_tools"]["credits"], 1.25)

    def test_agent_filter_limits_results(self):
        tz = timezone.get_current_timezone()
        base_dt = timezone.make_aware(datetime(2024, 6, 1, 8, 0, 0), tz)

        self._create_tool_call(
            persistent_agent=self.primary_persistent,
            created_at=base_dt,
            tool_name="api_call",
            credits=Decimal("2.0"),
        )
        self._create_tool_call(
            persistent_agent=self.secondary_persistent,
            created_at=base_dt + timedelta(hours=2),
            tool_name="api_call",
            credits=Decimal("5.0"),
        )

        response = self.client.get(
            reverse("console_usage_tools"),
            {
                "from": base_dt.date().isoformat(),
                "to": (base_dt + timedelta(days=1)).date().isoformat(),
                "agent": str(self.primary_agent.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["total_count"], 1)
        self.assertAlmostEqual(payload["total_credits"], 2.0)
        self.assertEqual(len(payload.get("tools", [])), 1)
        self.assertEqual(payload["tools"][0]["name"], "api_call")
        self.assertEqual(payload["tools"][0]["count"], 1)

    def test_org_context_limits_results(self):
        organization = Organization.objects.create(
            name="Tool Org",
            slug="tool-org",
            created_by=self.user,
        )
        billing = organization.billing
        billing.purchased_seats = 1
        billing.save()

        OrganizationMembership.objects.create(
            org=organization,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
        )

        _grant_task_credits(organization=organization)

        org_browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Org Tool Agent")
        org_persistent = PersistentAgent.objects.create(
            user=self.user,
            organization=organization,
            name="Org Persistent",
            charter="Org charter",
            browser_use_agent=org_browser_agent,
        )

        tz = timezone.get_current_timezone()
        base_dt = timezone.make_aware(datetime(2024, 7, 1, 9, 0, 0), tz)

        self._create_tool_call(
            persistent_agent=self.primary_persistent,
            created_at=base_dt,
            tool_name="api_call",
            credits=Decimal("3.0"),
        )
        self._create_tool_call(
            persistent_agent=org_persistent,
            created_at=base_dt + timedelta(hours=1),
            tool_name="api_call",
            credits=Decimal("4.0"),
        )

        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(organization.id)
        session["context_name"] = organization.name
        session.save()

        response = self.client.get(
            reverse("console_usage_tools"),
            {
                "from": base_dt.date().isoformat(),
                "to": (base_dt + timedelta(days=1)).date().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["total_count"], 1)
        self.assertAlmostEqual(payload["total_credits"], 4.0)
        self.assertEqual(len(payload.get("tools", [])), 1)
        self.assertEqual(payload["tools"][0]["name"], "api_call")

        reset_session = self.client.session
        reset_session["context_type"] = "personal"
        reset_session["context_id"] = str(self.user.id)
        reset_session["context_name"] = self.user.username
        reset_session.save()

    def test_api_tasks_surface_in_tool_breakdown(self):
        tz = timezone.get_current_timezone()
        base_dt = timezone.make_aware(datetime(2024, 8, 1, 10, 0, 0), tz)

        _create_api_task(user=self.user, created_at=base_dt, credits_cost=Decimal("1.5"))
        _create_api_task(user=self.user, created_at=base_dt + timedelta(hours=1), credits_cost=Decimal("2.0"))

        response = self.client.get(
            reverse("console_usage_tools"),
            {
                "from": base_dt.date().isoformat(),
                "to": base_dt.date().isoformat(),
                "agent": [API_AGENT_ID],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["total_count"], 2)
        self.assertAlmostEqual(payload["total_credits"], 2.0)
        tools = payload.get("tools", [])
        self.assertEqual(len(tools), 1)
        api_tool = tools[0]
        self.assertEqual(api_tool["name"], "api_task")
        self.assertEqual(api_tool["count"], 2)
        self.assertAlmostEqual(api_tool["credits"], 2.0)


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
        self.assertIn("API", agent_names)
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
        self.assertEqual(agent_ids, {str(self.org_agent.id), API_AGENT_ID})

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
        metrics = payload["metrics"]["tasks"]
        self.assertEqual(metrics["count"], 1)
        self.assertAlmostEqual(payload["metrics"]["credits"]["total"], 1.0)

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
        tasks_metrics = payload["metrics"]["tasks"]
        self.assertEqual(tasks_metrics["count"], 1)
        self.assertEqual(tasks_metrics["completed"], 1)
        self.assertEqual(tasks_metrics["in_progress"], 0)
        self.assertEqual(tasks_metrics["pending"], 0)
        self.assertAlmostEqual(payload["metrics"]["credits"]["total"], 2.5)
