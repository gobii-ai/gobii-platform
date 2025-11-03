from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from api.models import PersistentAgent, PersistentAgentStep, PersistentAgentSystemStep, UserQuota
from api.services.proactive_activation import ProactiveActivationService
from api.tasks.proactive_agents import schedule_proactive_agents_task
from tests.unit.test_api_persistent_agents import create_browser_agent_without_proxy
from util.analytics import AnalyticsEvent


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


@override_settings(GOBII_RELEASE_ENV="prod")
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
        )
        self.agent_b = PersistentAgent.objects.create(
            user=self.user,
            name="agent-b",
            charter="Prepare reports",
            schedule="@daily",
            browser_use_agent=self.browser_agent_b,
            proactive_opt_in=True,
        )
        stale_timestamp = timezone.now() - timedelta(days=4)
        PersistentAgent.objects.filter(pk__in=[self.agent_a.pk, self.agent_b.pk]).update(
            last_interaction_at=stale_timestamp
        )
        self.agent_a.refresh_from_db()
        self.agent_b.refresh_from_db()

    @patch("api.services.proactive_activation.ProactiveActivationService._is_rollout_enabled_for_agent", return_value=True)
    @patch("api.services.proactive_activation.get_redis_client")
    def test_only_one_agent_per_user_selected(self, mock_redis_client, _mock_flag):
        mock_redis_client.return_value = _FakeRedis()

        triggered = ProactiveActivationService.trigger_agents(batch_size=5)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0].user_id, self.user.id)

        system_steps = PersistentAgentSystemStep.objects.filter(
            code=PersistentAgentSystemStep.Code.PROACTIVE_TRIGGER
        )
        self.assertEqual(system_steps.count(), 1)

        # Second run should respect the redis gate
        triggered_again = ProactiveActivationService.trigger_agents(batch_size=5)
        self.assertEqual(len(triggered_again), 0)

    @patch("api.services.proactive_activation.ProactiveActivationService._is_rollout_enabled_for_agent", return_value=True)
    @patch("api.services.proactive_activation.get_redis_client")
    def test_skips_agents_without_daily_credit(self, mock_redis_client, _mock_flag):
        mock_redis_client.return_value = _FakeRedis()

        self.agent_a.daily_credit_limit = 1
        self.agent_a.save(update_fields=["daily_credit_limit"])

        PersistentAgentStep.objects.create(
            agent=self.agent_a,
            description="Consumed credit",
            credits_cost=Decimal("1"),
        )

        triggered = ProactiveActivationService.trigger_agents(batch_size=5)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0].id, self.agent_b.id)

    @patch("api.services.proactive_activation.ProactiveActivationService._is_rollout_enabled_for_agent", return_value=True)
    @patch("api.services.proactive_activation.Analytics.track_event")
    @patch("api.services.proactive_activation.transaction.on_commit", side_effect=lambda fn: fn())
    @patch("api.services.proactive_activation.get_redis_client")
    def test_emits_analytics_event_on_trigger(self, mock_redis_client, _mock_on_commit, mock_track_event, _mock_flag):
        mock_redis_client.return_value = _FakeRedis()

        triggered = ProactiveActivationService.trigger_agents(batch_size=5)
        self.assertEqual(len(triggered), 1)

        self.assertTrue(mock_track_event.called)
        _, kwargs = mock_track_event.call_args
        self.assertEqual(kwargs["user_id"], self.user.id)
        self.assertEqual(kwargs["event"], AnalyticsEvent.PERSISTENT_AGENT_PROACTIVE_TRIGGERED)
        properties = kwargs["properties"]
        self.assertEqual(properties["agent_id"], str(triggered[0].id))
        self.assertEqual(properties["trigger_mode"], "scheduled")

    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.services.proactive_activation.ProactiveActivationService.trigger_agents")
    def test_schedule_task_enqueues_processing(self, mock_trigger, mock_delay):
        mock_trigger.return_value = [self.agent_a]
        processed = schedule_proactive_agents_task(batch_size=3)
        self.assertEqual(processed, 1)
        mock_delay.assert_called_once_with(str(self.agent_a.id))

    @override_settings(GOBII_RELEASE_ENV="staging")
    @patch("api.services.proactive_activation.ProactiveActivationService.trigger_agents")
    def test_schedule_task_skips_outside_production(self, mock_trigger):
        processed = schedule_proactive_agents_task(batch_size=3)
        self.assertEqual(processed, 0)
        mock_trigger.assert_not_called()

    @patch("api.services.proactive_activation.ProactiveActivationService._is_rollout_enabled_for_agent", return_value=True)
    @patch("api.services.proactive_activation.get_redis_client")
    def test_respects_minimum_weekly_interval(self, mock_redis_client, _mock_flag):
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

    @patch("api.services.proactive_activation.ProactiveActivationService._is_rollout_enabled_for_agent", return_value=True)
    @patch("api.services.proactive_activation.get_redis_client")
    def test_respects_recent_activity_cooldown(self, mock_redis_client, _mock_flag):
        mock_redis_client.return_value = _FakeRedis()
        self.agent_b.proactive_opt_in = False
        self.agent_b.save(update_fields=["proactive_opt_in"])

        now = timezone.now()
        cooldown = ProactiveActivationService.MIN_ACTIVITY_COOLDOWN
        almost_recent = cooldown - timedelta(hours=1)
        if almost_recent <= timedelta(0):
            almost_recent = cooldown / 2 if cooldown > timedelta(0) else timedelta(hours=1)

        self.agent_a.proactive_last_trigger_at = None
        self.agent_a.last_interaction_at = now - almost_recent
        self.agent_a.save(update_fields=["proactive_last_trigger_at", "last_interaction_at"])

        triggered = ProactiveActivationService.trigger_agents(batch_size=5)
        self.assertEqual(triggered, [])

        mock_redis_client.return_value = _FakeRedis()
        self.agent_a.refresh_from_db()
        self.agent_a.last_interaction_at = now - (cooldown + timedelta(hours=1))
        self.agent_a.save(update_fields=["last_interaction_at"])

        triggered_after_wait = ProactiveActivationService.trigger_agents(batch_size=5)
        self.assertEqual(len(triggered_after_wait), 1)
        self.assertEqual(triggered_after_wait[0].id, self.agent_a.id)

    @patch("api.services.proactive_activation.get_redis_client")
    def test_rollout_flag_blocks_agents(self, mock_redis_client):
        mock_redis_client.return_value = _FakeRedis()

        with patch(
            "api.services.proactive_activation.ProactiveActivationService._is_rollout_enabled_for_agent",
            return_value=False,
        ):
            triggered = ProactiveActivationService.trigger_agents(batch_size=5)

        self.assertEqual(triggered, [])

    @patch("api.services.proactive_activation.ProactiveActivationService._is_rollout_enabled_for_agent")
    @patch("api.services.proactive_activation.get_redis_client")
    def test_scans_additional_batches_when_rollout_disables_initial_candidates(self, mock_redis_client, mock_flag):
        mock_redis_client.return_value = _FakeRedis()
        # Ensure existing fixture agents do not appear in the candidate list.
        PersistentAgent.objects.filter(pk__in=[self.agent_a.pk, self.agent_b.pk]).update(proactive_opt_in=False)

        User = get_user_model()
        stale_timestamp = timezone.now() - timedelta(days=3)
        eligible_agents: list[PersistentAgent] = []
        for idx in range(3):
            user = User.objects.create_user(
                username=f"proactive-scan-{idx}@example.com",
                email=f"proactive-scan-{idx}@example.com",
                password="password",
            )
            quota, _ = UserQuota.objects.get_or_create(user=user)
            quota.agent_limit = 5
            quota.save()
            browser_agent = create_browser_agent_without_proxy(user, f"browser-scan-{idx}")
            agent = PersistentAgent.objects.create(
                user=user,
                name=f"agent-scan-{idx}",
                charter="Reach out",
                schedule="@daily",
                browser_use_agent=browser_agent,
                proactive_opt_in=True,
            )
            PersistentAgent.objects.filter(pk=agent.pk).update(last_interaction_at=stale_timestamp)
            agent.refresh_from_db()
            eligible_agents.append(agent)

        def flag_side_effect(agent):
            if flag_side_effect.calls < 2:
                flag_side_effect.calls += 1
                return False
            flag_side_effect.calls += 1
            return True

        flag_side_effect.calls = 0
        mock_flag.side_effect = flag_side_effect

        with patch.object(ProactiveActivationService, "SCAN_LIMIT", 1):
            triggered = ProactiveActivationService.trigger_agents(batch_size=1)

        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0].id, eligible_agents[-1].id)
