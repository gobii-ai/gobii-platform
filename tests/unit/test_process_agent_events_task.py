from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, tag

from api.agent.core.event_processing import _resolve_low_latency_preference
from api.agent.tasks.process_events import (
    AGENT_DEFAULT_PROCESSING_QUEUE,
    AGENT_INTERACTIVE_PROCESSING_QUEUE,
    PROCESS_AGENT_EVENTS_QUEUED_AT_KWARG,
    PROCESS_AGENT_EVENTS_QUEUED_QUEUE_KWARG,
    enqueue_interactive_process_agent_events,
    process_discord_inbound_debounce_task,
    process_agent_events_task,
    queue_agent_process_events_batch_task,
    _record_process_agent_events_queue_latency,
)
from config.redis_client import _FakeRedis


class ProcessAgentEventsTaskTests(SimpleTestCase):
    @tag("batch_agent_chat")
    def test_fake_redis_transaction_pipeline_returns_command_results(self):
        redis_client = _FakeRedis()

        results = (
            redis_client.pipeline(transaction=True)
            .set("discord-debounce", "scheduled")
            .set("discord-debounce", "duplicate", nx=True)
            .execute()
        )

        self.assertEqual(results, [True, False])

    @tag("batch_agent_chat")
    def test_discord_debounce_survives_worker_loss(self):
        self.assertTrue(process_discord_inbound_debounce_task.acks_late)
        self.assertTrue(process_discord_inbound_debounce_task.reject_on_worker_lost)

    @tag("batch_agent_chat")
    def test_explicit_low_latency_preference_overrides_web_session_detection(self):
        agent = Mock()

        with patch("api.agent.core.event_processing.has_deliverable_web_session") as mock_has_session:
            self.assertTrue(_resolve_low_latency_preference(agent, True))
            self.assertFalse(_resolve_low_latency_preference(agent, False))

        mock_has_session.assert_not_called()

    @tag("batch_agent_chat")
    def test_unspecified_low_latency_preference_uses_web_session_detection(self):
        agent = Mock()

        with patch(
            "api.agent.core.event_processing.has_deliverable_web_session",
            return_value=True,
        ) as mock_has_session:
            self.assertTrue(_resolve_low_latency_preference(agent, None))

        mock_has_session.assert_called_once_with(agent)

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
    def test_enqueue_interactive_process_agent_events_includes_low_latency_preference(self):
        agent_id = "44444444-4444-4444-4444-444444444444"

        with patch("api.agent.tasks.process_events.process_agent_events_task.apply_async") as mock_apply_async:
            enqueue_interactive_process_agent_events(agent_id, prefer_low_latency=True)

        mock_apply_async.assert_called_once_with(
            args=[agent_id],
            kwargs={"prefer_low_latency": True},
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
    def test_interactive_delivery_applies_interactive_loop_controls(self):
        agent_id = "55555555-5555-5555-5555-555555555555"

        with patch(
            "api.agent.tasks.process_events.is_human_inbound_generation_consumed",
            return_value=False,
        ), \
             patch("api.agent.tasks.process_events.is_agent_pending", return_value=False), \
             patch("api.agent.tasks.process_events.clear_processing_queued_flag"), \
             patch("api.agent.tasks.process_events._broadcast_processing_state"), \
             patch("api.agent.tasks.process_events.ReferralService.check_and_grant_deferred_referral_credits"), \
             patch("api.models.PersistentAgent.objects.select_related") as mock_select_related, \
             patch("api.agent.tasks.process_events.process_agent_events") as mock_process:
            mock_select_related.return_value.filter.return_value.first.return_value = None
            process_agent_events_task.push_request(
                delivery_info={"routing_key": AGENT_INTERACTIVE_PROCESSING_QUEUE},
                id="interactive-task",
            )
            try:
                process_agent_events_task.run(agent_id, prefer_low_latency=True)
            finally:
                process_agent_events_task.pop_request()

        call_kwargs = mock_process.call_args.kwargs
        self.assertEqual(call_kwargs["max_loop_iterations"], 10)
        self.assertEqual(call_kwargs["max_iterations_followup_delay_seconds"], 0)
        self.assertEqual(call_kwargs["max_iterations_followup_queue"], AGENT_DEFAULT_PROCESSING_QUEUE)
        self.assertIs(call_kwargs["prefer_low_latency"], True)

    @tag("batch_agent_chat")
    def test_default_delivery_leaves_loop_controls_unset(self):
        agent_id = "66666666-6666-6666-6666-666666666666"

        with patch(
            "api.agent.tasks.process_events.is_human_inbound_generation_consumed",
            return_value=False,
        ), \
             patch("api.agent.tasks.process_events.is_agent_pending", return_value=False), \
             patch("api.agent.tasks.process_events.clear_processing_queued_flag"), \
             patch("api.agent.tasks.process_events._broadcast_processing_state"), \
             patch("api.agent.tasks.process_events.ReferralService.check_and_grant_deferred_referral_credits"), \
             patch("api.models.PersistentAgent.objects.select_related") as mock_select_related, \
             patch("api.agent.tasks.process_events.process_agent_events") as mock_process:
            mock_select_related.return_value.filter.return_value.first.return_value = None
            process_agent_events_task.push_request(
                delivery_info={"routing_key": AGENT_DEFAULT_PROCESSING_QUEUE},
                id="default-task",
            )
            try:
                process_agent_events_task.run(agent_id)
            finally:
                process_agent_events_task.pop_request()

        call_kwargs = mock_process.call_args.kwargs
        self.assertIsNone(call_kwargs["max_loop_iterations"])
        self.assertIsNone(call_kwargs["max_iterations_followup_delay_seconds"])
        self.assertIsNone(call_kwargs["max_iterations_followup_queue"])

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
