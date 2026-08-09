import uuid
from unittest.mock import patch

from celery.exceptions import Reject, Retry
from kombu.exceptions import OperationalError as KombuOperationalError
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from redis.exceptions import RedisError

from api.agent.core.compaction_exceptions import CompactionSummaryError
from api.agent.core.history_compaction import enqueue_history_compaction
from api.agent.core.history_compaction import history_compaction_needed
from api.agent.core.history_compaction import release_history_compaction_lease
from api.agent.core.internal_reasoning import build_internal_reasoning_description
from api.agent.tasks.history_compaction import compact_agent_history_task
from api.models import BrowserUseAgent, LLMRoutingProfile, PersistentAgent, PersistentAgentStep
from config.redis_client import get_redis_client


@tag("batch_compaction")
class AsyncHistoryCompactionTests(TestCase):
    def setUp(self):
        get_redis_client.cache_clear()
        self.redis = get_redis_client()
        user = get_user_model().objects.create_user(
            username="async-compaction@example.com",
            email="async-compaction@example.com",
            password="secret",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Async BA")
        self.agent = PersistentAgent.objects.create(
            user=user,
            name="Async Compaction Agent",
            charter="Compact safely",
            browser_use_agent=browser_agent,
        )

    def tearDown(self):
        get_redis_client.cache_clear()
        super().tearDown()

    @patch("api.agent.tasks.history_compaction.compact_agent_history_task.apply_async")
    @patch("api.agent.core.history_compaction.history_compaction_needed", return_value=True)
    def test_repeated_dispatch_is_coalesced(self, _needed_mock, apply_async_mock):
        first = enqueue_history_compaction(agent=self.agent)
        second = enqueue_history_compaction(agent=self.agent)

        self.assertTrue(first)
        self.assertFalse(second)
        apply_async_mock.assert_called_once()
        self.assertEqual(apply_async_mock.call_args.kwargs["queue"], "celery")

    @override_settings(PA_RAW_STEP_LIMIT=2)
    def test_threshold_check_uses_uncompacted_step_count(self):
        PersistentAgentStep.objects.bulk_create(
            [
                PersistentAgentStep(agent=self.agent, description="one"),
                PersistentAgentStep(agent=self.agent, description="two"),
            ]
        )
        self.assertFalse(history_compaction_needed(self.agent))

        PersistentAgentStep.objects.create(agent=self.agent, description="three")

        self.assertTrue(history_compaction_needed(self.agent))

    @override_settings(PA_RAW_STEP_LIMIT=2)
    def test_threshold_ignores_internal_reasoning_steps(self):
        for index in range(3):
            PersistentAgentStep.objects.create(
                agent=self.agent,
                description=build_internal_reasoning_description(f"thought {index}"),
            )

        self.assertFalse(history_compaction_needed(self.agent))

    @patch("api.agent.tasks.history_compaction.compact_agent_history_task.apply_async")
    @patch("api.agent.core.history_compaction.history_compaction_needed", return_value=True)
    def test_publish_failure_releases_lease(self, _needed_mock, apply_async_mock):
        lease_key = f"agent-history-compaction:{self.agent.id}"
        for error in (KombuOperationalError("publish failed"), OSError("connection reset")):
            apply_async_mock.side_effect = error

            with self.subTest(error=type(error).__name__):
                self.assertFalse(enqueue_history_compaction(agent=self.agent))
                self.assertIsNone(self.redis.get(lease_key))

        self.assertEqual(apply_async_mock.call_count, 2)

    @patch("api.agent.tasks.history_compaction.compact_agent_history_task.apply_async")
    @patch("api.agent.core.history_compaction.get_redis_client")
    @patch("api.agent.core.history_compaction.history_compaction_needed", return_value=True)
    def test_redis_failure_publishes_without_a_lease(
        self,
        _needed_mock,
        redis_mock,
        apply_async_mock,
    ):
        redis_mock.side_effect = RedisError("unavailable")

        self.assertTrue(enqueue_history_compaction(agent=self.agent))

        apply_async_mock.assert_called_once()
        self.assertEqual(apply_async_mock.call_args.kwargs["args"][1], "")

    @patch("api.agent.tasks.history_compaction.compact_agent_history_task.apply_async")
    @patch("api.agent.core.history_compaction.history_compaction_needed", return_value=False)
    def test_below_threshold_does_not_publish(self, _needed_mock, apply_async_mock):
        self.assertFalse(enqueue_history_compaction(agent=self.agent))
        apply_async_mock.assert_not_called()

    @patch("api.agent.tasks.history_compaction.ensure_comms_compacted")
    @patch("api.agent.tasks.history_compaction.ensure_steps_compacted")
    def test_task_preserves_routing_and_eval_attribution(
        self,
        ensure_steps_mock,
        ensure_comms_mock,
    ):
        profile = LLMRoutingProfile.objects.create(
            name="async-compaction-profile",
            display_name="Async Compaction Profile",
        )
        lease_token = "owned-token"
        lease_key = f"agent-history-compaction:{self.agent.id}"
        self.redis.set(lease_key, lease_token, ex=3600)

        compact_agent_history_task.run(
            str(self.agent.id),
            lease_token,
            str(profile.id),
            "eval-run-id",
        )

        self.assertIsNone(self.redis.get(lease_key))
        for mocked_call in (ensure_steps_mock, ensure_comms_mock):
            kwargs = mocked_call.call_args.kwargs
            self.assertEqual(kwargs["agent"].id, self.agent.id)
            self.assertEqual(kwargs["safety_identifier"], self.agent.user_id)
            summarise_fn = kwargs["summarise_fn"]
            self.assertEqual(summarise_fn.keywords["routing_profile"], profile)
            self.assertEqual(summarise_fn.keywords["eval_run_id"], "eval-run-id")

    @patch("api.agent.tasks.history_compaction.ensure_steps_compacted")
    def test_terminal_failure_releases_lease(self, ensure_steps_mock):
        ensure_steps_mock.side_effect = CompactionSummaryError("failed")
        lease_token = "failed-token"
        lease_key = f"agent-history-compaction:{self.agent.id}"
        self.redis.set(lease_key, lease_token, ex=3600)

        with patch.object(compact_agent_history_task, "max_retries", 0):
            compact_agent_history_task.run(str(self.agent.id), lease_token)

        self.assertIsNone(self.redis.get(lease_key))

    @patch("api.agent.tasks.history_compaction.ensure_comms_compacted")
    @patch("api.agent.tasks.history_compaction.ensure_steps_compacted")
    def test_missing_routing_profile_does_not_compact(
        self,
        ensure_steps_mock,
        ensure_comms_mock,
    ):
        lease_token = "missing-profile-token"
        lease_key = f"agent-history-compaction:{self.agent.id}"
        self.redis.set(lease_key, lease_token, ex=3600)

        with patch.object(compact_agent_history_task, "max_retries", 0):
            compact_agent_history_task.run(
                str(self.agent.id),
                lease_token,
                str(uuid.uuid4()),
            )

        ensure_steps_mock.assert_not_called()
        ensure_comms_mock.assert_not_called()
        self.assertIsNone(self.redis.get(lease_key))

    @patch("api.agent.tasks.history_compaction.ensure_steps_compacted")
    def test_retry_keeps_and_refreshes_owned_lease(self, ensure_steps_mock):
        ensure_steps_mock.side_effect = CompactionSummaryError("retry")
        lease_token = "retry-token"
        lease_key = f"agent-history-compaction:{self.agent.id}"
        self.redis.set(lease_key, lease_token, ex=1)

        with patch.object(
            compact_agent_history_task,
            "retry",
            side_effect=Retry(),
        ) as retry_mock:
            with self.assertRaises(Retry):
                compact_agent_history_task.run(str(self.agent.id), lease_token)

        self.assertEqual(self.redis.get(lease_key), lease_token)
        self.assertGreater(self.redis.ttl(lease_key), 1)
        self.assertEqual(retry_mock.call_args.kwargs["countdown"], 5)

    @patch("api.agent.tasks.history_compaction.ensure_steps_compacted")
    def test_retry_publish_failure_releases_lease(self, ensure_steps_mock):
        ensure_steps_mock.side_effect = CompactionSummaryError("retry")
        lease_token = "retry-publish-failed-token"
        lease_key = f"agent-history-compaction:{self.agent.id}"
        self.redis.set(lease_key, lease_token, ex=3600)

        with patch.object(
            compact_agent_history_task,
            "retry",
            side_effect=Reject(RuntimeError("broker unavailable"), requeue=False),
        ):
            with self.assertRaises(Reject):
                compact_agent_history_task.run(str(self.agent.id), lease_token)

        self.assertIsNone(self.redis.get(lease_key))

    def test_old_worker_cannot_release_replacement_lease(self):
        lease_key = f"agent-history-compaction:{self.agent.id}"
        self.redis.set(lease_key, "replacement-token", ex=3600)

        release_history_compaction_lease(self.agent.id, "old-token")

        self.assertEqual(self.redis.get(lease_key), "replacement-token")

    def test_missing_agent_releases_lease(self):
        missing_agent_id = str(uuid.uuid4())
        lease_token = "missing-token"
        lease_key = f"agent-history-compaction:{missing_agent_id}"
        self.redis.set(lease_key, lease_token, ex=3600)

        compact_agent_history_task.run(missing_agent_id, lease_token)

        self.assertIsNone(self.redis.get(lease_key))
