from unittest.mock import patch

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
