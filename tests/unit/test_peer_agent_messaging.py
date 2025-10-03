from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.peer_comm import PeerMessagingError, PeerMessagingService, PeerSendResult
from api.models import (
    AgentCommPeerState,
    AgentPeerLink,
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentStep,
)


@tag("batch_peer_dm")
class PeerMessagingServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="peer-owner",
            email="owner@example.com",
            password="testpass123",
        )

        cls.browser_agent_a = BrowserUseAgent.objects.create(
            user=cls.user,
            name="Browser A",
        )
        cls.browser_agent_b = BrowserUseAgent.objects.create(
            user=cls.user,
            name="Browser B",
        )

        cls.agent_a = PersistentAgent.objects.create(
            user=cls.user,
            name="Agent Alpha",
            charter="Assist with ops",
            browser_use_agent=cls.browser_agent_a,
        )
        cls.agent_b = PersistentAgent.objects.create(
            user=cls.user,
            name="Agent Beta",
            charter="Handle finance",
            browser_use_agent=cls.browser_agent_b,
        )

    def setUp(self):
        AgentPeerLink.objects.all().delete()
        AgentCommPeerState.objects.all().delete()
        PersistentAgentMessage.objects.all().delete()

        self.link = AgentPeerLink.objects.create(
            agent_a=self.agent_a,
            agent_b=self.agent_b,
            messages_per_window=2,
            window_hours=6,
            created_by=self.user,
        )
        self.service = PeerMessagingService(self.agent_a, self.agent_b)

    def test_send_message_creates_records_and_triggers_processing(self):
        with patch('api.agent.tasks.process_agent_events_task') as task_mock, patch(
            'api.agent.peer_comm.transaction.on_commit', lambda cb: cb()
        ):
            task_mock.delay = MagicMock()
            result = self.service.send_message("Hello Beta")

        self.assertEqual(result.status, "ok")
        state = AgentCommPeerState.objects.get(link=self.link, channel=CommsChannel.OTHER)
        self.assertEqual(state.credits_remaining, 1)
        self.assertTrue(self.link.conversation)
        self.assertTrue(self.link.conversation.is_peer_dm)

        outbound = PersistentAgentMessage.objects.filter(owner_agent=self.agent_a).first()
        inbound = PersistentAgentMessage.objects.filter(owner_agent=self.agent_b).first()

        self.assertIsNotNone(outbound)
        self.assertTrue(outbound.is_outbound)
        self.assertEqual(outbound.peer_agent, self.agent_b)
        self.assertEqual(outbound.conversation, self.link.conversation)

        self.assertIsNotNone(inbound)
        self.assertFalse(inbound.is_outbound)
        self.assertEqual(inbound.peer_agent, self.agent_a)
        self.assertEqual(inbound.body, "Hello Beta")

        task_mock.delay.assert_called_once_with(str(self.agent_b.id))

    def test_debounce_prevents_rapid_repeat(self):
        with patch('api.agent.tasks.process_agent_events_task') as task_mock, patch(
            'api.agent.peer_comm.transaction.on_commit', lambda cb: cb()
        ):
            task_mock.delay = MagicMock()
            self.service.send_message("First message")

        with self.assertRaises(PeerMessagingError) as err_ctx, patch(
            'api.agent.tasks.process_agent_events_task'
        ) as task_mock, patch('api.agent.peer_comm.transaction.on_commit', lambda cb: cb()):
            task_mock.delay = MagicMock()
            self.service.send_message("Too soon")

        self.assertEqual(err_ctx.exception.status, "debounced")
        # Only original outbound + inbound messages should exist
        self.assertEqual(
            PersistentAgentMessage.objects.filter(owner_agent=self.agent_a, is_outbound=True).count(),
            1,
        )

    def test_throttle_when_quota_exhausted(self):
        AgentCommPeerState.objects.all().delete()
        self.link.delete()
        self.link = AgentPeerLink.objects.create(
            agent_a=self.agent_a,
            agent_b=self.agent_b,
            messages_per_window=1,
            window_hours=6,
            created_by=self.user,
        )
        self.service = PeerMessagingService(self.agent_a, self.agent_b)

        with patch('api.agent.tasks.process_agent_events_task') as task_mock, patch(
            'api.agent.peer_comm.transaction.on_commit', lambda cb: cb()
        ):
            task_mock.delay = MagicMock()
            self.service.send_message("First")

        state = AgentCommPeerState.objects.get(link=self.link, channel=CommsChannel.OTHER)
        self.assertEqual(state.credits_remaining, 0)
        state.last_message_at = timezone.now() - timedelta(seconds=10)
        state.save(update_fields=['last_message_at'])

        with patch('api.agent.tasks.process_agent_events_task') as task_mock:
            task_mock.delay = MagicMock()
            task_mock.apply_async = MagicMock()
            with patch('api.agent.peer_comm.transaction.on_commit', lambda cb: cb()):
                with self.assertRaises(PeerMessagingError) as err_ctx:
                    self.service.send_message("Second")

        self.assertEqual(err_ctx.exception.status, "throttled")
        task_mock.apply_async.assert_called_once()
        self.assertEqual(
            PersistentAgentMessage.objects.filter(owner_agent=self.agent_a, is_outbound=True).count(),
            1,
        )

    def test_execute_tool_handles_errors(self):
        from api.agent.tools.peer_dm import execute_send_agent_message

        response = execute_send_agent_message(self.agent_a, {"peer_agent_id": str(self.agent_a.id), "message": "hi"})
        self.assertEqual(response["status"], "error")
        self.assertIn("Cannot send", response["message"])

    def test_execute_tool_success_sets_auto_sleep_flag(self):
        from api.agent.tools.peer_dm import execute_send_agent_message

        with patch("api.agent.tools.peer_dm.PeerMessagingService") as service_cls:
            service_cls.return_value.send_message.return_value = PeerSendResult(
                status="ok",
                message="delivered",
                remaining_credits=1,
                window_reset_at=timezone.now(),
            )

            response = execute_send_agent_message(
                self.agent_a,
                {"peer_agent_id": str(self.agent_b.id), "message": "handoff"},
            )

        self.assertEqual(response["status"], "ok")
        self.assertTrue(response.get("auto_sleep_ok"))


@tag("batch_peer_intro")
class AgentPeerLinkSignalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="peer-owner-signal",
            email="owner-signal@example.com",
            password="testpass123",
        )

        cls.browser_agent_a = BrowserUseAgent.objects.create(
            user=cls.user,
            name="Browser Signal A",
        )
        cls.browser_agent_b = BrowserUseAgent.objects.create(
            user=cls.user,
            name="Browser Signal B",
        )

        cls.agent_a = PersistentAgent.objects.create(
            user=cls.user,
            name="Signal Alpha",
            charter="Coordinate launch readiness",
            browser_use_agent=cls.browser_agent_a,
        )
        cls.agent_b = PersistentAgent.objects.create(
            user=cls.user,
            name="Signal Beta",
            charter="Own vendor negotiations",
            browser_use_agent=cls.browser_agent_b,
        )

    def test_peer_link_creation_skips_intro_steps_and_processing(self):
        def immediate_on_commit(func, using=None):
            func()

        with patch('django.db.transaction.on_commit', immediate_on_commit), patch(
            'api.agent.tasks.process_agent_events_task.delay'
        ) as delay_mock:
            link = AgentPeerLink.objects.create(
                agent_a=self.agent_a,
                agent_b=self.agent_b,
                messages_per_window=2,
                window_hours=6,
                created_by=self.user,
            )

        self.assertTrue(AgentPeerLink.objects.filter(id=link.id).exists())

        steps_a = PersistentAgentStep.objects.filter(agent=self.agent_a)
        steps_b = PersistentAgentStep.objects.filter(agent=self.agent_b)

        self.assertEqual(steps_a.count(), 0)
        self.assertEqual(steps_b.count(), 0)
        delay_mock.assert_not_called()
