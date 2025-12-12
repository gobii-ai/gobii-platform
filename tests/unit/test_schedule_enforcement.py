from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.models import BrowserUseAgent, PersistentAgent
from api.services.schedule_enforcement import enforce_minimum_for_agents, normalize_schedule


@tag("batch_schedule")
class ScheduleNormalizationTests(TestCase):
    def test_interval_is_clamped(self):
        result = normalize_schedule("@every 10m", 30)

        self.assertTrue(result.changed)
        self.assertEqual(result.normalized, "@every 30m")
        self.assertEqual(result.reason, "interval_clamped")

    def test_cron_minute_reduction(self):
        result = normalize_schedule("13,43 * * * *", 60)

        self.assertTrue(result.changed)
        self.assertEqual(result.normalized, "13 * * * *")
        self.assertEqual(result.reason, "minute_reduced")

    def test_hour_step_adjustment(self):
        result = normalize_schedule("*/15 * * * *", 120)

        self.assertTrue(result.changed)
        self.assertEqual(result.normalized, "0 */2 * * *")
        self.assertEqual(result.reason, "hour_step_adjusted")

    def test_interval_fallback_for_large_minimum(self):
        result = normalize_schedule("*/15 * * * *", 3000)

        self.assertTrue(result.changed)
        self.assertEqual(result.normalized, "@every 3000m")
        self.assertEqual(result.reason, "interval_fallback")


@tag("batch_schedule")
class ScheduleEnforcementTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="schedule-owner",
            email="schedule-owner@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="BA")

    @patch("api.models.PersistentAgent._sync_celery_beat_task", autospec=True)
    def test_dry_run_does_not_modify_agent(self, _mock_sync):
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Agent",
            charter="test",
            browser_use_agent=self.browser_agent,
            schedule="*/15 * * * *",
        )

        result = enforce_minimum_for_agents([agent], 60, dry_run=True)

        agent.refresh_from_db()
        self.assertEqual(agent.schedule, "*/15 * * * *")
        self.assertEqual(result["updated"], 1)
        self.assertTrue(result["dry_run"])

    @patch("api.models.PersistentAgent._sync_celery_beat_task", autospec=True)
    def test_enforcement_updates_schedule_and_snapshot(self, _mock_sync):
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Agent",
            charter="test",
            browser_use_agent=self.browser_agent,
            schedule="13,43 * * * *",
            schedule_snapshot="*/10 * * * *",
        )

        result = enforce_minimum_for_agents([agent], 60, dry_run=False)

        agent.refresh_from_db()
        self.assertEqual(agent.schedule, "13 * * * *")
        self.assertEqual(agent.schedule_snapshot, "0 * * * *")
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["snapshot_updated"], 1)
