from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.evals.execution import ScenarioExecutionTools
from api.models import BrowserUseAgent, PersistentAgent


@tag("batch_eval_fingerprint")
class EvalExecutionProcessingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="eval-execution@example.com",
            email="eval-execution@example.com",
            password="testpass",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Eval Execution Browser Agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            browser_use_agent=self.browser_agent,
            name="Eval Execution Agent",
            charter="Test eval processing dispatch.",
        )
        self.tools = ScenarioExecutionTools()

    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.tasks.process_agent_events_task.apply")
    def test_eval_injected_message_processes_agent_inline(self, mock_apply, mock_delay):
        self.tools.inject_message(
            self.agent.id,
            "Fetch this exact URL.",
            trigger_processing=True,
            eval_run_id="00000000-0000-0000-0000-000000000123",
            mock_config={"http_request": {"status": "ok"}},
        )

        mock_apply.assert_called_once_with(
            args=(str(self.agent.id),),
            kwargs={
                "eval_run_id": "00000000-0000-0000-0000-000000000123",
                "mock_config": {"http_request": {"status": "ok"}},
            },
            throw=True,
        )
        mock_delay.assert_not_called()

    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.tasks.process_agent_events_task.apply")
    def test_non_eval_injected_message_keeps_async_processing(self, mock_apply, mock_delay):
        self.tools.inject_message(
            self.agent.id,
            "Normal user message.",
            trigger_processing=True,
        )

        mock_apply.assert_not_called()
        mock_delay.assert_called_once_with(
            str(self.agent.id),
            eval_run_id=None,
            mock_config=None,
        )

    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.tasks.process_agent_events_task.apply")
    def test_eval_trigger_processing_processes_agent_inline(self, mock_apply, mock_delay):
        self.tools.trigger_processing(
            self.agent.id,
            eval_run_id="00000000-0000-0000-0000-000000000456",
            mock_config={"create_csv": {"status": "ok"}},
        )

        mock_apply.assert_called_once_with(
            args=(str(self.agent.id),),
            kwargs={
                "eval_run_id": "00000000-0000-0000-0000-000000000456",
                "mock_config": {"create_csv": {"status": "ok"}},
            },
            throw=True,
        )
        mock_delay.assert_not_called()
