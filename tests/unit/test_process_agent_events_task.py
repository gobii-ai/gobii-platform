from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, tag

from api.agent.tasks.process_events import (
    AGENT_DEFAULT_PROCESSING_QUEUE,
    AGENT_INTERACTIVE_PROCESSING_QUEUE,
    PROCESS_AGENT_EVENTS_QUEUED_AT_KWARG,
    PROCESS_AGENT_EVENTS_QUEUED_QUEUE_KWARG,
    enqueue_interactive_process_agent_events,
    process_agent_events_task,
    queue_agent_process_events_batch_task,
    _record_process_agent_events_queue_latency,
)


class ProcessAgentEventsTaskTests(SimpleTestCase):
    @tag("batch_console_agents")
    def test_queue_agent_process_events_batch_fans_out_valid_agent_ids(self):
        valid_agent_id = "11111111-1111-1111-1111-111111111111"

        with patch("api.agent.tasks.process_events.process_agent_events_task.delay") as mock_delay:
            result = queue_agent_process_events_batch_task.run([valid_agent_id, "not-a-uuid"])

        mock_delay.assert_called_once_with(valid_agent_id)
        self.assertEqual(result, {"queued_count": 1, "invalid_count": 1})

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
    def test_apply_async_stamps_enqueue_metadata(self):
        agent_id = "agent-apply-async-test"

        with patch("api.agent.tasks.process_events.time.time", return_value=123.45), \
             patch("api.agent.tasks.process_events.set_processing_queued_flag"), \
             patch("api.agent.tasks.process_events._broadcast_processing_state"), \
             patch("celery.app.task.Task.apply_async", return_value="ok") as mock_super:
            result = process_agent_events_task.apply_async(
                args=(agent_id,),
                kwargs={"inbound_generation": 4},
                queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
            )

        mock_super.assert_called_once_with(
            args=(agent_id,),
            kwargs={
                "inbound_generation": 4,
                PROCESS_AGENT_EVENTS_QUEUED_AT_KWARG: 123.45,
                PROCESS_AGENT_EVENTS_QUEUED_QUEUE_KWARG: AGENT_INTERACTIVE_PROCESSING_QUEUE,
            },
            queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
        )
        self.assertEqual(result, "ok")

    @tag("batch_agent_chat")
    def test_apply_async_defaults_enqueue_queue_metadata(self):
        agent_id = "agent-apply-async-test"

        with patch("api.agent.tasks.process_events.time.time", return_value=234.56), \
             patch("api.agent.tasks.process_events.set_processing_queued_flag"), \
             patch("api.agent.tasks.process_events._broadcast_processing_state"), \
             patch("celery.app.task.Task.apply_async", return_value="ok") as mock_super:
            process_agent_events_task.apply_async(args=(agent_id,))

        call_kwargs = mock_super.call_args.kwargs["kwargs"]
        self.assertEqual(call_kwargs[PROCESS_AGENT_EVENTS_QUEUED_AT_KWARG], 234.56)
        self.assertEqual(call_kwargs[PROCESS_AGENT_EVENTS_QUEUED_QUEUE_KWARG], AGENT_DEFAULT_PROCESSING_QUEUE)

    @tag("batch_agent_chat")
    def test_record_process_agent_events_queue_latency(self):
        histogram = Mock()

        with patch("api.agent.tasks.process_events.time.time", return_value=130.0), \
             patch(
                 "api.agent.tasks.process_events._process_agent_events_queue_latency_histogram",
                 return_value=histogram,
             ):
            latency = _record_process_agent_events_queue_latency(
                100.0,
                queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
            )

        self.assertEqual(latency, 30.0)
        histogram.record.assert_called_once_with(
            30.0,
            attributes={
                "celery.queue": AGENT_INTERACTIVE_PROCESSING_QUEUE,
                "celery.task_name": "api.agent.tasks.process_agent_events",
            },
        )

    @tag("batch_agent_chat")
    def test_enqueue_interactive_process_agent_events_uses_interactive_queue(self):
        agent_id = "33333333-3333-3333-3333-333333333333"

        with patch("api.agent.tasks.process_events.process_agent_events_task.apply_async") as mock_apply_async:
            enqueue_interactive_process_agent_events(agent_id, inbound_generation=7)

        mock_apply_async.assert_called_once_with(
            args=[agent_id],
            kwargs={"inbound_generation": 7},
            queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
        )

    @tag("batch_agent_chat")
    def test_enqueue_interactive_process_agent_events_omits_empty_generation(self):
        agent_id = "44444444-4444-4444-4444-444444444444"

        with patch("api.agent.tasks.process_events.process_agent_events_task.apply_async") as mock_apply_async:
            enqueue_interactive_process_agent_events(agent_id)

        mock_apply_async.assert_called_once_with(
            args=[agent_id],
            kwargs={},
            queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
        )

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
