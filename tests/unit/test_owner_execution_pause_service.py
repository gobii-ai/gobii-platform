from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from api.models import BrowserUseAgent, CommsChannel, Organization, PersistentAgent, PersistentAgentCommsEndpoint, UserPhoneNumber
from api.services.owner_execution_pause import (
    EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY,
    EXECUTION_PAUSE_REASON_TRIAL_ENDED_NON_RENEWAL,
    pause_owner_execution,
    pause_owner_execution_by_ref,
    resume_owner_execution,
)
from util.analytics import AnalyticsEvent, AnalyticsSource


@override_settings(PUBLIC_SITE_URL="https://example.com")
@tag("batch_owner_billing")
class OwnerExecutionPauseAnalyticsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="pause-analytics@example.com",
            email="pause-analytics@example.com",
            password="password123",
        )
        self.org = Organization.objects.create(
            name="Pause Analytics Org",
            slug="pause-analytics-org",
            plan="free",
            created_by=self.user,
        )
        self.owner_phone = "+15551234567"
        UserPhoneNumber.objects.create(
            user=self.user,
            phone_number=self.owner_phone,
            is_verified=True,
        )
        self.owner_email_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.EMAIL,
            address=self.user.email,
            defaults={"owner_agent": None, "is_primary": True},
        )
        self.owner_sms_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.SMS,
            address=self.owner_phone,
            defaults={"owner_agent": None, "is_primary": True},
        )

    def _create_agent(
        self,
        *,
        name: str,
        email_address: str | None = None,
        sms_number: str | None = None,
        preferred_endpoint=None,
        last_interaction_at=None,
    ) -> PersistentAgent:
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name=f"{name} Browser")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name=name,
            charter="charter",
            browser_use_agent=browser_agent,
        )
        if last_interaction_at is not None:
            agent.last_interaction_at = last_interaction_at
        if preferred_endpoint is not None:
            agent.preferred_contact_endpoint = preferred_endpoint
        update_fields = []
        if last_interaction_at is not None:
            update_fields.append("last_interaction_at")
        if preferred_endpoint is not None:
            update_fields.append("preferred_contact_endpoint")
        if update_fields:
            agent.save(update_fields=update_fields)

        if email_address:
            PersistentAgentCommsEndpoint.objects.create(
                owner_agent=agent,
                channel=CommsChannel.EMAIL,
                address=email_address,
                is_primary=True,
            )
        if sms_number:
            PersistentAgentCommsEndpoint.objects.create(
                owner_agent=agent,
                channel=CommsChannel.SMS,
                address=sms_number,
                is_primary=True,
            )
        return agent

    @patch("api.services.owner_execution_pause.Analytics.track_event_anonymous")
    @patch("api.services.owner_execution_pause.Analytics.track_event")
    def test_direct_pause_defaults_to_na_medium(
        self,
        mock_track_event,
        mock_track_event_anonymous,
    ):
        changed = pause_owner_execution(
            self.user,
            "trial_conversion_failed",
            source="billing.lifecycle.trial_conversion_failed",
            trigger_agent_cleanup=False,
        )

        self.assertTrue(changed)
        mock_track_event_anonymous.assert_not_called()
        mock_track_event.assert_called_once()

        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.user.id)
        self.assertEqual(kwargs["event"], AnalyticsEvent.ACCOUNT_EXECUTION_PAUSED)
        self.assertEqual(kwargs["source"], AnalyticsSource.NA)
        self.assertEqual(kwargs["properties"]["owner_type"], "user")
        self.assertEqual(kwargs["properties"]["owner_id"], str(self.user.id))
        self.assertEqual(kwargs["properties"]["execution_pause_reason"], "trial_conversion_failed")
        self.assertEqual(kwargs["properties"]["pause_source"], "billing.lifecycle.trial_conversion_failed")
        self.assertFalse(kwargs["properties"]["trigger_agent_cleanup"])
        self.assertIn("paused_at", kwargs["properties"])

    @patch("api.services.owner_execution_pause.Analytics.track_event_anonymous")
    @patch("api.services.owner_execution_pause.Analytics.track_event")
    def test_pause_by_ref_uses_api_medium_for_billing_callers(
        self,
        mock_track_event,
        mock_track_event_anonymous,
    ):
        changed = pause_owner_execution_by_ref(
            "organization",
            self.org.id,
            "billing_delinquency",
            source="billing.lifecycle.subscription_delinquency_entered",
            trigger_agent_cleanup=False,
        )

        self.assertTrue(changed)
        mock_track_event_anonymous.assert_not_called()
        mock_track_event.assert_called_once()

        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.user.id)
        self.assertEqual(kwargs["event"], AnalyticsEvent.ACCOUNT_EXECUTION_PAUSED)
        self.assertEqual(kwargs["source"], AnalyticsSource.API)
        self.assertEqual(kwargs["properties"]["owner_type"], "organization")
        self.assertEqual(kwargs["properties"]["owner_id"], str(self.org.id))
        self.assertEqual(kwargs["properties"]["execution_pause_reason"], "billing_delinquency")
        self.assertEqual(
            kwargs["properties"]["pause_source"],
            "billing.lifecycle.subscription_delinquency_entered",
        )

    @patch("api.services.owner_execution_pause.Analytics.track_event")
    def test_reason_update_does_not_emit_duplicate_pause_event(self, mock_track_event):
        paused_at = timezone.now()
        billing = self.user.billing
        billing.execution_paused = True
        billing.execution_pause_reason = "billing_delinquency"
        billing.execution_paused_at = paused_at
        billing.save(
            update_fields=[
                "execution_paused",
                "execution_pause_reason",
                "execution_paused_at",
            ]
        )

        changed = pause_owner_execution(
            self.user,
            "trial_conversion_failed",
            source="billing.lifecycle.subscription_delinquency_entered",
            paused_at=paused_at,
            trigger_agent_cleanup=False,
        )

        self.assertTrue(changed)
        mock_track_event.assert_not_called()

    @patch("api.agent.comms.outbound_delivery.deliver_agent_sms")
    @patch("api.agent.comms.outbound_delivery.deliver_agent_email")
    @patch("api.services.owner_execution_pause.Analytics.track_event")
    def test_pause_sends_owner_notice_from_latest_interacted_agent(
        self,
        mock_track_event,
        mock_deliver_email,
        mock_deliver_sms,
    ):
        now = timezone.now()
        older_agent = self._create_agent(
            name="Older Agent",
            email_address="older-agent@example.com",
            preferred_endpoint=self.owner_email_endpoint,
            last_interaction_at=now - timedelta(days=2),
        )
        newer_agent = self._create_agent(
            name="Newer Agent",
            email_address="newer-agent@example.com",
            preferred_endpoint=self.owner_email_endpoint,
            last_interaction_at=now - timedelta(hours=1),
        )

        with self.captureOnCommitCallbacks(execute=True):
            changed = pause_owner_execution(
                self.user,
                EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY,
                source="billing.lifecycle.subscription_delinquency_entered",
                trigger_agent_cleanup=False,
            )

        self.assertTrue(changed)
        mock_deliver_sms.assert_not_called()
        mock_deliver_email.assert_called_once()
        outbound = mock_deliver_email.call_args.args[0]
        self.assertEqual(outbound.owner_agent, newer_agent)
        self.assertNotEqual(outbound.owner_agent, older_agent)
        self.assertEqual(outbound.to_endpoint, self.owner_email_endpoint)
        self.assertIn("billing is resolved", outbound.raw_payload.get("subject", ""))

    @patch("api.agent.comms.outbound_delivery.deliver_agent_email")
    @patch("api.services.owner_execution_pause.Analytics.track_event")
    def test_repeat_pause_does_not_resend_owner_notice(self, mock_track_event, mock_deliver_email):
        self._create_agent(
            name="Repeat Agent",
            email_address="repeat-agent@example.com",
            preferred_endpoint=self.owner_email_endpoint,
            last_interaction_at=timezone.now(),
        )

        with self.captureOnCommitCallbacks(execute=True):
            pause_owner_execution(
                self.user,
                EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY,
                source="billing.lifecycle.subscription_delinquency_entered",
                trigger_agent_cleanup=False,
            )
        with self.captureOnCommitCallbacks(execute=True):
            pause_owner_execution(
                self.user,
                EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY,
                source="billing.lifecycle.subscription_delinquency_entered",
                trigger_agent_cleanup=False,
            )

        self.assertEqual(mock_deliver_email.call_count, 1)

    @patch("api.agent.comms.outbound_delivery.deliver_agent_sms")
    @patch("api.agent.comms.outbound_delivery.deliver_agent_email")
    @patch("api.services.owner_execution_pause.Analytics.track_event")
    def test_pause_sends_owner_notice_via_sms_when_latest_agent_prefers_sms(
        self,
        mock_track_event,
        mock_deliver_email,
        mock_deliver_sms,
    ):
        self._create_agent(
            name="SMS Notice Agent",
            sms_number="+15550001111",
            preferred_endpoint=self.owner_sms_endpoint,
            last_interaction_at=timezone.now(),
        )

        with self.captureOnCommitCallbacks(execute=True):
            pause_owner_execution(
                self.user,
                EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY,
                source="billing.lifecycle.subscription_delinquency_entered",
                trigger_agent_cleanup=False,
            )

        mock_deliver_email.assert_not_called()
        mock_deliver_sms.assert_called_once()
        outbound = mock_deliver_sms.call_args.args[0]
        self.assertEqual(outbound.to_endpoint, self.owner_sms_endpoint)
        self.assertIn("billing is resolved", outbound.body)

    @patch("api.agent.comms.outbound_delivery.deliver_agent_email")
    @patch("api.services.owner_execution_pause.Analytics.track_event")
    def test_resume_then_pause_resends_owner_notice(self, mock_track_event, mock_deliver_email):
        agent = self._create_agent(
            name="Resume Agent",
            email_address="resume-agent@example.com",
            preferred_endpoint=self.owner_email_endpoint,
            last_interaction_at=timezone.now(),
        )

        with self.captureOnCommitCallbacks(execute=True):
            pause_owner_execution(
                self.user,
                EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY,
                source="billing.lifecycle.subscription_delinquency_entered",
                trigger_agent_cleanup=False,
            )
        resume_owner_execution(self.user, enqueue_agent_resume=False)
        with self.captureOnCommitCallbacks(execute=True):
            pause_owner_execution(
                self.user,
                EXECUTION_PAUSE_REASON_TRIAL_ENDED_NON_RENEWAL,
                source="billing.lifecycle.trial_ended_non_renewal",
                trigger_agent_cleanup=False,
            )

        outbound_messages = PersistentAgent.objects.get(id=agent.id).agent_messages.filter(
            is_outbound=True,
        )
        self.assertEqual(outbound_messages.count(), 2)
        self.assertGreaterEqual(mock_deliver_email.call_count, 1)

    @patch("api.agent.comms.outbound_delivery.deliver_agent_email")
    @patch("api.agent.comms.outbound_delivery.deliver_agent_sms")
    @patch("api.services.owner_execution_pause.Analytics.track_event")
    def test_pause_skips_owner_notice_when_latest_agent_prefers_web(
        self,
        mock_track_event,
        mock_deliver_sms,
        mock_deliver_email,
    ):
        web_agent = self._create_agent(
            name="Web Preferred Agent",
            email_address="web-pref@example.com",
            last_interaction_at=timezone.now(),
        )
        web_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=f"web://user/{self.user.id}/agent/{web_agent.id}",
            owner_agent=None,
        )
        web_agent.preferred_contact_endpoint = web_endpoint
        web_agent.save(update_fields=["preferred_contact_endpoint"])

        with self.captureOnCommitCallbacks(execute=True):
            pause_owner_execution(
                self.user,
                EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY,
                source="billing.lifecycle.subscription_delinquency_entered",
                trigger_agent_cleanup=False,
            )

        mock_deliver_email.assert_not_called()
        mock_deliver_sms.assert_not_called()

    @patch("api.agent.comms.outbound_delivery.deliver_agent_email")
    @patch("api.agent.comms.outbound_delivery.deliver_agent_sms")
    @patch("api.services.owner_execution_pause.Analytics.track_event")
    def test_pause_skips_owner_notice_when_latest_agent_has_no_preferred_endpoint(
        self,
        mock_track_event,
        mock_deliver_sms,
        mock_deliver_email,
    ):
        self._create_agent(
            name="No Preferred Agent",
            email_address="no-preferred@example.com",
            last_interaction_at=timezone.now(),
        )

        with self.captureOnCommitCallbacks(execute=True):
            pause_owner_execution(
                self.user,
                EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY,
                source="billing.lifecycle.subscription_delinquency_entered",
                trigger_agent_cleanup=False,
            )

        mock_deliver_email.assert_not_called()
        mock_deliver_sms.assert_not_called()

    @patch("api.agent.comms.outbound_delivery.deliver_agent_email")
    @patch("api.agent.comms.outbound_delivery.deliver_agent_sms")
    @patch("api.services.owner_execution_pause.Analytics.track_event")
    def test_pause_skips_owner_notice_when_latest_agent_lacks_email_sender(
        self,
        mock_track_event,
        mock_deliver_sms,
        mock_deliver_email,
    ):
        self._create_agent(
            name="No Sender Agent",
            preferred_endpoint=self.owner_email_endpoint,
            last_interaction_at=timezone.now(),
        )

        with self.captureOnCommitCallbacks(execute=True):
            pause_owner_execution(
                self.user,
                EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY,
                source="billing.lifecycle.subscription_delinquency_entered",
                trigger_agent_cleanup=False,
            )

        mock_deliver_email.assert_not_called()
        mock_deliver_sms.assert_not_called()
