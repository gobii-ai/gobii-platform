import os
from unittest.mock import patch

from celery.exceptions import CeleryError
from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.core.processing_flags import (
    claim_pending_agent,
    claim_pending_agents,
    clear_processing_work_state,
    enqueue_pending_agent,
    pending_set_key,
    remove_pending_agent,
)
from api.agent.tasks.process_events import (
    AGENT_DEFAULT_PROCESSING_QUEUE,
    AGENT_INTERACTIVE_PROCESSING_QUEUE,
    process_pending_agent_events_task,
)
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
        remove_pending_agent(self.agent.id, client=self.redis)
        self.redis.delete(pending_set_key())

    @patch("api.agent.tasks.process_events.process_agent_events_task.apply_async")
    def test_pending_drain_skips_invalid_ids(self, mock_apply_async) -> None:
        self.redis.sadd(pending_set_key(), str(self.agent.id), "schedule")

        process_pending_agent_events_task.run(max_agents=10, delay_seconds=0)

        mock_apply_async.assert_called_once_with(
            args=[str(self.agent.id)],
            kwargs={},
            queue=AGENT_DEFAULT_PROCESSING_QUEUE,
        )
        self.assertEqual(self.redis.scard(pending_set_key()), 0)

    def test_pending_metadata_coalesces_latest_generation_and_interactive_queue(self) -> None:
        enqueue_pending_agent(
            self.agent.id,
            inbound_generation=2,
            inbound_message_id="00000000-0000-0000-0000-000000000002",
            queue=AGENT_DEFAULT_PROCESSING_QUEUE,
            client=self.redis,
        )
        enqueue_pending_agent(
            self.agent.id,
            inbound_generation=4,
            inbound_message_id="00000000-0000-0000-0000-000000000004",
            queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
            client=self.redis,
        )
        enqueue_pending_agent(
            self.agent.id,
            inbound_generation=3,
            inbound_message_id="00000000-0000-0000-0000-000000000003",
            queue=AGENT_DEFAULT_PROCESSING_QUEUE,
            client=self.redis,
        )

        pending_work = claim_pending_agent(self.agent.id, client=self.redis)

        self.assertIsNotNone(pending_work)
        self.assertEqual(pending_work.inbound_generation, 4)
        self.assertEqual(pending_work.queue, AGENT_INTERACTIVE_PROCESSING_QUEUE)
        self.assertEqual(
            pending_work.inbound_message_id,
            "00000000-0000-0000-0000-000000000004",
        )
        self.assertFalse(pending_work.has_generic_work)
        self.assertIsNone(claim_pending_agent(self.agent.id, client=self.redis))

    def test_generic_pending_work_is_preserved_with_generation_metadata(self) -> None:
        enqueue_pending_agent(
            self.agent.id,
            inbound_generation=5,
            queue=AGENT_DEFAULT_PROCESSING_QUEUE,
            client=self.redis,
        )
        enqueue_pending_agent(
            self.agent.id,
            queue=AGENT_DEFAULT_PROCESSING_QUEUE,
            client=self.redis,
        )

        pending_work = claim_pending_agent(self.agent.id, client=self.redis)

        self.assertEqual(pending_work.inbound_generation, 5)
        self.assertTrue(pending_work.has_generic_work)

    def test_claimed_mixed_work_can_be_restored_atomically(self) -> None:
        enqueue_pending_agent(
            self.agent.id,
            inbound_generation=5,
            queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
            has_generic_work=True,
            client=self.redis,
        )

        pending_work = claim_pending_agent(self.agent.id, client=self.redis)

        self.assertEqual(pending_work.inbound_generation, 5)
        self.assertEqual(pending_work.queue, AGENT_INTERACTIVE_PROCESSING_QUEUE)
        self.assertTrue(pending_work.has_generic_work)

    def test_specific_and_drain_claims_are_mutually_exclusive(self) -> None:
        enqueue_pending_agent(
            self.agent.id,
            inbound_generation=6,
            queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
            client=self.redis,
        )

        claimed_on_lock_release = claim_pending_agent(self.agent.id, client=self.redis)
        claimed_by_drain = claim_pending_agents(limit=10, client=self.redis)

        self.assertEqual(claimed_on_lock_release.inbound_generation, 6)
        self.assertEqual(claimed_by_drain, [])

    def test_lifecycle_cleanup_removes_pending_metadata(self) -> None:
        enqueue_pending_agent(
            self.agent.id,
            inbound_generation=6,
            queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
            client=self.redis,
        )

        clear_processing_work_state(self.agent.id, client=self.redis)

        self.assertIsNone(claim_pending_agent(self.agent.id, client=self.redis))

    @patch("api.agent.tasks.process_events.process_agent_events_task.apply_async")
    def test_pending_drain_carries_generation_and_queue(self, mock_apply_async) -> None:
        enqueue_pending_agent(
            self.agent.id,
            inbound_generation=7,
            inbound_message_id="00000000-0000-0000-0000-000000000007",
            queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
            client=self.redis,
        )

        process_pending_agent_events_task.run(max_agents=10, delay_seconds=0)

        mock_apply_async.assert_called_once_with(
            args=[str(self.agent.id)],
            kwargs={
                "inbound_generation": 7,
                "inbound_message_id": "00000000-0000-0000-0000-000000000007",
            },
            queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
        )

    @patch("api.agent.tasks.process_events.process_pending_agent_events_task.apply_async")
    @patch(
        "api.agent.tasks.process_events.process_agent_events_task.apply_async",
        side_effect=CeleryError("broker unavailable"),
    )
    def test_pending_drain_restores_claim_when_publish_fails(
        self,
        _mock_process_apply_async,
        mock_drain_apply_async,
    ) -> None:
        enqueue_pending_agent(
            self.agent.id,
            inbound_generation=8,
            queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
            has_generic_work=True,
            client=self.redis,
        )

        process_pending_agent_events_task.run(max_agents=10, delay_seconds=0)

        restored_work = claim_pending_agent(self.agent.id, client=self.redis)
        self.assertEqual(restored_work.inbound_generation, 8)
        self.assertEqual(restored_work.queue, AGENT_INTERACTIVE_PROCESSING_QUEUE)
        self.assertTrue(restored_work.has_generic_work)
        mock_drain_apply_async.assert_called_once_with(countdown=0)
