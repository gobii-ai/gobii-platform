import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, tag
from django.urls import reverse

from api.agent.core.processing_flags import clear_processing_queued_flag, set_processing_queued_flag
from console.agent_chat.suggestions import _context_from_timeline_events, _generate_dynamic_suggestions
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
    build_web_agent_address,
    build_web_user_address,
)


@tag("batch_agent_chat")
class AgentChatSuggestionsAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="suggestions-owner",
            email="suggestions-owner@example.com",
            password="password123",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Suggestions Browser Agent")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Suggestions Agent",
            charter="Help with suggestions",
            browser_use_agent=cls.browser_agent,
        )

        cls.user_address = build_web_user_address(cls.user.id, cls.agent.id)
        cls.agent_address = build_web_agent_address(cls.agent.id)
        cls.user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.WEB,
            address=cls.user_address,
            is_primary=False,
        )
        cls.agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=cls.agent,
            channel=CommsChannel.WEB,
            address=cls.agent_address,
            is_primary=True,
        )
        cls.conversation = PersistentAgentConversation.objects.create(
            owner_agent=cls.agent,
            channel=CommsChannel.WEB,
            address=cls.user_address,
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)
        cache.clear()
        clear_processing_queued_flag(self.agent.id)

    def tearDown(self):
        clear_processing_queued_flag(self.agent.id)
        super().tearDown()

    def _create_message(self, *, body: str, is_outbound: bool) -> PersistentAgentMessage:
        endpoint = self.agent_endpoint if is_outbound else self.user_endpoint
        return PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            conversation=self.conversation,
            from_endpoint=endpoint,
            is_outbound=is_outbound,
            body=body,
            raw_payload={},
        )

    def _suggestions_url(self) -> str:
        return reverse("console_agent_suggestions", kwargs={"agent_id": str(self.agent.id)})

    def test_returns_empty_while_processing_active(self):
        set_processing_queued_flag(self.agent.id)

        response = self.client.get(self._suggestions_url())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("source"), "none")
        self.assertEqual(payload.get("suggestions"), [])

    def test_returns_static_prompts_for_initial_phase(self):
        self._create_message(body="What can you help with?", is_outbound=False)

        response = self.client.get(self._suggestions_url())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("source"), "static")
        self.assertEqual(len(payload.get("suggestions", [])), 3)

    @patch("console.agent_chat.suggestions._generate_dynamic_suggestions")
    def test_returns_dynamic_prompts_after_completed_loop(self, mock_generate):
        self._create_message(body="Can you review this plan?", is_outbound=False)
        self._create_message(body="Absolutely. Here is a draft approach.", is_outbound=True)
        mock_generate.return_value = [
            {"id": "dynamic-1", "text": "Refine the rollout plan with risks and owners.", "category": "planning"},
            {"id": "dynamic-2", "text": "Create an exec summary with top decisions.", "category": "deliverables"},
            {"id": "dynamic-3", "text": "Identify which integrations to prioritize first.", "category": "integrations"},
        ]

        response = self.client.get(self._suggestions_url())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("source"), "dynamic")
        self.assertEqual(len(payload.get("suggestions", [])), 3)
        mock_generate.assert_called_once()

    @patch("console.agent_chat.suggestions._generate_dynamic_suggestions")
    def test_falls_back_to_static_when_dynamic_generation_fails(self, mock_generate):
        self._create_message(body="Can you summarize this?", is_outbound=False)
        self._create_message(body="Here is your summary.", is_outbound=True)
        mock_generate.return_value = []

        response = self.client.get(self._suggestions_url())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("source"), "static")
        self.assertEqual(len(payload.get("suggestions", [])), 3)
        mock_generate.assert_called_once()

    @patch("console.agent_chat.suggestions._generate_dynamic_suggestions")
    def test_uses_cache_for_same_cursor(self, mock_generate):
        self._create_message(body="Please analyze this report.", is_outbound=False)
        self._create_message(body="I analyzed it. Here are findings.", is_outbound=True)
        mock_generate.return_value = [
            {"id": "dynamic-a", "text": "Draft a stakeholder-ready recap email.", "category": "deliverables"},
            {"id": "dynamic-b", "text": "List assumptions that need validation.", "category": "planning"},
            {"id": "dynamic-c", "text": "Suggest follow-up tasks for this week.", "category": "capabilities"},
        ]

        first = self.client.get(self._suggestions_url())
        second = self.client.get(self._suggestions_url())
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), second.json())
        mock_generate.assert_called_once()

    @patch("console.agent_chat.suggestions.log_agent_completion")
    @patch("console.agent_chat.suggestions.run_completion")
    @patch("console.agent_chat.suggestions.get_summarization_llm_configs")
    def test_dynamic_prompt_includes_agent_and_user_names(
        self,
        mock_get_configs,
        mock_run_completion,
        _mock_log_completion,
    ):
        mock_get_configs.return_value = [("test-provider", "test-model", {})]
        suggestions_payload = {
            "suggestions": [
                {"text": "Draft my weekly summary for me.", "category": "deliverables"},
                {"text": "List my next priorities.", "category": "planning"},
                {"text": "Analyze my latest metrics and highlight changes.", "category": "capabilities"},
            ]
        }
        mock_run_completion.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        tool_calls=[
                            SimpleNamespace(
                                function=SimpleNamespace(
                                    name="provide_suggestions",
                                    arguments=json.dumps(suggestions_payload),
                                )
                            )
                        ]
                    )
                )
            ]
        )

        results = _generate_dynamic_suggestions(
            self.agent,
            context="User: Please review my draft plan.",
            prompt_count=3,
        )

        self.assertEqual(len(results), 3)
        self.assertTrue(mock_run_completion.called)
        messages = mock_run_completion.call_args.kwargs["messages"]
        combined_prompt = "\n".join(str(message.get("content") or "") for message in messages)
        self.assertIn(f"Agent name: {self.agent.name}", combined_prompt)
        self.assertIn(f"Current user name: {self.user.username}", combined_prompt)
        self.assertIn("Do not use or mention the user's name or the agent's name", combined_prompt)
        self.assertIn("Do not mention tool calls, tools, steps, or internal agent mechanics", combined_prompt)


@tag("batch_agent_chat")
class AgentChatSuggestionsContextTests(TestCase):
    def test_context_window_excludes_thinking_events_before_truncation(self):
        events = [
            {
                "kind": "message",
                "message": {
                    "bodyText": "Please summarize my latest pipeline results.",
                    "isOutbound": False,
                },
            }
        ]
        events.extend(
            {
                "kind": "thinking",
                "reasoning": f"internal-thought-{index}",
            }
            for index in range(30)
        )

        context = _context_from_timeline_events(events)
        self.assertIn("User: Please summarize my latest pipeline results.", context)

    def test_context_uses_recent_messages_only(self):
        events = [
            {
                "kind": "message",
                "message": {
                    "bodyText": f"Message {index}",
                    "isOutbound": False,
                },
            }
            for index in range(10)
        ]
        events.append(
            {
                "kind": "steps",
                "entries": [
                    {"toolName": f"tool-{index}", "summary": f"Result {index}"}
                    for index in range(8)
                ],
            }
        )
        events.append({"kind": "kanban", "displayText": "Plan changed"})

        context = _context_from_timeline_events(events)
        self.assertNotIn("Message 0", context)
        self.assertIn("User: Message 9", context)
        self.assertNotIn("Recent action: tool-7: Result 7", context)
        self.assertNotIn("tool-7", context)
        self.assertNotIn("Plan changed", context)
