"""Tests for scheduling agent follow-up after background web tasks."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.models import PersistentAgent, BrowserUseAgent, BrowserUseAgentTask
from api.tasks.browser_agent_tasks import _schedule_agent_follow_up


@tag("batch_web_task_followup")
class BrowserTaskFollowUpTests(TestCase):
    """Ensure agent follow-up scheduling handles closed cycles."""

    def setUp(self) -> None:
        User = get_user_model()
        self.user = User.objects.create_user(
            username="test-user",
            email="test@example.com",
        )

        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            self.browser_agent = BrowserUseAgent.objects.create(
                user=self.user,
                name="Test Browser Agent",
            )

        self.persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Test Persistent Agent",
            charter="Test",
            browser_use_agent=self.browser_agent,
        )

        self.task = BrowserUseAgentTask.objects.create(
            agent=self.browser_agent,
            user=self.user,
            prompt="do something",
        )

    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    @patch("api.tasks.browser_agent_tasks.AgentBudgetManager.get_active_budget_id")
    @patch("api.tasks.browser_agent_tasks.AgentBudgetManager.get_cycle_status")
    def test_follow_up_uses_existing_cycle_when_active(
        self,
        mock_cycle_status,
        mock_active_id,
        mock_delay,
    ) -> None:
        mock_cycle_status.return_value = "active"
        mock_active_id.return_value = "budget-123"

        _schedule_agent_follow_up(
            self.task,
            budget_id="budget-123",
            branch_id="branch-1",
            depth=2,
        )

        mock_delay.assert_called_once_with(
            str(self.persistent_agent.id),
            budget_id="budget-123",
            branch_id="branch-1",
            depth=1,
        )

    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    @patch("api.tasks.browser_agent_tasks.AgentBudgetManager.get_active_budget_id")
    @patch("api.tasks.browser_agent_tasks.AgentBudgetManager.get_cycle_status")
    def test_follow_up_schedules_fresh_cycle_when_inactive(
        self,
        mock_cycle_status,
        mock_active_id,
        mock_delay,
    ) -> None:
        mock_cycle_status.return_value = "closed"
        mock_active_id.return_value = "budget-123"

        _schedule_agent_follow_up(
            self.task,
            budget_id="budget-123",
            branch_id="branch-1",
            depth=2,
        )

        mock_delay.assert_called_once_with(str(self.persistent_agent.id))

