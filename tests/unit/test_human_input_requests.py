import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import Client, TestCase, override_settings, tag
import litellm

from api.agent.comms.human_input_requests import (
    attach_originating_step_from_result,
    create_human_input_request,
    list_pending_human_input_requests,
    resolve_human_input_request_for_message,
)
from api.agent.core.prompt_context import _get_recent_human_input_responses_block
from console.agent_chat.timeline import serialize_step_entry
from console.agent_chat.pending_actions import list_pending_action_requests
from api.agent.tools.request_human_input import (
    execute_request_human_input,
    get_request_human_input_tool,
)
from api.agent.tools.runtime_execution_context import tool_execution_context
from api.models import (
    AgentCollaborator,
    BrowserUseAgent,
    CommsAllowlistEntry,
    CommsChannel,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentHumanInputRequest,
    PersistentAgentMessage,
    PersistentAgentStep,
    UserPhoneNumber,
    build_web_agent_address,
    build_web_user_address,
)
from tests.utils.token_usage import make_completion_response


def _make_tool_call_completion_response(payload: dict) -> MagicMock:
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "resolve_human_input_requests",
            "arguments": json.dumps(payload),
        },
    }
    message = MagicMock()
    message.content = ""
    setattr(message, "tool_calls", [tool_call])
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


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

    def _create_prompt_message(
        self,
        body: str = "Prompt",
        *,
        agent: PersistentAgent | None = None,
        conversation: PersistentAgentConversation | None = None,
    ) -> PersistentAgentMessage:
        target_agent = agent or self.agent
        target_conversation = conversation or self.conversation
        agent_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            owner_agent=target_agent,
            channel=target_conversation.channel,
            address=build_web_agent_address(target_agent.id)
            if target_conversation.channel == CommsChannel.WEB
            else ("agent@example.com" if target_conversation.channel == CommsChannel.EMAIL else "+15555550100"),
            defaults={"is_primary": target_conversation.channel == CommsChannel.WEB},
        )
        user_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=target_conversation.channel,
            address=target_conversation.address,
        )
        return PersistentAgentMessage.objects.create(
            is_outbound=True,
            from_endpoint=agent_endpoint,
            to_endpoint=user_endpoint,
            conversation=target_conversation,
            owner_agent=target_agent,
            body=body,
            raw_payload={"source": "test"},
        )

    def _create_request(
        self,
        *,
        question: str = "Which option works best?",
        options: list[dict[str, str]] | None = None,
        agent: PersistentAgent | None = None,
        conversation: PersistentAgentConversation | None = None,
        requested_via_channel: str = CommsChannel.WEB,
        originating_step=None,
        recipient_channel: str = "",
        recipient_address: str = "",
    ) -> PersistentAgentHumanInputRequest:
        target_agent = agent or self.agent
        return PersistentAgentHumanInputRequest.objects.create(
            agent=target_agent,
            conversation=conversation or self.conversation,
            originating_step=originating_step,
            question=question,
            options_json=options or [],
            input_mode=(
                PersistentAgentHumanInputRequest.InputMode.OPTIONS_PLUS_TEXT
                if options
                else PersistentAgentHumanInputRequest.InputMode.FREE_TEXT_ONLY
            ),
            recipient_channel=recipient_channel,
            recipient_address=recipient_address,
            requested_via_channel=requested_via_channel,
            requested_message=self._create_prompt_message(
                agent=target_agent,
                conversation=conversation or self.conversation,
            ),
        )

    def _create_web_reply_from_user(
        self,
        *,
        user,
        body: str,
        agent: PersistentAgent | None = None,
        raw_payload: dict | None = None,
    ) -> PersistentAgentMessage:
        target_agent = agent or self.agent
        user_address = build_web_user_address(user.id, target_agent.id)
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=user_address,
        )
        agent_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            owner_agent=target_agent,
            channel=CommsChannel.WEB,
            address=build_web_agent_address(target_agent.id),
            defaults={"is_primary": True},
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=target_agent,
            channel=CommsChannel.WEB,
            address=user_address,
        )
        return PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=user_endpoint,
            to_endpoint=agent_endpoint,
            conversation=conversation,
            owner_agent=target_agent,
            body=body,
            raw_payload=raw_payload or {"source": "test"},
        )

    def _create_org_agent(self) -> tuple[Organization, PersistentAgent]:
        org = Organization.objects.create(
            name="Human Input Org",
            slug="human-input-org",
            plan="free",
            created_by=self.user,
        )
        billing = org.billing
        billing.purchased_seats = 3
        billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        org_browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Org Browser Agent")
        org_agent = PersistentAgent.objects.create(
            user=self.user,
            organization=org,
            name="Org Human Input Agent",
            charter="Collect internal human input.",
            browser_use_agent=org_browser_agent,
        )
        owner_address = build_web_user_address(self.user.id, org_agent.id)
        agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=org_agent,
            channel=CommsChannel.WEB,
            address=build_web_agent_address(org_agent.id),
            is_primary=True,
        )
        owner_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=owner_address,
        )
        owner_conversation = PersistentAgentConversation.objects.create(
            owner_agent=org_agent,
            channel=CommsChannel.WEB,
            address=owner_address,
        )
        PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=owner_endpoint,
            to_endpoint=agent_endpoint,
            conversation=owner_conversation,
            owner_agent=org_agent,
            body="Org owner here",
            raw_payload={"source": "test"},
        )
        return org, org_agent

    def _create_cross_channel_message(
        self,
        *,
        channel: str,
        body: str,
        sender_address: str | None = None,
        raw_payload: dict | None = None,
    ) -> PersistentAgentMessage:
        agent_address = "agent@example.com" if channel == CommsChannel.EMAIL else "+15555550100"
        user_address = sender_address or (self.user.email if channel == CommsChannel.EMAIL else "+15555550199")
        agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=channel,
            address=agent_address,
        )
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=channel,
            address=user_address,
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=channel,
            address=user_address,
        )
        return PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=user_endpoint,
            to_endpoint=agent_endpoint,
            conversation=conversation,
            owner_agent=self.agent,
            body=body,
            raw_payload=raw_payload or {"source": "test"},
        )

    def test_tool_definition_allows_optional_options(self):
        tool = get_request_human_input_tool()
        function = tool["function"]
        description = function["description"]
        self.assertEqual(function["name"], "request_human_input")
        self.assertIn("always appears in the web chat composer panel", description)
        self.assertIn("does not send email or SMS by itself", description)
        self.assertIn("same tool-call batch as request_human_input", description)
        self.assertIn("already include the questions", description)
        self.assertIn("Do not send a bare notification", description)
        self.assertNotIn("title", function["parameters"]["properties"])
        self.assertIn("options", function["parameters"]["properties"])
        self.assertIn("requests", function["parameters"]["properties"])
        self.assertIn("recipient", function["parameters"]["properties"])
        self.assertIn("will_continue_work", function["parameters"]["properties"])
        self.assertIn(
            "use true when you will send an email/SMS containing these questions",
            function["parameters"]["properties"]["will_continue_work"]["description"],
        )
        self.assertEqual(function["parameters"]["required"], ["will_continue_work"])
        self.assertEqual(
            function["parameters"]["properties"]["requests"]["items"]["required"],
            ["question"],
        )

    def test_execute_request_human_input_creates_free_text_request(self):
        result = execute_request_human_input(
            self.agent,
            {
                "question": "What should I tell the team?",
                "options": [],
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertNotIn("reference_code", result)
        self.assertEqual(result["request_ids"], [result["request_id"]])
        self.assertEqual(result["requests_count"], 1)
        self.assertEqual(result["target_channel"], CommsChannel.WEB)
        self.assertEqual(result["target_address"], self.user_address)
        self.assertTrue(result["web_chat_visible"])
        self.assertTrue(result["auto_sleep_ok"])
        self.assertNotIn("relay_mode", result)
        self.assertNotIn("relay_payload", result)
        self.assertNotIn("next_message_suggestion", result)
        self.assertEqual(result["requests"][0]["question"], "What should I tell the team?")
        request_obj = PersistentAgentHumanInputRequest.objects.get(id=result["request_id"])
        self.assertEqual(
            request_obj.input_mode,
            PersistentAgentHumanInputRequest.InputMode.FREE_TEXT_ONLY,
        )
        self.assertEqual(request_obj.conversation_id, self.conversation.id)
        self.assertEqual(request_obj.recipient_channel, "")
        self.assertEqual(request_obj.recipient_address, "")
        self.assertIsNone(request_obj.requested_message_id)

    def test_execute_request_human_input_will_continue_true_keeps_panel_request_active(self):
        result = execute_request_human_input(
            self.agent,
            {
                "question": "What should I tell the team?",
                "options": [],
                "will_continue_work": True,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["web_chat_visible"])
        self.assertNotIn("relay_mode", result)
        self.assertNotIn("relay_payload", result)
        self.assertNotIn("auto_sleep_ok", result)

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

    def test_execute_request_human_input_creates_multiple_requests(self):
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
        self.assertEqual(result["request_id"], result["request_ids"][0])
        self.assertEqual(len(result["request_ids"]), 2)
        self.assertEqual(result["requests_count"], 2)
        self.assertTrue(result["web_chat_visible"])
        self.assertNotIn("relay_mode", result)
        self.assertNotIn("relay_payload", result)
        self.assertTrue(result["auto_sleep_ok"])
        self.assertEqual(
            [request["question"] for request in result["requests"]],
            ["What should happen first?", "What should happen second?"],
        )
        self.assertEqual(
            PersistentAgentHumanInputRequest.objects.filter(agent=self.agent).count(),
            2,
        )
        self.assertFalse(
            PersistentAgentHumanInputRequest.objects.filter(
                agent=self.agent,
                requested_message__isnull=False,
            ).exists()
        )

    def test_execute_request_human_input_uses_current_tool_step_for_batch(self):
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Pending request_human_input",
            credits_cost=0,
        )

        with tool_execution_context(step_id=str(step.id)):
            result = execute_request_human_input(
                self.agent,
                {
                    "requests": [
                        {"question": "What should happen first?"},
                        {"question": "What should happen second?"},
                    ],
                },
            )

        self.assertEqual(result["status"], "ok")
        request_objects = list(
            PersistentAgentHumanInputRequest.objects.filter(id__in=result["request_ids"]).order_by("created_at")
        )
        self.assertEqual(len(request_objects), 2)
        self.assertTrue(all(request_obj.originating_step_id == step.id for request_obj in request_objects))
        pending_requests = list_pending_human_input_requests(self.agent)
        self.assertEqual({request["batchId"] for request in pending_requests}, {str(step.id)})
        self.assertEqual(
            sorted(request["batchPosition"] for request in pending_requests),
            [1, 2],
        )
        self.assertEqual(
            sorted(request["batchSize"] for request in pending_requests),
            [2, 2],
        )

    def test_execute_request_human_input_targets_explicit_recipient(self):
        collaborator = get_user_model().objects.create_user(
            username="recipient-collaborator",
            email="recipient-collaborator@example.com",
            password="password123",
        )
        AgentCollaborator.objects.create(agent=self.agent, user=collaborator)

        result = execute_request_human_input(
            self.agent,
            {
                "question": "Who should review this?",
                "recipient": {
                    "channel": CommsChannel.EMAIL,
                    "address": collaborator.email.upper(),
                },
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["target_channel"], CommsChannel.EMAIL)
        self.assertEqual(result["target_address"], collaborator.email)
        self.assertTrue(result["web_chat_visible"])
        self.assertNotIn("auto_sleep_ok", result)
        self.assertNotIn("relay_mode", result)
        self.assertNotIn("relay_payload", result)
        self.assertEqual(result["next_message_suggestion"]["send_tool"], "send_email")
        self.assertEqual(result["next_message_suggestion"]["address"], collaborator.email)
        self.assertIn("Who should review this?", result["next_message_suggestion"]["questions"][0]["question"])
        request_obj = PersistentAgentHumanInputRequest.objects.get(id=result["request_id"])
        self.assertEqual(request_obj.conversation.channel, CommsChannel.EMAIL)
        self.assertEqual(request_obj.conversation.address, collaborator.email)
        self.assertEqual(request_obj.recipient_channel, CommsChannel.EMAIL)
        self.assertEqual(request_obj.recipient_address, collaborator.email)
        self.assertNotEqual(request_obj.conversation_id, self.conversation.id)
        self.assertIsNone(request_obj.requested_message_id)

    def test_execute_request_human_input_batch_applies_top_level_recipient(self):
        result = execute_request_human_input(
            self.agent,
            {
                "recipient": {
                    "channel": CommsChannel.EMAIL,
                    "address": self.user.email,
                },
                "requests": [
                    {
                        "question": "What should happen first?",
                        "options": [{"title": "Ship", "description": "Move now."}],
                    },
                    {
                        "question": "What should happen second?",
                        "options": [{"title": "Wait", "description": "Pause for context."}],
                    },
                ],
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["requests_count"], 2)
        self.assertTrue(result["web_chat_visible"])
        self.assertNotIn("relay_mode", result)
        self.assertNotIn("relay_payload", result)
        self.assertEqual(result["next_message_suggestion"]["send_tool"], "send_email")
        self.assertEqual(
            [question["question"] for question in result["next_message_suggestion"]["questions"]],
            ["What should happen first?", "What should happen second?"],
        )
        self.assertEqual(result["requests"][0]["options"][0]["title"], "Ship")
        self.assertEqual(result["requests"][1]["options"][0]["title"], "Wait")
        request_objects = list(
            PersistentAgentHumanInputRequest.objects.filter(id__in=result["request_ids"]).order_by("created_at")
        )
        self.assertEqual(len(request_objects), 2)
        self.assertTrue(all(request_obj.conversation.channel == CommsChannel.EMAIL for request_obj in request_objects))
        self.assertTrue(all(request_obj.conversation.address == self.user.email for request_obj in request_objects))
        self.assertTrue(all(request_obj.recipient_channel == CommsChannel.EMAIL for request_obj in request_objects))
        self.assertTrue(all(request_obj.recipient_address == self.user.email for request_obj in request_objects))
        self.assertTrue(all(request_obj.requested_message_id is None for request_obj in request_objects))

    def test_batch_request_returns_partial_success_details_when_later_create_fails(self):
        original_create = PersistentAgentHumanInputRequest.objects.create
        create_attempts = {"count": 0}

        def flaky_create(*args, **kwargs):
            create_attempts["count"] += 1
            if create_attempts["count"] == 2:
                raise DatabaseError("Database is unavailable.")
            return original_create(*args, **kwargs)

        with patch.object(PersistentAgentHumanInputRequest.objects, "create", side_effect=flaky_create):
            result = execute_request_human_input(
                self.agent,
                {
                    "recipient": {
                        "channel": CommsChannel.EMAIL,
                        "address": self.user.email,
                    },
                    "requests": [
                        {"question": "What should happen first?"},
                        {"question": "What should happen second?"},
                    ],
                },
            )

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["partial_success"])
        self.assertEqual(len(result["request_ids"]), 1)
        self.assertEqual(result["request_id"], result["request_ids"][0])
        self.assertTrue(result["web_chat_visible"])
        self.assertNotIn("relay_mode", result)
        self.assertNotIn("relay_payload", result)
        self.assertEqual(result["next_message_suggestion"]["send_tool"], "send_email")
        self.assertIn("Created 1 of 2 human input requests", result["message"])
        self.assertIn("Database is unavailable.", result["message"])
        self.assertEqual(
            PersistentAgentHumanInputRequest.objects.filter(agent=self.agent).count(),
            1,
        )

    def test_create_human_input_request_returns_email_notification_suggestion(self):
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
        result = create_human_input_request(
            self.agent,
            question="How should I send this?",
            raw_options=[
                {"title": "Short summary", "description": "A concise update."},
                {"title": "Detailed memo", "description": "A fuller write-up."},
            ],
        )

        self.assertEqual(result["target_channel"], CommsChannel.EMAIL)
        self.assertEqual(result["target_address"], "person@example.com")
        self.assertTrue(result["web_chat_visible"])
        self.assertNotIn("relay_mode", result)
        self.assertNotIn("relay_payload", result)
        suggestion = result["next_message_suggestion"]
        self.assertEqual(suggestion["channel"], CommsChannel.EMAIL)
        self.assertEqual(suggestion["address"], "person@example.com")
        self.assertEqual(suggestion["send_tool"], "send_email")
        self.assertIn("normal email message", suggestion["instruction"])
        self.assertIn("same tool-call batch", suggestion["instruction"])
        self.assertIn("cannot inject them into another tool call", suggestion["instruction"])
        self.assertIn("How should I send this?", suggestion["questions"][0]["question"])
        self.assertEqual(result["requests"][0]["options"][0]["title"], "Short summary")
        self.assertEqual(result["requests"][0]["options"][1]["title"], "Detailed memo")
        self.assertIsNone(
            PersistentAgentHumanInputRequest.objects.get(id=result["request_id"]).requested_message_id
        )

    def test_create_human_input_request_returns_sms_notification_suggestion(self):
        sms_agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15555550100",
        )
        sms_user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.SMS,
            address="+15555550199",
        )
        sms_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15555550199",
        )
        PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=sms_user_endpoint,
            to_endpoint=sms_agent_endpoint,
            conversation=sms_conversation,
            owner_agent=self.agent,
            body="Please text me",
            raw_payload={"source": "test"},
        )
        result = create_human_input_request(
            self.agent,
            question="How should I send this?",
            raw_options=[{"title": "Short summary", "description": "A concise update."}],
        )

        self.assertEqual(result["target_channel"], CommsChannel.SMS)
        self.assertEqual(result["target_address"], "+15555550199")
        self.assertTrue(result["web_chat_visible"])
        self.assertNotIn("relay_mode", result)
        self.assertNotIn("relay_payload", result)
        suggestion = result["next_message_suggestion"]
        self.assertEqual(suggestion["channel"], CommsChannel.SMS)
        self.assertEqual(suggestion["address"], "+15555550199")
        self.assertEqual(suggestion["send_tool"], "send_sms")
        self.assertIn("normal sms message", suggestion["instruction"])
        self.assertIn("same tool-call batch", suggestion["instruction"])
        self.assertIn("cannot inject them into another tool call", suggestion["instruction"])
        self.assertIn("How should I send this?", suggestion["questions"][0]["question"])
        self.assertEqual(result["requests"][0]["options"][0]["title"], "Short summary")

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

    @patch("api.agent.comms.human_input_requests.get_summarization_llm_config")
    @patch("api.agent.comms.human_input_requests.run_completion")
    def test_ambiguous_requests_stay_pending_when_llm_returns_no_match(
        self,
        mock_run_completion,
        mock_get_summarization_llm_config,
    ):
        older = self._create_request(question="Old question?")
        newer = self._create_request(question="New question?")
        mock_get_summarization_llm_config.return_value = ("openai", "openai/gpt-4.1", {})
        mock_run_completion.return_value = make_completion_response(content="no tool call")
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

        self.assertIsNone(resolved)
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.status, PersistentAgentHumanInputRequest.Status.PENDING)
        self.assertEqual(newer.status, PersistentAgentHumanInputRequest.Status.PENDING)

    def test_omitted_recipient_request_can_be_resolved_by_collaborator(self):
        collaborator = get_user_model().objects.create_user(
            username="human-input-collaborator",
            email="human-input-collaborator@example.com",
            password="password123",
        )
        AgentCollaborator.objects.create(agent=self.agent, user=collaborator)
        result = create_human_input_request(
            self.agent,
            question="Who should join the kickoff?",
            raw_options=[],
        )
        request_obj = PersistentAgentHumanInputRequest.objects.get(id=result["request_id"])

        reply = self._create_web_reply_from_user(
            user=collaborator,
            body="Invite the design lead.",
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertEqual(resolved.id, request_obj.id)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.free_text, "Invite the design lead.")
        self.assertEqual(request_obj.recipient_channel, "")

    def test_omitted_recipient_request_can_be_resolved_by_active_org_member(self):
        org, org_agent = self._create_org_agent()
        member = get_user_model().objects.create_user(
            username="org-member-human-input",
            email="org-member-human-input@example.com",
            password="password123",
        )
        OrganizationMembership.objects.create(
            org=org,
            user=member,
            role=OrganizationMembership.OrgRole.MEMBER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        result = create_human_input_request(
            org_agent,
            question="Who should review the budget?",
            raw_options=[],
        )
        request_obj = PersistentAgentHumanInputRequest.objects.get(id=result["request_id"])

        reply = self._create_web_reply_from_user(
            user=member,
            body="Have finance review it.",
            agent=org_agent,
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertEqual(resolved.id, request_obj.id)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.free_text, "Have finance review it.")

    def test_omitted_recipient_request_cannot_be_resolved_by_allowlisted_external_contact(self):
        self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
        self.agent.save(update_fields=["whitelist_policy"])
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="external-contact@example.com",
        )
        result = create_human_input_request(
            self.agent,
            question="What should we tell the customer?",
            raw_options=[],
        )
        request_obj = PersistentAgentHumanInputRequest.objects.get(id=result["request_id"])

        reply = self._create_cross_channel_message(
            channel=CommsChannel.EMAIL,
            body="Tell them we'll follow up tomorrow.",
            sender_address="external-contact@example.com",
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertIsNone(resolved)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, PersistentAgentHumanInputRequest.Status.PENDING)

    def test_direct_request_id_fails_for_unauthorized_explicit_recipient(self):
        collaborator = get_user_model().objects.create_user(
            username="explicit-recipient-collaborator",
            email="explicit-recipient-collaborator@example.com",
            password="password123",
        )
        AgentCollaborator.objects.create(agent=self.agent, user=collaborator)
        result = create_human_input_request(
            self.agent,
            question="Should we approve this launch?",
            raw_options=[{"title": "Yes", "description": "Approve it."}],
            recipient={
                "channel": CommsChannel.WEB,
                "address": build_web_user_address(collaborator.id, self.agent.id),
            },
        )
        request_obj = PersistentAgentHumanInputRequest.objects.get(id=result["request_id"])
        reply = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=self.user_endpoint,
            to_endpoint=self.agent_endpoint,
            conversation=self.conversation,
            owner_agent=self.agent,
            body="Yes",
            raw_payload={
                "source": "test",
                "human_input_request_id": str(request_obj.id),
                "human_input_selected_option_key": "yes",
                "human_input_selected_option_title": "Yes",
            },
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertIsNone(resolved)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, PersistentAgentHumanInputRequest.Status.PENDING)

    @patch("api.agent.comms.human_input_requests.get_summarization_llm_config")
    @patch("api.agent.comms.human_input_requests.run_completion")
    def test_llm_resolves_non_latest_same_conversation_request(
        self,
        mock_run_completion,
        mock_get_summarization_llm_config,
    ):
        older = self._create_request(question="When should we ship the launch email?")
        newer = self._create_request(question="Who should approve the homepage changes?")
        mock_get_summarization_llm_config.return_value = ("openai", "openai/gpt-4.1", {})
        mock_run_completion.return_value = _make_tool_call_completion_response(
            {
                "matches": [
                {
                    "request_id": str(older.id),
                    "confidence": 0.93,
                    "answer_span": "Send it on Tuesday morning.",
                }
                ]
            }
        )
        reply = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=self.user_endpoint,
            to_endpoint=self.agent_endpoint,
            conversation=self.conversation,
            owner_agent=self.agent,
            body="Send it on Tuesday morning.",
            raw_payload={"source": "test"},
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertEqual(resolved.id, older.id)
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.status, PersistentAgentHumanInputRequest.Status.ANSWERED)
        self.assertEqual(older.free_text, "Send it on Tuesday morning.")
        self.assertEqual(
            older.resolution_source,
            PersistentAgentHumanInputRequest.ResolutionSource.LLM_EXTRACTION,
        )
        self.assertEqual(newer.status, PersistentAgentHumanInputRequest.Status.PENDING)
        _, kwargs = mock_run_completion.call_args
        self.assertIn("tools", kwargs)
        self.assertEqual(
            kwargs.get("tool_choice"),
            {"type": "function", "function": {"name": "resolve_human_input_requests"}},
        )

    @patch("api.agent.comms.human_input_requests.get_summarization_llm_config")
    @patch("api.agent.comms.human_input_requests.run_completion")
    def test_llm_resolves_multiple_requests_with_mixed_option_and_text(
        self,
        mock_run_completion,
        mock_get_summarization_llm_config,
    ):
        first_request = self._create_request(
            question="What's our next foodie destination?",
            options=[
                {"key": "sushi", "title": "Sushi", "description": "Fresh fish."},
                {"key": "ramen", "title": "Ramen", "description": "Hot noodles."},
            ],
        )
        second_request = self._create_request(question="How should we travel to our next spot?")
        mock_get_summarization_llm_config.return_value = ("openai", "openai/gpt-4.1", {})
        mock_run_completion.return_value = _make_tool_call_completion_response(
            {
                "matches": [
                {
                    "request_id": str(first_request.id),
                    "confidence": 0.98,
                    "answer_span": "Ramen",
                },
                {
                    "request_id": str(second_request.id),
                    "confidence": 0.91,
                    "answer_span": "Take the metro",
                }
                ]
            }
        )
        reply = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=self.user_endpoint,
            to_endpoint=self.agent_endpoint,
            conversation=self.conversation,
            owner_agent=self.agent,
            body="For food, let's do Ramen. For travel, take the metro.",
            raw_payload={"source": "test"},
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertEqual(resolved.id, first_request.id)
        first_request.refresh_from_db()
        second_request.refresh_from_db()
        self.assertEqual(first_request.status, PersistentAgentHumanInputRequest.Status.ANSWERED)
        self.assertEqual(first_request.selected_option_key, "ramen")
        self.assertEqual(second_request.status, PersistentAgentHumanInputRequest.Status.ANSWERED)
        self.assertEqual(second_request.free_text, "Take the metro")
        self.assertEqual(
            first_request.resolution_source,
            PersistentAgentHumanInputRequest.ResolutionSource.LLM_EXTRACTION,
        )
        self.assertEqual(
            second_request.resolution_source,
            PersistentAgentHumanInputRequest.ResolutionSource.LLM_EXTRACTION,
        )

    @patch("api.agent.comms.human_input_requests.get_summarization_llm_config")
    @patch("api.agent.comms.human_input_requests.run_completion")
    def test_llm_only_applies_matches_meeting_confidence_threshold(
        self,
        mock_run_completion,
        mock_get_summarization_llm_config,
    ):
        high_confidence_request = self._create_request(question="What should we prioritize first?")
        low_confidence_request = self._create_request(question="What should we postpone?")
        mock_get_summarization_llm_config.return_value = ("openai", "openai/gpt-4.1", {})
        mock_run_completion.return_value = _make_tool_call_completion_response(
            {
                "matches": [
                {
                    "request_id": str(high_confidence_request.id),
                    "confidence": 0.88,
                    "answer_span": "Prioritize onboarding.",
                },
                {
                    "request_id": str(low_confidence_request.id),
                    "confidence": 0.61,
                    "answer_span": "Postpone pricing updates.",
                }
                ]
            }
        )
        reply = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=self.user_endpoint,
            to_endpoint=self.agent_endpoint,
            conversation=self.conversation,
            owner_agent=self.agent,
            body="Prioritize onboarding. Postpone pricing updates.",
            raw_payload={"source": "test"},
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertEqual(resolved.id, high_confidence_request.id)
        high_confidence_request.refresh_from_db()
        low_confidence_request.refresh_from_db()
        self.assertEqual(high_confidence_request.status, PersistentAgentHumanInputRequest.Status.ANSWERED)
        self.assertEqual(low_confidence_request.status, PersistentAgentHumanInputRequest.Status.PENDING)

    @patch("api.agent.comms.human_input_requests.run_completion", side_effect=AssertionError("LLM should not run"))
    def test_direct_request_id_bypasses_llm(self, _mock_run_completion):
        direct_request = self._create_request(
            question="Should we ship this now?",
            options=[{"key": "yes", "title": "Yes", "description": "Ship it now."}],
        )
        other_request = self._create_request(question="What should happen next?")
        reply = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=self.user_endpoint,
            to_endpoint=self.agent_endpoint,
            conversation=self.conversation,
            owner_agent=self.agent,
            body="Yes",
            raw_payload={
                "source": "test",
                "human_input_request_id": str(direct_request.id),
                "human_input_selected_option_key": "yes",
                "human_input_selected_option_title": "Yes",
            },
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertEqual(resolved.id, direct_request.id)
        direct_request.refresh_from_db()
        other_request.refresh_from_db()
        self.assertEqual(direct_request.status, PersistentAgentHumanInputRequest.Status.ANSWERED)
        self.assertEqual(
            direct_request.resolution_source,
            PersistentAgentHumanInputRequest.ResolutionSource.DIRECT,
        )
        self.assertEqual(other_request.status, PersistentAgentHumanInputRequest.Status.PENDING)

    @patch("api.agent.comms.human_input_requests.get_summarization_llm_config")
    @patch("api.agent.comms.human_input_requests.run_completion")
    def test_conflicting_llm_matches_resolve_nothing(
        self,
        mock_run_completion,
        mock_get_summarization_llm_config,
    ):
        first_request = self._create_request(question="What should we do first?")
        second_request = self._create_request(question="What should we do second?")
        mock_get_summarization_llm_config.return_value = ("openai", "openai/gpt-4.1", {})
        mock_run_completion.return_value = _make_tool_call_completion_response(
            {
                "matches": [
                {
                    "request_id": str(first_request.id),
                    "confidence": 0.94,
                    "answer_span": "Wait until Friday.",
                },
                {
                    "request_id": str(second_request.id),
                    "confidence": 0.91,
                    "answer_span": "Wait until Friday.",
                }
                ]
            }
        )
        reply = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=self.user_endpoint,
            to_endpoint=self.agent_endpoint,
            conversation=self.conversation,
            owner_agent=self.agent,
            body="Wait until Friday.",
            raw_payload={"source": "test"},
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertIsNone(resolved)
        first_request.refresh_from_db()
        second_request.refresh_from_db()
        self.assertEqual(first_request.status, PersistentAgentHumanInputRequest.Status.PENDING)
        self.assertEqual(second_request.status, PersistentAgentHumanInputRequest.Status.PENDING)

    @patch("api.agent.comms.human_input_requests.get_summarization_llm_config")
    @patch("api.agent.comms.human_input_requests.run_completion")
    def test_llm_failures_fall_back_without_accidental_resolution(
        self,
        mock_run_completion,
        mock_get_summarization_llm_config,
    ):
        first_request = self._create_request(question="What should we do first?")
        second_request = self._create_request(question="What should we do second?")
        mock_get_summarization_llm_config.return_value = ("openai", "openai/gpt-4.1", {})

        failure_cases = [
            litellm.Timeout("timeout", model="mock-model", llm_provider="mock"),
            make_completion_response(content="not-json"),
            RuntimeError("boom"),
        ]
        for index, failure_case in enumerate(failure_cases, start=1):
            with self.subTest(case=index):
                mock_run_completion.reset_mock()
                if isinstance(failure_case, Exception):
                    mock_run_completion.side_effect = failure_case
                else:
                    mock_run_completion.side_effect = None
                    mock_run_completion.return_value = failure_case

                reply = PersistentAgentMessage.objects.create(
                    is_outbound=False,
                    from_endpoint=self.user_endpoint,
                    to_endpoint=self.agent_endpoint,
                    conversation=self.conversation,
                    owner_agent=self.agent,
                    body=f"Fallback case {index}",
                    raw_payload={"source": "test"},
                )

                resolved = resolve_human_input_request_for_message(reply)

                self.assertIsNone(resolved)
                first_request.refresh_from_db()
                second_request.refresh_from_db()
                self.assertEqual(first_request.status, PersistentAgentHumanInputRequest.Status.PENDING)
                self.assertEqual(second_request.status, PersistentAgentHumanInputRequest.Status.PENDING)

    @patch("api.agent.comms.human_input_requests.run_completion", side_effect=AssertionError("LLM should not run"))
    def test_wrong_sender_cross_channel_does_not_trigger_llm_matching(self, _mock_run_completion):
        request_obj = self._create_request(question="What's our next foodie destination?")
        reply = self._create_cross_channel_message(
            channel=CommsChannel.EMAIL,
            body="Sushi",
            sender_address="other-person@example.com",
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertIsNone(resolved)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, PersistentAgentHumanInputRequest.Status.PENDING)

    @patch("api.agent.comms.human_input_requests.run_completion", side_effect=AssertionError("LLM should not run"))
    def test_llm_path_rejects_unauthorized_sender_for_explicit_recipient_request(self, _mock_run_completion):
        collaborator = get_user_model().objects.create_user(
            username="llm-explicit-collaborator",
            email="llm-explicit-collaborator@example.com",
            password="password123",
        )
        AgentCollaborator.objects.create(agent=self.agent, user=collaborator)
        result = create_human_input_request(
            self.agent,
            question="Which reviewer should approve this?",
            raw_options=[],
            recipient={
                "channel": CommsChannel.WEB,
                "address": build_web_user_address(collaborator.id, self.agent.id),
            },
        )
        request_obj = PersistentAgentHumanInputRequest.objects.get(id=result["request_id"])
        stranger = get_user_model().objects.create_user(
            username="llm-explicit-stranger",
            email="llm-explicit-stranger@example.com",
            password="password123",
        )
        reply = self._create_web_reply_from_user(
            user=stranger,
            body="I can review it.",
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertIsNone(resolved)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, PersistentAgentHumanInputRequest.Status.PENDING)

    @patch("api.agent.comms.human_input_requests.run_completion", side_effect=AssertionError("LLM should not run"))
    def test_llm_path_rejects_unauthorized_sender_for_internal_only_request(self, _mock_run_completion):
        self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
        self.agent.save(update_fields=["whitelist_policy"])
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="llm-external@example.com",
        )
        result = create_human_input_request(
            self.agent,
            question="What should we tell the customer?",
            raw_options=[],
        )
        request_obj = PersistentAgentHumanInputRequest.objects.get(id=result["request_id"])
        reply = self._create_cross_channel_message(
            channel=CommsChannel.EMAIL,
            body="Tell them we need a day.",
            sender_address="llm-external@example.com",
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertIsNone(resolved)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, PersistentAgentHumanInputRequest.Status.PENDING)

    def test_email_reply_resolves_single_web_request_when_only_batch_is_open(self):
        request_obj = self._create_request(
            question="What's our next foodie destination?",
            options=[
                {"key": "sushi", "title": "Sushi", "description": "Fresh fish."},
                {"key": "ramen", "title": "Ramen", "description": "Hot noodles."},
            ],
        )
        reply = self._create_cross_channel_message(
            channel=CommsChannel.EMAIL,
            body="Sushi",
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertEqual(resolved.id, request_obj.id)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, PersistentAgentHumanInputRequest.Status.ANSWERED)
        self.assertEqual(request_obj.selected_option_key, "sushi")
        self.assertEqual(list_pending_human_input_requests(self.agent), [])

    def test_cross_channel_reply_from_wrong_sender_does_not_resolve_single_web_batch(self):
        request_obj = self._create_request(question="What's our next foodie destination?")
        reply = self._create_cross_channel_message(
            channel=CommsChannel.EMAIL,
            body="Sushi",
            sender_address="other-person@example.com",
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertIsNone(resolved)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, PersistentAgentHumanInputRequest.Status.PENDING)

    @patch("api.agent.comms.human_input_requests.get_summarization_llm_config")
    @patch("api.agent.comms.human_input_requests.run_completion")
    def test_cross_channel_reply_does_not_resolve_when_multiple_batches_are_open(
        self,
        mock_run_completion,
        mock_get_summarization_llm_config,
    ):
        first_request = self._create_request(question="First question?")
        second_request = self._create_request(question="Second question?")
        mock_get_summarization_llm_config.return_value = ("openai", "openai/gpt-4.1", {})
        mock_run_completion.return_value = make_completion_response(content="no tool call")
        reply = self._create_cross_channel_message(
            channel=CommsChannel.EMAIL,
            body="Take the metro",
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertIsNone(resolved)
        first_request.refresh_from_db()
        second_request.refresh_from_db()
        self.assertEqual(first_request.status, PersistentAgentHumanInputRequest.Status.PENDING)
        self.assertEqual(second_request.status, PersistentAgentHumanInputRequest.Status.PENDING)

    @patch("api.agent.comms.human_input_requests.get_summarization_llm_config")
    @patch("api.agent.comms.human_input_requests.run_completion")
    def test_email_reply_resolves_web_batch_from_numbered_answers(
        self,
        mock_run_completion,
        mock_get_summarization_llm_config,
    ):
        from api.models import PersistentAgentStep

        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Cross-channel batch",
            credits_cost=0,
        )
        mock_get_summarization_llm_config.return_value = ("openai", "openai/gpt-4.1", {})
        mock_run_completion.return_value = make_completion_response(content="no tool call")
        first_request = self._create_request(
            question="What's our next foodie destination?",
            options=[
                {"key": "sushi", "title": "Sushi", "description": "Fresh fish."},
                {"key": "ramen", "title": "Ramen", "description": "Hot noodles."},
            ],
            originating_step=step,
        )
        second_request = self._create_request(
            question="How should we travel to our next spot?",
            options=[],
            originating_step=step,
        )
        reply = self._create_cross_channel_message(
            channel=CommsChannel.EMAIL,
            body="1. Sushi\n2. Take the metro",
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertEqual(resolved.id, first_request.id)
        first_request.refresh_from_db()
        second_request.refresh_from_db()
        self.assertEqual(first_request.status, PersistentAgentHumanInputRequest.Status.ANSWERED)
        self.assertEqual(first_request.selected_option_key, "sushi")
        self.assertEqual(second_request.status, PersistentAgentHumanInputRequest.Status.ANSWERED)
        self.assertEqual(second_request.free_text, "Take the metro")
        self.assertEqual(first_request.raw_reply_message_id, second_request.raw_reply_message_id)
        self.assertEqual(list_pending_human_input_requests(self.agent), [])

    @patch("api.agent.comms.human_input_requests.get_summarization_llm_config")
    @patch("api.agent.comms.human_input_requests.run_completion")
    def test_partial_cross_channel_batch_reply_leaves_unanswered_requests_pending(
        self,
        mock_run_completion,
        mock_get_summarization_llm_config,
    ):
        from api.models import PersistentAgentStep

        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Partial SMS batch",
            credits_cost=0,
        )
        mock_get_summarization_llm_config.return_value = ("openai", "openai/gpt-4.1", {})
        mock_run_completion.return_value = make_completion_response(content="no tool call")
        first_request = self._create_request(
            question="What's our next foodie destination?",
            options=[],
            originating_step=step,
        )
        second_request = self._create_request(
            question="How should we travel to our next spot?",
            options=[],
            originating_step=step,
        )
        UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+15555550199",
            is_verified=True,
        )
        reply = self._create_cross_channel_message(
            channel=CommsChannel.SMS,
            body="2. Take the metro",
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertEqual(resolved.id, second_request.id)
        first_request.refresh_from_db()
        second_request.refresh_from_db()
        self.assertEqual(first_request.status, PersistentAgentHumanInputRequest.Status.PENDING)
        self.assertEqual(second_request.status, PersistentAgentHumanInputRequest.Status.ANSWERED)
        self.assertEqual(second_request.free_text, "Take the metro")

    @patch("api.agent.comms.human_input_requests.get_summarization_llm_config")
    @patch("api.agent.comms.human_input_requests.run_completion")
    def test_batch_reply_from_collaborator_respects_internal_only_authorization(
        self,
        mock_run_completion,
        mock_get_summarization_llm_config,
    ):
        from api.models import PersistentAgentStep

        collaborator = get_user_model().objects.create_user(
            username="batch-collaborator",
            email="batch-collaborator@example.com",
            password="password123",
        )
        mock_get_summarization_llm_config.return_value = ("openai", "openai/gpt-4.1", {})
        mock_run_completion.return_value = make_completion_response(content="no tool call")
        AgentCollaborator.objects.create(agent=self.agent, user=collaborator)
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Collaborator batch",
            credits_cost=0,
        )
        batch_result = execute_request_human_input(
            self.agent,
            {
                "requests": [
                    {"question": "What ships first?"},
                    {"question": "When should we launch?"},
                ],
            },
        )
        request_objects = list(
            PersistentAgentHumanInputRequest.objects.filter(id__in=batch_result["request_ids"]).order_by("created_at")
        )
        for request_obj in request_objects:
            request_obj.originating_step = step
            request_obj.save(update_fields=["originating_step", "updated_at"])

        reply = self._create_web_reply_from_user(
            user=collaborator,
            body="1. Ship onboarding\n2. Next Monday",
        )

        resolved = resolve_human_input_request_for_message(reply)

        self.assertEqual(resolved.id, request_objects[0].id)
        for request_obj in request_objects:
            request_obj.refresh_from_db()
            self.assertEqual(request_obj.status, PersistentAgentHumanInputRequest.Status.ANSWERED)
        self.assertEqual(request_objects[0].free_text, "Ship onboarding")
        self.assertEqual(request_objects[1].free_text, "Next Monday")

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

        self.assertIn("Answered human input responses (historical context only):", block)
        self.assertIn("Do NOT treat these as open tasks", block)
        self.assertIn("What is the status?", block)
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
        self.assertNotIn("reference_code", entry["result"])
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
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _create_cross_channel_message(
        self,
        *,
        channel: str,
        body: str,
        sender_address: str | None = None,
        raw_payload: dict | None = None,
    ) -> PersistentAgentMessage:
        agent_address = "agent@example.com" if channel == CommsChannel.EMAIL else "+15555550100"
        user_address = sender_address or (self.user.email if channel == CommsChannel.EMAIL else "+15555550199")
        agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=channel,
            address=agent_address,
        )
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=channel,
            address=user_address,
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=channel,
            address=user_address,
        )
        return PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=user_endpoint,
            to_endpoint=agent_endpoint,
            conversation=conversation,
            owner_agent=self.agent,
            body=body,
            raw_payload=raw_payload or {"source": "test"},
        )

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
        self.assertNotIn("referenceCode", timeline_payload["pending_human_input_requests"][0])
        self.assertEqual(
            timeline_payload["pending_human_input_requests"][0]["batchId"],
            str(self.request_obj.id),
        )
        self.assertEqual(timeline_payload["pending_human_input_requests"][0]["batchPosition"], 1)
        self.assertEqual(timeline_payload["pending_human_input_requests"][0]["batchSize"], 1)

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

    def test_batch_response_endpoint_submits_group_once(self):
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Collect multiple answers",
            credits_cost=0,
        )
        first_request = PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=self.conversation,
            originating_step=step,
            question="What should I do first?",
            options_json=[
                {"key": "ship", "title": "Ship it", "description": "Move forward now."},
                {"key": "wait", "title": "Wait", "description": "Pause for more info."},
            ],
            input_mode=PersistentAgentHumanInputRequest.InputMode.OPTIONS_PLUS_TEXT,
            requested_via_channel=CommsChannel.WEB,
        )
        second_request = PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=self.conversation,
            originating_step=step,
            question="What should I do second?",
            options_json=[],
            input_mode=PersistentAgentHumanInputRequest.InputMode.FREE_TEXT_ONLY,
            requested_via_channel=CommsChannel.WEB,
        )

        response = self.client.post(
            f"/console/api/agents/{self.agent.id}/human-input-requests/respond-batch/",
            data=json.dumps(
                {
                    "responses": [
                        {"request_id": str(first_request.id), "selected_option_key": "ship"},
                        {"request_id": str(second_request.id), "free_text": "Follow up with a summary."},
                    ]
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["event"]["kind"], "message")
        self.assertEqual(payload["event"]["message"]["channel"], CommsChannel.WEB)
        self.assertEqual(
            payload["event"]["message"]["bodyText"],
            "Question: What should I do first?\n"
            "Answer: Ship it\n\n"
            "Question: What should I do second?\n"
            "Answer: Follow up with a summary.",
        )
        self.assertEqual(len(payload["pending_human_input_requests"]), 1)
        self.assertEqual(payload["pending_human_input_requests"][0]["id"], str(self.request_obj.id))

        first_request.refresh_from_db()
        second_request.refresh_from_db()
        self.assertEqual(first_request.status, PersistentAgentHumanInputRequest.Status.ANSWERED)
        self.assertEqual(first_request.selected_option_key, "ship")
        self.assertEqual(second_request.status, PersistentAgentHumanInputRequest.Status.ANSWERED)
        self.assertEqual(second_request.free_text, "Follow up with a summary.")
        self.assertEqual(first_request.raw_reply_message_id, second_request.raw_reply_message_id)
        reply_message = PersistentAgentMessage.objects.get(id=first_request.raw_reply_message_id)
        self.assertEqual(reply_message.conversation.channel, CommsChannel.WEB)
        self.assertEqual(reply_message.conversation.address, self.user_address)
        self.assertEqual(reply_message.from_endpoint.address, self.user_address)
        self.assertEqual(reply_message.to_endpoint.address, self.agent_address)

    def test_console_batch_response_to_email_request_records_web_reply(self):
        email_agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.com",
        )
        email_user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address=self.user.email,
        )
        email_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=self.user.email,
        )
        requested_message = PersistentAgentMessage.objects.create(
            is_outbound=True,
            from_endpoint=email_agent_endpoint,
            to_endpoint=email_user_endpoint,
            conversation=email_conversation,
            owner_agent=self.agent,
            body="Please answer these questions.",
        )
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Collect email answers",
            credits_cost=0,
        )
        first_request = PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=email_conversation,
            originating_step=step,
            requested_message=requested_message,
            question="How many candidates?",
            options_json=[
                {"key": "large", "title": "Large Batch", "description": "Find many candidates."},
            ],
            input_mode=PersistentAgentHumanInputRequest.InputMode.OPTIONS_PLUS_TEXT,
            requested_via_channel=CommsChannel.EMAIL,
        )
        second_request = PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=email_conversation,
            originating_step=step,
            requested_message=requested_message,
            question="What format?",
            options_json=[],
            input_mode=PersistentAgentHumanInputRequest.InputMode.FREE_TEXT_ONLY,
            requested_via_channel=CommsChannel.EMAIL,
        )

        response = self.client.post(
            f"/console/api/agents/{self.agent.id}/human-input-requests/respond-batch/",
            data=json.dumps(
                {
                    "responses": [
                        {"request_id": str(first_request.id), "selected_option_key": "large"},
                        {"request_id": str(second_request.id), "free_text": "Spreadsheet"},
                    ]
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["event"]["message"]["channel"], CommsChannel.WEB)

        first_request.refresh_from_db()
        second_request.refresh_from_db()
        reply_message = PersistentAgentMessage.objects.get(id=first_request.raw_reply_message_id)
        self.assertEqual(reply_message.id, second_request.raw_reply_message_id)
        self.assertEqual(reply_message.conversation.channel, CommsChannel.WEB)
        self.assertEqual(reply_message.conversation.address, self.user_address)
        self.assertEqual(reply_message.from_endpoint.channel, CommsChannel.WEB)
        self.assertEqual(reply_message.from_endpoint.address, self.user_address)
        self.assertEqual(reply_message.to_endpoint.channel, CommsChannel.WEB)
        self.assertEqual(reply_message.to_endpoint.address, self.agent_address)
        self.assertEqual(first_request.requested_via_channel, CommsChannel.EMAIL)
        self.assertEqual(second_request.requested_via_channel, CommsChannel.EMAIL)

    def test_pending_action_payload_groups_human_input_batches(self):
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Collect multiple answers",
            credits_cost=0,
        )
        first_request = PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=self.conversation,
            originating_step=step,
            question="What should I do first?",
            options_json=[
                {"key": "ship", "title": "Ship it", "description": "Move forward now."},
            ],
            input_mode=PersistentAgentHumanInputRequest.InputMode.OPTIONS_PLUS_TEXT,
            requested_via_channel=CommsChannel.WEB,
        )
        second_request = PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=self.conversation,
            originating_step=step,
            question="What should I do second?",
            options_json=[],
            input_mode=PersistentAgentHumanInputRequest.InputMode.FREE_TEXT_ONLY,
            requested_via_channel=CommsChannel.WEB,
        )

        actions = list_pending_action_requests(self.agent, self.user)
        batch_action = next(action for action in actions if action["id"] == f"human_input:{step.id}")

        self.assertEqual(batch_action["kind"], "human_input")
        self.assertEqual(batch_action["count"], 2)
        self.assertEqual(
            [request["id"] for request in batch_action["requests"]],
            [str(first_request.id), str(second_request.id)],
        )
        self.assertEqual(
            [request["batchPosition"] for request in batch_action["requests"]],
            [1, 2],
        )

    def test_attach_originating_step_emits_corrected_batch_update(self):
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Collect multiple answers",
            credits_cost=0,
        )
        first_request = PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=self.conversation,
            question="What should I do first?",
            options_json=[
                {"key": "ship", "title": "Ship it", "description": "Move forward now."},
            ],
            input_mode=PersistentAgentHumanInputRequest.InputMode.OPTIONS_PLUS_TEXT,
            requested_via_channel=CommsChannel.WEB,
        )
        second_request = PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=self.conversation,
            question="What should I do second?",
            options_json=[],
            input_mode=PersistentAgentHumanInputRequest.InputMode.FREE_TEXT_ONLY,
            requested_via_channel=CommsChannel.WEB,
        )

        with patch("api.agent.comms.human_input_requests._emit_pending_human_input_updates") as mock_emit:
            with self.captureOnCommitCallbacks(execute=True):
                attach_originating_step_from_result(
                    step,
                    {"request_ids": [str(first_request.id), str(second_request.id)]},
                )

        first_request.refresh_from_db()
        second_request.refresh_from_db()
        self.assertEqual(first_request.originating_step_id, step.id)
        self.assertEqual(second_request.originating_step_id, step.id)
        mock_emit.assert_called_once_with(self.agent.id)

        actions = list_pending_action_requests(self.agent, self.user)
        batch_action = next(action for action in actions if action["id"] == f"human_input:{step.id}")
        self.assertEqual(batch_action["count"], 2)
        self.assertEqual(
            [request["id"] for request in batch_action["requests"]],
            [str(first_request.id), str(second_request.id)],
        )

    def test_timeline_pending_requests_clear_after_cross_channel_resolution(self):
        resolve_human_input_request_for_message(
            self._create_cross_channel_message(
                channel=CommsChannel.EMAIL,
                body="Ship it",
            )
        )

        timeline_response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(timeline_response.status_code, 200)
        self.assertEqual(timeline_response.json()["pending_human_input_requests"], [])
