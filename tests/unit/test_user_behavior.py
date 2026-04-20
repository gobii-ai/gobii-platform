from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone
from djstripe.models import Customer
from djstripe.models import Subscription as DjstripeSubscription

from constants.plans import PlanNames
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    TaskCredit,
    UserPhoneNumber,
    build_web_user_address,
)
from util.user_behavior import (
    count_messages_sent_to_gobii,
    get_custom_capi_event_delay_seconds,
    is_fast_cancel_user,
    is_user_currently_in_trial,
)


@tag("batch_pages_signals")
class UserBehaviorUtilsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="behavior-user",
            email="behavior@example.com",
            password="pw",
        )
        self.other_user = get_user_model().objects.create_user(
            username="behavior-other",
            email="other@example.com",
            password="pw",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Behavior Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Behavior Agent",
            charter="charter",
            browser_use_agent=self.browser_agent,
        )
        other_browser_agent = BrowserUseAgent.objects.create(user=self.other_user, name="Other Browser")
        self.other_agent = PersistentAgent.objects.create(
            user=self.other_user,
            name="Other Agent",
            charter="charter",
            browser_use_agent=other_browser_agent,
        )

    @override_settings(TRIAL_FAST_CANCEL_CUTOFF_HOURS=72)
    def test_is_fast_cancel_user_true_when_trial_cancel_scheduled_within_cutoff(self):
        trial_started_at = timezone.now() - timedelta(hours=48)
        TaskCredit.objects.create(
            user=self.user,
            credits=10,
            credits_used=0,
            granted_date=trial_started_at,
            expiration_date=trial_started_at + timedelta(days=14),
            plan=PlanNames.STARTUP,
            free_trial_start=True,
        )
        customer = Customer.objects.create(id="cus_fast_cancel", subscriber=self.user)
        subscription = DjstripeSubscription.objects.create(
            id="sub_fast_cancel",
            customer=customer,
            status="trialing",
            current_period_start=trial_started_at,
            current_period_end=trial_started_at + timedelta(days=14),
            trial_start=trial_started_at,
            trial_end=trial_started_at + timedelta(days=14),
            cancel_at_period_end=True,
            stripe_data={
                "status": "trialing",
                "cancel_at_period_end": True,
            },
        )
        subscription.djstripe_updated = trial_started_at + timedelta(hours=12)
        subscription.save(update_fields=["djstripe_updated"])

        self.assertTrue(is_fast_cancel_user(self.user))

    @override_settings(TRIAL_FAST_CANCEL_CUTOFF_HOURS=72)
    def test_is_fast_cancel_user_false_when_cancel_scheduled_after_cutoff(self):
        trial_started_at = timezone.now() - timedelta(days=5)
        TaskCredit.objects.create(
            user=self.user,
            credits=10,
            credits_used=0,
            granted_date=trial_started_at,
            expiration_date=trial_started_at + timedelta(days=14),
            plan=PlanNames.STARTUP,
            free_trial_start=True,
        )
        customer = Customer.objects.create(id="cus_slow_cancel", subscriber=self.user)
        subscription = DjstripeSubscription.objects.create(
            id="sub_slow_cancel",
            customer=customer,
            status="trialing",
            current_period_start=trial_started_at,
            current_period_end=trial_started_at + timedelta(days=14),
            trial_start=trial_started_at,
            trial_end=trial_started_at + timedelta(days=14),
            cancel_at_period_end=True,
            stripe_data={
                "status": "trialing",
                "cancel_at_period_end": True,
            },
        )
        subscription.djstripe_updated = trial_started_at + timedelta(hours=96)
        subscription.save(update_fields=["djstripe_updated"])

        self.assertFalse(is_fast_cancel_user(self.user))

    def test_is_user_currently_in_trial_true_for_trialing_subscription(self):
        trial_started_at = timezone.now() - timedelta(hours=24)
        customer = Customer.objects.create(id="cus_trialing", subscriber=self.user)
        DjstripeSubscription.objects.create(
            id="sub_trialing",
            customer=customer,
            status="trialing",
            current_period_start=trial_started_at,
            current_period_end=trial_started_at + timedelta(days=14),
            trial_start=trial_started_at,
            trial_end=trial_started_at + timedelta(days=14),
            stripe_data={
                "status": "trialing",
                "current_period_end": int((trial_started_at + timedelta(days=14)).timestamp()),
            },
        )

        self.assertTrue(is_user_currently_in_trial(self.user))

    def test_is_user_currently_in_trial_false_for_active_non_trial_subscription(self):
        period_start = timezone.now() - timedelta(hours=24)
        customer = Customer.objects.create(id="cus_active", subscriber=self.user)
        DjstripeSubscription.objects.create(
            id="sub_active",
            customer=customer,
            status="active",
            current_period_start=period_start,
            current_period_end=period_start + timedelta(days=30),
            stripe_data={
                "status": "active",
                "current_period_end": int((period_start + timedelta(days=30)).timestamp()),
            },
        )

        self.assertFalse(is_user_currently_in_trial(self.user))

    @override_settings(TRIAL_FAST_CANCEL_CUTOFF_HOURS=72, CAPI_CUSTOM_EVENT_DELAY_BUFFER_HOURS=1)
    @patch("util.user_behavior.timezone.now")
    def test_get_custom_capi_event_delay_seconds_uses_remaining_cutoff_plus_buffer(
        self,
        mock_now,
    ):
        trial_started_at = timezone.make_aware(datetime(2026, 1, 1, 9, 0, 0))
        mock_now.return_value = trial_started_at + timedelta(hours=62)
        TaskCredit.objects.create(
            user=self.user,
            credits=10,
            credits_used=0,
            granted_date=trial_started_at,
            expiration_date=trial_started_at + timedelta(days=14),
            plan=PlanNames.STARTUP,
            free_trial_start=True,
        )

        self.assertEqual(get_custom_capi_event_delay_seconds(self.user), 11 * 3600)

    @override_settings(TRIAL_FAST_CANCEL_CUTOFF_HOURS=72, CAPI_CUSTOM_EVENT_DELAY_BUFFER_HOURS=1)
    @patch("util.user_behavior.timezone.now")
    def test_get_custom_capi_event_delay_seconds_uses_buffer_after_cutoff(
        self,
        mock_now,
    ):
        trial_started_at = timezone.make_aware(datetime(2026, 1, 1, 9, 0, 0))
        mock_now.return_value = trial_started_at + timedelta(hours=80)
        TaskCredit.objects.create(
            user=self.user,
            credits=10,
            credits_used=0,
            granted_date=trial_started_at,
            expiration_date=trial_started_at + timedelta(days=14),
            plan=PlanNames.STARTUP,
            free_trial_start=True,
        )

        self.assertEqual(get_custom_capi_event_delay_seconds(self.user), 3600)

    def test_count_messages_sent_to_gobii_counts_owner_addresses_across_channels(self):
        UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+15555550123",
            is_verified=True,
        )

        web_from_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=build_web_user_address(self.user.id, self.agent.id),
        )
        email_from_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address=self.user.email,
        )
        sms_from_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.SMS,
            address="+15555550123",
        )
        other_email_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="someone-else@example.com",
        )
        agent_web_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=f"web://agent/{self.agent.id}",
        )

        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=web_from_endpoint,
            to_endpoint=agent_web_endpoint,
            is_outbound=False,
            body="web message",
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.other_agent,
            from_endpoint=email_from_endpoint,
            is_outbound=False,
            body="email message",
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.other_agent,
            from_endpoint=sms_from_endpoint,
            is_outbound=False,
            body="sms message",
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=other_email_endpoint,
            is_outbound=False,
            body="other inbound",
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=agent_web_endpoint,
            is_outbound=True,
            body="agent outbound",
        )

        self.assertEqual(count_messages_sent_to_gobii(self.user), 3)
