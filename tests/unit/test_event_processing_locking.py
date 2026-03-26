"""Tests for event processing lock fallback scheduling."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.core import event_processing as ep
from api.agent.core.processing_flags import (
    get_processing_locked_agent_ids,
    pending_drain_schedule_key,
    pending_set_key,
    processing_lock_storage_keys,
)
from api.agent.core import processing_flags as pf
from api.models import BrowserUseAgent, PersistentAgent
from config.redis_client import get_redis_client


class _BlockedRedlock:
    def __init__(self, *args, **kwargs):
        self.auto_release_time = kwargs.get("auto_release_time")

    def acquire(self, *, blocking=True, timeout=-1):
        return False


class _RetryingRedlock:
    def __init__(self, *args, **kwargs):
        self._calls = 0
        self.auto_release_time = kwargs.get("auto_release_time")

    def acquire(self, *, blocking=True, timeout=-1):
        self._calls += 1
        return self._calls > 1

    def release(self):
        return True


class _ReleaseFailingRedlock:
    def __init__(self, *args, **kwargs):
        self.key = kwargs.get("key")
        self.auto_release_time = kwargs.get("auto_release_time")
        self.redis_client = next(iter(kwargs.get("masters", set())))

    def acquire(self, *, blocking=True, timeout=-1):
        for storage_key in ep._lock_storage_keys(self.key):
            self.redis_client.set(storage_key, "1")
        return True

    def release(self):
        raise RuntimeError("release failed")


@tag("batch_event_processing")
class EventProcessingLockFallbackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="locktest@example.com",
            email="locktest@example.com",
            password="password",
        )

    def setUp(self):
        self._settings_patcher = patch.multiple(
            ep.settings,
            AGENT_EVENT_PROCESSING_LOCK_TIMEOUT_SECONDS=60,
            AGENT_EVENT_PROCESSING_PENDING_SET_TTL_SECONDS=300,
            AGENT_EVENT_PROCESSING_PENDING_DRAIN_DELAY_SECONDS=65,
            AGENT_EVENT_PROCESSING_PENDING_DRAIN_SCHEDULE_TTL_SECONDS=120,
        )
        self._settings_patcher.start()
        self.addCleanup(self._settings_patcher.stop)
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="browser-agent-for-lock-test",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Lock Test Agent",
            charter="Test charter",
            browser_use_agent=browser_agent,
        )
        self.redis = get_redis_client()
        self.redis.delete(pending_set_key())
        self.redis.delete(pending_drain_schedule_key())

    @patch("api.agent.tasks.process_events.process_pending_agent_events_task.apply_async")
    @patch("api.agent.core.event_processing.Redlock", new=_BlockedRedlock)
    def test_lock_busy_schedules_pending_drain(self, mock_apply_async):
        ep.process_agent_events(self.agent.id)

        self.assertTrue(mock_apply_async.called)
        call_kwargs = mock_apply_async.call_args.kwargs
        self.assertEqual(call_kwargs["countdown"], 65)
        self.assertTrue(self.redis.sismember(pending_set_key(), str(self.agent.id)))
        self.assertTrue(self.redis.exists(pending_drain_schedule_key()))

    @patch("api.agent.tasks.process_events.process_pending_agent_events_task.apply_async")
    @patch("api.agent.core.event_processing.Redlock", new=_BlockedRedlock)
    def test_lock_busy_does_not_reschedule_when_drain_slot_claimed(self, mock_apply_async):
        self.redis.set(pending_drain_schedule_key(), "1")

        ep.process_agent_events(self.agent.id)

        mock_apply_async.assert_not_called()
        self.assertTrue(self.redis.sismember(pending_set_key(), str(self.agent.id)))

    @patch("api.agent.core.event_processing._process_agent_events_locked", return_value=None)
    @patch("api.agent.core.event_processing.Redlock", new=_RetryingRedlock)
    def test_stale_lock_is_cleared_and_reacquired(self, _mock_locked):
        lock_key = f"agent-event-processing:{self.agent.id}"
        self.redis.set(lock_key, "1")
        self.redis.expire(lock_key, 14400)

        ep.process_agent_events(self.agent.id)

        self.assertTrue(_mock_locked.called)
        self.assertFalse(self.redis.exists(lock_key))

    @patch("api.agent.core.event_processing._process_agent_events_locked", return_value=None)
    @patch("api.agent.core.event_processing.Redlock", new=_ReleaseFailingRedlock)
    def test_release_failure_keeps_locked_index_when_lock_key_still_exists(self, _mock_locked):
        ep.process_agent_events(self.agent.id)

        self.assertIn(str(self.agent.id), get_processing_locked_agent_ids(client=self.redis))
        self.assertTrue(any(self.redis.exists(key) for key in processing_lock_storage_keys(self.agent.id)))

    def test_processing_lock_storage_keys_uses_redlock_prefix(self):
        with patch.object(pf.Redlock, "_KEY_PREFIX", "custom-redlock"):
            self.assertEqual(
                processing_lock_storage_keys(self.agent.id),
                (
                    f"custom-redlock:agent-event-processing:{self.agent.id}",
                    f"agent-event-processing:{self.agent.id}",
                ),
            )
