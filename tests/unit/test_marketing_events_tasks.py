from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag, override_settings

from api.models import Organization, OrganizationMembership
from constants.plans import PlanNames
from marketing_events.providers.base import PermanentError
from marketing_events.tasks import (
    _analytics_user_id,
    _complete_registration_candidate_owners,
    enqueue_complete_registration_marketing_event,
    enqueue_delayed_subscription_guarded_marketing_event,
    enqueue_marketing_event,
    enqueue_start_trial_marketing_event,
)
from util.subscription_helper import mark_organization_billing_with_plan


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
                "properties": {"event_time": 1_900_000_000, "event_id": "evt-123"},
                "user": {"id": "42", "email": "test@example.com"},
                "context": {},
            }
        )

        mock_track.assert_called_once()
        kwargs = mock_track.call_args.kwargs
        self.assertEqual(kwargs["user_id"], 42)
        self.assertEqual(kwargs["event"], "CAPI Event Sent")
        self.assertEqual(kwargs["properties"]["provider"], "MetaCAPI")
        self.assertEqual(kwargs["properties"]["event_id"], "evt-123")

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
                "properties": {"event_time": 1_900_000_000, "event_id": "evt-456"},
                "user": {"id": "77", "email": "test@example.com"},
                "context": {},
            }
        )

        mock_track.assert_called_once()
        kwargs = mock_track.call_args.kwargs
        self.assertEqual(kwargs["user_id"], 77)
        self.assertEqual(kwargs["event"], "CAPI Event Failed")
        self.assertEqual(kwargs["properties"]["provider"], "MetaCAPI")
        self.assertEqual(kwargs["properties"]["event_id"], "evt-456")
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
                "properties": {"event_time": 1_900_000_000, "event_id": "evt-789"},
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
        self.assertEqual(kwargs["event"], "CAPI Event Sent")
        self.assertEqual(kwargs["properties"]["provider"], "GoogleAnalyticsMP")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks._complete_registration_candidate_owners")
    @patch("marketing_events.tasks._dispatch_marketing_event")
    @patch("marketing_events.tasks.get_subscription_base_price", return_value=(Decimal("49.99"), "usd"))
    @patch("marketing_events.tasks.get_active_subscription", return_value=MagicMock())
    @patch("marketing_events.tasks.reconcile_user_plan_from_stripe", return_value={"id": "startup", "price": 50, "currency": "USD"})
    @patch("marketing_events.tasks.get_user_model")
    def test_enqueue_complete_registration_rehydrates_paid_plan_value(
        self,
        mock_get_user_model,
        _mock_reconcile_user_plan,
        _mock_get_active_subscription,
        _mock_get_subscription_base_price,
        mock_dispatch,
        mock_candidate_owners,
    ):
        user = MagicMock()
        mock_get_user_model.return_value.objects.get.return_value = user
        mock_candidate_owners.return_value = [{"owner": user, "owner_type": "user"}]

        enqueue_complete_registration_marketing_event(
            {
                "event_name": "CompleteRegistration",
                "properties": {"event_time": 1_900_000_000, "event_id": "evt-200", "plan": "free"},
                "user": {"id": "42", "email": "test@example.com"},
                "context": {},
            }
        )

        mock_dispatch.assert_called_once()
        dispatched_payload = mock_dispatch.call_args.args[0]
        self.assertEqual(dispatched_payload["properties"]["plan"], "startup")
        self.assertAlmostEqual(dispatched_payload["properties"]["value"], 14.997, places=6)
        self.assertEqual(dispatched_payload["properties"]["currency"], "USD")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks._complete_registration_candidate_owners")
    @patch("marketing_events.tasks._dispatch_marketing_event")
    @patch("marketing_events.tasks.get_active_subscription", return_value=None)
    @patch("marketing_events.tasks.reconcile_user_plan_from_stripe", return_value={"id": "free", "price": 0, "currency": "USD"})
    @patch("marketing_events.tasks.get_user_model")
    def test_enqueue_complete_registration_keeps_free_value_at_zero(
        self,
        mock_get_user_model,
        _mock_reconcile_user_plan,
        _mock_get_active_subscription,
        mock_dispatch,
        mock_candidate_owners,
    ):
        user = MagicMock()
        mock_get_user_model.return_value.objects.get.return_value = user
        mock_candidate_owners.return_value = [{"owner": user, "owner_type": "user"}]

        enqueue_complete_registration_marketing_event(
            {
                "event_name": "CompleteRegistration",
                "properties": {"event_time": 1_900_000_000, "event_id": "evt-201"},
                "user": {"id": "42", "email": "test@example.com"},
                "context": {},
            }
        )

        mock_dispatch.assert_called_once()
        dispatched_payload = mock_dispatch.call_args.args[0]
        self.assertEqual(dispatched_payload["properties"]["plan"], "free")
        self.assertEqual(dispatched_payload["properties"]["value"], 0.0)
        self.assertEqual(dispatched_payload["properties"]["currency"], "USD")

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
                },
                "user": {"id": "42", "email": "test@example.com"},
                "context": {},
            }
        )

        mock_dispatch.assert_not_called()
        mock_track.assert_called_once()
        track_kwargs = mock_track.call_args.kwargs
        self.assertEqual(track_kwargs["user_id"], 42)
        self.assertEqual(track_kwargs["event"], "CAPI Event Skipped")
        self.assertEqual(track_kwargs["properties"]["event_name"], "StartTrial")
        self.assertEqual(
            track_kwargs["properties"]["reason"],
            "subscription_canceled_or_cancel_at_period_end",
        )
        self.assertEqual(track_kwargs["properties"]["subscription_id"], "sub_123")
        self.assertEqual(track_kwargs["properties"]["decision_source"], "stripe")

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
        self.assertEqual(track_kwargs["event"], "CAPI Event Skipped")
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
        self.assertEqual(track_kwargs["event"], "CAPI Event Skipped")
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
class CompleteRegistrationOwnerResolutionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="marketing-owner-user",
            email="marketing-owner@example.com",
            password="pw",
        )
        self.other_user = user_model.objects.create_user(
            username="marketing-other-user",
            email="marketing-other@example.com",
            password="pw",
        )

    def test_complete_registration_candidates_include_active_owner_membership_org(self):
        organization = Organization.objects.create(
            name="Acme Org",
            slug="acme-org",
            created_by=self.other_user,
        )
        OrganizationMembership.objects.create(
            org=organization,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        candidates = _complete_registration_candidate_owners(self.user)
        candidate_owners = [candidate["owner"] for candidate in candidates]

        self.assertIn(self.user, candidate_owners)
        self.assertIn(organization, candidate_owners)

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks._dispatch_marketing_event")
    @patch("marketing_events.tasks.get_subscription_base_price", return_value=(Decimal("250"), "usd"))
    @patch("marketing_events.tasks.get_active_subscription")
    @patch("marketing_events.tasks.reconcile_user_plan_from_stripe", return_value={"id": "free", "price": 0, "currency": "USD"})
    def test_enqueue_complete_registration_prefers_paid_org_owner_over_free_user(
        self,
        _mock_reconcile_user_plan,
        mock_get_active_subscription,
        _mock_get_subscription_base_price,
        mock_dispatch,
    ):
        organization = Organization.objects.create(
            name="Paid Org",
            slug="paid-org",
            created_by=self.other_user,
        )
        OrganizationMembership.objects.create(
            org=organization,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        mark_organization_billing_with_plan(organization, PlanNames.ORG_TEAM)

        org_subscription = MagicMock()

        def active_subscription_side_effect(owner, **_kwargs):
            if getattr(owner, "pk", None) == organization.pk:
                return org_subscription
            return None

        mock_get_active_subscription.side_effect = active_subscription_side_effect

        enqueue_complete_registration_marketing_event(
            {
                "event_name": "CompleteRegistration",
                "properties": {"event_time": 1_900_000_000, "event_id": "evt-org-200", "plan": "free"},
                "user": {"id": str(self.user.id), "email": self.user.email},
                "context": {},
            }
        )

        mock_dispatch.assert_called_once()
        dispatched_payload = mock_dispatch.call_args.args[0]
        self.assertEqual(dispatched_payload["properties"]["plan"], PlanNames.ORG_TEAM)
        self.assertEqual(dispatched_payload["properties"]["value"], 75.0)
        self.assertEqual(dispatched_payload["properties"]["currency"], "USD")
