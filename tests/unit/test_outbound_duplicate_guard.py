from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, tag

from api.agent.tools.email_sender import execute_send_email
from api.agent.tools.sms_sender import execute_send_sms
from api.agent.tools.web_chat_sender import execute_send_chat_message
from api.agent.tools.outbound_duplicate_guard import detect_recent_duplicate_message
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
    UserPhoneNumber,
    ToolConfig,
    build_web_agent_address,
    build_web_user_address,
)
from api.services.tool_settings import invalidate_tool_settings_cache
from api.services.web_sessions import start_web_session
from config import settings
from constants.plans import PlanNamesChoices


User = get_user_model()


def create_browser_agent_without_proxy(user, name):
    """Create BrowserUseAgent without triggering proxy selection."""
    with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
        return BrowserUseAgent.objects.create(user=user, name=name)


@patch("django.db.close_old_connections")
@tag("batch_outbound_dedupe")
class OutboundDuplicateGuardTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="dup-user@example.com",
            email="dup-user@example.com",
            password="password123",
        )
        # Email verification is required for outbound email/SMS sending
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        self.browser_agent = create_browser_agent_without_proxy(self.user, "NoProxy Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Duplicate Guard Agent",
            charter="Test charter",
            browser_use_agent=self.browser_agent,
        )

        self.email_address = self.user.email
        default_domain = settings.DEFAULT_AGENT_EMAIL_DOMAIN
        self.email_from = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=f"{self.agent.id}@{default_domain}",
            is_primary=True,
        )

        self.sms_number = "+15550001111"
        UserPhoneNumber.objects.create(
            user=self.user,
            phone_number=self.sms_number,
            is_verified=True,
        )
        self.sms_from = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15559998888",
            is_primary=True,
        )

        self.web_user_address = build_web_user_address(self.user.id, self.agent.id)
        self.web_agent_address = build_web_agent_address(self.agent.id)
        self.web_agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=self.web_agent_address,
            is_primary=True,
        )
        self.web_user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.WEB,
            address=self.web_user_address,
        )
        self.web_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=self.web_user_address,
        )

    @patch("api.agent.tools.email_sender.deliver_agent_email")
    def test_email_duplicate_is_blocked(self, mock_deliver_email, mock_close_old_connections):
        params = {
            "to_address": self.email_address,
            "subject": "Update",
            "mobile_first_html": "<p>Status update</p>",
        }

        first = execute_send_email(self.agent, params)
        self.assertEqual(first.get("status"), "ok")

        mock_deliver_email.reset_mock()
        second = execute_send_email(self.agent, params)
        self.assertEqual(second.get("status"), "error")
        self.assertEqual(mock_deliver_email.call_count, 0)
        self.assertTrue(second.get("duplicate_detected"))

    @patch("api.agent.tools.sms_sender.deliver_agent_sms")
    def test_sms_duplicate_is_blocked(self, mock_deliver_sms, mock_close_old_connections):
        params = {
            "to_number": self.sms_number,
            "body": "Reminder to file the report.",
        }

        first = execute_send_sms(self.agent, params)
        self.assertEqual(first.get("status"), "ok")
        self.assertEqual(mock_deliver_sms.call_count, 1)

        mock_deliver_sms.reset_mock()
        second = execute_send_sms(self.agent, params)
        self.assertEqual(second.get("status"), "error")
        self.assertEqual(mock_deliver_sms.call_count, 0)
        self.assertTrue(second.get("duplicate_detected"))

    def test_web_chat_duplicate_is_blocked(self, mock_close_old_connections):
        start_web_session(self.agent, self.user)
        params = {"body": "Just checking in.", "to_address": self.web_user_address}

        initial_count = PersistentAgentMessage.objects.filter(owner_agent=self.agent, is_outbound=True).count()
        first = execute_send_chat_message(self.agent, params)
        self.assertEqual(first.get("status"), "ok")

        second = execute_send_chat_message(self.agent, params)
        self.assertEqual(second.get("status"), "error")
        self.assertTrue(second.get("duplicate_detected"))

        final_count = PersistentAgentMessage.objects.filter(owner_agent=self.agent, is_outbound=True).count()
        self.assertEqual(final_count, initial_count + 1)

    def test_duplicate_allowed_after_different_message(self, mock_close_old_connections):
        params = {
            "to_address": self.email_address,
            "subject": "Windowed Update",
            "mobile_first_html": "<p>Original</p>",
        }

        first = execute_send_email(self.agent, params)
        self.assertEqual(first.get("status"), "ok")

        followup_params = {
            "to_address": self.email_address,
            "subject": "Windowed Update Variant",
            "mobile_first_html": "<p>Variant</p>",
        }
        followup = execute_send_email(self.agent, followup_params)
        self.assertEqual(followup.get("status"), "ok")

        # Original content should be allowed after a different message was sent in between.
        second = execute_send_email(self.agent, params)
        self.assertEqual(second.get("status"), "ok")

    @patch("api.agent.tools.email_sender.deliver_agent_email")
    def test_duplicate_allows_nonconsecutive_match(self, mock_deliver_email, mock_close_old_connections):
        first_params = {
            "to_address": self.email_address,
            "subject": "Report Reminder",
            "mobile_first_html": "<p>Version A</p>",
        }
        second_params = {
            "to_address": self.email_address,
            "subject": "Report Reminder",
            "mobile_first_html": "<p>Version B</p>",
        }

        first = execute_send_email(self.agent, first_params)
        self.assertEqual(first.get("status"), "ok")

        second = execute_send_email(self.agent, second_params)
        self.assertEqual(second.get("status"), "ok")

        third = execute_send_email(self.agent, first_params)
        self.assertEqual(third.get("status"), "ok")
        self.assertFalse(third.get("duplicate_detected"))
        self.assertEqual(mock_deliver_email.call_count, 3)

    @patch("api.agent.tools.outbound_duplicate_guard._compute_levenshtein_ratio")
    @patch("api.agent.tools.outbound_duplicate_guard._embedding_similarity", return_value=0.985)
    def test_similarity_uses_embeddings_when_available(
        self,
        mock_embedding_similarity,
        mock_levenshtein_ratio,
        mock_close_old_connections,
    ):
        to_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.EMAIL,
            address=self.email_address,
            defaults={"owner_agent": None},
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.email_from,
            to_endpoint=to_endpoint,
            is_outbound=True,
            body="Status update with numbers 123",
            raw_payload={"subject": "Status"},
        )

        result = detect_recent_duplicate_message(
            self.agent,
            channel=CommsChannel.EMAIL,
            body="Status update with numbers 123!",
            to_address=self.email_address,
        )

        self.assertIsNotNone(result)
        assert result is not None  # satisfy type checkers
        self.assertEqual(result.reason, "similarity")
        self.assertGreaterEqual(result.similarity or 0.0, 0.97)
        mock_embedding_similarity.assert_called_once()
        mock_levenshtein_ratio.assert_not_called()

    @patch("api.agent.tools.outbound_duplicate_guard._compute_levenshtein_ratio", return_value=0.99)
    @patch("api.agent.tools.outbound_duplicate_guard._embedding_similarity", return_value=None)
    def test_similarity_falls_back_to_levenshtein_when_embeddings_unavailable(
        self,
        mock_embedding_similarity,
        mock_levenshtein_ratio,
        mock_close_old_connections,
    ):
        to_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.EMAIL,
            address=self.email_address,
            defaults={"owner_agent": None},
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.email_from,
            to_endpoint=to_endpoint,
            is_outbound=True,
            body="Please review the attached file at your convenience.",
            raw_payload={"subject": "Review"},
        )

        result = detect_recent_duplicate_message(
            self.agent,
            channel=CommsChannel.EMAIL,
            body="Please review the attached file soon.",
            to_address=self.email_address,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.reason, "similarity")
        self.assertAlmostEqual(result.similarity or 0.0, 0.99)
        mock_embedding_similarity.assert_called_once()
        mock_levenshtein_ratio.assert_called_once()

    @patch("api.agent.tools.outbound_duplicate_guard._compute_levenshtein_ratio", return_value=0.82)
    @patch("api.agent.tools.outbound_duplicate_guard._embedding_similarity", return_value=None)
    def test_similarity_threshold_respects_tool_config(
        self,
        mock_embedding_similarity,
        mock_levenshtein_ratio,
        mock_close_old_connections,
    ):
        ToolConfig.objects.update_or_create(
            plan_name=PlanNamesChoices.FREE,
            defaults={"duplicate_similarity_threshold": 0.8},
        )
        invalidate_tool_settings_cache()

        to_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.EMAIL,
            address=self.email_address,
            defaults={"owner_agent": None},
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.email_from,
            to_endpoint=to_endpoint,
            is_outbound=True,
            body="Please review the latest draft.",
            raw_payload={"subject": "Draft"},
        )

        result = detect_recent_duplicate_message(
            self.agent,
            channel=CommsChannel.EMAIL,
            body="Please review the latest draft soon.",
            to_address=self.email_address,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.reason, "similarity")
        self.assertAlmostEqual(result.similarity or 0.0, 0.82)
        mock_embedding_similarity.assert_called_once()
        mock_levenshtein_ratio.assert_called_once()
