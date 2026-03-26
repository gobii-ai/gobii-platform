from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.models import TaskCredit, UserTrialActivation
from api.services.trial_activation import (
    ACTIVATION_REASON_NOT_IMPLEMENTED,
    TRIAL_ACTIVATION_VERSION,
    TrialActivationAssessmentResult,
    assess_trial_user_activation,
)
from api.tasks.trial_activation import assess_trial_user_activation_task
from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNames
from marketing_events.constants import AD_CAPI_PROVIDER_TARGETS
from util.analytics import AnalyticsEvent, AnalyticsSource


User = get_user_model()


@tag("batch_marketing_events")
class TrialActivationTests(TestCase):
    def _create_user(self, email: str) -> User:
        return User.objects.create_user(
            username=email,
            email=email,
            password="pw",
        )

    def _grant_trial_start(self, user) -> None:
        now = timezone.now()
        TaskCredit.objects.create(
            user=user,
            credits=Decimal("100"),
            credits_used=Decimal("0"),
            granted_date=now,
            expiration_date=now + timedelta(days=30),
            plan=PlanNames.FREE,
            additional_task=False,
            free_trial_start=True,
            grant_type=GrantTypeChoices.PROMO,
        )

    @patch("api.services.trial_activation.Analytics.track_event")
    def test_assess_persists_stubbed_activation_state_and_tracks_event(self, mock_track_event):
        user = self._create_user("trial-activation@example.com")
        self._grant_trial_start(user)

        result = assess_trial_user_activation(
            user,
            source=AnalyticsSource.API,
            trigger="browser_task_completed",
        )

        activation = UserTrialActivation.objects.get(user=user)

        self.assertFalse(result.activated)
        self.assertFalse(result.newly_activated)
        self.assertTrue(result.is_individual_trial_user)
        self.assertEqual(result.activation_version, TRIAL_ACTIVATION_VERSION)
        self.assertEqual(result.activation_reason, ACTIVATION_REASON_NOT_IMPLEMENTED)
        self.assertFalse(activation.is_activated)
        self.assertIsNone(activation.activated_at)
        self.assertIsNotNone(activation.last_assessed_at)
        self.assertEqual(activation.activation_version, TRIAL_ACTIVATION_VERSION)
        self.assertEqual(activation.activation_reason, ACTIVATION_REASON_NOT_IMPLEMENTED)

        mock_track_event.assert_called_once()
        track_kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(track_kwargs["user_id"], user.id)
        self.assertEqual(track_kwargs["event"], AnalyticsEvent.ACTIVATION_ASSESSED)
        self.assertEqual(track_kwargs["source"], AnalyticsSource.API)
        self.assertEqual(
            track_kwargs["properties"],
            {
                "activated": False,
                "newly_activated": False,
                "activation_version": TRIAL_ACTIVATION_VERSION,
                "activation_reason": ACTIVATION_REASON_NOT_IMPLEMENTED,
                "is_individual_trial_user": True,
                "trigger": "browser_task_completed",
            },
        )

    @patch("api.services.trial_activation.Analytics.track_event")
    def test_assess_keeps_existing_activation_one_way(self, mock_track_event):
        user = self._create_user("already-activated@example.com")
        self._grant_trial_start(user)
        activated_at = timezone.now() - timedelta(days=2)
        UserTrialActivation.objects.create(
            user=user,
            is_activated=True,
            activated_at=activated_at,
            activation_version=99,
            activation_reason="manual_backfill",
            last_assessed_at=activated_at,
        )

        result = assess_trial_user_activation(user, source=AnalyticsSource.API)

        activation = UserTrialActivation.objects.get(user=user)

        self.assertTrue(result.activated)
        self.assertFalse(result.newly_activated)
        self.assertEqual(result.activation_reason, "manual_backfill")
        self.assertTrue(activation.is_activated)
        self.assertEqual(activation.activated_at, activated_at)
        self.assertEqual(activation.activation_reason, "manual_backfill")
        self.assertEqual(activation.activation_version, TRIAL_ACTIVATION_VERSION)
        self.assertGreater(activation.last_assessed_at, activated_at)

        mock_track_event.assert_called_once()
        self.assertTrue(mock_track_event.call_args.kwargs["properties"]["activated"])

    @patch("api.tasks.trial_activation.build_marketing_context_from_user", return_value={"consent": True})
    @patch("api.tasks.trial_activation.capi")
    @patch("api.tasks.trial_activation.assess_trial_user_activation")
    def test_task_sends_capi_only_for_newly_activated_users(
        self,
        mock_assess,
        mock_capi,
        mock_build_context,
    ):
        user = self._create_user("capi-activated@example.com")
        mock_assess.return_value = TrialActivationAssessmentResult(
            activated=True,
            newly_activated=True,
            is_individual_trial_user=True,
            activation_version=TRIAL_ACTIVATION_VERSION,
            activation_reason="real_work_detected_v1",
        )

        result = assess_trial_user_activation_task(user.id, trigger="browser_task_completed")

        self.assertTrue(result)
        mock_assess.assert_called_once_with(
            user,
            source=AnalyticsSource.API,
            trigger="browser_task_completed",
        )
        mock_build_context.assert_called_once_with(
            user,
            synthesized_fbc_source="api.tasks.assess_trial_user_activation_task",
        )
        mock_capi.assert_called_once_with(
            user=user,
            event_name="Activated",
            properties={
                "activation_version": TRIAL_ACTIVATION_VERSION,
                "activation_reason": "real_work_detected_v1",
                "event_id": f"trial-activated:{user.id}:v{TRIAL_ACTIVATION_VERSION}",
                "activation_trigger": "browser_task_completed",
            },
            request=None,
            context={"consent": True},
            provider_targets=AD_CAPI_PROVIDER_TARGETS,
        )

    @patch("api.tasks.trial_activation.capi")
    @patch("api.tasks.trial_activation.assess_trial_user_activation")
    def test_task_skips_capi_when_user_was_not_newly_activated(self, mock_assess, mock_capi):
        user = self._create_user("capi-skipped@example.com")
        mock_assess.return_value = TrialActivationAssessmentResult(
            activated=False,
            newly_activated=False,
            is_individual_trial_user=True,
            activation_version=TRIAL_ACTIVATION_VERSION,
            activation_reason=ACTIVATION_REASON_NOT_IMPLEMENTED,
        )

        result = assess_trial_user_activation_task(user.id, trigger="persistent_agent_completion")

        self.assertFalse(result)
        mock_capi.assert_not_called()
