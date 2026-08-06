from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from api.models import Organization, TaskCredit
from api.services.task_credit_analytics import (
    TaskCreditGrantOperation,
    TaskCreditGrantSource,
    capture_task_credit_billing_context,
    track_task_credit_grant,
)
from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNames
from billing.addons import AddonEntitlementService
from tasks.services import TaskCreditService
from util.analytics import AnalyticsEvent
from util.analytics_billing import (
    AnalyticsAccessType,
    AnalyticsBillingContext,
    AnalyticsBillingStatus,
    bind_request_billing_context_cache,
    reset_request_billing_context_cache,
    resolve_analytics_billing_context_safely,
)


User = get_user_model()


@tag("batch_task_credits")
@override_settings(SEGMENT_WRITE_KEY="test-segment-key")
class TaskCreditGrantAnalyticsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="credit-analytics-user")
        TaskCredit.objects.filter(user=self.user).update(voided=True)

    def _personal_credit(self, **overrides):
        fields = {
            "user": self.user,
            "credits": Decimal("12.500"),
            "credits_used": Decimal("0"),
            "granted_date": timezone.now(),
            "expiration_date": timezone.now() + timedelta(days=30),
            "plan": PlanNames.STARTUP,
            "grant_type": GrantTypeChoices.PROMO,
            "additional_task": False,
            "voided": False,
        }
        fields.update(overrides)
        return TaskCredit.objects.create(**fields)

    def test_event_contains_grant_contract_and_explicit_trial_snapshot(self):
        task_credit = self._personal_credit(stripe_invoice_id="inv_credit_analytics")
        billing_context = AnalyticsBillingContext(
            organization_id=f"user:{self.user.id}",
            plan_at_event=PlanNames.STARTUP,
            access_type_at_event=AnalyticsAccessType.TRIAL,
            billing_status_at_event=AnalyticsBillingStatus.TRIALING,
            is_internal=False,
        )

        with patch("util.analytics.analytics.track") as mock_segment_track:
            with self.captureOnCommitCallbacks(execute=True):
                track_task_credit_grant(
                    task_credit,
                    credits_granted=Decimal("12.500"),
                    operation=TaskCreditGrantOperation.CREATED,
                    grant_source=TaskCreditGrantSource.STAFF_CONSOLE,
                    automated=False,
                    grant_actor_user_id=987,
                    billing_context=billing_context,
                )

        mock_segment_track.assert_called_once()
        user_id, event, properties, _context = mock_segment_track.call_args.args
        self.assertEqual(user_id, self.user.id)
        self.assertEqual(event, AnalyticsEvent.TASK_CREDITS_GRANTED)
        self.assertEqual(properties["task_credit_id"], str(task_credit.id))
        self.assertEqual(properties["credits_granted"], 12.5)
        self.assertEqual(properties["grant_type"], GrantTypeChoices.PROMO)
        self.assertEqual(properties["grant_source"], TaskCreditGrantSource.STAFF_CONSOLE)
        self.assertEqual(properties["grant_operation"], TaskCreditGrantOperation.CREATED)
        self.assertFalse(properties["automated"])
        self.assertEqual(properties["credit_plan"], PlanNames.STARTUP)
        self.assertEqual(properties["owner_type"], "user")
        self.assertEqual(properties["owner_id"], str(self.user.id))
        self.assertEqual(properties["grant_actor_user_id"], "987")
        self.assertEqual(properties["stripe.invoice_id"], "inv_credit_analytics")
        self.assertEqual(properties["plan_at_event"], PlanNames.STARTUP)
        self.assertEqual(properties["access_type_at_event"], AnalyticsAccessType.TRIAL)
        self.assertEqual(properties["billing_status_at_event"], AnalyticsBillingStatus.TRIALING)
        self.assertTrue(properties["is_trial"])
        self.assertFalse(properties["is_internal"])

    def test_billing_snapshot_is_immutable_until_commit_callback_runs(self):
        task_credit = self._personal_credit(grant_type=GrantTypeChoices.PLAN)

        with patch("util.analytics.analytics.track") as mock_segment_track:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                track_task_credit_grant(
                    task_credit,
                    credits_granted=task_credit.credits,
                    operation=TaskCreditGrantOperation.CREATED,
                    grant_source=TaskCreditGrantSource.MONTHLY_FREE_GRANT,
                    automated=True,
                )

            self.user.billing.subscription = PlanNames.SCALE
            self.user.billing.save(update_fields=["subscription"])
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()

        properties = mock_segment_track.call_args.args[2]
        self.assertEqual(properties["plan_at_event"], PlanNames.FREE)
        self.assertFalse(properties["is_trial"])

    def test_grant_snapshot_ignores_stale_request_and_owner_caches(self):
        task_credit = self._personal_credit(grant_type=GrantTypeChoices.PLAN)
        token = bind_request_billing_context_cache()
        try:
            stale_context = resolve_analytics_billing_context_safely(
                self.user.id,
                actor_user=self.user,
                billing_owner=self.user,
            )
            self.assertEqual(stale_context.plan_at_event, PlanNames.FREE)

            type(self.user.billing).objects.filter(user=self.user).update(
                subscription=PlanNames.SCALE,
            )
            captured_context = capture_task_credit_billing_context(task_credit)
        finally:
            reset_request_billing_context_cache(token)

        self.assertEqual(captured_context.plan_at_event, "scale")

    def test_organization_grant_uses_creator_and_org_billing_context(self):
        organization = Organization.objects.create(
            name="Credit Analytics Org",
            slug="credit-analytics-org",
            created_by=self.user,
        )
        organization.billing.subscription = PlanNames.ORG_TEAM
        organization.billing.save(update_fields=["subscription"])
        task_credit = TaskCredit.objects.create(
            organization=organization,
            credits=Decimal("22.000"),
            credits_used=0,
            granted_date=timezone.now(),
            expiration_date=timezone.now() + timedelta(days=30),
            plan=PlanNames.ORG_TEAM,
            grant_type=GrantTypeChoices.COMPENSATION,
        )

        with patch("util.analytics.analytics.track") as mock_segment_track:
            with self.captureOnCommitCallbacks(execute=True):
                track_task_credit_grant(
                    task_credit,
                    credits_granted=task_credit.credits,
                    operation=TaskCreditGrantOperation.CREATED,
                    grant_source=TaskCreditGrantSource.STAFF_CONSOLE,
                    automated=False,
                    grant_actor_user_id=self.user.id,
                )

        user_id, _event, properties, _context = mock_segment_track.call_args.args
        self.assertEqual(user_id, self.user.id)
        self.assertEqual(properties["owner_type"], "organization")
        self.assertEqual(properties["owner_id"], str(organization.id))
        self.assertTrue(properties["organization"])
        self.assertEqual(properties["organization_id"], str(organization.id))
        self.assertEqual(properties["plan_at_event"], PlanNames.ORG_TEAM)

    def test_non_usable_rows_and_non_positive_amounts_are_not_tracked(self):
        additional_task = self._personal_credit(
            additional_task=True,
            credits_used=Decimal("1"),
        )
        voided_credit = self._personal_credit(voided=True)

        with patch("api.services.task_credit_analytics.Analytics.track_event") as mock_track:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                for task_credit, amount in (
                    (additional_task, Decimal("1")),
                    (voided_credit, Decimal("1")),
                    (self._personal_credit(), Decimal("0")),
                ):
                    track_task_credit_grant(
                        task_credit,
                        credits_granted=amount,
                        operation=TaskCreditGrantOperation.CREATED,
                        grant_source=TaskCreditGrantSource.SUBSCRIPTION,
                        automated=True,
                    )

        self.assertEqual(callbacks, [])
        mock_track.assert_not_called()

    def test_every_grant_type_is_preserved_in_event_properties(self):
        billing_context = AnalyticsBillingContext(
            organization_id=f"user:{self.user.id}",
            plan_at_event=PlanNames.STARTUP,
            access_type_at_event=AnalyticsAccessType.PAID,
            billing_status_at_event=AnalyticsBillingStatus.ACTIVE,
            is_internal=False,
        )

        with patch("util.analytics.analytics.track") as mock_segment_track:
            with self.captureOnCommitCallbacks(execute=True):
                for grant_type in GrantTypeChoices.values:
                    task_credit = self._personal_credit(grant_type=grant_type)
                    track_task_credit_grant(
                        task_credit,
                        credits_granted=task_credit.credits,
                        operation=TaskCreditGrantOperation.CREATED,
                        grant_source=TaskCreditGrantSource.SUBSCRIPTION,
                        automated=True,
                        billing_context=billing_context,
                    )

        emitted_types = [call.args[2]["grant_type"] for call in mock_segment_track.call_args_list]
        self.assertEqual(emitted_types, list(GrantTypeChoices.values))

    @patch("api.models.track_task_credit_grant")
    def test_signup_bootstrap_tracks_after_billing_record_exists(self, mock_track):
        signup_user = User.objects.create_user(username="credit-analytics-signup")

        mock_track.assert_called_once()
        task_credit = mock_track.call_args.args[0]
        self.assertEqual(task_credit.user_id, signup_user.id)
        self.assertTrue(hasattr(signup_user, "billing"))
        self.assertEqual(mock_track.call_args.kwargs["grant_source"], TaskCreditGrantSource.SIGNUP_BOOTSTRAP)
        self.assertTrue(mock_track.call_args.kwargs["automated"])

    def test_analytics_transport_failure_does_not_fail_committed_grant(self):
        task_credit = self._personal_credit()
        billing_context = AnalyticsBillingContext(
            organization_id=f"user:{self.user.id}",
            plan_at_event=PlanNames.STARTUP,
            access_type_at_event=AnalyticsAccessType.PAID,
            billing_status_at_event=AnalyticsBillingStatus.ACTIVE,
            is_internal=False,
        )

        with patch("util.analytics.analytics.track", side_effect=RuntimeError("segment unavailable")), \
             patch("util.analytics.logger.exception") as mock_logger:
            with self.captureOnCommitCallbacks(execute=True):
                track_task_credit_grant(
                    task_credit,
                    credits_granted=task_credit.credits,
                    operation=TaskCreditGrantOperation.CREATED,
                    grant_source=TaskCreditGrantSource.SUBSCRIPTION,
                    automated=True,
                    billing_context=billing_context,
                )

        self.assertTrue(TaskCredit.objects.filter(pk=task_credit.pk).exists())
        mock_logger.assert_called_once()

    def test_rolled_back_transaction_discards_pending_event(self):
        task_credit = self._personal_credit()

        with patch("api.services.task_credit_analytics.Analytics.track_event") as mock_track:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                try:
                    with transaction.atomic():
                        track_task_credit_grant(
                            task_credit,
                            credits_granted=task_credit.credits,
                            operation=TaskCreditGrantOperation.CREATED,
                            grant_source=TaskCreditGrantSource.SUBSCRIPTION,
                            automated=True,
                        )
                        raise ValueError("roll back grant")
                except ValueError:
                    pass

        self.assertEqual(callbacks, [])
        mock_track.assert_not_called()

    @patch("tasks.services.track_task_credit_grant")
    def test_subscription_grant_tracks_once_and_duplicate_invoice_is_a_noop(self, mock_track):
        plan = {"id": PlanNames.STARTUP, "monthly_task_credits": 25}

        first = TaskCreditService.grant_subscription_credits(
            self.user,
            plan=plan,
            credit_override=Decimal("25"),
            invoice_id="inv-idempotent-credit-grant",
            grant_source=TaskCreditGrantSource.SUBSCRIPTION_CREATE,
        )
        duplicate = TaskCreditService.grant_subscription_credits(
            self.user,
            plan=plan,
            credit_override=Decimal("25"),
            invoice_id="inv-idempotent-credit-grant",
            grant_source=TaskCreditGrantSource.SUBSCRIPTION_CREATE,
        )

        self.assertEqual(first, Decimal("25"))
        self.assertEqual(duplicate, 0)
        mock_track.assert_called_once()
        self.assertEqual(mock_track.call_args.kwargs["credits_granted"], Decimal("25"))
        self.assertEqual(mock_track.call_args.kwargs["operation"], TaskCreditGrantOperation.CREATED)
        self.assertTrue(mock_track.call_args.kwargs["automated"])

    @patch("tasks.services.track_task_credit_grant")
    def test_subscription_grant_can_suppress_backfill_analytics(self, mock_track):
        TaskCreditService.grant_subscription_credits(
            self.user,
            plan={"id": PlanNames.STARTUP, "monthly_task_credits": 5},
            credit_override=5,
            invoice_id="inv-backfill-credit-grant",
            emit_grant_analytics=False,
        )

        mock_track.assert_not_called()

    @patch("tasks.services.track_task_credit_grant")
    def test_organization_renewal_tracks_replenishment_once(self, mock_track):
        organization = Organization.objects.create(
            name="Credit Renewal Org",
            slug="credit-renewal-org",
            created_by=self.user,
        )
        organization.billing.subscription = PlanNames.ORG_TEAM
        organization.billing.save(update_fields=["subscription"])
        task_credit = TaskCredit.objects.create(
            organization=organization,
            credits=Decimal("20"),
            credits_used=Decimal("15"),
            granted_date=timezone.now() - timedelta(days=30),
            expiration_date=timezone.now() + timedelta(days=1),
            plan=PlanNames.ORG_TEAM,
            grant_type=GrantTypeChoices.PLAN,
        )

        TaskCreditService.grant_subscription_credits_for_organization(
            organization,
            seats=2,
            plan={"id": PlanNames.ORG_TEAM, "credits_per_seat": 10},
            invoice_id="inv-org-credit-renewal",
            grant_date=timezone.now(),
            expiration_date=timezone.now() + timedelta(days=30),
            replace_current=True,
            grant_source=TaskCreditGrantSource.SUBSCRIPTION_RENEWAL,
        )
        replay = TaskCreditService.grant_subscription_credits_for_organization(
            organization,
            seats=2,
            plan={"id": PlanNames.ORG_TEAM, "credits_per_seat": 10},
            invoice_id="inv-org-credit-renewal",
            replace_current=True,
            grant_source=TaskCreditGrantSource.SUBSCRIPTION_RENEWAL,
        )

        self.assertEqual(replay, 0)
        mock_track.assert_called_once()
        self.assertEqual(mock_track.call_args.kwargs["operation"], TaskCreditGrantOperation.REPLENISHED)
        self.assertEqual(mock_track.call_args.kwargs["credits_granted"], Decimal("20"))
        self.assertEqual(mock_track.call_args.args[0].pk, task_credit.pk)

    @patch("api.services.task_credit_analytics.track_task_credit_grant")
    def test_task_pack_tracks_creation_and_only_positive_increases(self, mock_track):
        starts_at = timezone.now()
        entitlement = type(
            "EntitlementStub",
            (),
            {
                "price_id": "price_credit_analytics_pack",
                "starts_at": starts_at,
                "expires_at": starts_at + timedelta(days=30),
                "task_credits_delta": 10,
                "quantity": 1,
            },
        )()

        AddonEntitlementService._upsert_task_credit_block(
            self.user,
            "user",
            PlanNames.STARTUP,
            entitlement,
            entitlement.expires_at,
        )
        entitlement.quantity = 2
        AddonEntitlementService._upsert_task_credit_block(
            self.user,
            "user",
            PlanNames.STARTUP,
            entitlement,
            entitlement.expires_at,
        )
        AddonEntitlementService._upsert_task_credit_block(
            self.user,
            "user",
            PlanNames.STARTUP,
            entitlement,
            entitlement.expires_at,
        )
        entitlement.quantity = 1
        AddonEntitlementService._upsert_task_credit_block(
            self.user,
            "user",
            PlanNames.STARTUP,
            entitlement,
            entitlement.expires_at,
        )

        self.assertEqual(mock_track.call_count, 2)
        self.assertEqual(mock_track.call_args_list[0].kwargs["operation"], TaskCreditGrantOperation.CREATED)
        self.assertEqual(mock_track.call_args_list[0].kwargs["credits_granted"], Decimal("10"))
        self.assertEqual(mock_track.call_args_list[1].kwargs["operation"], TaskCreditGrantOperation.INCREASED)
        self.assertEqual(mock_track.call_args_list[1].kwargs["credits_granted"], Decimal("10"))

    def test_legacy_staff_endpoint_tracks_manual_actor(self):
        staff_user = User.objects.create_superuser(username="credit-analytics-staff")
        self.client.force_login(staff_user)

        with patch("console.views.track_task_credit_grant") as mock_track:
            response = self.client.post(reverse("grant_credits"), {"user_id": self.user.id})

        self.assertEqual(response.status_code, 200)
        mock_track.assert_called_once()
        self.assertEqual(mock_track.call_args.kwargs["grant_source"], TaskCreditGrantSource.LEGACY_STAFF_ENDPOINT)
        self.assertFalse(mock_track.call_args.kwargs["automated"])
        self.assertEqual(mock_track.call_args.kwargs["grant_actor_user_id"], staff_user.id)
