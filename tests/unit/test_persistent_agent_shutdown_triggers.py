from unittest.mock import patch

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

        with patch("api.services.agent_lifecycle.AgentLifecycleService.shutdown", side_effect=_mock_shutdown):
            # 1) Pause: is_active True -> False
            with transaction.atomic():
                agent.is_active = False
                agent.save(update_fields=["is_active"])
            self.assertIn((str(agent.id), "PAUSE"), calls)

            # 2) Cron disabled: schedule set -> empty
            calls.clear()
            with transaction.atomic():
                agent.schedule = ""
                agent.save(update_fields=["schedule"])
            self.assertIn((str(agent.id), "CRON_DISABLED"), calls)

            # 3) Soft expire: life_state ACTIVE -> EXPIRED
            calls.clear()
            with transaction.atomic():
                agent.life_state = PersistentAgent.LifeState.EXPIRED
                agent.save(update_fields=["life_state"])
            self.assertIn((str(agent.id), "SOFT_EXPIRE"), calls)

