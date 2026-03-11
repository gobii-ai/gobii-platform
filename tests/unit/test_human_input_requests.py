import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings, tag

from api.agent.comms.human_input_requests import (
    create_human_input_request,
    resolve_human_input_request_for_message,
)
from api.agent.core.prompt_context import _get_recent_human_input_responses_block
from console.agent_chat.timeline import serialize_step_entry
from api.agent.tools.request_human_input import (
    execute_request_human_input,
    get_request_human_input_tool,
)
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentHumanInputRequest,
    PersistentAgentMessage,
    build_web_agent_address,
    build_web_user_address,
)


@override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
@tag("batch_human_input")
class HumanInputRequestTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="human-input-owner",
            email="human-input-owner@example.com",
            password="password123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Browser Agent")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Human Input Agent",
            charter="Collect human input when needed.",
            browser_use_agent=self.browser_agent,
        )
        self.user_address = build_web_user_address(self.user.id, self.agent.id)
        self.agent_address = build_web_agent_address(self.agent.id)
        self.agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=self.agent_address,
            is_primary=True,
        )
        self.user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.WEB,
            address=self.user_address,
        )
        self.conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=self.user_address,
        )
        self.latest_inbound = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=self.user_endpoint,
            to_endpoint=self.agent_endpoint,
            conversation=self.conversation,
            owner_agent=self.agent,
            body="What do you need from me?",
            raw_payload={"source": "test"},
        )

    def _create_prompt_message(self, body: str = "Prompt") -> PersistentAgentMessage:
        return PersistentAgentMessage.objects.create(
            is_outbound=True,
            from_endpoint=self.agent_endpoint,
            to_endpoint=self.user_endpoint,
            conversation=self.conversation,
            owner_agent=self.agent,
            body=body,
            raw_payload={"source": "test"},
        )

    def _create_request(
        self,
        *,
        question: str = "Which option works best?",
        options: list[dict[str, str]] | None = None,
    ) -> PersistentAgentHumanInputRequest:
        return PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=self.conversation,
            question=question,
            options_json=options or [],
            input_mode=(
                PersistentAgentHumanInputRequest.InputMode.OPTIONS_PLUS_TEXT
                if options
                else PersistentAgentHumanInputRequest.InputMode.FREE_TEXT_ONLY
            ),
            requested_via_channel=CommsChannel.WEB,
            requested_message=self._create_prompt_message(),
        )

    def test_tool_definition_allows_optional_options(self):
        tool = get_request_human_input_tool()
        function = tool["function"]
        self.assertEqual(function["name"], "request_human_input")
        self.assertNotIn("title", function["parameters"]["properties"])
        self.assertIn("options", function["parameters"]["properties"])
        self.assertIn("requests", function["parameters"]["properties"])
        self.assertEqual(
            function["parameters"]["properties"]["requests"]["items"]["required"],
            ["question"],
        )

    @patch("api.agent.comms.human_input_requests.execute_send_chat_message")
    def test_execute_request_human_input_creates_free_text_request(self, mock_send_chat_message):
        prompt_message = self._create_prompt_message("Need your input")
        mock_send_chat_message.return_value = {"status": "ok", "message_id": str(prompt_message.id)}

        result = execute_request_human_input(
            self.agent,
            {
                "question": "What should I tell the team?",
                "options": [],
            },
        )

        self.assertEqual(result["status"], "ok")
        request_obj = PersistentAgentHumanInputRequest.objects.get(id=result["request_id"])
        self.assertEqual(
            request_obj.input_mode,
            PersistentAgentHumanInputRequest.InputMode.FREE_TEXT_ONLY,
        )
        self.assertEqual(request_obj.requested_message_id, prompt_message.id)

    def test_execute_request_human_input_rejects_more_than_six_options(self):
        result = execute_request_human_input(
            self.agent,
            {
                "question": "Which one?",
                "options": [
                    {"title": f"Option {index}", "description": "Choice"}
                    for index in range(1, 8)
                ],
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("cannot exceed 6", result["message"])

    @patch("api.agent.comms.human_input_requests.execute_send_chat_message")
    def test_execute_request_human_input_creates_multiple_requests(self, mock_send_chat_message):
        first_prompt = self._create_prompt_message("First prompt")
        second_prompt = self._create_prompt_message("Second prompt")
        mock_send_chat_message.side_effect = [
            {"status": "ok", "message_id": str(first_prompt.id)},
            {"status": "ok", "message_id": str(second_prompt.id)},
        ]

        result = execute_request_human_input(
            self.agent,
            {
                "requests": [
                    {
                        "question": "What should happen first?",
                        "options": [{"title": "Ship", "description": "Move now."}],
                    },
                    {
                        "question": "What should happen second?",
                        "options": [],
                    },
                ],
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["requests_count"], 2)
        self.assertEqual(len(result["request_ids"]), 2)
        self.assertEqual(
            PersistentAgentHumanInputRequest.objects.filter(agent=self.agent).count(),
            2,
        )

    @patch("api.agent.comms.human_input_requests.execute_send_email")
    def test_create_human_input_request_renders_email_options(self, mock_send_email):
        email_agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.com",
        )
        email_user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="person@example.com",
        )
        email_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="person@example.com",
        )
        PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=email_user_endpoint,
            to_endpoint=email_agent_endpoint,
            conversation=email_conversation,
            owner_agent=self.agent,
            body="Please email me",
            raw_payload={"subject": "Planning"},
        )
        prompt_message = PersistentAgentMessage.objects.create(
            is_outbound=True,
            from_endpoint=email_agent_endpoint,
            to_endpoint=email_user_endpoint,
            conversation=email_conversation,
            owner_agent=self.agent,
            body="<p>Prompt</p>",
            raw_payload={"subject": "Quick question"},
        )
        mock_send_email.return_value = {"status": "ok", "message_id": str(prompt_message.id)}

        create_human_input_request(
            self.agent,
            question="How should I send this?",
            raw_options=[
                {"title": "Short summary", "description": "A concise update."},
                {"title": "Detailed memo", "description": "A fuller write-up."},
            ],
        )

        self.assertTrue(mock_send_email.called)
        params = mock_send_email.call_args.args[1]
        self.assertEqual(params["to_address"], "person@example.com")
        self.assertIn("Quick question: How should I send this?", params["subject"])
        self.assertIn("Reply with the number, the option title, or your own words.", params["mobile_first_html"])
        self.assertIn("Short summary", params["mobile_first_html"])
        self.assertIn("Detailed memo", params["mobile_first_html"])
        self.assertIn("Ref:", params["mobile_first_html"])

    def test_resolve_request_by_option_number(self):
        request_obj = self._create_request(
            options=[
                {"key": "yes", "title": "Yes", "description": "Proceed now"},
                {"key": "later", "title": "Later", "description": "Wait a bit"},
            ]
        )
        reply = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=self.user_endpoint,
            to_endpoint=self.agent_endpoint,
            conversation=self.conversation,
            owner_agent=self.agent,
            body="2",
            raw_payload={"source": "test"},
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertEqual(resolved.id, request_obj.id)
        resolved.refresh_from_db()
        self.assertEqual(resolved.selected_option_key, "later")
        self.assertEqual(
            resolved.resolution_source,
            PersistentAgentHumanInputRequest.ResolutionSource.OPTION_NUMBER,
        )

    def test_resolve_request_by_option_title(self):
        request_obj = self._create_request(
            options=[
                {"key": "summary", "title": "Short summary", "description": "A concise update."},
                {"key": "memo", "title": "Detailed memo", "description": "A fuller write-up."},
            ]
        )
        reply = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=self.user_endpoint,
            to_endpoint=self.agent_endpoint,
            conversation=self.conversation,
            owner_agent=self.agent,
            body="Detailed memo",
            raw_payload={"source": "test"},
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertEqual(resolved.id, request_obj.id)
        resolved.refresh_from_db()
        self.assertEqual(resolved.selected_option_key, "memo")
        self.assertEqual(
            resolved.resolution_source,
            PersistentAgentHumanInputRequest.ResolutionSource.OPTION_TITLE,
        )

    def test_resolve_request_as_free_text_when_no_option_matches(self):
        request_obj = self._create_request(
            options=[
                {"key": "summary", "title": "Short summary", "description": "A concise update."},
                {"key": "memo", "title": "Detailed memo", "description": "A fuller write-up."},
            ]
        )
        reply = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=self.user_endpoint,
            to_endpoint=self.agent_endpoint,
            conversation=self.conversation,
            owner_agent=self.agent,
            body="Can you combine both and keep it brief?",
            raw_payload={"source": "test"},
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertEqual(resolved.id, request_obj.id)
        resolved.refresh_from_db()
        self.assertEqual(resolved.free_text, "Can you combine both and keep it brief?")
        self.assertEqual(
            resolved.resolution_source,
            PersistentAgentHumanInputRequest.ResolutionSource.FREE_TEXT,
        )

    def test_resolve_free_text_only_request(self):
        request_obj = self._create_request(question="What should I include?")
        reply = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=self.user_endpoint,
            to_endpoint=self.agent_endpoint,
            conversation=self.conversation,
            owner_agent=self.agent,
            body="Mention the risks and the launch date.",
            raw_payload={"source": "test"},
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertEqual(resolved.id, request_obj.id)
        resolved.refresh_from_db()
        self.assertEqual(resolved.free_text, "Mention the risks and the launch date.")

    def test_reference_code_targets_older_request(self):
        older = self._create_request(
            question="Old question?",
            options=[{"key": "yes", "title": "Yes", "description": "Proceed"}],
        )
        newer = self._create_request(
            question="New question?",
            options=[{"key": "no", "title": "No", "description": "Stop"}],
        )
        reply = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=self.user_endpoint,
            to_endpoint=self.agent_endpoint,
            conversation=self.conversation,
            owner_agent=self.agent,
            body=f"{older.reference_code} Yes",
            raw_payload={"source": "test"},
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertEqual(resolved.id, older.id)
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.status, PersistentAgentHumanInputRequest.Status.ANSWERED)
        self.assertEqual(newer.status, PersistentAgentHumanInputRequest.Status.PENDING)

    def test_latest_open_request_is_fallback_when_ambiguous(self):
        older = self._create_request(question="Old question?")
        newer = self._create_request(question="New question?")
        reply = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=self.user_endpoint,
            to_endpoint=self.agent_endpoint,
            conversation=self.conversation,
            owner_agent=self.agent,
            body="I need another day.",
            raw_payload={"source": "test"},
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertEqual(resolved.id, newer.id)
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.status, PersistentAgentHumanInputRequest.Status.PENDING)
        self.assertEqual(newer.status, PersistentAgentHumanInputRequest.Status.ANSWERED)

    def test_prompt_context_block_includes_recent_response(self):
        request_obj = self._create_request(question="What is the status?")
        request_obj.status = PersistentAgentHumanInputRequest.Status.ANSWERED
        request_obj.free_text = "Ship it tomorrow."
        request_obj.raw_reply_text = "Ship it tomorrow."
        request_obj.resolution_source = PersistentAgentHumanInputRequest.ResolutionSource.FREE_TEXT
        request_obj.resolved_at = request_obj.created_at
        request_obj.save(
            update_fields=["status", "free_text", "raw_reply_text", "resolution_source", "resolved_at", "updated_at"]
        )

        block = _get_recent_human_input_responses_block(self.agent)

        self.assertIn("Recent human input responses:", block)
        self.assertIn(request_obj.reference_code, block)
        self.assertIn("Ship it tomorrow.", block)

    def test_serialize_step_entry_uses_live_request_state(self):
        from api.models import PersistentAgentStep, PersistentAgentToolCall

        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Requested human input",
            credits_cost=0,
        )
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="request_human_input",
            tool_params={
                "question": "What should I do next?",
                "options": [{"title": "Ship it", "description": "Move forward now."}],
            },
            result=json.dumps({"status": "ok", "message": "Human input request sent via web."}),
        )
        request_obj = PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=self.conversation,
            originating_step=step,
            question="What should I do next?",
            options_json=[{"key": "ship", "title": "Ship it", "description": "Move forward now."}],
            input_mode=PersistentAgentHumanInputRequest.InputMode.OPTIONS_PLUS_TEXT,
            requested_via_channel=CommsChannel.WEB,
            requested_message=self._create_prompt_message(),
            status=PersistentAgentHumanInputRequest.Status.ANSWERED,
            selected_option_key="ship",
            selected_option_title="Ship it",
            raw_reply_text="Ship it",
            resolution_source=PersistentAgentHumanInputRequest.ResolutionSource.DIRECT,
            resolved_at=self.latest_inbound.timestamp,
        )

        entry = serialize_step_entry(step)

        self.assertEqual(entry["toolName"], "request_human_input")
        self.assertEqual(entry["result"]["status"], PersistentAgentHumanInputRequest.Status.ANSWERED)
        self.assertEqual(entry["result"]["request_id"], str(request_obj.id))
        self.assertNotIn("title", entry["result"])
        self.assertEqual(entry["result"]["selected_option_title"], "Ship it")


@override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
@tag("batch_human_input")
class HumanInputRequestApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="human-input-api-owner",
            email="human-input-api-owner@example.com",
            password="password123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Browser Agent")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Human Input API Agent",
            charter="Collect human input when needed.",
            browser_use_agent=self.browser_agent,
        )
        self.user_address = build_web_user_address(self.user.id, self.agent.id)
        self.agent_address = build_web_agent_address(self.agent.id)
        self.agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=self.agent_address,
            is_primary=True,
        )
        self.user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.WEB,
            address=self.user_address,
        )
        self.conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=self.user_address,
        )
        self.prompt_message = PersistentAgentMessage.objects.create(
            is_outbound=True,
            from_endpoint=self.agent_endpoint,
            to_endpoint=self.user_endpoint,
            conversation=self.conversation,
            owner_agent=self.agent,
            body="What should I do next?",
            raw_payload={"source": "test"},
        )
        self.request_obj = PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=self.conversation,
            question="What should I do next?",
            options_json=[
                {"key": "ship", "title": "Ship it", "description": "Move forward now."},
                {"key": "wait", "title": "Wait", "description": "Pause for more info."},
            ],
            input_mode=PersistentAgentHumanInputRequest.InputMode.OPTIONS_PLUS_TEXT,
            requested_via_channel=CommsChannel.WEB,
            requested_message=self.prompt_message,
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_timeline_and_response_endpoint(self):
        timeline_response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(timeline_response.status_code, 200)
        timeline_payload = timeline_response.json()
        self.assertEqual(len(timeline_payload["pending_human_input_requests"]), 1)
        self.assertNotIn("title", timeline_payload["pending_human_input_requests"][0])
        self.assertEqual(
            timeline_payload["pending_human_input_requests"][0]["question"],
            "What should I do next?",
        )
        self.assertEqual(
            timeline_payload["pending_human_input_requests"][0]["referenceCode"],
            self.request_obj.reference_code,
        )

        response = self.client.post(
            f"/console/api/agents/{self.agent.id}/human-input-requests/{self.request_obj.id}/respond/",
            data=json.dumps({"selected_option_key": "ship"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["event"]["kind"], "message")
        self.assertEqual(payload["event"]["message"]["bodyText"], "Ship it")
        self.assertEqual(payload["pending_human_input_requests"], [])

        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, PersistentAgentHumanInputRequest.Status.ANSWERED)
        self.assertEqual(self.request_obj.selected_option_key, "ship")
