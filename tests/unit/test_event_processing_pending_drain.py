import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.core.processing_flags import pending_set_key
from api.agent.tasks.process_events import process_pending_agent_events_task
from api.models import BrowserUseAgent, PersistentAgent
from config.redis_client import get_redis_client


@tag("batch_event_processing")
class PendingDrainValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="pending-drain-user",
            email="pending-drain@example.com",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(
            user=cls.user,
            name="Pending Drain Browser Agent",
        )
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Pending Drain Agent",
            charter="test",
            browser_use_agent=cls.browser_agent,
        )

    def setUp(self) -> None:
        os.environ["USE_FAKE_REDIS"] = "1"
        get_redis_client.cache_clear()
        self.redis = get_redis_client()
        self.redis.delete(pending_set_key())

    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    def test_pending_drain_skips_invalid_ids(self, mock_delay) -> None:
        self.redis.sadd(pending_set_key(), str(self.agent.id), "schedule")

        process_pending_agent_events_task.run(max_agents=10, delay_seconds=0)

        mock_delay.assert_called_once_with(str(self.agent.id))
        self.assertEqual(self.redis.scard(pending_set_key()), 0)
