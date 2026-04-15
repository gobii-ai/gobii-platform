from __future__ import annotations

from datetime import timedelta
from email.message import EmailMessage
import json
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock, patch

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone
from waffle.testutils import override_flag

from api.agent.files.attachment_helpers import resolve_filespace_attachments
from api.agent.files.filespace_service import write_bytes_to_dir
from api.agent.comms.imap_adapter import ImapEmailAdapter
from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message, ingest_inbound_webhook_message
from api.agent.core.prompt_context import build_prompt_context
from api.agent.peer_comm import PeerMessagingService
from api.agent.tools.sqlite_kanban import KanbanBoardSnapshot, KanbanCardChange
from api.models import (
    AgentCollaborator,
    AgentPeerLink,
    AgentSpawnRequest,
    BrowserUseAgent,
    BrowserUseAgentTask,
    CommsAllowlistRequest,
    CommsChannel,
    DeliveryStatus,
    PersistentAgent,
    PersistentAgentKanbanCard,
    PersistentAgentKanbanEvent,
    PersistentAgentCompletion,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentHumanInputRequest,
    PersistentAgentInboundWebhook,
    PersistentAgentMessage,
    PersistentAgentMessageAttachment,
    PersistentAgentSecret,
    PersistentAgentStep,
    PersistentAgentToolCall,
    PersistentAgentWebSession,
    MCPServerConfig,
    PipedreamAppSelection,
    build_web_agent_address,
    build_web_user_address,
)
from api.agent.core.processing_flags import clear_processing_queued_flag, set_processing_queued_flag
from api.agent.core.processing_flags import (
    clear_processing_stop_requested,
    enqueue_pending_agent,
    is_agent_pending,
    is_processing_stop_requested,
)
from api.agent.tools.web_chat_sender import execute_send_chat_message
from api.services.pipedream_apps import get_owner_apps_state
from api.services.web_sessions import heartbeat_web_session, start_web_session
from console.agent_chat.kanban_events import persist_kanban_event
from console.agent_chat.timeline import build_processing_snapshot
from console.agent_chat.timeline import fetch_timeline_window
from console.agent_chat.timeline import serialize_kanban_event
from util.onboarding import (
    TRIAL_ONBOARDING_PENDING_SESSION_KEY,
    TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY,
    TRIAL_ONBOARDING_TARGET_AGENT_UI,
    TRIAL_ONBOARDING_TARGET_SESSION_KEY,
)
from util.personal_signup_preview import SIGNUP_PREVIEW_EXISTING_AGENT_MESSAGE
from util.trial_enforcement import PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE
from util.analytics import AnalyticsEvent

CHANNEL_LAYER_SETTINGS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}


@override_settings(
    CHANNEL_LAYERS=CHANNEL_LAYER_SETTINGS,
    PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False,
)
class AgentChatAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="agent-owner",
            email="owner@example.com",
            password="password123",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Browser Agent")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Console Tester",
            charter="Do useful things",
            browser_use_agent=cls.browser_agent,
        )

        cls.user_address = build_web_user_address(cls.user.id, cls.agent.id)
        cls.agent_address = build_web_agent_address(cls.agent.id)

        cls.agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=cls.agent,
            channel=CommsChannel.WEB,
            address=cls.agent_address,
            is_primary=True,
        )
        cls.user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.WEB,
            address=cls.user_address,
            is_primary=False,
        )
        cls.conversation = PersistentAgentConversation.objects.create(
            owner_agent=cls.agent,
            channel=CommsChannel.WEB,
            address=cls.user_address,
        )

        PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=cls.user_endpoint,
            conversation=cls.conversation,
            body="Hello from the owner",
            owner_agent=cls.agent,
        )

        step = PersistentAgentStep.objects.create(
            agent=cls.agent,
            description="Send recap email",
        )
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="send_email",
            tool_params={"to": "user@example.com"},
            result="queued",
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    @tag("batch_agent_chat")
    def test_quick_create_prefers_web_channel(self):
        message_text = "Plan my weekly operating cadence"
        response = self.client.post(
            "/console/api/agents/create/",
            data=json.dumps({"message": message_text, "preferred_llm_tier": "standard"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        created_agent = PersistentAgent.objects.get(id=payload["agent_id"])
        self.assertIsNotNone(created_agent.preferred_contact_endpoint)
        self.assertEqual(created_agent.preferred_contact_endpoint.channel, CommsChannel.WEB)

        expected_sender = build_web_user_address(self.user.id, created_agent.id)
        expected_recipient = build_web_agent_address(created_agent.id)
        self.assertEqual(created_agent.preferred_contact_endpoint.address, expected_sender)

        seeded_message = (
            PersistentAgentMessage.objects.filter(owner_agent=created_agent, body=message_text)
            .order_by("-timestamp")
            .first()
        )
        self.assertIsNotNone(seeded_message)
        self.assertEqual(seeded_message.from_endpoint.channel, CommsChannel.WEB)
        self.assertEqual(seeded_message.from_endpoint.address, expected_sender)
        self.assertIsNotNone(seeded_message.to_endpoint)
        self.assertEqual(seeded_message.to_endpoint.address, expected_recipient)
        self.assertEqual(seeded_message.conversation.channel, CommsChannel.WEB)
        self.assertEqual(seeded_message.conversation.address, expected_sender)

    @override_settings(ENABLE_DEFAULT_AGENT_EMAIL=True, DEFAULT_AGENT_EMAIL_DOMAIN="agents.test")
    @tag("batch_agent_chat")
    def test_quick_create_allows_email_preference_override(self):
        message_text = "Build me an agent from a stored CTA intent"
        response = self.client.post(
            "/console/api/agents/create/",
            data=json.dumps(
                {
                    "message": message_text,
                    "preferred_llm_tier": "standard",
                    "preferred_contact_method": "email",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        created_agent = PersistentAgent.objects.get(id=payload["agent_id"])
        self.assertIsNotNone(created_agent.preferred_contact_endpoint)
        self.assertEqual(created_agent.preferred_contact_endpoint.channel, CommsChannel.EMAIL)
        self.assertEqual(created_agent.preferred_contact_endpoint.address, self.user.email)

        seeded_message = (
            PersistentAgentMessage.objects.filter(owner_agent=created_agent, body=message_text)
            .order_by("-timestamp")
            .first()
        )
        self.assertIsNotNone(seeded_message)
        self.assertEqual(seeded_message.from_endpoint.channel, CommsChannel.EMAIL)
        self.assertEqual(seeded_message.from_endpoint.address, self.user.email)
        self.assertIsNotNone(seeded_message.to_endpoint)
        self.assertEqual(seeded_message.to_endpoint.channel, CommsChannel.EMAIL)
        self.assertEqual(seeded_message.conversation.channel, CommsChannel.EMAIL)
        self.assertEqual(seeded_message.conversation.address, self.user.email)

    @tag("batch_agent_chat")
    def test_quick_create_without_account_email(self):
        user_model = get_user_model()
        no_email_user = user_model.objects.create_user(
            username="quick-create-no-email",
            email="",
            password="password123",
        )
        client = Client()
        client.force_login(no_email_user)

        message_text = "Build a deeply reliable research assistant"
        response = client.post(
            "/console/api/agents/create/",
            data=json.dumps({"message": message_text, "preferred_llm_tier": "standard"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        created_agent = PersistentAgent.objects.get(id=payload["agent_id"])
        self.assertIsNotNone(created_agent.preferred_contact_endpoint)
        self.assertEqual(created_agent.preferred_contact_endpoint.channel, CommsChannel.WEB)

        expected_sender = build_web_user_address(no_email_user.id, created_agent.id)
        self.assertEqual(created_agent.preferred_contact_endpoint.address, expected_sender)

        seeded_message = (
            PersistentAgentMessage.objects.filter(owner_agent=created_agent, body=message_text)
            .order_by("-timestamp")
            .first()
        )
        self.assertIsNotNone(seeded_message)
        self.assertEqual(seeded_message.from_endpoint.channel, CommsChannel.WEB)
        self.assertEqual(seeded_message.conversation.channel, CommsChannel.WEB)

    @tag("batch_agent_chat")
    def test_quick_create_email_preference_without_account_email_falls_back_to_web(self):
        user_model = get_user_model()
        no_email_user = user_model.objects.create_user(
            username="quick-create-email-fallback",
            email="",
            password="password123",
        )
        client = Client()
        client.force_login(no_email_user)

        message_text = "Create an agent even without an account email"
        response = client.post(
            "/console/api/agents/create/",
            data=json.dumps(
                {
                    "message": message_text,
                    "preferred_llm_tier": "standard",
                    "preferred_contact_method": "email",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        created_agent = PersistentAgent.objects.get(id=payload["agent_id"])
        self.assertIsNotNone(created_agent.preferred_contact_endpoint)
        self.assertEqual(created_agent.preferred_contact_endpoint.channel, CommsChannel.WEB)

        expected_sender = build_web_user_address(no_email_user.id, created_agent.id)
        expected_recipient = build_web_agent_address(created_agent.id)
        self.assertEqual(created_agent.preferred_contact_endpoint.address, expected_sender)

        seeded_message = (
            PersistentAgentMessage.objects.filter(owner_agent=created_agent, body=message_text)
            .order_by("-timestamp")
            .first()
        )
        self.assertIsNotNone(seeded_message)
        self.assertEqual(seeded_message.from_endpoint.channel, CommsChannel.WEB)
        self.assertEqual(seeded_message.from_endpoint.address, expected_sender)
        self.assertIsNotNone(seeded_message.to_endpoint)
        self.assertEqual(seeded_message.to_endpoint.address, expected_recipient)
        self.assertEqual(seeded_message.conversation.channel, CommsChannel.WEB)
        self.assertEqual(seeded_message.conversation.address, expected_sender)

    @tag("batch_agent_chat")
    def test_quick_create_rejects_invalid_preferred_contact_method(self):
        response = self.client.post(
            "/console/api/agents/create/",
            data=json.dumps(
                {
                    "message": "Create from immersive app",
                    "preferred_contact_method": "sms",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json().get("error"),
            "Preferred contact method must be 'email' or 'web'.",
        )

    @tag("batch_agent_chat")
    def test_quick_create_invalid_org_override_returns_context_error(self):
        response = self.client.post(
            "/console/api/agents/create/",
            data=json.dumps({"message": "Create from immersive app"}),
            content_type="application/json",
            HTTP_X_GOBII_CONTEXT_TYPE="organization",
            HTTP_X_GOBII_CONTEXT_ID="not-a-uuid",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json().get("error"), "Invalid context override.")

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    @tag("batch_agent_chat")
    def test_quick_create_returns_trial_onboarding_metadata_when_trial_required(self):
        response = self.client.post(
            "/console/api/agents/create/",
            data=json.dumps({"message": "Create from immersive app"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload.get("error"), PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE)
        self.assertEqual(payload.get("onboarding_target"), TRIAL_ONBOARDING_TARGET_AGENT_UI)
        self.assertTrue(payload.get("requires_plan_selection"))

        session = self.client.session
        self.assertTrue(session.get(TRIAL_ONBOARDING_PENDING_SESSION_KEY))
        self.assertEqual(
            session.get(TRIAL_ONBOARDING_TARGET_SESSION_KEY),
            TRIAL_ONBOARDING_TARGET_AGENT_UI,
        )
        self.assertTrue(session.get(TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY))

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True,
    )
    @tag("batch_agent_chat")
    @patch("console.agent_creation.Analytics.track_event")
    def test_quick_create_allows_signup_preview_creation_when_processing_limit_flag_enabled(
        self,
        mock_track_event,
    ):
        fresh_user = get_user_model().objects.create_user(
            username="preview-user",
            email="preview@example.com",
            password="password123",
        )
        preview_client = Client()
        preview_client.force_login(fresh_user)
        with (
            override_flag("personal_agent_signup_preview_processing_limit", active=True),
            patch("console.agent_creation.can_user_use_personal_agents_and_api", return_value=False),
            patch("util.personal_signup_preview.can_user_use_personal_agents_and_api", return_value=False),
            patch("console.agent_creation.process_agent_events_task.delay"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                response = preview_client.post(
                    "/console/api/agents/create/",
                    data=json.dumps({"message": "Create from immersive app"}),
                    content_type="application/json",
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        created_agent = PersistentAgent.objects.get(id=payload["agent_id"])
        self.assertEqual(
            created_agent.signup_preview_state,
            PersistentAgent.SignupPreviewState.AWAITING_FIRST_REPLY_PAUSE,
        )
        event_names = [call.kwargs.get("event") for call in mock_track_event.call_args_list]
        self.assertIn(AnalyticsEvent.SIGNUP_PREVIEW_AGENT_CREATED, event_names)

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True,
    )
    @tag("batch_agent_chat")
    def test_quick_create_blocks_second_signup_preview_agent_without_plan(self):
        fresh_user = get_user_model().objects.create_user(
            username="preview-repeat-user",
            email="preview-repeat@example.com",
            password="password123",
        )
        preview_client = Client()
        preview_client.force_login(fresh_user)
        with (
            override_flag("personal_agent_signup_preview_processing_limit", active=True),
            patch("console.agent_creation.can_user_use_personal_agents_and_api", return_value=False),
            patch("util.personal_signup_preview.can_user_use_personal_agents_and_api", return_value=False),
        ):
            first_response = preview_client.post(
                "/console/api/agents/create/",
                data=json.dumps({"message": "Create from immersive app"}),
                content_type="application/json",
            )
            second_response = preview_client.post(
                "/console/api/agents/create/",
                data=json.dumps({"message": "Create another preview"}),
                content_type="application/json",
            )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 400)
        self.assertEqual(second_response.json().get("error"), SIGNUP_PREVIEW_EXISTING_AGENT_MESSAGE)
        self.assertEqual(
            PersistentAgent.objects.filter(
                user=fresh_user,
                organization__isnull=True,
                is_deleted=False,
            ).count(),
            1,
        )

    @tag("batch_agent_chat")
    @patch("api.services.signup_preview.Analytics.track_event")
    def test_first_outbound_message_transitions_signup_preview_to_completion_wait(self, mock_track_event):
        self.agent.signup_preview_state = PersistentAgent.SignupPreviewState.AWAITING_FIRST_REPLY_PAUSE
        self.agent.save(update_fields=["signup_preview_state", "updated_at"])

        with self.captureOnCommitCallbacks(execute=True):
            PersistentAgentMessage.objects.create(
                is_outbound=True,
                from_endpoint=self.agent_endpoint,
                conversation=self.conversation,
                body="Here's the first preview reply.",
                owner_agent=self.agent,
            )

        self.agent.refresh_from_db()
        self.assertEqual(
            self.agent.signup_preview_state,
            PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION,
        )
        self.assertEqual(mock_track_event.call_count, 1)
        self.assertEqual(
            mock_track_event.call_args.kwargs["event"],
            AnalyticsEvent.SIGNUP_PREVIEW_PAUSED_AFTER_FIRST_REPLY,
        )
        self.assertEqual(
            mock_track_event.call_args.kwargs["properties"]["signup_preview_state"],
            PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION,
        )

    @tag("batch_agent_chat")
    def test_quick_create_ignores_unsupported_tier_selection(self):
        response = self.client.post(
            "/console/api/agents/create/",
            data=json.dumps({"message": "Create with stale tier", "preferred_llm_tier": "lite"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        created_agent = PersistentAgent.objects.get(id=payload["agent_id"])
        self.assertIsNotNone(created_agent.preferred_llm_tier)
        self.assertEqual(created_agent.preferred_llm_tier.key, "standard")

    @override_settings(PIPEDREAM_PREFETCH_APPS="trello")
    @tag("batch_agent_chat")
    def test_quick_create_enables_selected_pipedream_apps(self):
        PipedreamAppSelection.objects.create(
            user=self.user,
            selected_app_slugs=["notion"],
        )

        response = self.client.post(
            "/console/api/agents/create/",
            data=json.dumps(
                {
                    "message": "Create with integrations",
                    "selected_pipedream_app_slugs": ["slack", "notion", "trello"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        selection = PipedreamAppSelection.objects.get(user=self.user)
        self.assertEqual(selection.selected_app_slugs, ["notion", "slack"])

        owner_state = get_owner_apps_state(
            MCPServerConfig.Scope.USER,
            self.user.get_full_name() or self.user.username,
            owner_user=self.user,
        )
        self.assertEqual(owner_state.effective_app_slugs, ["trello", "notion", "slack"])

    @tag("batch_agent_chat")
    def test_timeline_endpoint_returns_expected_events(self):
        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        events = payload.get("events", [])
        self.assertGreaterEqual(len(events), 2)
        kinds = {event.get("kind") for event in events}
        self.assertIn("message", kinds)
        self.assertIn("steps", kinds)
        message_event = next(event for event in events if event["kind"] == "message")
        self.assertEqual(message_event["message"]["bodyText"], "Hello from the owner")
        self.assertEqual(message_event["message"]["senderUserId"], self.user.id)
        self.assertEqual(message_event["message"]["senderName"], self.user.email)
        self.assertEqual(message_event["message"]["senderAddress"], self.user_address)
        tool_cluster = next(event for event in events if event["kind"] == "steps")
        self.assertEqual(tool_cluster["entries"][0]["toolName"], "send_email")
        self.assertTrue(payload.get("newest_cursor"))
        self.assertIsNotNone(payload.get("processing_active"))
        snapshot = payload.get("processing_snapshot")
        self.assertIsInstance(snapshot, dict)
        self.assertIn("active", snapshot)
        self.assertIn("webTasks", snapshot)
        self.assertIsInstance(snapshot.get("webTasks"), list)

    @tag("batch_agent_chat")
    def test_timeline_includes_pending_action_requests_for_manager(self):
        step = PersistentAgentStep.objects.create(agent=self.agent, description="Need operator answers")
        PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=self.conversation,
            originating_step=step,
            question="Which plan should we use?",
            options_json=[{"key": "pro", "title": "Pro", "description": "Use the Pro plan"}],
            input_mode=PersistentAgentHumanInputRequest.InputMode.OPTIONS_PLUS_TEXT,
            requested_via_channel=CommsChannel.WEB,
        )
        AgentSpawnRequest.objects.create(
            agent=self.agent,
            requested_charter="Handle procurement approvals.",
            handoff_message="Take over vendor approvals.",
        )
        requested_secret = PersistentAgentSecret(
            agent=self.agent,
            name="Procurement API Key",
            description="Used for procurement sync",
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
            domain_pattern="https://procurement.example.com",
            requested=True,
        )
        requested_secret.key = "procurement_api_key"
        requested_secret.encrypted_value = b""
        requested_secret.save()
        CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="approver@example.com",
            reason="Need procurement approval",
            purpose="Approve vendor contract",
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [item.get("kind") for item in payload.get("pending_action_requests", [])],
            ["human_input", "spawn_request", "requested_secrets", "contact_requests"],
        )
        self.assertEqual(len(payload.get("pending_human_input_requests", [])), 1)

    @tag("batch_agent_chat")
    def test_timeline_filters_manager_only_pending_actions_for_collaborator(self):
        collaborator = get_user_model().objects.create_user(
            username="timeline-collaborator",
            email="timeline-collaborator@example.com",
            password="password123",
        )
        AgentCollaborator.objects.create(
            agent=self.agent,
            user=collaborator,
            invited_by=self.user,
        )
        step = PersistentAgentStep.objects.create(agent=self.agent, description="Need operator answers")
        PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=self.conversation,
            originating_step=step,
            question="Which plan should we use?",
            options_json=[],
            input_mode=PersistentAgentHumanInputRequest.InputMode.FREE_TEXT_ONLY,
            requested_via_channel=CommsChannel.WEB,
        )
        AgentSpawnRequest.objects.create(
            agent=self.agent,
            requested_charter="Handle procurement approvals.",
            handoff_message="Take over vendor approvals.",
        )
        requested_secret = PersistentAgentSecret(
            agent=self.agent,
            name="Procurement API Key",
            description="Used for procurement sync",
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
            domain_pattern="https://procurement.example.com",
            requested=True,
        )
        requested_secret.key = "procurement_api_key"
        requested_secret.encrypted_value = b""
        requested_secret.save()
        CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="approver@example.com",
            reason="Need procurement approval",
            purpose="Approve vendor contract",
        )

        collaborator_client = Client()
        collaborator_client.force_login(collaborator)
        response = collaborator_client.get(f"/console/api/agents/{self.agent.id}/timeline/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [item.get("kind") for item in payload.get("pending_action_requests", [])],
            ["human_input"],
        )

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    @tag("batch_agent_chat")
    @patch("console.agent_chat.access.can_user_use_personal_agents_and_api", return_value=False)
    @patch("console.agent_chat.access.can_user_access_personal_agent_chat", return_value=True)
    def test_timeline_includes_requested_secrets_for_delinquent_personal_owner(
        self,
        _mock_can_access_personal_agent_chat,
        _mock_can_use_personal_agents_and_api,
    ):
        requested_secret = PersistentAgentSecret(
            agent=self.agent,
            name="Procurement API Key",
            description="Used for procurement sync",
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
            domain_pattern="https://procurement.example.com",
            requested=True,
        )
        requested_secret.key = "procurement_api_key"
        requested_secret.encrypted_value = b""
        requested_secret.save()

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(
            "requested_secrets",
            [item.get("kind") for item in payload.get("pending_action_requests", [])],
        )

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    @tag("batch_agent_chat")
    @patch("console.agent_chat.access.can_user_use_personal_agents_and_api", return_value=False)
    @patch("console.agent_chat.access.can_user_access_personal_agent_chat", return_value=True)
    @patch("console.api_views.process_agent_events_task.delay")
    def test_requested_secrets_fulfill_api_allows_delinquent_personal_owner(
        self,
        mock_delay,
        _mock_can_access_personal_agent_chat,
        _mock_can_use_personal_agents_and_api,
    ):
        secret = PersistentAgentSecret(
            agent=self.agent,
            name="Procurement API Key",
            description="Used for procurement sync",
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
            domain_pattern="https://procurement.example.com",
            requested=True,
        )
        secret.key = "procurement_api_key"
        secret.encrypted_value = b""
        secret.save()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/console/api/agents/{self.agent.id}/requested-secrets/fulfill/",
                data=json.dumps(
                    {
                        "values": {str(secret.id): "super-secret-value"},
                        "make_global": False,
                    }
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        secret.refresh_from_db()
        self.assertFalse(secret.requested)
        self.assertEqual(secret.get_value(), "super-secret-value")
        mock_delay.assert_called_once_with(str(self.agent.id))

    @tag("batch_agent_chat")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_timeline_serializes_inbound_webhook_messages(self, mock_delay):
        webhook = PersistentAgentInboundWebhook.objects.create(
            agent=self.agent,
            name="Ops Deploy",
        )
        with self.captureOnCommitCallbacks(execute=True):
            ingest_inbound_webhook_message(
                webhook,
                body='{\n  "status": "ok"\n}',
                raw_payload={
                    "source": "inbound_webhook",
                    "source_kind": "webhook",
                    "source_label": "Ops Deploy",
                    "content_type": "application/json",
                    "method": "POST",
                    "payload_kind": "json",
                    "json_payload": {"status": "ok"},
                    "query_params": {"source": "ci"},
                    "webhook_id": str(webhook.id),
                    "webhook_name": webhook.name,
                },
            )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        events = response.json().get("events", [])
        message_event = next(
            event for event in events
            if event.get("kind") == "message" and event.get("message", {}).get("sourceKind") == "webhook"
        )
        message_payload = message_event["message"]
        self.assertEqual(message_payload["sourceKind"], "webhook")
        self.assertEqual(message_payload["sourceLabel"], "Ops Deploy")
        self.assertEqual(message_payload["senderName"], "Ops Deploy")
        self.assertEqual(message_payload["channel"], "other")
        self.assertEqual(message_payload["bodyText"], '{\n  "status": "ok"\n}')
        self.assertEqual(message_payload["webhookMeta"]["payloadKind"], "json")
        self.assertEqual(message_payload["webhookMeta"]["payload"], {"status": "ok"})
        self.assertEqual(message_payload["webhookMeta"]["contentType"], "application/json")
        mock_delay.assert_called_once_with(str(self.agent.id))

    @tag("batch_agent_chat")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_inbound_webhook_messages_group_by_webhook_thread(self, mock_delay):
        first_hook = PersistentAgentInboundWebhook.objects.create(agent=self.agent, name="Build Hook")
        second_hook = PersistentAgentInboundWebhook.objects.create(agent=self.agent, name="Deploy Hook")

        with self.captureOnCommitCallbacks(execute=True):
            ingest_inbound_webhook_message(
                first_hook,
                body="Build started",
                raw_payload={"source": "inbound_webhook", "source_kind": "webhook", "source_label": first_hook.name},
            )
            ingest_inbound_webhook_message(
                first_hook,
                body="Build finished",
                raw_payload={"source": "inbound_webhook", "source_kind": "webhook", "source_label": first_hook.name},
            )
            ingest_inbound_webhook_message(
                second_hook,
                body="Deploy finished",
                raw_payload={"source": "inbound_webhook", "source_kind": "webhook", "source_label": second_hook.name},
            )

        first_messages = PersistentAgentMessage.objects.filter(owner_agent=self.agent, conversation__display_name=first_hook.name)
        second_messages = PersistentAgentMessage.objects.filter(owner_agent=self.agent, conversation__display_name=second_hook.name)
        self.assertEqual(first_messages.count(), 2)
        self.assertEqual(second_messages.count(), 1)
        self.assertEqual(first_messages.values_list("conversation_id", flat=True).distinct().count(), 1)
        self.assertEqual(second_messages.values_list("conversation_id", flat=True).distinct().count(), 1)
        self.assertEqual(mock_delay.call_count, 3)

    @tag("batch_agent_chat")
    @patch("api.agent.core.prompt_context.ensure_steps_compacted")
    @patch("api.agent.core.prompt_context.ensure_comms_compacted")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_prompt_context_uses_webhook_label_for_other_channel_messages(self, mock_delay, _mock_comms_compacted, _mock_steps_compacted):
        webhook = PersistentAgentInboundWebhook.objects.create(agent=self.agent, name="Pager Trigger")
        with self.captureOnCommitCallbacks(execute=True):
            ingest_inbound_webhook_message(
                webhook,
                body="Alert fired",
                raw_payload={
                    "source": "inbound_webhook",
                    "source_kind": "webhook",
                    "source_label": webhook.name,
                    "content_type": "application/json",
                    "method": "POST",
                    "payload_kind": "json",
                    "json_payload": {"alert": "fired"},
                    "query_params": {"priority": "high"},
                    "webhook_name": webhook.name,
                },
            )

        context, _, _ = build_prompt_context(self.agent)
        user_message = next((message for message in context if message["role"] == "user"), None)
        self.assertIsNotNone(user_message)
        self.assertIn('Inbound webhook "Pager Trigger" triggered:', user_message["content"])
        self.assertIn("Content-Type: application/json", user_message["content"])
        self.assertIn('Query params: {"priority": "high"}', user_message["content"])
        self.assertNotIn("On other, you received a message", user_message["content"])
        mock_delay.assert_called_once_with(str(self.agent.id))

    @tag("batch_agent_chat")
    def test_timeline_includes_create_image_preview_url(self):
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Create hero image",
        )
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="create_image",
            tool_params={
                "prompt": "Minimal poster art",
                "file_path": "/exports/generated-image.png",
            },
            result=json.dumps(
                {
                    "status": "ok",
                    "file": "$[/exports/generated-image.png]",
                }
            ),
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        entries = [
            entry
            for event in payload.get("events", [])
            if event.get("kind") == "steps"
            for entry in event.get("entries", [])
        ]
        image_entry = next(entry for entry in entries if entry.get("toolName") == "create_image")

        preview_url = image_entry.get("createImageUrl")
        self.assertIsInstance(preview_url, str)
        parsed = urlparse(preview_url)
        expected_path = reverse("console_agent_fs_download", kwargs={"agent_id": self.agent.id})
        self.assertEqual(parsed.path, expected_path)
        self.assertEqual(parse_qs(parsed.query).get("path"), ["/exports/generated-image.png"])

    @tag("batch_agent_chat")
    def test_timeline_includes_create_video_preview_url(self):
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Create teaser video",
        )
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="create_video",
            tool_params={
                "prompt": "A neon city at dusk",
                "file_path": "/exports/generated-video.mp4",
            },
            result=json.dumps(
                {
                    "status": "ok",
                    "file": "$[/exports/generated-video.mp4]",
                }
            ),
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        entries = [
            entry
            for event in payload.get("events", [])
            if event.get("kind") == "steps"
            for entry in event.get("entries", [])
        ]
        video_entry = next(entry for entry in entries if entry.get("toolName") == "create_video")

        preview_url = video_entry.get("createVideoUrl")
        self.assertIsInstance(preview_url, str)
        parsed = urlparse(preview_url)
        expected_path = reverse("console_agent_fs_download", kwargs={"agent_id": self.agent.id})
        self.assertEqual(parsed.path, expected_path)
        self.assertEqual(parse_qs(parsed.query).get("path"), ["/exports/generated-video.mp4"])

    @tag("batch_agent_chat")
    def test_timeline_has_no_older_when_under_limit(self):
        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertFalse(payload.get("has_more_older"))

    @tag("batch_agent_chat")
    @patch("console.agent_chat.timeline.get_processing_heartbeat")
    def test_processing_snapshot_uses_heartbeat(self, mock_get_heartbeat):
        mock_get_heartbeat.return_value = {"last_seen": 123.0}

        snapshot = build_processing_snapshot(self.agent)

        self.assertTrue(snapshot.active)

    @tag("batch_agent_chat")
    def test_processing_snapshot_includes_next_scheduled_at_when_idle(self):
        self.agent.schedule = "@hourly"
        self.agent.execution_environment = getattr(settings, "GOBII_RELEASE_ENV", "local")
        self.agent.is_active = True
        self.agent.life_state = PersistentAgent.LifeState.ACTIVE
        self.agent.save(update_fields=["schedule", "execution_environment", "is_active", "life_state"])

        snapshot = build_processing_snapshot(self.agent)

        self.assertIsNotNone(snapshot.next_scheduled_at)

    @tag("batch_agent_chat")
    def test_timeline_includes_thinking_events(self):
        completion = PersistentAgentCompletion.objects.create(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.ORCHESTRATOR,
            thinking_content="Reasoned path",
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        events = payload.get("events", [])
        thinking_event = next(event for event in events if event.get("kind") == "thinking")

        self.assertEqual(thinking_event.get("reasoning"), "Reasoned path")
        self.assertEqual(thinking_event.get("completionId"), str(completion.id))

    @tag("batch_agent_chat")
    def test_timeline_preserves_deleted_peer_agent_name_after_soft_delete(self):
        peer_browser = BrowserUseAgent.objects.create(user=self.user, name="Peer Browser")
        peer_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Deleted Peer Agent",
            charter="Coordinate peer work",
            browser_use_agent=peer_browser,
        )
        peer_link = AgentPeerLink.objects.create(
            agent_a=self.agent,
            agent_b=peer_agent,
            created_by=self.user,
        )
        peer_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=peer_agent,
            channel=CommsChannel.OTHER,
            address=f"peer-{peer_agent.id}",
            is_primary=True,
        )
        peer_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.OTHER,
            address=f"peer-{peer_agent.id}",
            is_peer_dm=True,
            peer_link=peer_link,
        )
        peer_message = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=peer_endpoint,
            conversation=peer_conversation,
            body="Historical peer DM",
            owner_agent=self.agent,
            peer_agent=peer_agent,
        )

        peer_agent.soft_delete()

        self.assertFalse(AgentPeerLink.objects.filter(id=peer_link.id).exists())
        peer_conversation.refresh_from_db()
        self.assertIsNone(peer_conversation.peer_link_id)
        self.assertFalse(peer_conversation.is_peer_dm)
        self.assertTrue(PersistentAgentMessage.objects.filter(id=peer_message.id).exists())

        peer_event = next(
            event
            for event in fetch_timeline_window(self.agent).events
            if event.get("kind") == "message"
            and event["message"].get("bodyText") == "Historical peer DM"
        )

        self.assertTrue(peer_event["message"].get("isPeer"))
        self.assertEqual(peer_event["message"].get("peerAgent", {}).get("name"), peer_agent.name)
        self.assertIsNone(peer_event["message"].get("peerLinkId"))

    @tag("batch_agent_chat")
    def test_timeline_includes_kanban_events(self):
        card = PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Investigate kanban persistence",
            description="Ensure kanban survives refresh.",
            status=PersistentAgentKanbanCard.Status.TODO,
        )
        snapshot = KanbanBoardSnapshot(
            todo_count=1,
            doing_count=0,
            done_count=0,
            todo_titles=[card.title],
            doing_titles=[],
            done_titles=[],
        )
        changes = [
            KanbanCardChange(
                card_id=str(card.id),
                title=card.title,
                action="created",
                to_status=PersistentAgentKanbanCard.Status.TODO,
            )
        ]
        agent_name = (self.agent.name or "Agent").split()[0]
        kanban_payload = serialize_kanban_event(agent_name, changes, snapshot)
        persist_kanban_event(self.agent, kanban_payload)

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        timeline_payload = response.json()
        events = timeline_payload.get("events", [])
        kanban_event = next(event for event in events if event.get("kind") == "kanban")

        self.assertEqual(kanban_event.get("displayText"), kanban_payload.get("displayText"))
        self.assertEqual(kanban_event.get("primaryAction"), kanban_payload.get("primaryAction"))
        snapshot_payload = kanban_event.get("snapshot", {})
        self.assertEqual(snapshot_payload.get("todoCount"), 1)
        self.assertEqual(snapshot_payload.get("todoTitles"), [card.title])
        self.assertEqual(kanban_event.get("changes")[0].get("cardId"), str(card.id))

    @tag("batch_agent_chat")
    def test_timeline_creates_baseline_kanban_event(self):
        card = PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Baseline task",
            description="Baseline snapshot coverage.",
            status=PersistentAgentKanbanCard.Status.TODO,
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        events = response.json().get("events", [])
        kanban_event = next(event for event in events if event.get("kind") == "kanban")

        snapshot_payload = kanban_event.get("snapshot", {})
        self.assertEqual(snapshot_payload.get("todoCount"), 1)
        self.assertEqual(snapshot_payload.get("todoTitles"), [card.title])
        self.assertTrue(PersistentAgentKanbanEvent.objects.filter(agent=self.agent).exists())

    @tag("batch_agent_chat")
    def test_timeline_preserves_html_email_body(self):
        html_body = (
            "<p>Email intro</p>"
            "<p><strong>Bold</strong> value</p>"
            "<ul><li>Bullet</li></ul>"
            "<p><img src='https://example.com/generated.png' alt='Generated image' /></p>"
        )
        email_address = "louise@example.com"

        email_sender = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.EMAIL,
            address=email_address,
            is_primary=False,
        )
        email_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=email_address,
        )
        PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=email_sender,
            conversation=email_conversation,
            body=html_body,
            owner_agent=self.agent,
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        html_event = next(
            event
            for event in payload.get("events", [])
            if event.get("kind") == "message" and event["message"].get("bodyText") == html_body
        )

        rendered_html = html_event["message"]["bodyHtml"]
        self.assertIn("<strong>Bold</strong>", rendered_html)
        self.assertIn("<li>Bullet</li>", rendered_html)
        self.assertIn("https://example.com/generated.png", rendered_html)
        self.assertIn("<img", rendered_html)
        self.assertNotIn("&lt;", rendered_html)

    @tag("batch_agent_chat")
    def test_timeline_prefers_preserved_email_html_from_raw_payload(self):
        html_body = (
            "<table><tr><th>Status</th></tr><tr><td><strong>Ready</strong></td></tr></table>"
        )
        plain_body = "Status: Ready"
        email_address = "raw-html@example.com"

        email_sender = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.EMAIL,
            address=email_address,
            is_primary=False,
        )
        email_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=email_address,
        )
        PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=email_sender,
            conversation=email_conversation,
            body=plain_body,
            owner_agent=self.agent,
            raw_payload={"body_html": html_body},
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        html_event = next(
            event
            for event in payload.get("events", [])
            if event.get("kind") == "message" and event["message"].get("bodyText") == plain_body
        )

        rendered_html = html_event["message"]["bodyHtml"]
        self.assertIn("<table>", rendered_html)
        self.assertIn("<strong>Ready</strong>", rendered_html)
        self.assertNotIn("<p>Status: Ready</p>", rendered_html)

    @tag("batch_agent_chat")
    def test_timeline_uses_preserved_html_for_ingested_imap_email(self):
        recipient_address = f"agent-{self.agent.id}@example.com"
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=recipient_address,
            is_primary=True,
        )

        message = EmailMessage()
        message["From"] = "sender@example.com"
        message["To"] = recipient_address
        message["Subject"] = "Status update"
        message.set_content("Plain status update")
        message.add_alternative(
            "<table><tr><th>Status</th></tr><tr><td><strong>Ready</strong></td></tr></table>",
            subtype="html",
        )

        parsed = ImapEmailAdapter.parse_bytes(message.as_bytes(), recipient_address=recipient_address)
        ingest_inbound_message(CommsChannel.EMAIL, parsed)

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        html_event = next(
            event
            for event in payload.get("events", [])
            if event.get("kind") == "message" and event["message"].get("bodyText") == "Plain status update\n"
        )

        rendered_html = html_event["message"]["bodyHtml"]
        self.assertIn("<table>", rendered_html)
        self.assertIn("<strong>Ready</strong>", rendered_html)
        self.assertNotIn("<p>Plain status update</p>", rendered_html)

    @tag("batch_agent_chat")
    def test_timeline_preserves_safe_inline_email_styles(self):
        html_body = (
            "<div style='background: #f8f9fa; padding: 14px; border-radius: 8px; border-left: 4px solid #1976d2;'>"
            "<strong style='color: #1976d2;'>Consultant Note</strong>"
            "<span style='font-size: 14px; color: #333; line-height: 1.5;'>Styled body</span>"
            "</div>"
            "<h2 style='margin-top: 28px; border-bottom: 2px solid #1976d2; padding-bottom: 6px;'>Bigger Picture</h2>"
            "<p><em>Supporting detail</em></p>"
        )
        plain_body = "Consultant Note\nStyled body\nBigger Picture"
        email_address = "styled-html@example.com"

        email_sender = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.EMAIL,
            address=email_address,
            is_primary=False,
        )
        email_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=email_address,
        )
        PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=email_sender,
            conversation=email_conversation,
            body=plain_body,
            owner_agent=self.agent,
            raw_payload={"body_html": html_body},
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        html_event = next(
            event
            for event in payload.get("events", [])
            if event.get("kind") == "message" and event["message"].get("bodyText") == plain_body
        )

        rendered_html = html_event["message"]["bodyHtml"]
        self.assertIn("<div style=", rendered_html)
        self.assertIn("background: #f8f9fa", rendered_html)
        self.assertIn("border-left: 4px solid #1976d2", rendered_html)
        self.assertIn("<strong style=", rendered_html)
        self.assertIn("color: #1976d2", rendered_html)
        self.assertIn("<span style=", rendered_html)
        self.assertIn("font-size: 14px", rendered_html)
        self.assertIn("<h2 style=", rendered_html)
        self.assertIn("margin-top: 28px", rendered_html)
        self.assertIn("<em>Supporting detail</em>", rendered_html)

    @tag("batch_agent_chat")
    def test_timeline_rewrites_cid_image_src_from_preserved_email_html(self):
        html_body = "<p><img src='cid:roadmap-card.png' alt='Roadmap card' /></p>"
        plain_body = "See roadmap card"
        email_address = "raw-html-cid@example.com"

        email_sender = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.EMAIL,
            address=email_address,
            is_primary=False,
        )
        email_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=email_address,
        )
        message = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=email_sender,
            conversation=email_conversation,
            body=plain_body,
            owner_agent=self.agent,
            raw_payload={"body_html": html_body},
        )
        PersistentAgentMessageAttachment.objects.create(
            message=message,
            file=ContentFile(b"image-bytes", name="roadmap-card.png"),
            content_type="image/png",
            file_size=11,
            filename="roadmap-card.png",
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        cid_event = next(
            event
            for event in payload.get("events", [])
            if event.get("kind") == "message" and event["message"].get("bodyText") == plain_body
        )

        rendered_html = cid_event["message"]["bodyHtml"]
        attachment_url = cid_event["message"]["attachments"][0]["url"]
        self.assertIn(attachment_url, rendered_html)
        self.assertNotIn("cid:roadmap-card.png", rendered_html)

    @tag("batch_agent_chat")
    def test_timeline_rewrites_cid_image_src_to_attachment_url(self):
        html_body = "<p><img src='cid:Screenshot 2026-02-25 at 19.51.54.png' alt='Screenshot' /></p>"
        email_address = "image-cid@example.com"

        email_sender = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.EMAIL,
            address=email_address,
            is_primary=False,
        )
        email_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=email_address,
        )
        message = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=email_sender,
            conversation=email_conversation,
            body=html_body,
            owner_agent=self.agent,
        )
        PersistentAgentMessageAttachment.objects.create(
            message=message,
            file=ContentFile(b"image-bytes", name="Screenshot 2026-02-25 at 19.51.54.png"),
            content_type="image/png",
            file_size=11,
            filename="Screenshot 2026-02-25 at 19.51.54.png",
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        cid_event = next(
            event
            for event in payload.get("events", [])
            if event.get("kind") == "message" and event["message"].get("bodyText") == html_body
        )

        rendered_html = cid_event["message"]["bodyHtml"]
        attachment_url = cid_event["message"]["attachments"][0]["url"]
        self.assertIn(attachment_url, rendered_html)
        self.assertNotIn("cid:Screenshot 2026-02-25 at 19.51.54.png", rendered_html)

    @tag("batch_agent_chat")
    def test_timeline_rewrites_percent_encoded_cid_image_src_to_attachment_url(self):
        html_body = "<p><img src='cid:Screenshot%202026-02-25%20at%2019.51.54.png' alt='Screenshot' /></p>"
        email_address = "image-cid-encoded@example.com"

        email_sender = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.EMAIL,
            address=email_address,
            is_primary=False,
        )
        email_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=email_address,
        )
        message = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=email_sender,
            conversation=email_conversation,
            body=html_body,
            owner_agent=self.agent,
        )
        PersistentAgentMessageAttachment.objects.create(
            message=message,
            file=ContentFile(b"image-bytes", name="Screenshot 2026-02-25 at 19.51.54.png"),
            content_type="image/png",
            file_size=11,
            filename="Screenshot 2026-02-25 at 19.51.54.png",
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        cid_event = next(
            event
            for event in payload.get("events", [])
            if event.get("kind") == "message" and event["message"].get("bodyText") == html_body
        )

        rendered_html = cid_event["message"]["bodyHtml"]
        attachment_url = cid_event["message"]["attachments"][0]["url"]
        self.assertIn(attachment_url, rendered_html)
        self.assertNotIn("cid:Screenshot%202026-02-25%20at%2019.51.54.png", rendered_html)

    @tag("batch_agent_chat")
    def test_timeline_does_not_reuse_last_basename_attachment_url_when_cid_refs_exceed_matches(self):
        html_body = "<p><img src='cid:charts/logo.png' /><img src='cid:footer/logo.png' /></p>"
        email_address = "image-cid-overflow@example.com"

        email_sender = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.EMAIL,
            address=email_address,
            is_primary=False,
        )
        email_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=email_address,
        )
        message = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=email_sender,
            conversation=email_conversation,
            body=html_body,
            owner_agent=self.agent,
        )
        PersistentAgentMessageAttachment.objects.create(
            message=message,
            file=ContentFile(b"image-bytes", name="logo.png"),
            content_type="image/png",
            file_size=11,
            filename="logo.png",
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        cid_event = next(
            event
            for event in payload.get("events", [])
            if event.get("kind") == "message" and event["message"].get("bodyText") == html_body
        )

        rendered_html = cid_event["message"]["bodyHtml"]
        attachment_url = cid_event["message"]["attachments"][0]["url"]
        self.assertEqual(rendered_html.count(attachment_url), 1)
        self.assertEqual(rendered_html.count("src="), 1)
        self.assertEqual(rendered_html.count("<img"), 2)

    @tag("batch_agent_chat")
    def test_timeline_includes_peer_dm_attachment_refs_for_sender_and_recipient(self):
        peer_browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Peer Browser")
        peer_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Peer Receiver",
            charter="Handle incoming peer work",
            browser_use_agent=peer_browser_agent,
        )
        AgentPeerLink.objects.create(
            agent_a=self.agent,
            agent_b=peer_agent,
            created_by=self.user,
        )

        result = write_bytes_to_dir(
            self.agent,
            b"peer handoff",
            "/handoffs/brief.txt",
            "text/plain",
        )
        self.assertEqual(result["status"], "ok")
        attachments = resolve_filespace_attachments(self.agent, ["/handoffs/brief.txt"])

        with patch("api.agent.tasks.process_agent_events_task") as task_mock, patch(
            "api.agent.peer_comm.transaction.on_commit", lambda cb: cb()
        ):
            task_mock.delay = MagicMock()
            PeerMessagingService(self.agent, peer_agent).send_message(
                "Peer handoff with file",
                attachments=attachments,
            )

        sender_response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(sender_response.status_code, 200)
        sender_payload = sender_response.json()
        sender_event = next(
            event
            for event in sender_payload.get("events", [])
            if event.get("kind") == "message"
            and event["message"].get("bodyText") == "Peer handoff with file"
            and event["message"].get("isOutbound")
        )

        sender_attachment = sender_event["message"]["attachments"][0]
        self.assertEqual(sender_attachment["filename"], "brief.txt")
        self.assertEqual(sender_attachment["filespacePath"], "/handoffs/brief.txt")
        self.assertIn(f"/console/api/agents/{self.agent.id}/files/download/", sender_attachment["downloadUrl"])

        recipient_response = self.client.get(f"/console/api/agents/{peer_agent.id}/timeline/")
        self.assertEqual(recipient_response.status_code, 200)
        recipient_payload = recipient_response.json()
        recipient_event = next(
            event
            for event in recipient_payload.get("events", [])
            if event.get("kind") == "message"
            and event["message"].get("bodyText") == "Peer handoff with file"
            and not event["message"].get("isOutbound")
        )

        recipient_attachment = recipient_event["message"]["attachments"][0]
        expected_prefix = f"/Inbox/{sender_event['message']['timestamp'][:10]}/peer-Console_Tester/"
        self.assertEqual(recipient_attachment["filename"], "brief.txt")
        self.assertTrue(recipient_attachment["filespacePath"].startswith(expected_prefix))
        self.assertIn(f"/console/api/agents/{peer_agent.id}/files/download/", recipient_attachment["downloadUrl"])

    @tag("batch_agent_chat")
    def test_plaintext_and_markdown_prefer_body_text(self):
        response = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        original_event = next(
            event
            for event in payload.get("events", [])
            if event.get("kind") == "message" and event["message"].get("bodyText") == "Hello from the owner"
        )

        self.assertEqual(original_event["message"].get("bodyHtml"), "")

    @tag("batch_agent_chat")
    def test_web_session_api_flow(self):
        start_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/start/",
            data=json.dumps({"is_visible": True}),
            content_type="application/json",
        )
        self.assertEqual(start_response.status_code, 200)
        start_payload = start_response.json()
        session_key = start_payload["session_key"]
        self.assertTrue(start_payload["is_visible"])

        heartbeat_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/heartbeat/",
            data=json.dumps({"session_key": session_key, "is_visible": False}),
            content_type="application/json",
        )
        self.assertEqual(heartbeat_response.status_code, 200)
        self.assertFalse(heartbeat_response.json()["is_visible"])

        end_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/end/",
            data=json.dumps({"session_key": session_key}),
            content_type="application/json",
        )
        self.assertEqual(end_response.status_code, 200)
        end_payload = end_response.json()
        self.assertIn("ended_at", end_payload)

        # Ending an already-deleted session should still succeed idempotently.
        repeat_end = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/end/",
            data=json.dumps({"session_key": session_key}),
            content_type="application/json",
        )
        self.assertEqual(repeat_end.status_code, 200)
        repeat_payload = repeat_end.json()
        self.assertTrue(
            repeat_payload.get("ended") or repeat_payload.get("ended_at"),
            repeat_payload,
        )

    @tag("batch_agent_chat")
    def test_web_session_start_creates_distinct_sessions_per_tab(self):
        first_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/start/",
            data=json.dumps({"is_visible": True}),
            content_type="application/json",
        )
        self.assertEqual(first_response.status_code, 200)

        second_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/start/",
            data=json.dumps({"is_visible": True}),
            content_type="application/json",
        )
        self.assertEqual(second_response.status_code, 200)

        first_key = first_response.json()["session_key"]
        second_key = second_response.json()["session_key"]
        self.assertNotEqual(first_key, second_key)
        self.assertEqual(
            PersistentAgentWebSession.objects.filter(agent=self.agent, user=self.user).count(),
            2,
        )

    @tag("batch_agent_chat")
    @patch("console.api_views.Analytics.track_event")
    def test_session_analytics_emitted(self, mock_track_event):
        start_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/start/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(start_response.status_code, 200)
        start_payload = start_response.json()
        session_key = start_payload["session_key"]

        heartbeat_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/heartbeat/",
            data=json.dumps({"session_key": session_key}),
            content_type="application/json",
        )
        self.assertEqual(heartbeat_response.status_code, 200)

        end_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/end/",
            data=json.dumps({"session_key": session_key}),
            content_type="application/json",
        )
        self.assertEqual(end_response.status_code, 200)

        self.assertEqual(mock_track_event.call_count, 2)
        event_names = {record.kwargs.get("event") for record in mock_track_event.call_args_list}
        self.assertIn(AnalyticsEvent.WEB_CHAT_SESSION_STARTED, event_names)
        self.assertIn(AnalyticsEvent.WEB_CHAT_SESSION_ENDED, event_names)

        start_call_record = next(
            record for record in mock_track_event.call_args_list if record.kwargs.get("event") == AnalyticsEvent.WEB_CHAT_SESSION_STARTED
        )
        end_call_record = next(
            record for record in mock_track_event.call_args_list if record.kwargs.get("event") == AnalyticsEvent.WEB_CHAT_SESSION_ENDED
        )

        self.assertEqual(start_call_record.kwargs["properties"].get("agent_id"), str(self.agent.id))
        self.assertEqual(end_call_record.kwargs["properties"].get("agent_id"), str(self.agent.id))
        self.assertEqual(end_call_record.kwargs["properties"].get("session_key"), session_key)

    @tag("batch_agent_chat")
    @patch("console.api_views.Analytics.track_event")
    def test_message_post_records_analytics(self, mock_track_event):
        with patch("api.agent.tasks.process_agent_events_task.delay") as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    f"/console/api/agents/{self.agent.id}/messages/",
                    data=json.dumps({"body": "Hello agent"}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 201)

        mock_delay.assert_called()

        self.assertEqual(mock_track_event.call_count, 1)
        self.assertEqual(mock_track_event.call_args.kwargs.get("event"), AnalyticsEvent.WEB_CHAT_MESSAGE_SENT)

        message_call = next(
            record for record in mock_track_event.call_args_list if record.kwargs.get("event") == AnalyticsEvent.WEB_CHAT_MESSAGE_SENT
        )
        props = message_call.kwargs["properties"]
        self.assertEqual(props.get("agent_id"), str(self.agent.id))
        self.assertIn("message_id", props)
        self.assertEqual(props.get("message_length"), len("Hello agent"))

    @tag("batch_agent_chat")
    @patch("api.agent.comms.message_service.emit_configured_custom_capi_event")
    def test_message_post_emits_inbound_message_custom_event(self, mock_emit_custom_event):
        with patch("api.agent.tasks.process_agent_events_task.delay"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    f"/console/api/agents/{self.agent.id}/messages/",
                    data=json.dumps({"body": "Hello agent"}),
                    content_type="application/json",
                )

        self.assertEqual(response.status_code, 201)
        mock_emit_custom_event.assert_called_once()
        call_kwargs = mock_emit_custom_event.call_args.kwargs
        self.assertEqual(call_kwargs["event_name"], "InboundMessage")
        self.assertEqual(call_kwargs["user"], self.user)
        self.assertEqual(call_kwargs["properties"]["agent_id"], str(self.agent.id))
        self.assertEqual(call_kwargs["properties"]["channel"], CommsChannel.WEB)
        self.assertEqual(call_kwargs["properties"]["message_length"], len("Hello agent"))

    @tag("batch_agent_chat")
    @patch("api.agent.comms.message_service.emit_configured_custom_capi_event")
    def test_ingest_inbound_message_does_not_emit_inbound_message_custom_event_for_collaborator_web_sender(
        self,
        mock_emit_custom_event,
    ):
        collaborator = get_user_model().objects.create_user(
            username="agent-collaborator",
            email="collaborator@example.com",
            password="password123",
        )
        AgentCollaborator.objects.create(agent=self.agent, user=collaborator)
        parsed = ParsedMessage(
            sender=build_web_user_address(collaborator.id, self.agent.id),
            recipient=self.agent_address,
            subject=None,
            body="Hello from collaborator",
            attachments=[],
            raw_payload={},
            msg_channel=CommsChannel.WEB,
        )

        with patch("api.agent.tasks.process_agent_events_task.delay"):
            with self.captureOnCommitCallbacks(execute=True):
                ingest_inbound_message(CommsChannel.WEB, parsed)

        mock_emit_custom_event.assert_not_called()

    @tag("batch_agent_chat")
    def test_processing_status_endpoint_includes_active_web_tasks(self):
        task = BrowserUseAgentTask.objects.create(
            agent=self.browser_agent,
            user=self.user,
            prompt="Visit example.com",
            status=BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
        )

        response = self.client.get(f"/console/api/agents/{self.agent.id}/processing/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        snapshot = payload.get("processing_snapshot")

        self.assertIsInstance(snapshot, dict)
        self.assertTrue(snapshot.get("active"))

        web_tasks = snapshot.get("webTasks") or []
        self.assertEqual(len(web_tasks), 1)
        web_task = web_tasks[0]

        self.assertEqual(web_task.get("id"), str(task.id))
        self.assertEqual(web_task.get("status"), BrowserUseAgentTask.StatusChoices.IN_PROGRESS)
        self.assertEqual(web_task.get("statusLabel"), task.get_status_display())
        self.assertEqual(web_task.get("promptPreview"), "Visit example.com")
        self.assertIn("nextScheduledAt", snapshot)

    @tag("batch_agent_chat")
    def test_processing_status_endpoint_includes_next_scheduled_at(self):
        self.agent.schedule = "@hourly"
        self.agent.execution_environment = getattr(settings, "GOBII_RELEASE_ENV", "local")
        self.agent.is_active = True
        self.agent.life_state = PersistentAgent.LifeState.ACTIVE
        self.agent.save(update_fields=["schedule", "execution_environment", "is_active", "life_state"])

        response = self.client.get(f"/console/api/agents/{self.agent.id}/processing/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        snapshot = payload.get("processing_snapshot") or {}

        self.assertIsInstance(snapshot.get("nextScheduledAt"), str)

    @tag("batch_agent_chat")
    def test_processing_status_reports_active_when_only_queued(self):
        set_processing_queued_flag(self.agent.id)
        try:
            response = self.client.get(f"/console/api/agents/{self.agent.id}/processing/")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload.get("processing_active"))

            snapshot = payload.get("processing_snapshot") or {}
            self.assertTrue(snapshot.get("active"))
            self.assertEqual(snapshot.get("webTasks"), [])
        finally:
            clear_processing_queued_flag(self.agent.id)

    @tag("batch_agent_chat")
    def test_stop_endpoint_stops_processing_and_cancels_active_web_tasks(self):
        first_task = BrowserUseAgentTask.objects.create(
            agent=self.browser_agent,
            user=self.user,
            prompt="Visit example.com",
            status=BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
        )
        second_task = BrowserUseAgentTask.objects.create(
            agent=self.browser_agent,
            user=self.user,
            prompt="Visit gobii.com",
            status=BrowserUseAgentTask.StatusChoices.PENDING,
        )
        set_processing_queued_flag(self.agent.id)
        enqueue_pending_agent(self.agent.id)

        try:
            response = self.client.post(
                f"/console/api/agents/{self.agent.id}/stop/",
                data=json.dumps({}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()

            self.assertTrue(payload.get("stopping"))
            self.assertEqual(payload.get("cancelledWebTaskCount"), 2)
            self.assertFalse(payload.get("processing_active"))

            snapshot = payload.get("processing_snapshot") or {}
            self.assertFalse(snapshot.get("active"))
            self.assertEqual(snapshot.get("webTasks"), [])

            self.assertFalse(is_processing_stop_requested(self.agent.id))
            self.assertFalse(is_agent_pending(self.agent.id))

            self.assertFalse(
                BrowserUseAgentTask.objects.filter(
                    id__in=[first_task.id, second_task.id],
                    status__in=[
                        BrowserUseAgentTask.StatusChoices.PENDING,
                        BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
                    ],
                ).exists()
            )
            first_task.refresh_from_db()
            second_task.refresh_from_db()
            self.assertEqual(first_task.status, BrowserUseAgentTask.StatusChoices.CANCELLED)
            self.assertEqual(second_task.status, BrowserUseAgentTask.StatusChoices.CANCELLED)
        finally:
            clear_processing_queued_flag(self.agent.id)
            clear_processing_stop_requested(self.agent.id)

    @tag("batch_agent_chat")
    def test_stop_endpoint_forbids_shared_collaborator_without_manage_permission(self):
        user_model = get_user_model()
        collaborator = user_model.objects.create_user(
            username="chat-stop-collab",
            email="chat-stop-collab@example.com",
            password="password123",
        )
        AgentCollaborator.objects.create(agent=self.agent, user=collaborator)
        collaborator_client = Client()
        collaborator_client.force_login(collaborator)

        response = collaborator_client.post(
            f"/console/api/agents/{self.agent.id}/stop/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(is_processing_stop_requested(self.agent.id))

    @tag("batch_agent_chat")
    def test_stop_endpoint_clears_stop_request_when_agent_is_already_idle(self):
        response = self.client.post(
            f"/console/api/agents/{self.agent.id}/stop/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertTrue(payload.get("stopping"))
        self.assertEqual(payload.get("cancelledWebTaskCount"), 0)
        self.assertFalse(payload.get("processing_active"))
        self.assertFalse(is_processing_stop_requested(self.agent.id))

    @tag("batch_agent_chat")
    @patch("console.api_views.process_agent_events_task.delay")
    def test_requested_secrets_fulfill_api_updates_secret_and_returns_pending_actions(self, mock_delay):
        secret = PersistentAgentSecret(
            agent=self.agent,
            name="Procurement API Key",
            description="Used for procurement sync",
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
            domain_pattern="https://procurement.example.com",
            requested=True,
        )
        secret.key = "procurement_api_key"
        secret.encrypted_value = b""
        secret.save()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/console/api/agents/{self.agent.id}/requested-secrets/fulfill/",
                data=json.dumps(
                    {
                        "values": {str(secret.id): "super-secret-value"},
                        "make_global": False,
                    }
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        secret.refresh_from_db()
        self.assertFalse(secret.requested)
        self.assertEqual(secret.get_value(), "super-secret-value")
        self.assertEqual(response.json().get("pending_action_requests"), [])
        mock_delay.assert_called_once_with(str(self.agent.id))

    @tag("batch_agent_chat")
    def test_requested_secrets_remove_api_deletes_selected_requests(self):
        secret = PersistentAgentSecret(
            agent=self.agent,
            name="Procurement API Key",
            description="Used for procurement sync",
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
            domain_pattern="https://procurement.example.com",
            requested=True,
        )
        secret.key = "procurement_api_key"
        secret.encrypted_value = b""
        secret.save()

        response = self.client.post(
            f"/console/api/agents/{self.agent.id}/requested-secrets/remove/",
            data=json.dumps({"secret_ids": [str(secret.id)]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersistentAgentSecret.objects.filter(id=secret.id).exists())
        self.assertEqual(response.json().get("pending_action_requests"), [])

    @tag("batch_agent_chat")
    @patch("console.api_views.process_agent_events_task.delay")
    def test_contact_request_resolve_api_approves_with_requested_permissions(self, mock_delay):
        request_obj = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="approver@example.com",
            reason="Need procurement approval",
            purpose="Approve vendor contract",
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/console/api/agents/{self.agent.id}/contact-requests/resolve/",
                data=json.dumps(
                    {
                        "responses": [
                            {
                                "request_id": str(request_obj.id),
                                "decision": "approve",
                                "allow_inbound": False,
                                "allow_outbound": True,
                                "can_configure": True,
                            }
                        ]
                    }
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, CommsAllowlistRequest.RequestStatus.APPROVED)
        allowlist_entry = self.agent.manual_allowlist.get(
            channel=CommsChannel.EMAIL,
            address="approver@example.com",
        )
        self.assertFalse(allowlist_entry.allow_inbound)
        self.assertTrue(allowlist_entry.allow_outbound)
        self.assertTrue(allowlist_entry.can_configure)
        self.assertEqual(response.json().get("pending_action_requests"), [])
        mock_delay.assert_called_once_with(str(self.agent.id))


    @tag("batch_agent_chat")
    def test_web_chat_tool_requires_active_session(self):
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.com",
            is_primary=True,
        )
        result = execute_send_chat_message(
            self.agent,
            {"body": "Ping", "to_address": self.user_address},
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("No active web chat session", result["message"])

        start_web_session(self.agent, self.user)
        success = execute_send_chat_message(
            self.agent,
            {"body": "Ping", "to_address": self.user_address},
        )
        self.assertEqual(success["status"], "ok")

        markdown_body = "# Heading\n\n- Item"
        PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=self.user_endpoint,
            conversation=self.conversation,
            body=markdown_body,
            owner_agent=self.agent,
        )

        refreshed = self.client.get(f"/console/api/agents/{self.agent.id}/timeline/")
        self.assertEqual(refreshed.status_code, 200)
        payload = refreshed.json()

        markdown_event = next(
            event
            for event in payload.get("events", [])
            if event.get("kind") == "message" and event["message"].get("bodyText") == markdown_body
        )

        self.assertEqual(markdown_event["message"].get("bodyHtml"), "")

    @tag("batch_agent_chat")
    def test_web_chat_tool_allows_during_visibility_grace_window(self):
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.com",
            is_primary=True,
        )
        result = start_web_session(self.agent, self.user)
        heartbeat_web_session(
            result.session.session_key,
            self.agent,
            self.user,
            is_visible=False,
        )

        success = execute_send_chat_message(
            self.agent,
            {"body": "Still here", "to_address": self.user_address},
        )
        self.assertEqual(success["status"], "ok")

    @tag("batch_agent_chat")
    def test_web_chat_tool_rejects_after_visibility_grace_when_other_channels_exist(self):
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.com",
            is_primary=True,
        )
        result = start_web_session(self.agent, self.user)
        PersistentAgentWebSession.objects.filter(pk=result.session.pk).update(
            is_visible=False,
            last_seen_at=timezone.now() - timedelta(seconds=30),
            last_visible_at=timezone.now() - timedelta(seconds=61),
        )

        rejected = execute_send_chat_message(
            self.agent,
            {"body": "Still here", "to_address": self.user_address},
        )
        self.assertEqual(rejected["status"], "error")
        self.assertIn("No active web chat session", rejected["message"])

    @tag("batch_agent_chat")
    def test_web_chat_tool_allows_after_visibility_grace_when_web_is_only_channel(self):
        result = start_web_session(self.agent, self.user)
        PersistentAgentWebSession.objects.filter(pk=result.session.pk).update(
            is_visible=False,
            last_seen_at=timezone.now() - timedelta(seconds=30),
            last_visible_at=timezone.now() - timedelta(seconds=61),
        )

        allowed = execute_send_chat_message(
            self.agent,
            {"body": "Still here", "to_address": self.user_address},
        )
        self.assertEqual(allowed["status"], "ok")

    @tag("batch_agent_chat")
    def test_web_chat_tool_allows_without_session_when_no_other_channels(self):
        result = execute_send_chat_message(
            self.agent,
            {"body": "Ping", "to_address": self.user_address},
        )
        self.assertEqual(result["status"], "ok")
        message = PersistentAgentMessage.objects.filter(
            owner_agent=self.agent,
            is_outbound=True,
            body="Ping",
        ).first()
        self.assertIsNotNone(message)

    @tag("batch_agent_chat")
    def test_send_chat_tool_defaults_to_owner_without_to_address_and_no_other_channels(self):
        result = execute_send_chat_message(
            self.agent,
            {"body": "Ping"},
        )
        self.assertEqual(result["status"], "ok")
        message = PersistentAgentMessage.objects.filter(
            owner_agent=self.agent,
            is_outbound=True,
            body="Ping",
        ).first()
        self.assertIsNotNone(message)
        self.assertEqual(message.to_endpoint.address, self.user_address)

    @tag("batch_agent_chat")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_message_post_creates_console_message(self, mock_delay):
        body = "Run weekly summary"
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/console/api/agents/{self.agent.id}/messages/",
                data={"body": body},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertIn("event", payload)
        event = payload["event"]
        self.assertEqual(event["kind"], "message")
        self.assertEqual(event["message"]["bodyText"], body)
        self.assertEqual(event["message"]["channel"], CommsChannel.WEB)
        relative_ts = event["message"].get("relativeTimestamp")
        self.assertIsInstance(relative_ts, str)

        stored = (
            PersistentAgentMessage.objects.filter(owner_agent=self.agent, body=body)
            .order_by("-timestamp")
            .first()
        )
        self.assertIsNotNone(stored)
        self.assertEqual(stored.from_endpoint.address, self.user_address)

    @override_settings(MAX_FILE_SIZE=20)
    @tag("batch_agent_chat")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_message_post_accepts_under_limit_attachment(self, mock_delay):
        attachment = SimpleUploadedFile("notes.txt", b"hello world", content_type="text/plain")

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/console/api/agents/{self.agent.id}/messages/",
                data={"body": "Attached", "attachments": attachment},
            )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["event"]["kind"], "message")
        self.assertEqual(payload["event"]["message"]["bodyText"], "Attached")
        self.assertEqual(len(payload["event"]["message"]["attachments"]), 1)
        stored = (
            PersistentAgentMessage.objects.filter(owner_agent=self.agent, body="Attached")
            .order_by("-timestamp")
            .first()
        )
        self.assertIsNotNone(stored)
        self.assertEqual(stored.conversation.address, self.user_address)
        mock_delay.assert_called()

    @override_settings(MAX_FILE_SIZE=5)
    @tag("batch_agent_chat")
    def test_message_post_rejects_over_limit_attachment(self):
        attachment = SimpleUploadedFile("report.pdf", b"hello-bytes", content_type="application/pdf")
        before_count = PersistentAgentMessage.objects.filter(owner_agent=self.agent).count()

        response = self.client.post(
            f"/console/api/agents/{self.agent.id}/messages/",
            data={"body": "Attached", "attachments": attachment},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {"error": '"report.pdf" is too large. Max file size is 5 bytes.'},
        )
        self.assertEqual(PersistentAgentMessage.objects.filter(owner_agent=self.agent).count(), before_count)

    @tag("batch_agent_chat")
    def test_send_chat_tool_creates_outbound_message(self):
        start_web_session(self.agent, self.user)
        params = {"body": "Tool says hi", "to_address": self.user_address}
        result = execute_send_chat_message(self.agent, params)
        self.assertEqual(result["status"], "ok")

        message = PersistentAgentMessage.objects.get(owner_agent=self.agent, is_outbound=True, body="Tool says hi")
        self.assertEqual(message.from_endpoint.channel, CommsChannel.WEB)
        self.assertEqual(message.conversation.channel, CommsChannel.WEB)
        self.assertEqual(message.latest_status, DeliveryStatus.DELIVERED)

    @tag("batch_agent_chat")
    def test_send_chat_tool_can_mark_continuation(self):
        start_web_session(self.agent, self.user)
        params = {
            "body": "I'll keep working",
            "to_address": self.user_address,
            "will_continue_work": True,
        }
        result = execute_send_chat_message(self.agent, params)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result.get("auto_sleep_ok"))

    @tag("batch_agent_chat")
    def test_send_chat_tool_rejects_unlisted_address(self):
        start_web_session(self.agent, self.user)
        stranger_address = build_web_user_address(self.user.id + 999, self.agent.id)
        params = {"body": "Nope", "to_address": stranger_address}
        result = execute_send_chat_message(self.agent, params)
        self.assertEqual(result["status"], "error")
        self.assertIn("no active web chat session", result["message"].lower())
