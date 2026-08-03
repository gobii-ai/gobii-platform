import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings, tag
from django.utils import timezone

from api.models import Organization, OrganizationMembership, UserBilling, UserFlags
from constants.plans import PlanNames
from middleware.analytics_billing import AnalyticsBillingContextCacheMiddleware
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.analytics_billing import (
    AnalyticsAccessType,
    AnalyticsBillingContext,
    AnalyticsBillingStatus,
    EVENT_SCHEMA_VERSION,
    resolve_analytics_billing_context_safely,
)


class _FakeCustomer:
    def __init__(self, status: str, *, subscription_id: str = "sub_test"):
        subscription = SimpleNamespace(
            id=subscription_id,
            status=status,
            stripe_data={
                "status": status,
                "created": 1,
                "current_period_end": 4_102_444_800,
            },
        )
        self.subscriptions = SimpleNamespace(all=lambda: [subscription])


@tag("batch_analytics_billing")
@override_settings(
    SEGMENT_WRITE_KEY="segment-test",
    SEGMENT_WEB_WRITE_KEY="web-segment-test",
    SEGMENT_WEB_ENABLE_IN_DEBUG=True,
)
class AnalyticsBillingEnrichmentTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="analytics-customer",
            email="customer@example.com",
            password="password",
        )

    def _set_personal_plan(self, plan: str):
        billing = UserBilling.objects.get(user=self.user)
        billing.subscription = plan
        billing.save(update_fields=["subscription"])
        self.user.refresh_from_db()
        return self.user

    def _capture_event(
        self,
        *,
        event=AnalyticsEvent.TASK_CREATED,
        status: str | None = None,
        actor=None,
        owner=None,
        properties=None,
    ) -> dict:
        actor = actor or self.user
        owner = owner or actor
        customer = _FakeCustomer(status) if status else None
        with (
            patch("util.analytics_billing.get_stripe_customer", return_value=customer),
            patch("util.analytics.analytics.track") as track_mock,
        ):
            Analytics.track_event(
                user_id=actor.pk,
                event=event,
                source=AnalyticsSource.WEB,
                properties=properties,
                user=actor,
                billing_owner=owner,
            )
        self.assertEqual(track_mock.call_count, 1)
        return track_mock.call_args.args[2]

    def _paid_context(self):
        return AnalyticsBillingContext(
            organization_id=f"user:{self.user.pk}",
            plan_at_event="startup",
            access_type_at_event=AnalyticsAccessType.PAID,
            billing_status_at_event=AnalyticsBillingStatus.ACTIVE,
            is_internal=False,
        )

    def test_active_paid_startup_and_scale_accounts(self):
        cases = (
            (PlanNames.STARTUP, "startup"),
            (PlanNames.SCALE, "scale"),
        )
        for legacy_plan, expected_plan in cases:
            with self.subTest(plan=legacy_plan):
                actor = self._set_personal_plan(legacy_plan)
                properties = self._capture_event(status="active", actor=actor, owner=actor)
                self.assertEqual(properties["plan_at_event"], expected_plan)
                self.assertEqual(properties["access_type_at_event"], AnalyticsAccessType.PAID)
                self.assertEqual(properties["billing_status_at_event"], AnalyticsBillingStatus.ACTIVE)
                self.assertEqual(properties["organization_id"], f"user:{actor.pk}")

    def test_cc_required_trial(self):
        actor = self._set_personal_plan(PlanNames.STARTUP)
        properties = self._capture_event(status="trialing", actor=actor, owner=actor)
        self.assertEqual(properties["access_type_at_event"], AnalyticsAccessType.TRIAL)
        self.assertEqual(properties["billing_status_at_event"], AnalyticsBillingStatus.TRIALING)

    def test_grandfathered_free_user(self):
        UserFlags.objects.update_or_create(
            user=self.user,
            defaults={"is_freemium_grandfathered": True},
        )
        self.user.refresh_from_db()

        properties = self._capture_event(actor=self.user, owner=self.user)
        self.assertEqual(properties["plan_at_event"], "free")
        self.assertEqual(properties["access_type_at_event"], AnalyticsAccessType.GRANDFATHERED_FREE)
        self.assertEqual(properties["billing_status_at_event"], AnalyticsBillingStatus.NONE)

    def test_internal_staff_user_overrides_billing_classification(self):
        actor = self._set_personal_plan(PlanNames.STARTUP)
        actor.is_staff = True
        actor.save(update_fields=["is_staff"])

        properties = self._capture_event(status="active", actor=actor, owner=actor)
        self.assertTrue(properties["is_internal"])
        self.assertEqual(properties["access_type_at_event"], AnalyticsAccessType.INTERNAL)

    def test_multi_organization_user_uses_action_organization(self):
        first = Organization.objects.create(
            name="First Account",
            slug="analytics-first-account",
            created_by=self.user,
        )
        second = Organization.objects.create(
            name="Second Account",
            slug="analytics-second-account",
            created_by=self.user,
        )
        for organization in (first, second):
            OrganizationMembership.objects.create(
                org=organization,
                user=self.user,
                role=OrganizationMembership.OrgRole.OWNER,
            )
        second.billing.subscription = PlanNames.ORG_TEAM
        second.billing.stripe_subscription_id = "sub_second"
        second.billing.save(update_fields=["subscription", "stripe_subscription_id"])

        properties = self._capture_event(
            status="active",
            owner=second,
            properties={"organization_id": str(first.pk)},
        )
        self.assertEqual(properties["organization_id"], str(second.pk))
        self.assertEqual(properties["plan_at_event"], "org_team")
        self.assertEqual(properties["access_type_at_event"], AnalyticsAccessType.PAID)

    def test_missing_billing_data_is_unknown(self):
        UserBilling.objects.filter(user=self.user).delete()
        self.user.refresh_from_db()

        properties = self._capture_event(actor=self.user, owner=self.user)
        self.assertEqual(properties["plan_at_event"], "unknown")
        self.assertEqual(properties["billing_status_at_event"], AnalyticsBillingStatus.UNKNOWN)
        self.assertEqual(properties["access_type_at_event"], AnalyticsAccessType.UNKNOWN)

    def test_missing_organization_is_not_misclassified_as_personal(self):
        missing_organization_id = str(uuid.uuid4())
        with patch("util.analytics.analytics.track") as track_mock:
            Analytics.track_event(
                user_id=self.user.pk,
                event=AnalyticsEvent.TASK_CREATED,
                source=AnalyticsSource.API,
                properties={"organization_id": missing_organization_id},
                user=self.user,
            )

        properties = track_mock.call_args.args[2]
        self.assertEqual(properties["organization_id"], missing_organization_id)
        self.assertEqual(properties["plan_at_event"], "unknown")
        self.assertEqual(properties["access_type_at_event"], AnalyticsAccessType.UNKNOWN)

    def test_past_due_and_canceled_subscriptions_are_not_paid_activity(self):
        actor = self._set_personal_plan(PlanNames.STARTUP)
        for status in ("past_due", "canceled"):
            with self.subTest(status=status):
                properties = self._capture_event(status=status, actor=actor, owner=actor)
                self.assertEqual(properties["billing_status_at_event"], status)
                self.assertEqual(properties["access_type_at_event"], AnalyticsAccessType.NONE)

    def test_canceled_free_subscription_is_no_access_not_unknown(self):
        actor = self._set_personal_plan(PlanNames.FREE)

        properties = self._capture_event(status="canceled", actor=actor, owner=actor)

        self.assertEqual(properties["plan_at_event"], PlanNames.FREE)
        self.assertEqual(properties["billing_status_at_event"], AnalyticsBillingStatus.CANCELED)
        self.assertEqual(properties["access_type_at_event"], AnalyticsAccessType.NONE)

    def test_plan_and_status_transition_is_snapshotted_per_event(self):
        actor = self._set_personal_plan(PlanNames.STARTUP)
        trial_properties = self._capture_event(status="trialing", actor=actor, owner=actor)

        actor = self._set_personal_plan(PlanNames.SCALE)
        paid_properties = self._capture_event(status="active", actor=actor, owner=actor)

        self.assertEqual(trial_properties["plan_at_event"], "startup")
        self.assertEqual(trial_properties["access_type_at_event"], AnalyticsAccessType.TRIAL)
        self.assertEqual(paid_properties["plan_at_event"], "scale")
        self.assertEqual(paid_properties["access_type_at_event"], AnalyticsAccessType.PAID)

    def test_mixpanel_failure_does_not_interrupt_tracking_caller(self):
        with patch("util.analytics.analytics.track", side_effect=RuntimeError("Mixpanel unavailable")):
            result = Analytics.track_event(
                user_id=self.user.pk,
                event=AnalyticsEvent.TASK_CREATED,
                source=AnalyticsSource.API,
                billing_context=self._paid_context(),
            )
        self.assertIsNone(result)

    def test_enriched_event_strips_pii_and_replaces_untrusted_billing_values(self):
        with patch("util.analytics.analytics.track") as track_mock:
            Analytics.track_event(
                user_id=self.user.pk,
                event=AnalyticsEvent.WEB_CHAT_MESSAGE_SENT,
                source=AnalyticsSource.WEB,
                properties={
                    "message_id": "message-1",
                    "email": "customer@example.com",
                    "from_address": "customer@example.com",
                    "from_number": "+15555550123",
                    "nested": {"recipient_email": "customer@example.com", "safe": "kept"},
                    "plan_at_event": "browser-supplied-plan",
                    "access_type_at_event": "paid",
                },
                billing_context=self._paid_context(),
            )

        properties = track_mock.call_args.args[2]
        self.assertEqual(properties["message_id"], "message-1")
        self.assertEqual(properties["nested"], {"safe": "kept"})
        self.assertNotIn("email", properties)
        self.assertNotIn("from_address", properties)
        self.assertNotIn("from_number", properties)
        self.assertEqual(properties["plan_at_event"], "startup")
        self.assertEqual(properties["event_schema_version"], EVENT_SCHEMA_VERSION)

    def test_all_attributable_events_are_enriched_and_explicit_opt_out_is_unchanged(self):
        context = self._paid_context()
        with (
            patch("util.analytics.resolve_analytics_billing_context_safely", return_value=context) as resolver,
            patch("util.analytics.analytics.track") as track_mock,
        ):
            Analytics.track_event(
                user_id=self.user.pk,
                event=AnalyticsEvent.AGENT_FILE_SENT,
                source=AnalyticsSource.AGENT,
                properties={"node_id": "node-1"},
            )
        resolver.assert_called_once()
        self.assertEqual(track_mock.call_args.args[2]["node_id"], "node-1")

        with (
            patch("util.analytics.resolve_analytics_billing_context_safely", return_value=context) as resolver,
            patch("util.analytics.analytics.track") as track_mock,
        ):
            Analytics.track_event(
                user_id=self.user.pk,
                event=AnalyticsEvent.LOGGED_IN,
                source=AnalyticsSource.WEB,
                properties={"safe": "kept"},
            )
        resolver.assert_called_once()
        login_properties = track_mock.call_args.args[2]
        self.assertEqual(login_properties["safe"], "kept")
        self.assertEqual(login_properties["event_schema_version"], EVENT_SCHEMA_VERSION)

        with (
            patch("util.analytics.resolve_analytics_billing_context_safely") as resolver,
            patch("util.analytics.analytics.track") as track_mock,
        ):
            Analytics.track_event(
                user_id=self.user.pk,
                event=AnalyticsEvent.AGENT_FILE_DOWNLOADED,
                source=AnalyticsSource.WEB,
                properties={"download_type": "signed"},
                billing_enrichment=False,
            )
        resolver.assert_not_called()
        opted_out_properties = track_mock.call_args.args[2]
        self.assertEqual(opted_out_properties["download_type"], "signed")
        self.assertNotIn("event_schema_version", opted_out_properties)

    def test_billing_context_resolution_is_cached_and_isolated_per_request(self):
        context = self._paid_context()

        def resolve_twice(_request):
            first = resolve_analytics_billing_context_safely(
                self.user.pk,
                actor_user=self.user,
                billing_owner=self.user,
            )
            second = resolve_analytics_billing_context_safely(
                self.user.pk,
            )
            self.assertIs(first, second)
            return SimpleNamespace()

        middleware = AnalyticsBillingContextCacheMiddleware(resolve_twice)
        with patch(
            "util.analytics_billing.resolve_analytics_billing_context",
            return_value=context,
        ) as resolver:
            middleware(RequestFactory().get("/first/"))
            middleware(RequestFactory().get("/second/"))

        self.assertEqual(resolver.call_count, 2)

    def test_email_open_event_receives_billing_snapshot_and_strips_recipient(self):
        actor = self._set_personal_plan(PlanNames.STARTUP)
        with (
            patch("util.analytics_billing.get_stripe_customer", return_value=_FakeCustomer("active")),
            patch("util.analytics.analytics.track") as track_mock,
        ):
            Analytics.track_agent_email_opened(
                {
                    "Recipient": actor.email,
                    "FirstOpen": True,
                    "MessageID": "message-1",
                    "Geo": {},
                }
            )

        properties = track_mock.call_args.args[2]
        self.assertEqual(properties["plan_at_event"], "startup")
        self.assertEqual(properties["access_type_at_event"], AnalyticsAccessType.PAID)
        self.assertNotIn("recipient", properties)

    def test_legacy_track_enriches_user_ids_but_not_hashed_or_anonymous_ids(self):
        context = self._paid_context()
        with (
            patch("util.analytics.resolve_analytics_billing_context_safely", return_value=context) as resolver,
            patch("util.analytics.analytics.track") as track_mock,
        ):
            Analytics.track(
                user_id=self.user.pk,
                event=AnalyticsEvent.SIGNUP,
                properties={"safe": "kept"},
            )

        resolver.assert_called_once()
        properties = track_mock.call_args.args[2]
        self.assertEqual(properties["event_schema_version"], EVENT_SCHEMA_VERSION)

        with (
            patch("util.analytics.resolve_analytics_billing_context_safely") as resolver,
            patch("util.analytics.analytics.track") as track_mock,
        ):
            Analytics.track(
                user_id="hashed-external-id",
                event=AnalyticsEvent.CAPI_EVENT_SENT,
                properties={"safe": "kept"},
            )

        resolver.assert_not_called()
        self.assertNotIn("event_schema_version", track_mock.call_args.args[2])

    def test_authenticated_web_context_exposes_server_resolved_billing_defaults(self):
        from pages.context_processors import analytics as analytics_context

        actor = self._set_personal_plan(PlanNames.STARTUP)
        request = RequestFactory().get("/pricing/")
        request.user = actor
        request.session = {}

        with patch(
            "util.analytics_billing.get_stripe_customer",
            return_value=_FakeCustomer("active"),
        ):
            context = analytics_context(request)

        billing_context = context["analytics"]["data"]["billing_context"]
        self.assertEqual(billing_context["organization_id"], f"user:{actor.pk}")
        self.assertEqual(billing_context["plan_at_event"], "startup")
        self.assertEqual(billing_context["access_type_at_event"], AnalyticsAccessType.PAID)

    def test_console_session_hydrates_immersive_app_billing_defaults(self):
        actor = self._set_personal_plan(PlanNames.STARTUP)
        self.client.force_login(actor)

        with patch(
            "util.analytics_billing.get_stripe_customer",
            return_value=_FakeCustomer("active"),
        ):
            response = self.client.get("/console/api/session/")

        self.assertEqual(response.status_code, 200)
        billing_context = response.json()["billing_context"]
        self.assertEqual(billing_context["organization_id"], f"user:{actor.pk}")
        self.assertEqual(billing_context["plan_at_event"], "startup")
        self.assertEqual(billing_context["access_type_at_event"], AnalyticsAccessType.PAID)

    def test_immersive_app_initial_page_waits_for_billing_context(self):
        from middleware.app_shell import _format_segment_snippet

        snippet = _format_segment_snippet()

        self.assertIn("fetch('/console/api/session/'", snippet)
        self.assertIn("setDefaultProperties(payload.billing_context)", snippet)
        self.assertLess(
            snippet.index("setDefaultProperties(payload.billing_context)"),
            snippet.index("trackInitialPage();"),
        )

    def test_current_profile_traits_are_separate_from_event_snapshot(self):
        actor = self._set_personal_plan(PlanNames.STARTUP)
        with (
            patch("util.analytics_billing.get_stripe_customer", return_value=_FakeCustomer("active")),
            patch("util.analytics.analytics.identify") as identify_mock,
        ):
            Analytics.sync_billing_profile(actor)

        traits = identify_mock.call_args.args[1]
        self.assertEqual(traits["current_plan"], "startup")
        self.assertEqual(traits["current_access_type"], AnalyticsAccessType.PAID)
        self.assertEqual(traits["current_billing_status"], AnalyticsBillingStatus.ACTIVE)
        self.assertFalse(traits["is_grandfathered_free"])
        self.assertNotIn("organization_id", traits)

    def test_real_task_completion_emits_value_event_but_eval_completion_does_not(self):
        from api.models import BrowserUseAgentTask
        from api.tasks.browser_agent_tasks import _track_task_completion_analytics

        completed_at = timezone.now()
        task = SimpleNamespace(
            id=uuid.uuid4(),
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            eval_run_id=None,
            user_id=self.user.pk,
            user=self.user,
            organization_id=None,
            agent_id=uuid.uuid4(),
            created_at=completed_at - timedelta(seconds=12),
            updated_at=completed_at,
        )
        with patch("api.tasks.browser_agent_tasks.Analytics.track_event") as track_mock:
            _track_task_completion_analytics(task, self.user)

        track_mock.assert_called_once()
        kwargs = track_mock.call_args.kwargs
        self.assertEqual(kwargs["event"], AnalyticsEvent.TASK_COMPLETED)
        self.assertEqual(kwargs["properties"]["task_id"], str(task.id))
        self.assertEqual(kwargs["properties"]["duration_seconds"], 12)
        self.assertIs(kwargs["billing_owner"], self.user)

        task.eval_run_id = uuid.uuid4()
        with patch("api.tasks.browser_agent_tasks.Analytics.track_event") as track_mock:
            _track_task_completion_analytics(task, self.user)
        track_mock.assert_not_called()
