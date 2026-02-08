from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentSystemStep
from api.services.agent_settings_resume import (
    queue_owner_task_pack_resume,
    queue_settings_change_resume,
)


@tag("batch_console_agents")
class AgentSettingsResumeTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="resume-owner",
            email="resume-owner@example.com",
            password="pw",
        )

    def _create_agent(self, *, name: str) -> PersistentAgent:
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name=f"{name} Browser")
        return PersistentAgent.objects.create(
            user=self.user,
            name=name,
            charter=f"{name} charter",
            browser_use_agent=browser_agent,
        )

    @patch("api.services.agent_settings_resume.process_agent_events_task.delay")
    def test_queue_settings_change_resume_task_pack_changed(self, mock_delay):
        agent = self._create_agent(name="Task Pack Resume Agent")

        with self.captureOnCommitCallbacks(execute=True):
            queued = queue_settings_change_resume(
                agent,
                task_pack_changed=True,
                source="unit_test_task_pack",
            )

        self.assertTrue(queued)
        mock_delay.assert_called_once_with(str(agent.id))

        latest_system_step = (
            PersistentAgentSystemStep.objects
            .filter(step__agent=agent, code=PersistentAgentSystemStep.Code.SYSTEM_DIRECTIVE)
            .select_related("step")
            .order_by("-step__created_at")
            .first()
        )
        self.assertIsNotNone(latest_system_step)
        self.assertIn("Task pack credits were updated.", latest_system_step.step.description)
        self.assertIn('"task_pack":{"updated":true}', latest_system_step.notes)

    @patch("api.services.agent_settings_resume.process_agent_events_task.delay")
    def test_queue_owner_task_pack_resume_targets_active_owner_agents(self, mock_delay):
        paused_agent = self._create_agent(name="Paused Agent")
        self._create_agent(name="Second Agent")

        other_user = get_user_model().objects.create_user(
            username="resume-other",
            email="resume-other@example.com",
            password="pw",
        )
        other_browser_agent = BrowserUseAgent.objects.create(user=other_user, name="Other Browser")
        other_agent = PersistentAgent.objects.create(
            user=other_user,
            name="Other Agent",
            charter="Other charter",
            browser_use_agent=other_browser_agent,
        )

        with self.captureOnCommitCallbacks(execute=True):
            resumed = queue_owner_task_pack_resume(
                owner_id=self.user.id,
                owner_type="user",
                source="unit_test_owner_resume",
            )

        self.assertEqual(resumed, 2)
        self.assertIn((str(paused_agent.id),), [call.args for call in mock_delay.call_args_list])
        self.assertEqual(mock_delay.call_count, 2)
        self.assertNotIn((str(other_agent.id),), [call.args for call in mock_delay.call_args_list])
