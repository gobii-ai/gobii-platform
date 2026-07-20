import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from api.agent.core import event_processing as ep
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentConversation,
    PersistentAgentHumanInputRequest,
    PersistentAgentToolCall,
    UserQuota,
)


@tag("batch_event_processing_credits")
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
            "web_chat_visible": True,
            "requests": [
                {
                    "request_id": request_id,
                    "question": "What should I do next?",
                    "options": [{"title": "Proceed", "description": "Continue with this option."}],
                }
            ],
            "auto_sleep_ok": True,
        }
        mock_completion.return_value = (
            self._tool_completion(
                "request_human_input",
                (
                    '{"question": "What should I do next?", '
                    '"options": [{"title": "Proceed", "description": "Continue with this option."}], '
                    '"will_continue_work": false}'
                ),
            ),
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "model": "m", "provider": "p"},
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 2), patch.object(
            ep,
            "_latest_inbound_message_needs_reply",
            return_value=True,
        ):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertEqual(mock_completion.call_count, 1)
        mock_request_human_input.assert_called_once()
        self.assertEqual(
            list(PersistentAgentToolCall.objects.values_list("tool_name", flat=True)),
            ["request_human_input"],
        )

    def test_terminal_planning_cleanup_preserves_active_human_input_request(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=f"web://user/{self.user.id}/agent/{self.agent.id}",
        )
        request_obj = PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=conversation,
            question="Which market should I prioritize?",
            requested_via_channel=CommsChannel.WEB,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        finalized = ep._FinalizedToolBatch(
            executed_calls=1,
            followup_required=False,
            message_delivery_ok=True,
            last_explicit_continue=False,
            inferred_message_continue_this_iteration=False,
            executed_non_message_action=False,
            terminal_message_delivery_ok=True,
        )

        self.assertFalse(
            ep._should_skip_stale_planning_mode_after_terminal_delivery(
                self.agent,
                finalized,
                followup_required=False,
            )
        )

        self.agent.refresh_from_db()
        request_obj.refresh_from_db()
        self.assertEqual(self.agent.planning_state, PersistentAgent.PlanningState.PLANNING)
        self.assertEqual(request_obj.status, PersistentAgentHumanInputRequest.Status.PENDING)

        request_obj.expires_at = timezone.now() - timedelta(seconds=1)
        request_obj.save(update_fields=["expires_at", "updated_at"])
        self.assertTrue(
            ep._should_skip_stale_planning_mode_after_terminal_delivery(
                self.agent,
                finalized,
                followup_required=False,
            )
        )

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.execute_request_human_input")
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_request_human_input_will_continue_true_keeps_processing(
        self,
        mock_completion,
        mock_build_prompt,
        mock_request_human_input,
        mock_send_chat_message,
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
            "web_chat_visible": True,
            "requests": [
                {
                    "request_id": request_id,
                    "question": "What should I do next?",
                    "options": [{"title": "Proceed", "description": "Continue with this option."}],
                }
            ],
            "auto_sleep_ok": True,
        }
        first_response = self._tool_completion(
            "request_human_input",
            (
                '{"question": "What should I do next?", '
                '"options": [{"title": "Proceed", "description": "Continue with this option."}], '
                '"will_continue_work": true}'
            ),
        )
        second_response = self._tool_completion(
            "send_chat_message",
            '{"body": "I will keep planning.", "will_continue_work": false}',
        )
        mock_completion.side_effect = [
            (first_response, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "model": "m", "provider": "p"}),
            (second_response, {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12, "model": "m", "provider": "p"}),
        ]

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 2):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertEqual(mock_completion.call_count, 2)
        mock_request_human_input.assert_called_once()
        mock_send_chat_message.assert_called_once()
        self.assertEqual(
            list(PersistentAgentToolCall.objects.order_by("step__created_at").values_list("tool_name", flat=True)),
            ["request_human_input", "send_chat_message"],
        )

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_email", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.execute_request_human_input")
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_request_human_input_external_request_can_stop_without_followup_send(
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
            "web_chat_visible": True,
            "requests": [
                {
                    "request_id": request_id,
                    "question": "What should I do next?",
                    "options": [{"title": "Proceed", "description": "Continue with this option."}],
                }
            ],
            "next_message_suggestion": {
                "channel": "email",
                "address": "person@example.com",
                "send_tool": "send_email",
                "instruction": "Include the question in your next email.",
                "questions": [{"number": 1, "question": "What should I do next?"}],
            },
            "auto_sleep_ok": True,
        }

        first_response = self._tool_completion(
            "request_human_input",
            (
                '{"question": "What should I do next?", '
                '"options": [{"title": "Proceed", "description": "Continue with this option."}], '
                '"will_continue_work": false}'
            ),
        )
        mock_completion.return_value = (
            first_response,
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "model": "m", "provider": "p"},
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 2):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertEqual(mock_completion.call_count, 1)
        mock_request_human_input.assert_called_once_with(
            self.agent,
            {
                "question": "What should I do next?",
                "options": [{"title": "Proceed", "description": "Continue with this option."}],
                "will_continue_work": False,
            },
        )
        mock_send_email.assert_not_called()
        self.assertEqual(
            list(PersistentAgentToolCall.objects.order_by("step__created_at").values_list("tool_name", flat=True)),
            ["request_human_input"],
        )

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_email", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.execute_request_human_input")
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_request_human_input_external_will_continue_true_allows_normal_send(
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
            "web_chat_visible": True,
            "requests": [
                {
                    "request_id": request_id,
                    "question": "What should I do next?",
                    "options": [{"title": "Proceed", "description": "Continue with this option."}],
                }
            ],
            "next_message_suggestion": {
                "channel": "email",
                "address": "person@example.com",
                "send_tool": "send_email",
                "instruction": "Include the question in your next email.",
                "questions": [{"number": 1, "question": "What should I do next?"}],
            },
            "auto_sleep_ok": True,
        }
        first_response = self._tool_completion(
            "request_human_input",
            (
                '{"question": "What should I do next?", '
                '"options": [{"title": "Proceed", "description": "Continue with this option."}], '
                '"will_continue_work": true}'
            ),
        )
        second_response = self._tool_completion(
            "send_email",
            '{"to_address": "person@example.com", "subject": "Quick question", "mobile_first_html": "<p>What should I do next?</p>", "will_continue_work": false}',
        )
        mock_completion.side_effect = [
            (first_response, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "model": "m", "provider": "p"}),
            (second_response, {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12, "model": "m", "provider": "p"}),
        ]

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 2):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertEqual(mock_completion.call_count, 2)
        mock_request_human_input.assert_called_once_with(
            self.agent,
            {
                "question": "What should I do next?",
                "options": [{"title": "Proceed", "description": "Continue with this option."}],
                "will_continue_work": True,
            },
        )
        mock_send_email.assert_called_once()
        self.assertEqual(
            list(PersistentAgentToolCall.objects.order_by("step__created_at").values_list("tool_name", flat=True)),
            ["request_human_input", "send_email"],
        )
