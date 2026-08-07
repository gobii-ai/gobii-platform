from importlib import import_module
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.comms.email_endpoint_routing import can_agent_send_to
from api.agent.tasks.process_events import process_unseen_web_chat_followup_task
from api.agent.tools.web_chat_sender import execute_send_chat_message, get_send_chat_tool
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    PersistentAgentSystemMessage,
    build_web_user_address,
)
from api.services.web_sessions import start_web_session


@tag("batch_agent_chat")
class DisabledUnseenWebChatFollowupTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="disabled-unseen-followup@example.test",
            email="disabled-unseen-followup@example.test",
            password="password123",
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Disabled Unseen Follow-up Browser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Disabled Unseen Follow-up Agent",
            charter="Send web updates without cross-channel follow-up.",
            browser_use_agent=browser_agent,
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.test",
            is_primary=True,
        )
        self.user_web_address = build_web_user_address(self.user.id, self.agent.id)
        start_web_session(self.agent, self.user)

    @patch("api.agent.tasks.process_events.process_unseen_web_chat_followup_task.apply_async")
    def test_send_chat_message_does_not_schedule_cross_channel_followup(self, apply_async_mock):
        self.assertTrue(
            can_agent_send_to(
                self.agent,
                CommsChannel.EMAIL,
                self.user.email,
            )
        )
        with self.captureOnCommitCallbacks(execute=True):
            result = execute_send_chat_message(
                self.agent,
                {
                    "body": "Pipeline status",
                    "to_address": self.user_web_address,
                    "will_continue_work": False,
                },
            )

        self.assertEqual(result["status"], "ok", result)
        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertTrue(message.is_outbound)
        self.assertEqual(message.from_endpoint.owner_agent, self.agent)
        self.assertEqual(message.to_endpoint.address, self.user_web_address)
        apply_async_mock.assert_not_called()
        self.assertFalse(PersistentAgentSystemMessage.objects.filter(agent=self.agent).exists())

    def test_chat_contract_prefers_provided_link_handles(self):
        body_description = get_send_chat_tool()["function"]["parameters"]["properties"]["body"]["description"]

        self.assertIn("link entity names once with provided link handles", body_description)
        self.assertIn("exact raw URL only when no handle exists", body_description)

    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    def test_legacy_followup_task_is_a_noop(self, process_events_mock):
        message_count = PersistentAgentMessage.objects.count()

        process_unseen_web_chat_followup_task("00000000-0000-0000-0000-000000000001")

        process_events_mock.assert_not_called()
        self.assertEqual(PersistentAgentMessage.objects.count(), message_count)
        self.assertFalse(PersistentAgentSystemMessage.objects.exists())

    def test_cleanup_migration_only_deactivates_pending_followup_directives(self):
        pending_followup = PersistentAgentSystemMessage.objects.create(
            agent=self.agent,
            body="Unread web chat follow-up (message_id=pending): unseen.",
        )
        delivered_followup = PersistentAgentSystemMessage.objects.create(
            agent=self.agent,
            body="Unread web chat follow-up (message_id=delivered): unseen.",
            delivered_at=timezone.now(),
        )
        unrelated = PersistentAgentSystemMessage.objects.create(
            agent=self.agent,
            body="A different system directive.",
        )
        migration = import_module(
            "api.migrations.0441_disable_unseen_web_chat_followups",
        )

        migration.deactivate_pending_followup_directives(django_apps, None)

        pending_followup.refresh_from_db()
        delivered_followup.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertFalse(pending_followup.is_active)
        self.assertTrue(delivered_followup.is_active)
        self.assertTrue(unrelated.is_active)
