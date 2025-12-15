from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from api.models import BrowserUseAgent, PersistentAgent
from api.services.cron_throttle import evaluate_free_plan_cron_throttle

User = get_user_model()


@tag("batch_event_processing")
@override_settings(
    AGENT_CRON_THROTTLE_START_AGE_DAYS=16,
    AGENT_CRON_THROTTLE_STAGE_DAYS=7,
    AGENT_CRON_THROTTLE_MAX_INTERVAL_DAYS=30,
)
class CronThrottlePolicyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="cron-throttle@example.com",
            email="cron-throttle@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="CronThrottleBA")

    def _create_agent(self, *, created_at):
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="CronThrottleAgent",
            charter="cron throttle tests",
            browser_use_agent=self.browser_agent,
            schedule="@daily",
        )
        PersistentAgent.objects.filter(pk=agent.pk).update(created_at=created_at)
        agent.refresh_from_db()
        return agent

    def test_throttle_starts_at_2x_on_start_day(self):
        now = timezone.now()
        agent = self._create_agent(created_at=now - timedelta(days=16))

        decision = evaluate_free_plan_cron_throttle(agent, agent.schedule or "", now=now)

        self.assertTrue(decision.throttling_applies)
        self.assertEqual(decision.stage, 1)
        self.assertEqual(decision.base_interval_seconds, 86400)
        self.assertEqual(decision.effective_interval_seconds, 172800)

    def test_throttle_caps_at_monthly(self):
        now = timezone.now()
        agent = self._create_agent(created_at=now - timedelta(days=90))

        decision = evaluate_free_plan_cron_throttle(agent, agent.schedule or "", now=now)

        self.assertTrue(decision.throttling_applies)
        self.assertEqual(decision.effective_interval_seconds, 30 * 86400)

