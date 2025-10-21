from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone
from api.models import PersistentAgent, PersistentAgentSystemStep, UserQuota
from api.services.proactive_activation import ProactiveActivationService
from api.tasks.proactive_agents import schedule_proactive_agents_task
from tests.unit.test_api_persistent_agents import create_browser_agent_without_proxy


class _FakeRedis:
    def __init__(self):
        self._store: dict[str, tuple[str, int | None]] = {}

    def exists(self, key: str) -> int:
        data = self._store.get(key)
        if not data:
            return 0
        return 1

    def set(self, key: str, value: str, ex: int | None = None, nx: bool | None = None):
        if nx:
            if self.exists(key):
                return False
        self._store[key] = (value, ex)
        return True

    def delete(self, key: str):
        self._store.pop(key, None)


@tag("batch_api_persistent_agents", "batch_api_tasks")
class ProactiveActivationServiceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="proactive@example.com",
            email="proactive@example.com",
            password="password",
        )
        quota, _ = UserQuota.objects.get_or_create(user=self.user)
        quota.agent_limit = 5
        quota.save()

        self.browser_agent_a = create_browser_agent_without_proxy(self.user, "browser-a")
        self.browser_agent_b = create_browser_agent_without_proxy(self.user, "browser-b")

        self.agent_a = PersistentAgent.objects.create(
            user=self.user,
            name="agent-a",
            charter="Follow up with clients",
            schedule="@daily",
            browser_use_agent=self.browser_agent_a,
            proactive_opt_in=True,
            proactive_min_interval_minutes=0,
            proactive_max_daily=2,
        )
        self.agent_b = PersistentAgent.objects.create(
            user=self.user,
            name="agent-b",
            charter="Prepare reports",
            schedule="@daily",
            browser_use_agent=self.browser_agent_b,
            proactive_opt_in=True,
            proactive_min_interval_minutes=0,
            proactive_max_daily=2,
        )

    @patch("api.services.proactive_activation.get_redis_client")
    def test_only_one_agent_per_user_selected(self, mock_redis_client):
        mock_redis_client.return_value = _FakeRedis()

        triggered = ProactiveActivationService.trigger_agents(batch_size=5)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0].user_id, self.user.id)

        system_steps = PersistentAgentSystemStep.objects.filter(
            code=PersistentAgentSystemStep.Code.PROACTIVE_TRIGGER
        )
        self.assertEqual(system_steps.count(), 1)

        # Second run should respect per-day limit and redis gate
        triggered_again = ProactiveActivationService.trigger_agents(batch_size=5)
        self.assertEqual(len(triggered_again), 0)

    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.services.proactive_activation.ProactiveActivationService.trigger_agents")
    def test_schedule_task_enqueues_processing(self, mock_trigger, mock_delay):
        mock_trigger.return_value = [self.agent_a]
        processed = schedule_proactive_agents_task(batch_size=3)
        self.assertEqual(processed, 1)
        mock_delay.assert_called_once_with(str(self.agent_a.id))

    @patch("api.services.proactive_activation.get_redis_client")
    def test_respects_minimum_weekly_interval(self, mock_redis_client):
        mock_redis_client.return_value = _FakeRedis()
        self.agent_b.proactive_opt_in = False
        self.agent_b.save(update_fields=["proactive_opt_in"])

        self.agent_a.proactive_last_trigger_at = timezone.now() - timedelta(days=6)
        self.agent_a.last_interaction_at = timezone.now() - timedelta(days=10)
        self.agent_a.save(update_fields=["proactive_last_trigger_at", "last_interaction_at"])

        triggered = ProactiveActivationService.trigger_agents(batch_size=5)
        self.assertEqual(triggered, [])

        mock_redis_client.return_value = _FakeRedis()
        self.agent_a.refresh_from_db()
        self.agent_a.proactive_last_trigger_at = timezone.now() - timedelta(days=8)
        self.agent_a.save(update_fields=["proactive_last_trigger_at"])

        triggered_after_cooldown = ProactiveActivationService.trigger_agents(batch_size=5)
        self.assertEqual(len(triggered_after_cooldown), 1)
        self.assertEqual(triggered_after_cooldown[0].id, self.agent_a.id)

    @patch("api.services.proactive_activation.get_redis_client")
    def test_waits_three_days_since_last_interaction(self, mock_redis_client):
        mock_redis_client.return_value = _FakeRedis()
        self.agent_b.proactive_opt_in = False
        self.agent_b.save(update_fields=["proactive_opt_in"])

        now = timezone.now()

        self.agent_a.proactive_last_trigger_at = None
        self.agent_a.last_interaction_at = now - timedelta(days=2)
        self.agent_a.save(update_fields=["proactive_last_trigger_at", "last_interaction_at"])

        triggered = ProactiveActivationService.trigger_agents(batch_size=5)
        self.assertEqual(triggered, [])

        mock_redis_client.return_value = _FakeRedis()
        self.agent_a.refresh_from_db()
        self.agent_a.last_interaction_at = now - timedelta(days=4)
        self.agent_a.save(update_fields=["last_interaction_at"])

        triggered_after_wait = ProactiveActivationService.trigger_agents(batch_size=5)
        self.assertEqual(len(triggered_after_wait), 1)
        self.assertEqual(triggered_after_wait[0].id, self.agent_a.id)
