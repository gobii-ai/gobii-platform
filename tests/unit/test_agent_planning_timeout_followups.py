from datetime import timedelta
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from api.agent.tasks.process_events import (
    process_planning_timeout_task,
    process_unseen_web_chat_followup_task,
    schedule_unseen_web_chat_followup,
)
from api.agent.tools.web_chat_sender import execute_send_chat_message
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
    PersistentAgentMessageRead,
    PersistentAgentSystemMessage,
    build_web_agent_address,
    build_web_user_address,
)
from api.services.agent_planning import (
    build_planning_timeout_directive,
    is_planning_timeout_expired,
    schedule_planning_timeout_processing,
)


@tag("batch_agent_chat")
@override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
class PlanningTimeoutDirectiveTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="planning-timeout-owner",
            email="planning-timeout-owner@example.com",
            password="password123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Planning Timeout Browser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Planning Timeout Agent",
            charter="Initial charter",
            browser_use_agent=self.browser_agent,
            planning_state=PersistentAgent.PlanningState.PLANNING,
        )

    @override_settings(PERSISTENT_AGENT_PLANNING_TIMEOUT_SECONDS=3600)
    def test_expired_planning_builds_end_planning_directive(self):
        created_at = timezone.now() - timedelta(seconds=3601)
        PersistentAgent.objects.filter(pk=self.agent.pk).update(created_at=created_at)
        self.agent.refresh_from_db()

        self.assertTrue(is_planning_timeout_expired(self.agent))
        directive = build_planning_timeout_directive(self.agent)

        self.assertIsNotNone(directive)
        self.assertIn("Planning Timeout", directive)
        self.assertIn("more than 1 hour", directive)
        self.assertIn("Call end_planning(full_plan=...) now", directive)
        self.assertIn("continue with the work after planning ends", directive)

    @override_settings(PERSISTENT_AGENT_PLANNING_TIMEOUT_SECONDS=3600)
    def test_non_expired_or_finished_planning_does_not_build_directive(self):
        self.assertFalse(is_planning_timeout_expired(self.agent))
        self.assertIsNone(build_planning_timeout_directive(self.agent))

        for planning_state in (
            PersistentAgent.PlanningState.COMPLETED,
            PersistentAgent.PlanningState.SKIPPED,
        ):
            with self.subTest(planning_state=planning_state):
                self.agent.planning_state = planning_state
                self.agent.created_at = timezone.now() - timedelta(seconds=7200)
                self.assertFalse(is_planning_timeout_expired(self.agent))
                self.assertIsNone(build_planning_timeout_directive(self.agent))

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False, PERSISTENT_AGENT_PLANNING_TIMEOUT_SECONDS=3600)
    @patch("api.agent.tasks.process_planning_timeout_task.apply_async")
    def test_schedule_planning_timeout_processing_queues_timeout_task(self, apply_async_mock):
        with self.captureOnCommitCallbacks(execute=True):
            schedule_planning_timeout_processing(self.agent)

        apply_async_mock.assert_called_once_with(
            args=[str(self.agent.id)],
            countdown=3600,
        )

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True, PERSISTENT_AGENT_PLANNING_TIMEOUT_SECONDS=3600)
    @patch("api.agent.tasks.process_planning_timeout_task.apply_async")
    def test_schedule_planning_timeout_processing_skips_delayed_task_in_eager_mode(self, apply_async_mock):
        with self.captureOnCommitCallbacks(execute=True):
            schedule_planning_timeout_processing(self.agent)

        apply_async_mock.assert_not_called()

    @override_settings(PERSISTENT_AGENT_PLANNING_TIMEOUT_SECONDS=3600)
    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    def test_planning_timeout_task_creates_system_directive_and_queues_processing(self, delay_mock):
        created_at = timezone.now() - timedelta(seconds=3601)
        PersistentAgent.objects.filter(pk=self.agent.pk).update(created_at=created_at)

        process_planning_timeout_task(str(self.agent.id))

        directive = PersistentAgentSystemMessage.objects.get(agent=self.agent)
        self.assertIn("Planning Timeout", directive.body)
        self.assertIn("Call end_planning(full_plan=...) now", directive.body)
        delay_mock.assert_called_once_with(str(self.agent.id))

    @override_settings(PERSISTENT_AGENT_PLANNING_TIMEOUT_SECONDS=3600)
    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    def test_planning_timeout_task_skips_non_expired_or_finished_planning(self, delay_mock):
        process_planning_timeout_task(str(self.agent.id))
        self.assertFalse(PersistentAgentSystemMessage.objects.filter(agent=self.agent).exists())
        delay_mock.assert_not_called()

        self.agent.planning_state = PersistentAgent.PlanningState.COMPLETED
        self.agent.save(update_fields=["planning_state", "updated_at"])
        PersistentAgent.objects.filter(pk=self.agent.pk).update(
            created_at=timezone.now() - timedelta(seconds=7200),
        )
        process_planning_timeout_task(str(self.agent.id))

        self.assertFalse(PersistentAgentSystemMessage.objects.filter(agent=self.agent).exists())
        delay_mock.assert_not_called()


@tag("batch_agent_chat")
@override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
class UnseenWebChatFollowupTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="unseen-chat-owner",
            email="unseen-chat-owner@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Unseen Chat Browser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Unseen Chat Agent",
            charter="Follow up when chat is missed",
            browser_use_agent=self.browser_agent,
        )
        self.agent_web_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=build_web_agent_address(self.agent.id),
        )
        self.user_web_address = build_web_user_address(self.user.id, self.agent.id)
        self.user_web_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=self.user_web_address,
        )
        self.web_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=self.user_web_address,
        )

    def _create_unread_web_message(self, body="Please review this update"):
        return PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=True,
            from_endpoint=self.agent_web_endpoint,
            to_endpoint=self.user_web_endpoint,
            conversation=self.web_conversation,
            body=body,
            raw_payload={"source": "test"},
        )

    def _add_email_followup_channel(self, *, preferred=False):
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent-followup@example.com",
            is_primary=True,
        )
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address=self.user.email,
        )
        if preferred:
            self.agent.preferred_contact_endpoint = user_endpoint
            self.agent.save(update_fields=["preferred_contact_endpoint", "updated_at"])
        return user_endpoint

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False, WEB_CHAT_UNSEEN_FOLLOWUP_DELAY_SECONDS=3600)
    @patch("api.agent.tasks.process_events.process_unseen_web_chat_followup_task.apply_async")
    def test_schedule_unseen_followup_skips_without_fallback_channel(self, apply_async_mock):
        message = self._create_unread_web_message()

        with self.captureOnCommitCallbacks(execute=True):
            schedule_unseen_web_chat_followup(message)

        apply_async_mock.assert_not_called()

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False, WEB_CHAT_UNSEEN_FOLLOWUP_DELAY_SECONDS=3600)
    @patch("api.agent.tasks.process_events.process_unseen_web_chat_followup_task.apply_async")
    def test_schedule_unseen_followup_queues_when_fallback_channel_exists(self, apply_async_mock):
        message = self._create_unread_web_message()
        self._add_email_followup_channel()

        with self.captureOnCommitCallbacks(execute=True):
            schedule_unseen_web_chat_followup(message)

        apply_async_mock.assert_called_once_with(
            args=[str(message.id)],
            countdown=3600,
        )

    @patch("api.agent.tasks.process_events.schedule_unseen_web_chat_followup")
    def test_inactive_current_reply_schedules_unseen_followup_check(self, schedule_mock):
        self._add_email_followup_channel()
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=self.user_web_endpoint,
            to_endpoint=self.agent_web_endpoint,
            conversation=self.web_conversation,
            body="Please prepare the report",
        )

        result = execute_send_chat_message(
            self.agent,
            {"body": "Can you review this?", "to_address": self.user_web_address},
        )

        self.assertEqual(result["status"], "ok")
        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        schedule_mock.assert_called_once_with(message)

    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    def test_unseen_web_chat_followup_task_skips_read_message(self, delay_mock):
        message = self._create_unread_web_message()
        self._add_email_followup_channel()
        PersistentAgentMessageRead.objects.create(
            message=message,
            user=self.user,
            read_at=timezone.now(),
            read_source="chat_open",
        )

        process_unseen_web_chat_followup_task(str(message.id))

        self.assertFalse(PersistentAgentSystemMessage.objects.filter(agent=self.agent).exists())
        delay_mock.assert_not_called()

    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    def test_unseen_web_chat_followup_task_skips_without_non_web_channel(self, delay_mock):
        message = self._create_unread_web_message()

        process_unseen_web_chat_followup_task(str(message.id))

        self.assertFalse(PersistentAgentSystemMessage.objects.filter(agent=self.agent).exists())
        delay_mock.assert_not_called()

    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    def test_unseen_web_chat_followup_task_skips_when_message_is_not_latest(self, delay_mock):
        older_message = self._create_unread_web_message(body="Older web update")
        self._create_unread_web_message(body="Newer web update")
        self._add_email_followup_channel()

        process_unseen_web_chat_followup_task(str(older_message.id))

        self.assertFalse(PersistentAgentSystemMessage.objects.filter(agent=self.agent).exists())
        delay_mock.assert_not_called()

    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    def test_unseen_web_chat_followup_task_creates_directive_and_queues_processing(self, delay_mock):
        message = self._create_unread_web_message()
        self._add_email_followup_channel(preferred=True)

        process_unseen_web_chat_followup_task(str(message.id))

        directive = PersistentAgentSystemMessage.objects.get(agent=self.agent)
        self.assertIn(str(message.id), directive.body)
        self.assertIn("has not been seen", directive.body)
        self.assertIn(self.user.email, directive.body)
        self.assertIn("send_email", directive.body)
        delay_mock.assert_called_once_with(str(self.agent.id))
