from unittest.mock import patch, MagicMock

from django.test import TestCase, tag
from django.contrib.auth import get_user_model

from api.models import PersistentAgent, BrowserUseAgent
from api.services.agent_lifecycle import AgentCleanupRegistry
from api.tasks.agent_lifecycle import agent_shutdown_cleanup_task
from util.analytics import AnalyticsEvent


def _create_browser_agent(user):
    with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
        return BrowserUseAgent.objects.create(user=user, name="test-browser-agent")

@tag("batch_agent_lifecycle")
class AgentLifecycleRegistryTests(TestCase):
    def setUp(self):
        # Snapshot and clear registry handlers
        self._orig_handlers = list(AgentCleanupRegistry._handlers)
        AgentCleanupRegistry._handlers = []

    def tearDown(self):
        AgentCleanupRegistry._handlers = self._orig_handlers

    def test_reason_filtering(self):
        calls = []

        def h1(agent_id, reason, meta):
            calls.append(("h1", reason))

        def h2(agent_id, reason, meta):
            calls.append(("h2", reason))

        AgentCleanupRegistry.register(h1, reasons=["HARD_DELETE"])  # only delete
        AgentCleanupRegistry.register(h2)  # all reasons

        # HARD_DELETE: both
        hs = AgentCleanupRegistry.get_for_reason("HARD_DELETE")
        self.assertEqual(set(h.__name__ for h in hs), {"h1", "h2"})

        # PAUSE: only h2
        hs2 = AgentCleanupRegistry.get_for_reason("PAUSE")
        self.assertEqual(set(h.__name__ for h in hs2), {"h2"})

    @patch("api.tasks.agent_lifecycle.get_redis_client")
    @patch("util.analytics.Analytics.track_event")
    def test_task_runs_allowed_handlers_and_emits_analytics(self, mock_track, mock_redis):
        # Arrange redis guard to allow execution
        mock_r = MagicMock()
        mock_r.set.return_value = True
        mock_redis.return_value = mock_r

        # Create agent with owner
        User = get_user_model()
        user = User.objects.create_user(username="lifecycle@example.com")
        bua = _create_browser_agent(user)
        agent = PersistentAgent.objects.create(user=user, name="lifecycle-agent", charter="c", browser_use_agent=bua)

        # Register only our test handlers
        calls = []

        def allowed(agent_id, reason, meta):
            calls.append(("allowed", reason, dict(meta or {})))

        def blocked(agent_id, reason, meta):
            calls.append(("blocked", reason, dict(meta or {})))

        AgentCleanupRegistry._handlers = []
        AgentCleanupRegistry.register(allowed, reasons=["HARD_DELETE"])  # only for delete
        AgentCleanupRegistry.register(blocked, reasons=["PAUSE"])       # not for delete

        # Act: run task for HARD_DELETE
        # Provide user_id in meta so code uses track_event (not anonymous)
        agent_shutdown_cleanup_task(str(agent.id), "HARD_DELETE", {"foo": "bar", "user_id": user.id})

        # Assert only allowed handler ran
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "allowed")
        self.assertEqual(calls[0][1], "HARD_DELETE")
        self.assertEqual(calls[0][2].get("foo"), "bar")

        # Analytics was emitted with shutdown event
        self.assertTrue(mock_track.called)
        # Validate called with shutdown event (enum value string)
        _, kwargs = mock_track.call_args
        self.assertEqual(kwargs.get("event"), AnalyticsEvent.PERSISTENT_AGENT_SHUTDOWN)
