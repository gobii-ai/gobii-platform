from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, tag

from api.agent.tasks.process_events import process_agent_events_task


class ProcessAgentEventsTaskTests(SimpleTestCase):
    @tag("batch_agent_chat")
    def test_apply_async_marks_queue_and_broadcasts(self):
        agent_id = "agent-apply-async-test"

        with patch("api.agent.tasks.process_events.set_processing_queued_flag") as mock_set_flag, \
             patch("api.agent.tasks.process_events._broadcast_processing_state") as mock_broadcast, \
             patch("celery.app.task.Task.apply_async", return_value="ok") as mock_super:
            result = process_agent_events_task.apply_async(args=(agent_id,))

        mock_set_flag.assert_called_once_with(agent_id)
        mock_broadcast.assert_called_once_with(agent_id)
        mock_super.assert_called_once()
        self.assertEqual(result, "ok")

    @tag("batch_agent_chat")
    def test_redelivered_clears_stale_lock(self):
        agent_id = "11111111-1111-1111-1111-111111111111"
        fake_redis = SimpleNamespace(delete=Mock(return_value=1))
        current_pid = 2222

        with patch(
            "api.agent.tasks.process_events.get_processing_heartbeat",
            return_value={"last_seen": 195, "worker_pid": 1111},
        ), \
             patch("api.agent.tasks.process_events.time.time", return_value=400), \
             patch("api.agent.tasks.process_events.os.getpid", return_value=current_pid), \
             patch("api.agent.tasks.process_events.get_redis_client", return_value=fake_redis), \
             patch("api.agent.tasks.process_events._lock_storage_keys", return_value=(
                 f"redlock:agent-event-processing:{agent_id}",
                 f"agent-event-processing:{agent_id}",
             )), \
             patch("api.models.PersistentAgent") as mock_agent_model, \
             patch("api.agent.tasks.process_events.process_agent_events") as mock_process:
            mock_agent_model.objects.select_related.return_value.filter.return_value.first.return_value = None
            process_agent_events_task.push_request(
                redelivered=True,
                delivery_info={},
                id="task-current",
            )
            try:
                process_agent_events_task.run(agent_id)
            finally:
                process_agent_events_task.pop_request()

        mock_process.assert_called_once()
        fake_redis.delete.assert_any_call(f"redlock:agent-event-processing:{agent_id}")
        fake_redis.delete.assert_any_call(f"agent-event-processing:{agent_id}")

    @tag("batch_agent_chat")
    def test_redelivered_skips_clear_with_fresh_heartbeat_pid_mismatch(self):
        agent_id = "22222222-2222-2222-2222-222222222222"
        fake_redis = SimpleNamespace(delete=Mock(return_value=1))
        current_pid = 3333

        with patch(
            "api.agent.tasks.process_events.get_processing_heartbeat",
            return_value={"last_seen": 95, "worker_pid": 1111},
        ), \
             patch("api.agent.tasks.process_events.time.time", return_value=100), \
             patch("api.agent.tasks.process_events.os.getpid", return_value=current_pid), \
             patch(
                 "api.agent.tasks.process_events.settings.AGENT_EVENT_PROCESSING_REDELIVERY_PID_GRACE_SECONDS",
                 10,
             ), \
             patch("api.agent.tasks.process_events.get_redis_client", return_value=fake_redis), \
             patch("api.models.PersistentAgent") as mock_agent_model, \
             patch("api.agent.tasks.process_events.process_agent_events") as mock_process:
            mock_agent_model.objects.select_related.return_value.filter.return_value.first.return_value = None
            process_agent_events_task.push_request(
                redelivered=True,
                delivery_info={},
                id="task-current",
            )
            try:
                process_agent_events_task.run(agent_id)
            finally:
                process_agent_events_task.pop_request()

        mock_process.assert_called_once()
        fake_redis.delete.assert_not_called()
