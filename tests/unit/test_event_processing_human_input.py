import uuid
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from api.agent.core import event_processing as ep
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentToolCall,
    UserQuota,
)


@tag("batch_event_processing")
@override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
class EventProcessingHumanInputTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="event-processing-human-input@example.com",
            email="event-processing-human-input@example.com",
            password="password123",
        )
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100
        quota.save(update_fields=["agent_limit"])

    def setUp(self):
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="browser-agent-for-human-input-event-processing",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Human Input Event Processing Agent",
            charter="Collect human input when needed.",
            browser_use_agent=browser_agent,
        )

    def _tool_completion(self, tool_name: str, arguments: str) -> MagicMock:
        tool_call = MagicMock()
        tool_call.function = MagicMock()
        tool_call.function.name = tool_name
        tool_call.function.arguments = arguments

        message = MagicMock()
        message.tool_calls = [tool_call]
        message.content = None

        choice = MagicMock()
        choice.message = message

        response = MagicMock()
        response.choices = [choice]
        response.model_extra = {
            "usage": MagicMock(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                prompt_tokens_details=MagicMock(cached_tokens=0),
            )
        }
        return response

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_request_human_input")
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_request_human_input_web_request_can_stop_immediately(
        self,
        mock_completion,
        mock_build_prompt,
        mock_request_human_input,
        _mock_credit,
    ):
        mock_build_prompt.return_value = (
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
            1000,
            None,
        )
        request_id = str(uuid.uuid4())
        mock_request_human_input.return_value = {
            "status": "ok",
            "request_id": request_id,
            "request_ids": [request_id],
            "requests_count": 1,
            "target_channel": "web",
            "target_address": "web://user/1/agent/1",
            "relay_mode": "panel_only",
            "relay_payload": {"kind": "panel"},
            "auto_sleep_ok": True,
        }
        mock_completion.return_value = (
            self._tool_completion("request_human_input", '{"question": "What should I do next?"}'),
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "model": "m", "provider": "p"},
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 2):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertEqual(mock_completion.call_count, 1)
        mock_request_human_input.assert_called_once()
        self.assertEqual(
            list(PersistentAgentToolCall.objects.values_list("tool_name", flat=True)),
            ["request_human_input"],
        )

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_email", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.execute_request_human_input")
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_request_human_input_external_request_requires_followup_send(
        self,
        mock_completion,
        mock_build_prompt,
        mock_request_human_input,
        mock_send_email,
        _mock_credit,
    ):
        mock_build_prompt.return_value = (
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
            1000,
            None,
        )
        request_id = str(uuid.uuid4())
        mock_request_human_input.return_value = {
            "status": "ok",
            "request_id": request_id,
            "request_ids": [request_id],
            "requests_count": 1,
            "target_channel": "email",
            "target_address": "person@example.com",
            "relay_mode": "explicit_send_required",
            "relay_payload": {
                "kind": "send_email",
                "tool_name": "send_email",
                "to_address": "person@example.com",
                "subject": "Quick question: What should I do next?",
                "mobile_first_html": "<p>What should I do next?</p>",
                "body_text": "What should I do next?",
            },
        }

        first_response = self._tool_completion("request_human_input", '{"question": "What should I do next?"}')
        second_response = self._tool_completion(
            "send_email",
            '{"to_address": "person@example.com", "subject": "Quick question: What should I do next?", "mobile_first_html": "<p>What should I do next?</p>", "will_continue_work": false}',
        )
        mock_completion.side_effect = [
            (first_response, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "model": "m", "provider": "p"}),
            (second_response, {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12, "model": "m", "provider": "p"}),
        ]

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 2):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertEqual(mock_completion.call_count, 2)
        mock_request_human_input.assert_called_once()
        mock_send_email.assert_called_once()
        self.assertEqual(
            list(PersistentAgentToolCall.objects.order_by("step__created_at").values_list("tool_name", flat=True)),
            ["request_human_input", "send_email"],
        )
