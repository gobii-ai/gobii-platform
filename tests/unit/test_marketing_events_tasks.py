from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, tag, override_settings

from api.models import UserTrialEligibilityAutoStatusChoices
from marketing_events.providers.base import PermanentError
from marketing_events.tasks import (
    _analytics_user_id,
    enqueue_delayed_subscription_guarded_marketing_event,
    enqueue_marketing_event,
    enqueue_start_trial_marketing_event,
)
from util.analytics import AnalyticsEvent


@tag("batch_marketing_events")
class MarketingEventsTaskTests(SimpleTestCase):
    def test_analytics_user_id_prefers_numeric_raw_user_id(self):
        self.assertEqual(_analytics_user_id("123", "hashed-id"), 123)

    def test_analytics_user_id_falls_back_to_hashed_external_id(self):
        self.assertEqual(_analytics_user_id("", "hashed-id"), "hashed-id")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks.get_providers")
    def test_enqueue_tracks_success_with_raw_user_id(self, mock_get_providers, mock_track):
        provider = MagicMock()
        provider.__class__.__name__ = "MetaCAPI"
        provider.send.return_value = {}
        mock_get_providers.return_value = [provider]

        enqueue_marketing_event(
            {
                "event_name": "StartTrial",
                "properties": {"event_time": 1_900_000_000, "event_id": "evt-123", "value": 375.0},
                "user": {"id": "42", "email": "test@example.com"},
                "context": {},
            }
        )

        mock_track.assert_called_once()
        kwargs = mock_track.call_args.kwargs
        self.assertEqual(kwargs["user_id"], 42)
        self.assertEqual(kwargs["event"], AnalyticsEvent.CAPI_EVENT_SENT)
        self.assertEqual(kwargs["properties"]["provider"], "MetaCAPI")
        self.assertEqual(kwargs["properties"]["event_id"], "evt-123")
        self.assertEqual(kwargs["properties"]["value"], 375.0)

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks.get_providers")
    def test_enqueue_tracks_permanent_failure_with_raw_user_id(self, mock_get_providers, mock_track):
        provider = MagicMock()
        provider.__class__.__name__ = "MetaCAPI"
        provider.send.side_effect = PermanentError("400: bad request")
        mock_get_providers.return_value = [provider]

        enqueue_marketing_event(
            {
                "event_name": "Subscribe",
                "properties": {"event_time": 1_900_000_000, "event_id": "evt-456", "value": 1250.0},
                "user": {"id": "77", "email": "test@example.com"},
                "context": {},
            }
        )

        mock_track.assert_called_once()
        kwargs = mock_track.call_args.kwargs
        self.assertEqual(kwargs["user_id"], 77)
        self.assertEqual(kwargs["event"], AnalyticsEvent.CAPI_EVENT_FAILED)
        self.assertEqual(kwargs["properties"]["provider"], "MetaCAPI")
        self.assertEqual(kwargs["properties"]["event_id"], "evt-456")
        self.assertEqual(kwargs["properties"]["value"], 1250.0)
        self.assertEqual(kwargs["properties"]["error_type"], "permanent")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks.get_providers")
    def test_enqueue_respects_provider_targets(self, mock_get_providers, mock_track):
        meta_provider = MagicMock()
        meta_provider.__class__.__name__ = "MetaCAPI"
        meta_provider.send.return_value = {}

        ga_provider = MagicMock()
        ga_provider.__class__.__name__ = "GoogleAnalyticsMP"
        ga_provider.send.return_value = {}

        mock_get_providers.return_value = [meta_provider, ga_provider]

        enqueue_marketing_event(
            {
                "event_name": "Subscribe",
                "properties": {"event_time": 1_900_000_000, "event_id": "evt-789", "value": 1250.0},
                "user": {"id": "88", "email": "test@example.com"},
                "context": {},
                "provider_targets": ["google_analytics"],
            }
        )

        meta_provider.send.assert_not_called()
        ga_provider.send.assert_called_once()

        mock_track.assert_called_once()
        kwargs = mock_track.call_args.kwargs
        self.assertEqual(kwargs["user_id"], 88)
        self.assertEqual(kwargs["event"], AnalyticsEvent.CAPI_EVENT_SENT)
        self.assertEqual(kwargs["properties"]["provider"], "GoogleAnalyticsMP")
        self.assertEqual(kwargs["properties"]["value"], 1250.0)

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks.get_providers")
    def test_enqueue_provider_targets_can_select_linkedin(self, mock_get_providers, mock_track):
        meta_provider = MagicMock()
        meta_provider.__class__.__name__ = "MetaCAPI"
        meta_provider.send.return_value = {}

        linkedin_provider = MagicMock()
        linkedin_provider.__class__.__name__ = "LinkedInCAPI"
        linkedin_provider.send.return_value = {}

        mock_get_providers.return_value = [meta_provider, linkedin_provider]

        enqueue_marketing_event(
            {
                "event_name": "Activated",
                "properties": {"event_time": 1_900_000_000, "event_id": "evt-linkedin"},
                "user": {"id": "88", "email": "test@example.com"},
                "context": {},
                "provider_targets": ["linkedin"],
            }
        )

        meta_provider.send.assert_not_called()
        linkedin_provider.send.assert_called_once()

        self.assertEqual(mock_track.call_args.kwargs["properties"]["provider"], "LinkedInCAPI")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks.get_providers")
    def test_enqueue_does_not_track_provider_explicit_skip_as_sent(self, mock_get_providers, mock_track):
        linkedin_provider = MagicMock()
        linkedin_provider.__class__.__name__ = "LinkedInCAPI"
        linkedin_provider.send.return_value = False
        mock_get_providers.return_value = [linkedin_provider]

        enqueue_marketing_event(
            {
                "event_name": "UnconfiguredEvent",
                "properties": {"event_time": 1_900_000_000, "event_id": "evt-skip"},
                "user": {"id": "88", "email": "test@example.com"},
                "context": {},
                "provider_targets": ["linkedin"],
            }
        )

        linkedin_provider.send.assert_called_once()
        mock_track.assert_not_called()

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks._dispatch_marketing_event")
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks._subscription_state_from_db", return_value=(None, None))
    @patch("marketing_events.tasks._subscription_state_from_stripe", return_value=(True, "trialing"))
    def test_enqueue_start_trial_skips_when_cancel_at_period_end(
        self,
        _mock_cancel_from_stripe,
        _mock_cancel_from_db,
        mock_track,
        mock_dispatch,
    ):
        enqueue_start_trial_marketing_event(
            {
                "event_name": "StartTrial",
                "properties": {
                    "event_time": 1_900_000_000,
                    "event_id": "evt-123",
                    "subscription_id": "sub_123",
                    "value": 375.0,
                },
                "user": {"id": "42", "email": "test@example.com"},
                "context": {},
            }
        )

        mock_dispatch.assert_not_called()
        mock_track.assert_called_once()
        track_kwargs = mock_track.call_args.kwargs
        self.assertEqual(track_kwargs["user_id"], 42)
        self.assertEqual(track_kwargs["event"], AnalyticsEvent.CAPI_EVENT_SKIPPED)
        self.assertEqual(track_kwargs["properties"]["event_name"], "StartTrial")
        self.assertEqual(
            track_kwargs["properties"]["reason"],
            "subscription_canceled_or_cancel_at_period_end",
        )
        self.assertEqual(track_kwargs["properties"]["subscription_id"], "sub_123")
        self.assertEqual(track_kwargs["properties"]["decision_source"], "stripe")
        self.assertEqual(track_kwargs["properties"]["value"], 375.0)

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks._dispatch_marketing_event")
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks._subscription_state_from_db", return_value=(False, "trialing"))
    @patch("marketing_events.tasks._subscription_state_from_stripe", return_value=(None, None))
    def test_enqueue_start_trial_uses_db_fallback_and_dispatches(
        self,
        _mock_cancel_from_stripe,
        _mock_cancel_from_db,
        mock_track,
        mock_dispatch,
    ):
        payload = {
            "event_name": "StartTrial",
            "properties": {
                "event_time": 1_900_000_000,
                "event_id": "evt-124",
                "subscription_id": "sub_124",
            },
            "user": {"id": "42", "email": "test@example.com"},
            "context": {},
        }

        enqueue_start_trial_marketing_event(payload)

        mock_dispatch.assert_called_once_with(payload)
        mock_track.assert_not_called()

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks._dispatch_marketing_event")
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks._subscription_state_from_db", return_value=(None, None))
    @patch("marketing_events.tasks._subscription_state_from_stripe", return_value=(False, "canceled"))
    def test_enqueue_start_trial_skips_when_subscription_already_canceled(
        self,
        _mock_state_from_stripe,
        _mock_state_from_db,
        mock_track,
        mock_dispatch,
    ):
        enqueue_start_trial_marketing_event(
            {
                "event_name": "StartTrial",
                "properties": {
                    "event_time": 1_900_000_000,
                    "event_id": "evt-125",
                    "subscription_id": "sub_125",
                },
                "user": {"id": "42", "email": "test@example.com"},
                "context": {},
            }
        )

        mock_dispatch.assert_not_called()
        mock_track.assert_called_once()
        track_kwargs = mock_track.call_args.kwargs
        self.assertEqual(track_kwargs["event"], AnalyticsEvent.CAPI_EVENT_SKIPPED)
        self.assertEqual(
            track_kwargs["properties"]["reason"],
            "subscription_canceled_or_cancel_at_period_end",
        )
        self.assertEqual(track_kwargs["properties"]["decision_source"], "stripe")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks._dispatch_marketing_event")
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks._subscription_state_from_db", return_value=(None, None))
    @patch("marketing_events.tasks._subscription_state_from_stripe", return_value=(True, "trialing"))
    def test_enqueue_delayed_subscription_guarded_event_skips_when_cancel_at_period_end(
        self,
        _mock_state_from_stripe,
        _mock_state_from_db,
        mock_track,
        mock_dispatch,
    ):
        enqueue_delayed_subscription_guarded_marketing_event(
            {
                "event_name": "AgentCreated",
                "properties": {
                    "event_time": 1_900_000_000,
                    "event_id": "evt-126",
                    "agent_id": "agent-1",
                },
                "subscription_guard_id": "sub_126",
                "user": {"id": "42", "email": "test@example.com"},
                "context": {},
            }
        )

        mock_dispatch.assert_not_called()
        mock_track.assert_called_once()
        track_kwargs = mock_track.call_args.kwargs
        self.assertEqual(track_kwargs["event"], AnalyticsEvent.CAPI_EVENT_SKIPPED)
        self.assertEqual(track_kwargs["properties"]["event_name"], "AgentCreated")
        self.assertEqual(track_kwargs["properties"]["subscription_id"], "sub_126")
        self.assertEqual(
            track_kwargs["properties"]["reason"],
            "subscription_canceled_or_cancel_at_period_end",
        )

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks._dispatch_marketing_event")
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks._subscription_state_from_db", return_value=(False, "active"))
    @patch("marketing_events.tasks._subscription_state_from_stripe", return_value=(None, None))
    def test_enqueue_delayed_subscription_guarded_event_dispatches_when_not_canceled(
        self,
        _mock_state_from_stripe,
        _mock_state_from_db,
        mock_track,
        mock_dispatch,
    ):
        payload = {
            "event_name": "InboundMessage",
            "properties": {
                "event_time": 1_900_000_000,
                "event_id": "evt-127",
                "agent_id": "agent-1",
            },
            "subscription_guard_id": "sub_127",
            "user": {"id": "42", "email": "test@example.com"},
            "context": {},
        }

        enqueue_delayed_subscription_guarded_marketing_event(payload)

        mock_dispatch.assert_called_once_with(payload)
        mock_track.assert_not_called()


@tag("batch_marketing_events")
class StartTrialEligibilityEnforcementTaskTests(SimpleTestCase):
    def setUp(self):
        self.payload = {
            "event_name": "StartTrial",
            "properties": {
                "event_time": 1_900_000_000,
                "event_id": "evt-eligibility",
                "subscription_id": "sub_eligibility",
                "value": 375.0,
            },
            "user": {"id": "42", "email": "trial-capi-user@example.com"},
            "context": {},
        }

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks._dispatch_marketing_event")
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks._should_send_subscription_guarded_event", return_value=(True, None))
    def test_missing_snapshot_does_not_suppress_start_trial(
        self,
        _mock_subscription_guard,
        mock_track,
        mock_dispatch,
    ):
        enqueue_start_trial_marketing_event(self.payload)

        mock_dispatch.assert_called_once_with(self.payload)
        mock_track.assert_not_called()

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks._dispatch_marketing_event")
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks._should_send_subscription_guarded_event", return_value=(True, None))
    def test_snapshot_disallow_skips_start_trial(
        self,
        _mock_subscription_guard,
        mock_track,
        mock_dispatch,
    ):
        payload = self.payload | {
            "start_trial_eligibility": {
                "decision": UserTrialEligibilityAutoStatusChoices.NO_TRIAL,
                "manual_action": "inherit",
                "reason_codes": ["fpjs_history_match"],
                "send_allowed": False,
                "decision_source": "stored_trial_eligibility_snapshot",
            }
        }

        enqueue_start_trial_marketing_event(payload)

        mock_dispatch.assert_not_called()
        mock_track.assert_called_once()
        track_kwargs = mock_track.call_args.kwargs
        self.assertEqual(track_kwargs["event"], AnalyticsEvent.CAPI_EVENT_SKIPPED)
        self.assertEqual(track_kwargs["properties"]["reason"], "trial_eligibility_disallowed")
        self.assertEqual(
            track_kwargs["properties"]["decision_source"],
            "stored_trial_eligibility_snapshot",
        )
        self.assertEqual(
            track_kwargs["properties"]["trial_eligibility_decision"],
            UserTrialEligibilityAutoStatusChoices.NO_TRIAL,
        )
        self.assertEqual(
            track_kwargs["properties"]["trial_eligibility_reason_codes"],
            ["fpjs_history_match"],
        )
        self.assertFalse(track_kwargs["properties"]["trial_eligibility_policy_send_allowed"])
        self.assertEqual(track_kwargs["properties"]["value"], 375.0)

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks._dispatch_marketing_event")
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks._should_send_subscription_guarded_event", return_value=(True, None))
    def test_snapshot_allow_review_dispatches_start_trial(
        self,
        _mock_subscription_guard,
        mock_track,
        mock_dispatch,
    ):
        payload = self.payload | {
            "start_trial_eligibility": {
                "decision": UserTrialEligibilityAutoStatusChoices.REVIEW,
                "manual_action": "inherit",
                "reason_codes": ["multi_signal_history_match"],
                "send_allowed": True,
                "decision_source": "stored_trial_eligibility_snapshot",
            }
        }

        enqueue_start_trial_marketing_event(payload)

        mock_dispatch.assert_called_once_with(payload)
        mock_track.assert_not_called()


@tag("batch_marketing_events")
class CompleteRegistrationEligibilityEnforcementTaskTests(SimpleTestCase):
    def setUp(self):
        self.payload = {
            "event_name": "CompleteRegistration",
            "properties": {
                "event_time": 1_900_000_000,
                "event_id": "evt-complete-registration",
                "value": 10.0,
            },
            "user": {"id": "42", "email": "signup-capi-user@example.com"},
            "context": {},
        }

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks.get_providers")
    def test_enqueue_complete_registration_skips_all_providers_when_snapshot_disallows(
        self,
        mock_get_providers,
        mock_track,
    ):
        meta_provider = MagicMock()
        meta_provider.__class__.__name__ = "MetaCAPI"
        meta_provider.send.return_value = {}

        ga_provider = MagicMock()
        ga_provider.__class__.__name__ = "GoogleAnalyticsMP"
        ga_provider.send.return_value = {}

        mock_get_providers.return_value = [meta_provider, ga_provider]
        payload = self.payload | {
            "complete_registration_eligibility": {
                "decision": UserTrialEligibilityAutoStatusChoices.NO_TRIAL,
                "manual_action": "inherit",
                "reason_codes": ["fpjs_history_match"],
                "send_allowed": False,
                "decision_source": "stored_trial_eligibility_snapshot",
            }
        }

        enqueue_marketing_event(payload)

        meta_provider.send.assert_not_called()
        ga_provider.send.assert_not_called()

        mock_track.assert_called_once()
        track_kwargs = mock_track.call_args.kwargs
        self.assertEqual(track_kwargs["user_id"], 42)
        self.assertEqual(track_kwargs["event"], AnalyticsEvent.CAPI_EVENT_SKIPPED)
        self.assertEqual(track_kwargs["properties"]["event_name"], "CompleteRegistration")
        self.assertEqual(track_kwargs["properties"]["reason"], "trial_eligibility_disallowed")
        self.assertEqual(
            track_kwargs["properties"]["trial_eligibility_decision"],
            UserTrialEligibilityAutoStatusChoices.NO_TRIAL,
        )
        self.assertFalse(track_kwargs["properties"]["trial_eligibility_policy_send_allowed"])
        self.assertEqual(track_kwargs["properties"]["value"], 10.0)

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks.get_providers")
    def test_enqueue_complete_registration_dispatches_all_providers_when_snapshot_allows(
        self,
        mock_get_providers,
        mock_track,
    ):
        meta_provider = MagicMock()
        meta_provider.__class__.__name__ = "MetaCAPI"
        meta_provider.send.return_value = {}

        ga_provider = MagicMock()
        ga_provider.__class__.__name__ = "GoogleAnalyticsMP"
        ga_provider.send.return_value = {}

        mock_get_providers.return_value = [meta_provider, ga_provider]
        payload = self.payload | {
            "complete_registration_eligibility": {
                "decision": UserTrialEligibilityAutoStatusChoices.REVIEW,
                "manual_action": "inherit",
                "reason_codes": ["multi_signal_history_match"],
                "send_allowed": True,
                "decision_source": "stored_trial_eligibility_snapshot",
            }
        }

        enqueue_marketing_event(payload)

        meta_provider.send.assert_called_once()
        ga_provider.send.assert_called_once()
        self.assertEqual(mock_track.call_count, 2)
        self.assertTrue(
            all(call.kwargs["event"] == AnalyticsEvent.CAPI_EVENT_SENT for call in mock_track.call_args_list)
        )
