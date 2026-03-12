from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.files.attachment_helpers import resolve_filespace_attachments
from api.agent.files.filespace_service import get_or_create_default_filespace, write_bytes_to_dir
from api.agent.peer_comm import (
    PeerMessagingDuplicateError,
    PeerMessagingError,
    PeerMessagingService,
    PeerSendResult,
)
from api.models import (
    AgentCommPeerState,
    AgentFsNode,
    AgentPeerLink,
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentMessageAttachment,
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

    def _create_sender_attachment(self, path: str, content: bytes, mime_type: str = "text/plain") -> AgentFsNode:
        result = write_bytes_to_dir(
            self.agent_a,
            content,
            path,
            mime_type,
        )
        self.assertEqual(result["status"], "ok")
        return AgentFsNode.objects.get(id=result["node_id"])

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

    def test_send_message_with_attachment_copies_file_before_processing(self):
        source_node = self._create_sender_attachment("/reports/summary.txt", b"Quarterly summary")
        attachments = resolve_filespace_attachments(self.agent_a, [source_node.path])
        expected_prefix = f"/Inbox/{timezone.now().date().isoformat()}/peer-Agent_Alpha/"

        def assert_processing_after_copy(agent_id: str):
            copied_nodes = list(
                AgentFsNode.objects.alive()
                .filter(
                    filespace=get_or_create_default_filespace(self.agent_b),
                    path__startswith=expected_prefix,
                )
                .order_by("path")
            )
            self.assertEqual(agent_id, str(self.agent_b.id))
            self.assertEqual(len(copied_nodes), 1)
            self.assertEqual(copied_nodes[0].name, "summary.txt")

        with patch("api.agent.tasks.process_agent_events_task") as task_mock, patch(
            "api.agent.peer_comm.transaction.on_commit", lambda cb: cb()
        ):
            task_mock.delay = MagicMock(side_effect=assert_processing_after_copy)
            result = self.service.send_message("Hello Beta", attachments=attachments)

        self.assertEqual(result.status, "ok")
        outbound = PersistentAgentMessage.objects.get(owner_agent=self.agent_a, is_outbound=True)
        inbound = PersistentAgentMessage.objects.get(owner_agent=self.agent_b, is_outbound=False)

        outbound_attachment = outbound.attachments.get()
        inbound_attachment = inbound.attachments.get()

        self.assertEqual(outbound_attachment.filespace_node_id, source_node.id)
        self.assertEqual(outbound_attachment.filespace_node.path, "/reports/summary.txt")
        self.assertIsNotNone(inbound_attachment.filespace_node)
        self.assertTrue(inbound_attachment.filespace_node.path.startswith(expected_prefix))
        self.assertNotEqual(inbound_attachment.filespace_node_id, source_node.id)
        self.assertEqual(inbound_attachment.filename, "summary.txt")

    def test_send_message_with_multiple_attachments_dedupes_recipient_filenames(self):
        self._create_sender_attachment("/reports/q1/report.txt", b"Q1")
        self._create_sender_attachment("/reports/q2/report.txt", b"Q2")
        attachments = resolve_filespace_attachments(
            self.agent_a,
            ["/reports/q1/report.txt", "/reports/q2/report.txt"],
        )

        with patch("api.agent.tasks.process_agent_events_task") as task_mock, patch(
            "api.agent.peer_comm.transaction.on_commit", lambda cb: cb()
        ):
            task_mock.delay = MagicMock()
            self.service.send_message("Please review both reports", attachments=attachments)

        inbound = PersistentAgentMessage.objects.get(owner_agent=self.agent_b, is_outbound=False)
        inbound_names = sorted(inbound.attachments.values_list("filename", flat=True))
        self.assertEqual(inbound_names, ["report (2).txt", "report.txt"])

    def test_send_message_retries_directory_creation_after_race(self):
        from api.agent.peer_comm import get_or_create_dir as original_get_or_create_dir

        self._create_sender_attachment("/reports/race.txt", b"race")
        attachments = resolve_filespace_attachments(self.agent_a, ["/reports/race.txt"])
        original_helper = "api.agent.peer_comm.get_or_create_dir"
        peer_dir_name = self.service._peer_inbox_dir_name()
        failed_names: set[str] = set()

        def flaky_get_or_create_dir(filespace, parent, name):
            if name == peer_dir_name and name not in failed_names:
                failed_names.add(name)
                original_get_or_create_dir(filespace, parent, name)
                from django.db import IntegrityError

                raise IntegrityError("simulated race")
            return original_get_or_create_dir(filespace, parent, name)

        with patch(original_helper, side_effect=flaky_get_or_create_dir), patch(
            "api.agent.tasks.process_agent_events_task"
        ) as task_mock, patch("api.agent.peer_comm.transaction.on_commit", lambda cb: cb()):
            task_mock.delay = MagicMock()
            result = self.service.send_message("Race-safe send", attachments=attachments)

        self.assertEqual(result.status, "ok")

    def test_send_message_truncates_peer_inbox_directory_name_to_node_limit(self):
        self.agent_a.name = "A" * 255
        self.agent_a.save(update_fields=["name"])
        self._create_sender_attachment("/reports/long-name.txt", b"long")
        attachments = resolve_filespace_attachments(self.agent_a, ["/reports/long-name.txt"])

        with patch("api.agent.tasks.process_agent_events_task") as task_mock, patch(
            "api.agent.peer_comm.transaction.on_commit", lambda cb: cb()
        ):
            task_mock.delay = MagicMock()
            result = self.service.send_message("Long-name send", attachments=attachments)

        self.assertEqual(result.status, "ok")
        inbound = PersistentAgentMessage.objects.get(owner_agent=self.agent_b, is_outbound=False)
        copied_node = inbound.attachments.get().filespace_node
        self.assertIsNotNone(copied_node)
        self.assertLessEqual(len(copied_node.parent.name), 255)
        self.assertTrue(copied_node.parent.name.startswith("peer-"))

    def test_send_message_cleans_up_copied_blobs_when_later_step_fails(self):
        self._create_sender_attachment("/reports/failure.txt", b"cleanup")
        attachments = resolve_filespace_attachments(self.agent_a, ["/reports/failure.txt"])
        copied_nodes: list[AgentFsNode] = []
        original_copy = self.service._copy_attachments_to_peer_filespace

        def capture_copy(*args, **kwargs):
            nodes = original_copy(*args, **kwargs)
            copied_nodes.extend(nodes)
            return nodes

        with patch.object(self.service, "_copy_attachments_to_peer_filespace", side_effect=capture_copy), patch.object(
            self.service,
            "_create_inbound_attachment_rows",
            side_effect=RuntimeError("boom after copy"),
        ), patch("api.agent.tasks.process_agent_events_task") as task_mock, patch(
            "api.agent.peer_comm.transaction.on_commit", lambda cb: cb()
        ):
            task_mock.delay = MagicMock()
            with self.assertRaises(RuntimeError):
                self.service.send_message("This will fail", attachments=attachments)

        self.assertTrue(copied_nodes)
        for node in copied_nodes:
            self.assertFalse(node.content.storage.exists(node.content.name))
        self.assertEqual(
            AgentFsNode.objects.alive().filter(filespace=get_or_create_default_filespace(self.agent_b)).count(),
            0,
        )
        self.assertEqual(PersistentAgentMessage.objects.count(), 0)

    def test_execute_tool_rejects_missing_attachment_without_persisting_message(self):
        from api.agent.tools.peer_dm import execute_send_agent_message

        response = execute_send_agent_message(
            self.agent_a,
            {
                "peer_agent_id": str(self.agent_b.id),
                "message": "handoff",
                "attachments": ["/missing.txt"],
            },
        )

        self.assertEqual(response["status"], "error")
        self.assertIn("Attachment not found", response["message"])
        self.assertEqual(PersistentAgentMessage.objects.count(), 0)
        self.assertEqual(PersistentAgentMessageAttachment.objects.count(), 0)

    def test_execute_tool_rejects_oversized_attachment_without_persisting_message(self):
        from api.agent.tools.peer_dm import execute_send_agent_message

        self._create_sender_attachment("/exports/large.txt", b"toolarge")

        with patch("api.agent.files.attachment_helpers.get_max_file_size", return_value=3):
            response = execute_send_agent_message(
                self.agent_a,
                {
                    "peer_agent_id": str(self.agent_b.id),
                    "message": "handoff",
                    "attachments": ["/exports/large.txt"],
                },
            )

        self.assertEqual(response["status"], "error")
        self.assertIn("Attachment exceeds max size", response["message"])
        self.assertEqual(PersistentAgentMessage.objects.count(), 0)
        self.assertEqual(PersistentAgentMessageAttachment.objects.count(), 0)

    def test_debounce_prevents_rapid_repeat(self):
        self._create_sender_attachment("/reports/loop.txt", b"First loop")
        attachments = resolve_filespace_attachments(self.agent_a, ["/reports/loop.txt"])

        with patch('api.agent.tasks.process_agent_events_task') as task_mock, patch(
            'api.agent.peer_comm.transaction.on_commit', lambda cb: cb()
        ):
            task_mock.delay = MagicMock()
            self.service.send_message("First message", attachments=attachments)

        recipient_filespace = get_or_create_default_filespace(self.agent_b)
        initial_copy_count = AgentFsNode.objects.alive().filter(filespace=recipient_filespace).count()

        with self.assertRaises(PeerMessagingError) as err_ctx, patch(
            'api.agent.tasks.process_agent_events_task'
        ) as task_mock, patch('api.agent.peer_comm.transaction.on_commit', lambda cb: cb()):
            task_mock.delay = MagicMock()
            self.service.send_message("Too soon", attachments=attachments)

        self.assertEqual(err_ctx.exception.status, "debounced")
        # Only original outbound + inbound messages should exist
        self.assertEqual(
            PersistentAgentMessage.objects.filter(owner_agent=self.agent_a, is_outbound=True).count(),
            1,
        )
        self.assertEqual(
            AgentFsNode.objects.alive().filter(filespace=recipient_filespace).count(),
            initial_copy_count,
        )

    def test_duplicate_message_blocked(self):
        self._create_sender_attachment("/reports/duplicate.txt", b"duplicate")
        attachments = resolve_filespace_attachments(self.agent_a, ["/reports/duplicate.txt"])

        with patch('api.agent.tasks.process_agent_events_task') as task_mock, patch(
            'api.agent.peer_comm.transaction.on_commit', lambda cb: cb()
        ):
            task_mock.delay = MagicMock()
            self.service.send_message("Hello Beta", attachments=attachments)

        state = AgentCommPeerState.objects.get(link=self.link, channel=CommsChannel.OTHER)
        state.last_message_at = timezone.now() - timedelta(seconds=10)
        state.save(update_fields=['last_message_at'])
        recipient_filespace = get_or_create_default_filespace(self.agent_b)
        initial_copy_count = AgentFsNode.objects.alive().filter(filespace=recipient_filespace).count()

        with self.assertRaises(PeerMessagingError) as err_ctx, patch(
            'api.agent.tasks.process_agent_events_task'
        ) as task_mock, patch('api.agent.peer_comm.transaction.on_commit', lambda cb: cb()):
            task_mock.delay = MagicMock()
            self.service.send_message("Hello Beta", attachments=attachments)

        self.assertIsInstance(err_ctx.exception, PeerMessagingDuplicateError)
        self.assertTrue(err_ctx.exception.duplicate_response.get("duplicate_detected"))

        state.refresh_from_db()
        self.assertEqual(state.credits_remaining, 1)
        self.assertEqual(
            PersistentAgentMessage.objects.filter(owner_agent=self.agent_a, is_outbound=True).count(),
            1,
        )
        self.assertEqual(
            PersistentAgentMessage.objects.filter(owner_agent=self.agent_b, is_outbound=False).count(),
            1,
        )
        self.assertEqual(
            AgentFsNode.objects.alive().filter(filespace=recipient_filespace).count(),
            initial_copy_count,
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
        self._create_sender_attachment("/reports/throttle.txt", b"First")
        attachments = resolve_filespace_attachments(self.agent_a, ["/reports/throttle.txt"])

        with patch('api.agent.tasks.process_agent_events_task') as task_mock, patch(
            'api.agent.peer_comm.transaction.on_commit', lambda cb: cb()
        ):
            task_mock.delay = MagicMock()
            self.service.send_message("First", attachments=attachments)

        state = AgentCommPeerState.objects.get(link=self.link, channel=CommsChannel.OTHER)
        self.assertEqual(state.credits_remaining, 0)
        state.last_message_at = timezone.now() - timedelta(seconds=10)
        state.save(update_fields=['last_message_at'])
        recipient_filespace = get_or_create_default_filespace(self.agent_b)
        initial_copy_count = AgentFsNode.objects.alive().filter(filespace=recipient_filespace).count()

        with patch('api.agent.tasks.process_agent_events_task') as task_mock:
            task_mock.delay = MagicMock()
            task_mock.apply_async = MagicMock()
            with patch('api.agent.peer_comm.transaction.on_commit', lambda cb: cb()):
                with self.assertRaises(PeerMessagingError) as err_ctx:
                    self.service.send_message("Second", attachments=attachments)

        self.assertEqual(err_ctx.exception.status, "throttled")
        task_mock.apply_async.assert_called_once()
        self.assertEqual(
            PersistentAgentMessage.objects.filter(owner_agent=self.agent_a, is_outbound=True).count(),
            1,
        )
        self.assertEqual(
            AgentFsNode.objects.alive().filter(filespace=recipient_filespace).count(),
            initial_copy_count,
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

    def test_execute_tool_passes_resolved_attachments_to_service(self):
        from api.agent.tools.peer_dm import execute_send_agent_message

        source_node = self._create_sender_attachment("/handoffs/brief.txt", b"brief")

        with patch("api.agent.tools.peer_dm.PeerMessagingService") as service_cls:
            service_cls.return_value.send_message.return_value = PeerSendResult(
                status="ok",
                message="delivered",
                remaining_credits=1,
                window_reset_at=timezone.now(),
            )

            response = execute_send_agent_message(
                self.agent_a,
                {
                    "peer_agent_id": str(self.agent_b.id),
                    "message": "handoff",
                    "attachments": ["/handoffs/brief.txt"],
                },
            )

        self.assertEqual(response["status"], "ok")
        _, kwargs = service_cls.return_value.send_message.call_args
        self.assertEqual(kwargs["attachments"][0].node.id, source_node.id)

    def test_execute_tool_continue_flag_disables_auto_sleep(self):
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
                {
                    "peer_agent_id": str(self.agent_b.id),
                    "message": "still working",
                    "will_continue_work": True,
                },
            )

        self.assertEqual(response["status"], "ok")
        self.assertFalse(response.get("auto_sleep_ok"))


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
