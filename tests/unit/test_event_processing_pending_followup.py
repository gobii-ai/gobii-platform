"""Ensure pending follow-ups are dropped once a cycle closes early."""

import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.core.budget import AgentBudgetManager
from api.agent.core.event_processing import process_agent_events
from api.models import BrowserUseAgent, PersistentAgent
from config.redis_client import get_redis_client


@tag("batch_event_processing")
class PendingFollowUpClosureTests(TestCase):
    """Prove that a pending follow-up is skipped once the cycle is closed."""

    @classmethod
    def setUpTestData(cls) -> None:
        os.environ["USE_FAKE_REDIS"] = "1"
        get_redis_client.cache_clear()

        User = get_user_model()
        cls.user = User.objects.create_user(
            username="pending-followup-user",
            email="pending-followup@example.com",
        )

        cls.browser_agent = BrowserUseAgent.objects.create(
            user=cls.user,
            name="Pending Follow-Up Browser Agent",
        )

        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Pending Follow-Up Agent",
            charter="test",
            browser_use_agent=cls.browser_agent,
        )

    def setUp(self) -> None:
        os.environ["USE_FAKE_REDIS"] = "1"
        get_redis_client.cache_clear()
        self.redis = get_redis_client()

    @patch("api.agent.core.event_processing.Redlock")
    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    @patch("api.agent.core.event_processing._process_agent_events_locked")
    def test_pending_flag_triggers_follow_up_without_cycle_close(self, mock_locked, mock_delay, mock_redlock) -> None:
        pending_key = f"agent-event-processing:pending:{self.agent.id}"

        mock_lock = mock_redlock.return_value
        mock_lock.acquire.return_value = True
        mock_lock.release.return_value = True

        def mark_pending_only(*_args, **_kwargs):
            # Simulate a background completion setting the pending flag while the lock is held.
            self.redis.set(pending_key, "1")
            return self.agent

        mock_locked.side_effect = mark_pending_only

        process_agent_events(self.agent.id)

        # Pending flag should be consumed and a follow-up should be scheduled using the active cycle.
        self.assertIsNone(self.redis.get(pending_key))
        mock_delay.assert_called_once()

        args, kwargs = mock_delay.call_args
        self.assertEqual(str(self.agent.id), args[0])
        self.assertEqual(
            AgentBudgetManager.get_cycle_status(agent_id=str(self.agent.id)),
            "active",
        )
        self.assertEqual(kwargs.get("budget_id"), AgentBudgetManager.get_active_budget_id(agent_id=str(self.agent.id)))
