from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings, tag
from django.db import transaction
from django.contrib.auth import get_user_model

from api.models import PersistentAgent, BrowserUseAgent


def _create_browser_agent(user):
    with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
        return BrowserUseAgent.objects.create(user=user, name="bua")

@tag("batch_pa_shutdown_triggers")
class PersistentAgentShutdownTriggersTests(TestCase):
    def setUp(self):
        # Prevent schedule sync side effect (RedBeat) during these tests
        self._sync_patch = patch.object(PersistentAgent, "_sync_celery_beat_task", return_value=None)
        self._sync_patch.start()

    def tearDown(self):
        self._sync_patch.stop()

    @override_settings(GOBII_RELEASE_ENV="test")
    def test_shutdown_transitions_enqueue_service(self):
        User = get_user_model()
        user = User.objects.create_user(username="triggers@example.com")
        bua = _create_browser_agent(user)

        agent = PersistentAgent.objects.create(
            user=user,
            name="t",
            charter="c",
            browser_use_agent=bua,
            schedule="0 * * * *",  # start with a schedule
        )

        calls = []

        def _mock_shutdown(agent_id, reason, meta=None):
            calls.append((str(agent_id), str(reason)))

        # Execute on_commit callbacks immediately so we can assert in‑test
        with patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            with patch("api.services.agent_lifecycle.AgentLifecycleService.shutdown", side_effect=_mock_shutdown):
                # 1) Pause: is_active True -> False
                with transaction.atomic():
                    agent.is_active = False
                    agent.save(update_fields=["is_active"])
                self.assertIn((str(agent.id), "PAUSE"), calls)

                # 2) Cron disabled: schedule set -> None
                calls.clear()
                agent.refresh_from_db()
                with transaction.atomic():
                    agent.schedule = None
                    agent.save(update_fields=["schedule"])
                self.assertIn((str(agent.id), "CRON_DISABLED"), calls)

                # 2b) Saving without a schedule should not re-trigger cleanup
                calls.clear()
                agent.refresh_from_db()
                with transaction.atomic():
                    agent.charter = "updated charter"
                    agent.save(update_fields=["charter"])
                self.assertNotIn((str(agent.id), "CRON_DISABLED"), calls)

                # 3) Soft expire: life_state ACTIVE -> EXPIRED
                calls.clear()
                with transaction.atomic():
                    agent.life_state = PersistentAgent.LifeState.EXPIRED
                    agent.save(update_fields=["life_state"])
                self.assertIn((str(agent.id), "SOFT_EXPIRE"), calls)

    @override_settings(GOBII_RELEASE_ENV="test")
    def test_pause_transition_clears_processing_work_state(self):
        User = get_user_model()
        user = User.objects.create_user(username="pause-clears@example.com")
        bua = _create_browser_agent(user)

        agent = PersistentAgent.objects.create(
            user=user,
            name="pause-clear",
            charter="c",
            browser_use_agent=bua,
            schedule="0 * * * *",
        )
        fake_redis = MagicMock()

        with patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            with patch("api.agent.core.processing_flags.get_redis_client", return_value=fake_redis):
                with patch("api.services.agent_lifecycle.AgentLifecycleService.shutdown"):
                    with transaction.atomic():
                        agent.is_active = False
                        agent.save(update_fields=["is_active"])

        fake_redis.delete.assert_any_call(f"agent-event-processing:queued:{agent.id}")
        fake_redis.srem.assert_called_with("agent-event-processing:pending", str(agent.id))
