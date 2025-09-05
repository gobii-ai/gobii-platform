from __future__ import annotations

from unittest.mock import patch
from django.db.utils import OperationalError

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.models import BrowserUseAgent, BrowserUseAgentTask, BrowserUseAgentTaskStep


@tag("batch_browser_task_db")
class BrowserTaskDbConnectionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="dbconn@example.com", email="dbconn@example.com", password="password123"
        )
        self.agent = BrowserUseAgent.objects.create(user=self.user, name="DBConn Agent")

    def test_close_old_connections_called_around_final_writes_success_path(self):
        # Create a task without output_schema to avoid dynamic model creation
        task = BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="simple"
        )

        # Patch internals in the task module to simulate success quickly and avoid external deps
        with patch("api.tasks.browser_agent_tasks.LIBS_AVAILABLE", True), \
             patch("api.tasks.browser_agent_tasks.Controller") as MockController, \
             patch("api.tasks.browser_agent_tasks.select_proxy_for_task", return_value=None), \
             patch("api.tasks.browser_agent_tasks._execute_agent_with_failover", return_value=({"ok": True}, None)), \
             patch("api.tasks.browser_agent_tasks.close_old_connections") as mock_close:

            # Import inside the context to ensure patches are in effect
            from api.tasks.browser_agent_tasks import _process_browser_use_task_core

            _process_browser_use_task_core(str(task.id))

            # Ensure we refreshed DB connections before step creation and before final save
            self.assertGreaterEqual(mock_close.call_count, 2)

        # Verify task completed and result step was created
        task.refresh_from_db()
        self.assertEqual(task.status, BrowserUseAgentTask.StatusChoices.COMPLETED)
        self.assertTrue(
            BrowserUseAgentTaskStep.objects.filter(task=task, is_result=True).exists()
        )

    def test_step_creation_retry_is_idempotent_on_operational_error(self):
        task = BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="simple"
        )

        with patch("api.tasks.browser_agent_tasks.LIBS_AVAILABLE", True), \
             patch("api.tasks.browser_agent_tasks.Controller") as MockController, \
             patch("api.tasks.browser_agent_tasks.select_proxy_for_task", return_value=None), \
             patch("api.tasks.browser_agent_tasks._execute_agent_with_failover", return_value=({"ok": True}, None)), \
             patch("api.tasks.browser_agent_tasks.close_old_connections") as mock_close, \
             patch("api.tasks.browser_agent_tasks.BrowserUseAgentTaskStep.objects.create") as mock_create:

            # First call raises OperationalError, second path will use update_or_create
            mock_create.side_effect = OperationalError("simulated closed connection")

            from api.tasks.browser_agent_tasks import _process_browser_use_task_core

            _process_browser_use_task_core(str(task.id))

            # We called close_old_connections at least once for retry
            self.assertGreaterEqual(mock_close.call_count, 2)

        # Verify only a single result step exists after retry path
        steps = BrowserUseAgentTaskStep.objects.filter(task=task, step_number=1)
        self.assertEqual(steps.count(), 1)
        self.assertTrue(steps.first().is_result)
