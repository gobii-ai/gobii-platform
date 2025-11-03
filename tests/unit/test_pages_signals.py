import json
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, tag
from django.utils import timezone
from django.contrib.sessions.middleware import SessionMiddleware

from api.models import UserBilling, Organization, UserAttribution
from api.models import UserBilling, Organization, ProxyServer, DedicatedProxyAllocation
from constants.plans import PlanNames, PlanNamesChoices
from pages.signals import handle_subscription_event, handle_user_signed_up
from util.analytics import AnalyticsEvent
from util.subscription_helper import mark_user_billing_with_plan as real_mark_user_billing_with_plan
from constants.stripe import (
    ORG_OVERAGE_STATE_META_KEY,
    ORG_OVERAGE_STATE_DETACHED_PENDING,
)


User = get_user_model()


@tag("batch_pages")
class UserSignedUpSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="signup-user",
            email="signup@example.com",
            password="pw",
        )
        self.factory = RequestFactory()

    @patch("pages.signals.Analytics.track")
    @patch("pages.signals.Analytics.identify")
    def test_first_touch_traits_preserved_across_visits(self, mock_identify, mock_track):
        request = self.factory.get("/signup")
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()

        first_touch_payload = {
            "utm_source": "first-source",
            "utm_medium": "first-medium",
        }
        click_first_payload = {
            "gclid": "first-gclid",
            "gbraid": "first-gbraid",
            "wbraid": "first-wbraid",
            "msclkid": "first-msclkid",
            "ttclid": "first-ttclid",
        }
        now = timezone.now()
        later = now + timedelta(minutes=5)
        request.COOKIES = {
            "__utm_first": json.dumps(first_touch_payload),
            "utm_source": "last-source",
            "utm_medium": "last-medium",
            "__landing_first": "LP-100",
            "landing_code": "LP-200",
            "_fbc": "fb.1.123456789.abcdef",
            "fbclid": "fbclid-xyz",
            "__click_first": json.dumps(click_first_payload),
            "gclid": "last-gclid",
            "gbraid": "last-gbraid",
            "wbraid": "last-wbraid",
            "msclkid": "last-msclkid",
            "ttclid": "last-ttclid",
            "first_referrer": "https://first.example/",
            "last_referrer": "https://last.example/",
            "first_path": "/landing/first/",
            "last_path": "/pricing/",
            "ajs_anonymous_id": '"anon-123"',
            "_ga": "GA1.2.111.222",
        }
        request.session["landing_code_first"] = "LP-100"
        request.session["landing_code_last"] = "LP-200"
        request.session["landing_first_seen_at"] = now.isoformat()
        request.session["landing_last_seen_at"] = later.isoformat()

        handle_user_signed_up(sender=None, request=request, user=self.user)

        identify_call = mock_identify.call_args.kwargs
        traits = identify_call["traits"]
        self.assertEqual(traits["plan"], PlanNames.FREE)
        self.assertEqual(traits["utm_source_first"], "first-source")
        self.assertEqual(traits["utm_medium_first"], "first-medium")
        self.assertEqual(traits["utm_source_last"], "last-source")
        self.assertEqual(traits["utm_medium_last"], "last-medium")
        self.assertEqual(traits["landing_code_first"], "LP-100")
        self.assertEqual(traits["landing_code_last"], "LP-200")
        self.assertEqual(traits["fbc"], "fb.1.123456789.abcdef")
        self.assertEqual(traits["fbclid"], "fbclid-xyz")
        self.assertEqual(traits["gclid_first"], "first-gclid")
        self.assertEqual(traits["gclid_last"], "last-gclid")
        self.assertEqual(traits["msclkid_first"], "first-msclkid")
        self.assertEqual(traits["msclkid_last"], "last-msclkid")
        self.assertEqual(traits["first_referrer"], "https://first.example/")
        self.assertEqual(traits["last_referrer"], "https://last.example/")
        self.assertEqual(traits["first_landing_path"], "/landing/first/")
        self.assertEqual(traits["last_landing_path"], "/pricing/")
        self.assertEqual(traits["segment_anonymous_id"], "anon-123")
        self.assertEqual(traits["ga_client_id"], "GA1.2.111.222")

        track_call = mock_track.call_args.kwargs
        properties = track_call["properties"]
        context_campaign = track_call["context"]["campaign"]

        self.assertEqual(properties["plan"], PlanNames.FREE)
        self.assertEqual(properties["utm_source_first"], "first-source")
        self.assertEqual(properties["utm_source_last"], "last-source")
        self.assertEqual(context_campaign["source"], "last-source")
        self.assertEqual(context_campaign["medium"], "last-medium")
        self.assertEqual(context_campaign["landing_code"], "LP-200")
        self.assertEqual(context_campaign["gclid"], "last-gclid")
        self.assertEqual(context_campaign["referrer"], "https://last.example/")
        self.assertEqual(properties["landing_code_first"], "LP-100")
        self.assertEqual(properties["landing_code_last"], "LP-200")
        self.assertEqual(properties["fbc"], "fb.1.123456789.abcdef")
        self.assertEqual(properties["fbclid"], "fbclid-xyz")
        self.assertEqual(properties["gclid_first"], "first-gclid")
        self.assertEqual(properties["gclid_last"], "last-gclid")
        self.assertEqual(properties["first_referrer"], "https://first.example/")
        self.assertEqual(properties["last_referrer"], "https://last.example/")
        self.assertEqual(properties["first_landing_path"], "/landing/first/")
        self.assertEqual(properties["last_landing_path"], "/pricing/")
        self.assertEqual(properties["segment_anonymous_id"], "anon-123")
        self.assertEqual(properties["ga_client_id"], "GA1.2.111.222")

        attribution = UserAttribution.objects.get(user=self.user)
        self.assertEqual(attribution.utm_source_first, "first-source")
        self.assertEqual(attribution.utm_medium_first, "first-medium")
        self.assertEqual(attribution.utm_source_last, "last-source")
        self.assertEqual(attribution.utm_medium_last, "last-medium")
        self.assertEqual(attribution.landing_code_first, "LP-100")
        self.assertEqual(attribution.landing_code_last, "LP-200")
        self.assertEqual(attribution.fbc, "fb.1.123456789.abcdef")
        self.assertEqual(attribution.fbclid, "fbclid-xyz")
        self.assertIsNotNone(attribution.first_touch_at)
        self.assertIsNotNone(attribution.last_touch_at)
        self.assertEqual(attribution.gclid_first, "first-gclid")
        self.assertEqual(attribution.gclid_last, "last-gclid")
        self.assertEqual(attribution.msclkid_first, "first-msclkid")
        self.assertEqual(attribution.msclkid_last, "last-msclkid")
        self.assertEqual(attribution.first_referrer, "https://first.example/")
        self.assertEqual(attribution.last_referrer, "https://last.example/")
        self.assertEqual(attribution.first_landing_path, "/landing/first/")
        self.assertEqual(attribution.last_landing_path, "/pricing/")
        self.assertEqual(attribution.segment_anonymous_id, "anon-123")
        self.assertEqual(attribution.ga_client_id, "GA1.2.111.222")


def _build_event_payload(
    *,
    status="active",
    invoice_id="in_123",
    usage_type="licensed",
    quantity=1,
    billing_reason="subscription_update",
    product="prod_123",
    extra_items=None,
):
    items_data = [
        {
            "plan": {"usage_type": usage_type},
            "price": {"product": product},
            "quantity": quantity,
        }
    ]

    if extra_items:
        items_data.extend(extra_items)

    payload = {
        "object": "subscription",
        "id": "sub_123",
        "latest_invoice": invoice_id,
        "items": {
            "data": items_data,
        },
        "status": status,
        "cancel_at": None,
        "cancel_at_period_end": False,
        "current_period_start": None,
        "current_period_end": None,
    }

    if billing_reason is not None:
        payload["billing_reason"] = billing_reason

    return payload


def _build_djstripe_event(payload, event_type="customer.subscription.updated"):
    return SimpleNamespace(data={"object": payload}, type=event_type)


@tag("batch_pages")
class SubscriptionSignalTests(TestCase):
    maxDiff = None

    def setUp(self):
        self.user = User.objects.create_user(username="stripe-user", email="stripe@example.com", password="pw")
        self.billing = UserBilling.objects.get(user=self.user)
        self.billing.billing_cycle_anchor = 1
        self.billing.save(update_fields=["billing_cycle_anchor"])

    def _mock_subscription(self, current_period_day: int, *, subscriber=None):
        aware_start = timezone.make_aware(datetime(2025, 9, current_period_day, 8, 0, 0), timezone=dt_timezone.utc)
        aware_end = timezone.make_aware(datetime(2025, 10, current_period_day, 8, 0, 0), timezone=dt_timezone.utc)
        subscriber = subscriber or self.user
        sub = MagicMock()
        sub.status = "active"
        sub.id = "sub_123"
        sub.customer = SimpleNamespace(subscriber=subscriber)
        sub.billing_reason = None
        sub.stripe_data = _build_event_payload()
        sub.stripe_data['current_period_start'] = str(aware_start)
        sub.stripe_data['current_period_end'] = str(aware_end)
        return sub

    @tag("batch_pages")
    def test_subscription_anchor_updates_from_stripe(self):
        payload = _build_event_payload(billing_reason="subscription_create")
        event = _build_djstripe_event(payload)

        fresh_user = User.objects.get(pk=self.user.pk)
        sub = self._mock_subscription(current_period_day=17, subscriber=fresh_user)
        sub.stripe_data['billing_reason'] = "subscription_create"
        sub.billing_reason = "subscription_create"

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
            patch("pages.signals.TaskCreditService.grant_subscription_credits"), \
            patch("pages.signals.mark_user_billing_with_plan", wraps=real_mark_user_billing_with_plan) as mock_mark_plan, \
            patch("pages.signals.Analytics.identify") as mock_identify, \
            patch("pages.signals.Analytics.track_event") as mock_track_event, \
            patch("pages.signals.logger.exception") as mock_logger_exception:

            handle_subscription_event(event)

        self.user.refresh_from_db()
        updated_billing = self.user.billing
        self.assertEqual(updated_billing.billing_cycle_anchor, 17)

        mock_mark_plan.assert_called_once()
        _, kwargs = mock_mark_plan.call_args
        call_user = mock_mark_plan.call_args[0][0]
        self.assertEqual(call_user.pk, self.user.pk)
        self.assertFalse(kwargs.get("update_anchor", True))
        mock_identify.assert_called_once()
        mock_track_event.assert_called_once()
        track_kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(track_kwargs["event"], AnalyticsEvent.SUBSCRIPTION_CREATED)
        self.assertEqual(track_kwargs["properties"]["plan"], PlanNamesChoices.STARTUP.value)
        mock_logger_exception.assert_not_called()

    @tag("batch_pages")
    def test_subscription_cycle_emits_renewed_event(self):
        payload = _build_event_payload(billing_reason="subscription_cycle")
        event = _build_djstripe_event(payload)

        fresh_user = User.objects.get(pk=self.user.pk)
        sub = self._mock_subscription(current_period_day=15, subscriber=fresh_user)
        sub.stripe_data['billing_reason'] = "subscription_cycle"
        sub.billing_reason = "subscription_cycle"

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
            patch("pages.signals.TaskCreditService.grant_subscription_credits"), \
            patch("pages.signals.mark_user_billing_with_plan", wraps=real_mark_user_billing_with_plan), \
            patch("pages.signals.Analytics.identify") as mock_identify, \
            patch("pages.signals.Analytics.track_event") as mock_track_event:

            handle_subscription_event(event)

        mock_identify.assert_called_once()
        mock_track_event.assert_called_once()
        track_kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(track_kwargs["event"], AnalyticsEvent.SUBSCRIPTION_RENEWED)
        self.assertEqual(track_kwargs["properties"]["plan"], PlanNamesChoices.STARTUP.value)

    @tag("batch_pages")
    def test_missing_user_billing_logs_exception(self):
        payload = _build_event_payload()
        event = _build_djstripe_event(payload)

        fresh_user = User.objects.get(pk=self.user.pk)
        sub = self._mock_subscription(current_period_day=20, subscriber=fresh_user)

        # Remove billing record to trigger DoesNotExist branch
        UserBilling.objects.filter(user=self.user).delete()
        self.user.__dict__.pop("billing", None)

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
            patch("pages.signals.TaskCreditService.grant_subscription_credits"), \
            patch("pages.signals.mark_user_billing_with_plan"), \
            patch("pages.signals.Analytics.identify"), \
            patch("pages.signals.Analytics.track_event"), \
            patch("pages.signals.logger.exception") as mock_logger:

            handle_subscription_event(event)

        mock_logger.assert_called_once()
        self.assertFalse(UserBilling.objects.filter(user=self.user).exists())

    @tag("batch_pages")
    def test_subscription_cancellation_updates_plan_trait(self):
        payload = _build_event_payload(status="canceled")
        event = _build_djstripe_event(payload, event_type="customer.subscription.deleted")

        sub = self._mock_subscription(current_period_day=10, subscriber=self.user)
        sub.status = "canceled"
        sub.stripe_data = payload

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.downgrade_owner_to_free_plan") as mock_downgrade, \
            patch("pages.signals.DedicatedProxyService.release_for_owner") as mock_release, \
            patch("pages.signals.Analytics.identify") as mock_identify, \
            patch("pages.signals.Analytics.track_event") as mock_track_event:

            handle_subscription_event(event)

        mock_downgrade.assert_called_once_with(self.user)
        mock_release.assert_called_once_with(self.user)

        mock_identify.assert_called_once()
        identify_args, identify_kwargs = mock_identify.call_args
        self.assertEqual(identify_args[0], self.user.id)
        self.assertIn("plan", identify_args[1])
        self.assertEqual(identify_args[1]["plan"], PlanNames.FREE)
        self.assertEqual(identify_kwargs, {})

        mock_track_event.assert_called_once()
        track_kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(track_kwargs["properties"]["plan"], PlanNames.FREE)

    @tag("batch_pages")
    def test_dedicated_ip_allocation_from_subscription(self):
        dedicated_item = {
            "plan": {"usage_type": "licensed"},
            "price": {"id": "price_dedicated", "product": "prod_dedicated"},
            "quantity": 2,
        }
        payload = _build_event_payload(extra_items=[dedicated_item])
        payload["items"]["data"][0]["price"]["id"] = "price_startup"
        payload["items"]["data"][0]["price"]["product"] = "prod_startup"
        event = _build_djstripe_event(payload)

        sub = self._mock_subscription(current_period_day=15, subscriber=self.user)
        sub.stripe_data = payload

        custom_settings = SimpleNamespace(
            org_team_additional_task_price_id="",
            startup_dedicated_ip_price_id="price_dedicated",
            startup_dedicated_ip_product_id="prod_dedicated",
            org_team_dedicated_ip_price_id="",
            org_team_dedicated_ip_product_id="",
        )

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
            patch("pages.signals.TaskCreditService.grant_subscription_credits"), \
            patch("pages.signals.Analytics.identify"), \
            patch("pages.signals.Analytics.track_event"), \
            patch("pages.signals.DedicatedProxyService.allocate_proxy") as mock_allocate, \
            patch("pages.signals.DedicatedProxyService.release_for_owner") as mock_release, \
            patch("pages.signals.get_stripe_settings", return_value=custom_settings):

            handle_subscription_event(event)

        self.assertEqual(mock_allocate.call_count, 2)
        mock_release.assert_not_called()

    @tag("batch_pages")
    def test_dedicated_ip_release_on_quantity_decrease(self):
        proxy = ProxyServer.objects.create(
            name="Dedicated",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="dedicated.example.com",
            port=8080,
            username="user",
            password="pass",
            static_ip="203.0.113.10",
            is_active=True,
            is_dedicated=True,
        )
        DedicatedProxyAllocation.objects.assign_to_owner(proxy, self.user)

        payload = _build_event_payload()
        payload["items"]["data"][0]["price"]["id"] = "price_startup"
        payload["items"]["data"][0]["price"]["product"] = "prod_startup"
        event = _build_djstripe_event(payload)

        sub = self._mock_subscription(current_period_day=12, subscriber=self.user)
        sub.stripe_data = payload

        custom_settings = SimpleNamespace(
            org_team_additional_task_price_id="",
            startup_dedicated_ip_price_id="price_dedicated",
            startup_dedicated_ip_product_id="prod_dedicated",
            org_team_dedicated_ip_price_id="",
            org_team_dedicated_ip_product_id="",
        )

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
            patch("pages.signals.TaskCreditService.grant_subscription_credits"), \
            patch("pages.signals.Analytics.identify"), \
            patch("pages.signals.Analytics.track_event"), \
            patch("pages.signals.DedicatedProxyService.allocate_proxy") as mock_allocate, \
            patch("pages.signals.DedicatedProxyService.release_for_owner") as mock_release, \
            patch("pages.signals.get_stripe_settings", return_value=custom_settings):

            handle_subscription_event(event)

        mock_allocate.assert_not_called()
        mock_release.assert_called_once()
        self.assertEqual(mock_release.call_args.kwargs.get("limit"), 1)

    @tag("batch_pages")
    def test_dedicated_ip_release_on_cancellation(self):
        payload = _build_event_payload()
        event = _build_djstripe_event(payload, event_type="customer.subscription.deleted")

        sub = self._mock_subscription(current_period_day=10, subscriber=self.user)
        sub.status = "canceled"
        sub.stripe_data = payload

        with patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
            patch("pages.signals.TaskCreditService.grant_subscription_credits"), \
            patch("pages.signals.Analytics.identify"), \
            patch("pages.signals.Analytics.track_event"), \
            patch("pages.signals.DedicatedProxyService.release_for_owner") as mock_release:

            handle_subscription_event(event)

        mock_release.assert_called_once_with(self.user)


@tag("batch_pages")
class SubscriptionSignalOrganizationTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username="org-owner", email="org@example.com", password="pw")
        self.org = Organization.objects.create(name="Org", slug="org", created_by=owner)
        billing = self.org.billing
        billing.stripe_customer_id = "cus_org"
        billing.subscription = PlanNamesChoices.ORG_TEAM.value
        billing.save(update_fields=["stripe_customer_id", "subscription"])
        patcher = patch("pages.signals.stripe.Subscription.retrieve")
        self.addCleanup(patcher.stop)
        self.mock_subscription_retrieve = patcher.start()
        self.mock_subscription_retrieve.return_value = {
            "items": {"data": []},
            "metadata": {},
        }

    def _mock_subscription(self, *, quantity, billing_reason, payload_invoice="in_org"):
        aware_start = timezone.make_aware(datetime(2025, 9, 1, 0, 0, 0), timezone=dt_timezone.utc)
        aware_end = timezone.make_aware(datetime(2025, 10, 1, 0, 0, 0), timezone=dt_timezone.utc)
        sub = MagicMock()
        sub.status = "active"
        sub.id = "sub_org"
        sub.customer = SimpleNamespace(id="cus_org", subscriber=None)
        sub.billing_reason = billing_reason
        payload = _build_event_payload(
            invoice_id=payload_invoice,
            quantity=quantity,
            billing_reason=billing_reason,
            product="prod_org",
        )
        sub.stripe_data = payload
        sub.stripe_data['current_period_start'] = aware_start
        sub.stripe_data['current_period_end'] = aware_end
        sub.stripe_data['cancel_at'] = None
        sub.stripe_data['cancel_at_period_end'] = False

        return sub, payload

    @patch("pages.signals.stripe.SubscriptionItem.create")
    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_create_sets_seats_and_grants(self, mock_sync, mock_plan, mock_grant, mock_item_create):
        sub, payload = self._mock_subscription(quantity=2, billing_reason=None)
        mock_sync.return_value = sub
        mock_plan.return_value = {"id": PlanNamesChoices.ORG_TEAM.value, "credits_per_seat": 500}
        event = _build_djstripe_event(payload, event_type="customer.subscription.created")

        invoice_payload = {
            "id": payload["latest_invoice"],
            "object": "invoice",
            "billing_reason": "subscription_create",
        }
        invoice_obj = SimpleNamespace(billing_reason="subscription_create", stripe_data=invoice_payload)

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.stripe.Invoice.retrieve", return_value=invoice_payload) as mock_invoice_retrieve, \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj) as mock_invoice_sync:

            handle_subscription_event(event)

        mock_invoice_retrieve.assert_called_once_with(payload["latest_invoice"])
        mock_invoice_sync.assert_called_once()

        billing = self.org.billing
        billing.refresh_from_db()
        self.assertEqual(billing.purchased_seats, 2)
        mock_grant.assert_called_once()
        _, kwargs = mock_grant.call_args
        self.assertEqual(kwargs.get("seats"), 2)
        self.assertEqual(kwargs.get("invoice_id"), invoice_payload["id"])

    @patch("pages.signals.stripe.SubscriptionItem.create")
    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_create_with_existing_seats_grants_delta(self, mock_sync, mock_plan, mock_grant, mock_item_create):
        billing = self.org.billing
        billing.purchased_seats = 3
        billing.save(update_fields=["purchased_seats"])

        sub, payload = self._mock_subscription(quantity=5, billing_reason=None, payload_invoice="in_seat_add")
        mock_sync.return_value = sub
        mock_plan.return_value = {"id": PlanNamesChoices.ORG_TEAM.value, "credits_per_seat": 500}
        event = _build_djstripe_event(payload, event_type="customer.subscription.created")

        invoice_payload = {
            "id": payload["latest_invoice"],
            "object": "invoice",
            "billing_reason": "subscription_create",
        }
        invoice_obj = SimpleNamespace(billing_reason="subscription_create", stripe_data=invoice_payload)

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.stripe.Invoice.retrieve", return_value=invoice_payload), \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj):

            handle_subscription_event(event)

        billing.refresh_from_db()
        self.assertEqual(billing.purchased_seats, 5)
        mock_grant.assert_called_once()
        _, kwargs = mock_grant.call_args
        self.assertEqual(kwargs.get("seats"), 2)
        self.assertEqual(kwargs.get("invoice_id"), "")

    @patch("pages.signals.stripe.SubscriptionItem.create")
    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_update_grants_difference(self, mock_sync, mock_plan, mock_grant, mock_item_create):
        billing = self.org.billing
        billing.purchased_seats = 2
        billing.save(update_fields=["purchased_seats"])

        sub, payload = self._mock_subscription(quantity=3, billing_reason=None, payload_invoice="in_upgrade")
        mock_sync.return_value = sub
        mock_plan.return_value = {"id": PlanNamesChoices.ORG_TEAM.value, "credits_per_seat": 500}
        event = _build_djstripe_event(payload)

        invoice_payload = {
            "id": payload["latest_invoice"],
            "object": "invoice",
            "billing_reason": "subscription_update",
        }
        invoice_obj = SimpleNamespace(billing_reason="subscription_update", stripe_data=invoice_payload)

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.stripe.Invoice.retrieve", return_value=invoice_payload) as mock_invoice_retrieve, \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj):

            handle_subscription_event(event)

        mock_invoice_retrieve.assert_called_once()

        billing.refresh_from_db()
        self.assertEqual(billing.purchased_seats, 3)
        mock_grant.assert_called_once()
        _, kwargs = mock_grant.call_args
        self.assertEqual(kwargs.get("seats"), 1)
        self.assertEqual(kwargs.get("invoice_id"), "")

    @patch("pages.signals.stripe.SubscriptionItem.create")
    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_update_decrease_no_grant(self, mock_sync, mock_plan, mock_grant, mock_item_create):
        billing = self.org.billing
        billing.purchased_seats = 3
        billing.save(update_fields=["purchased_seats"])

        sub, payload = self._mock_subscription(quantity=1, billing_reason=None, payload_invoice="in_downgrade")
        mock_sync.return_value = sub
        mock_plan.return_value = {"id": PlanNamesChoices.ORG_TEAM.value, "credits_per_seat": 500}
        event = _build_djstripe_event(payload)

        invoice_payload = {
            "id": payload["latest_invoice"],
            "object": "invoice",
            "billing_reason": "subscription_update",
        }
        invoice_obj = SimpleNamespace(billing_reason="subscription_update", stripe_data=invoice_payload)

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.stripe.Invoice.retrieve", return_value=invoice_payload), \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj):

            handle_subscription_event(event)

        billing.refresh_from_db()
        self.assertEqual(billing.purchased_seats, 1)
        mock_grant.assert_not_called()

    @patch("pages.signals.stripe.SubscriptionItem.create")
    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_cycle_renews_with_replace_current(self, mock_sync, mock_plan, mock_grant, mock_item_create):
        billing = self.org.billing
        billing.purchased_seats = 3
        billing.billing_cycle_anchor = 17
        billing.save(update_fields=["purchased_seats", "billing_cycle_anchor"])

        sub, payload = self._mock_subscription(quantity=3, billing_reason="subscription_cycle", payload_invoice="in_cycle")
        mock_sync.return_value = sub
        mock_plan.return_value = {"id": PlanNamesChoices.ORG_TEAM.value, "credits_per_seat": 500}
        event = _build_djstripe_event(payload)

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.stripe.Invoice.retrieve") as mock_invoice_retrieve, \
            patch("pages.signals.Invoice.sync_from_stripe_data") as mock_invoice_sync:

            handle_subscription_event(event)

        mock_invoice_retrieve.assert_not_called()
        mock_invoice_sync.assert_not_called()

        billing.refresh_from_db()
        self.assertEqual(billing.purchased_seats, 3)
        self.assertEqual(billing.billing_cycle_anchor, 1)

        mock_plan.assert_called_once()
        mock_grant.assert_called_once()
        call_args, call_kwargs = mock_grant.call_args
        self.assertEqual(call_args[0], self.org)
        self.assertEqual(call_kwargs.get("seats"), 3)
        self.assertEqual(call_kwargs.get("invoice_id"), payload["latest_invoice"])
        self.assertTrue(call_kwargs.get("replace_current"))
        self.assertIs(call_kwargs.get("subscription"), sub)

    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_adds_overage_item_when_missing(self, mock_sync, mock_plan, mock_grant):
        sub, payload = self._mock_subscription(quantity=2, billing_reason="subscription_update")
        payload["items"]["data"][0]["price"]["id"] = "price_org_team"
        mock_sync.return_value = sub
        mock_plan.return_value = {"id": PlanNamesChoices.ORG_TEAM.value, "credits_per_seat": 500}

        event = _build_djstripe_event(payload)

        custom_settings = SimpleNamespace(
            org_team_additional_task_price_id="price_overage",
            startup_dedicated_ip_price_id="",
            startup_dedicated_ip_product_id="",
            org_team_dedicated_ip_price_id="",
            org_team_dedicated_ip_product_id="",
        )

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.get_stripe_settings", return_value=custom_settings), \
            patch("pages.signals.stripe.Subscription.retrieve", return_value={"items": {"data": payload["items"]["data"]}}) as mock_sub_retrieve, \
            patch("pages.signals.stripe.SubscriptionItem.create") as mock_item_create:

            handle_subscription_event(event)

        mock_sub_retrieve.assert_called_once_with(sub.id, expand=["items.data.price"])
        mock_item_create.assert_called_once_with(subscription=sub.id, price="price_overage")
        mock_grant.assert_called_once()

    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_skips_overage_item_when_present(self, mock_sync, mock_plan, mock_grant):
        sub, payload = self._mock_subscription(quantity=2, billing_reason="subscription_update")
        payload_items = payload["items"]["data"]
        payload_items[0]["price"]["id"] = "price_org_team"
        payload_items.append({
            "plan": {"usage_type": "metered"},
            "price": {"id": "price_overage"},
            "quantity": None,
        })

        mock_sync.return_value = sub
        mock_plan.return_value = {"id": PlanNamesChoices.ORG_TEAM.value, "credits_per_seat": 500}

        event = _build_djstripe_event(payload)

        custom_settings = SimpleNamespace(
            org_team_additional_task_price_id="price_overage",
            startup_dedicated_ip_price_id="",
            startup_dedicated_ip_product_id="",
            org_team_dedicated_ip_price_id="",
            org_team_dedicated_ip_product_id="",
        )

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.get_stripe_settings", return_value=custom_settings), \
            patch("pages.signals.stripe.SubscriptionItem.create") as mock_item_create:

            handle_subscription_event(event)

        mock_item_create.assert_not_called()

    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_detach_pending_skips_overage_create(self, mock_sync, mock_plan, mock_grant):
        sub, payload = self._mock_subscription(quantity=2, billing_reason="subscription_update")
        payload["items"]["data"][0]["price"]["id"] = "price_org_team"
        payload["metadata"] = {ORG_OVERAGE_STATE_META_KEY: ORG_OVERAGE_STATE_DETACHED_PENDING}

        mock_sync.return_value = sub
        mock_plan.return_value = {"id": PlanNamesChoices.ORG_TEAM.value, "credits_per_seat": 500}

        billing = self.org.billing
        billing.purchased_seats = 2
        billing.save(update_fields=["purchased_seats"])

        event = _build_djstripe_event(payload)

        custom_settings = SimpleNamespace(
            org_team_additional_task_price_id="price_overage",
            startup_dedicated_ip_price_id="",
            startup_dedicated_ip_product_id="",
            org_team_dedicated_ip_price_id="",
            org_team_dedicated_ip_product_id="",
        )

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.get_stripe_settings", return_value=custom_settings), \
            patch("pages.signals.stripe.SubscriptionItem.create") as mock_item_create, \
            patch("pages.signals.stripe.Subscription.modify") as mock_modify:

            handle_subscription_event(event)

        mock_item_create.assert_not_called()
        mock_modify.assert_not_called()

    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_detach_pending_clears_flag_when_item_present(self, mock_sync, mock_plan, mock_grant):
        sub, payload = self._mock_subscription(quantity=2, billing_reason="subscription_update")
        payload_items = payload["items"]["data"]
        payload_items[0]["price"]["id"] = "price_org_team"
        payload_items.append({
            "plan": {"usage_type": "metered"},
            "price": {"id": "price_overage"},
            "quantity": None,
        })
        payload["metadata"] = {ORG_OVERAGE_STATE_META_KEY: ORG_OVERAGE_STATE_DETACHED_PENDING}

        mock_sync.return_value = sub
        mock_plan.return_value = {"id": PlanNamesChoices.ORG_TEAM.value, "credits_per_seat": 500}

        billing = self.org.billing
        billing.purchased_seats = 2
        billing.save(update_fields=["purchased_seats"])

        event = _build_djstripe_event(payload)

        custom_settings = SimpleNamespace(
            org_team_additional_task_price_id="price_overage",
            startup_dedicated_ip_price_id="",
            startup_dedicated_ip_product_id="",
            org_team_dedicated_ip_price_id="",
            org_team_dedicated_ip_product_id="",
        )

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.get_stripe_settings", return_value=custom_settings), \
            patch("pages.signals.stripe.SubscriptionItem.create") as mock_item_create, \
            patch("pages.signals.stripe.Subscription.modify") as mock_modify:

            handle_subscription_event(event)

        mock_item_create.assert_not_called()
        mock_modify.assert_called_once_with(sub.id, metadata={ORG_OVERAGE_STATE_META_KEY: ""})
