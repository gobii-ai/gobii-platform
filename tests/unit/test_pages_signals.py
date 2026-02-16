import json
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.apps import apps
from django.test import RequestFactory, TestCase, tag, override_settings
from django.utils import timezone
from django.contrib.sessions.middleware import SessionMiddleware

from api.models import UserBilling, Organization, UserAttribution
from api.models import UserBilling, Organization, ProxyServer, DedicatedProxyAllocation
from constants.plans import PlanNames, PlanNamesChoices
from constants.grant_types import GrantTypeChoices
from dateutil.relativedelta import relativedelta
from pages.signals import (
    handle_subscription_event,
    handle_user_signed_up,
    handle_invoice_payment_failed,
    handle_invoice_payment_succeeded,
)
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
        request.META["REMOTE_ADDR"] = "198.51.100.24"
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

    @override_settings(GOBII_PROPRIETARY_MODE=True, CAPI_REGISTRATION_VALUE=12.5)
    @patch("pages.signals.capi")
    @patch("pages.signals.Analytics.track")
    @patch("pages.signals.Analytics.identify")
    def test_signup_capi_includes_value_and_currency(self, mock_identify, mock_track, mock_capi):
        request = self.factory.get("/signup")
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()

        with patch("pages.signals.transaction.on_commit", side_effect=lambda fn: fn()):
            handle_user_signed_up(sender=None, request=request, user=self.user)

        mock_capi.assert_called_once()
        capi_kwargs = mock_capi.call_args.kwargs
        self.assertEqual(capi_kwargs["event_name"], "CompleteRegistration")
        props = capi_kwargs["properties"]
        self.assertEqual(props["value"], 12.5)
        self.assertEqual(props["currency"], "USD")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("pages.signals.record_fbc_synthesized")
    @patch("pages.signals.capi")
    @patch("pages.signals.Analytics.track")
    @patch("pages.signals.Analytics.identify")
    def test_signup_capi_synthesizes_fbc_from_session_fbclid(
        self, mock_identify, mock_track, mock_capi, mock_record_fbc_synthesized
    ):
        """When user lands with fbclid but signs up on a page without it, fbc should be synthesized.

        This improves Meta Event Match Quality by ensuring fbc is present even when
        the signup URL doesn't contain fbclid in the querystring.
        """
        # Simulate signup on a page WITHOUT fbclid in URL
        request = self.factory.get("/signup")  # No fbclid param
        request.META["REMOTE_ADDR"] = "198.51.100.24"
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()

        # But fbclid WAS captured in session from earlier landing
        request.session["fbclid_last"] = "test-fbclid-from-session"
        # No _fbc cookie, no fbclid cookie - only session has it
        request.COOKIES = {}

        with patch("pages.signals.transaction.on_commit", side_effect=lambda fn: fn()):
            handle_user_signed_up(sender=None, request=request, user=self.user)

        mock_capi.assert_called_once()
        capi_kwargs = mock_capi.call_args.kwargs
        context = capi_kwargs["context"]
        click_ids = context.get("click_ids", {})

        # fbc should be synthesized from session fbclid
        self.assertIn("fbc", click_ids)
        self.assertTrue(
            click_ids["fbc"].startswith("fb.1."),
            f"fbc should start with 'fb.1.' but was: {click_ids.get('fbc')}"
        )
        self.assertTrue(
            click_ids["fbc"].endswith(".test-fbclid-from-session"),
            f"fbc should end with fbclid but was: {click_ids.get('fbc')}"
        )
        fbc_timestamp = click_ids["fbc"].split(".")[2]
        self.assertTrue(
            fbc_timestamp.isdigit() and len(fbc_timestamp) == 13,
            f"fbc timestamp should be a 13-digit millisecond value but was: {fbc_timestamp}"
        )
        # fbclid should also be included
        self.assertEqual(click_ids.get("fbclid"), "test-fbclid-from-session")
        mock_record_fbc_synthesized.assert_called_once_with(
            source="pages.signals.handle_user_signed_up"
        )

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("pages.signals.record_fbc_synthesized")
    @patch("pages.signals.capi")
    @patch("pages.signals.Analytics.track")
    @patch("pages.signals.Analytics.identify")
    def test_signup_capi_uses_existing_fbc_cookie_over_synthesis(
        self, mock_identify, mock_track, mock_capi, mock_record_fbc_synthesized
    ):
        """When _fbc cookie exists, use it instead of synthesizing from fbclid."""
        request = self.factory.get("/signup")
        request.META["REMOTE_ADDR"] = "198.51.100.24"
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()

        # Both _fbc cookie and session fbclid exist
        request.COOKIES = {"_fbc": "fb.1.existing.cookie-fbc-value"}
        request.session["fbclid_last"] = "session-fbclid"

        with patch("pages.signals.transaction.on_commit", side_effect=lambda fn: fn()):
            handle_user_signed_up(sender=None, request=request, user=self.user)

        mock_capi.assert_called_once()
        capi_kwargs = mock_capi.call_args.kwargs
        context = capi_kwargs["context"]
        click_ids = context.get("click_ids", {})

        # Should use existing _fbc cookie, not synthesize
        self.assertEqual(click_ids.get("fbc"), "fb.1.existing.cookie-fbc-value")
        mock_record_fbc_synthesized.assert_not_called()


@tag("batch_pages")
class BuildMarketingContextFromUserTests(TestCase):
    """Tests for _build_marketing_context_from_user used by Subscribe events."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="context-user",
            email="context@example.com",
            password="pw",
        )

    def test_synthesizes_fbc_from_fbclid_when_fbc_missing(self):
        """When fbc is missing but fbclid exists, fbc should be synthesized."""
        from pages.signals import _build_marketing_context_from_user

        UserAttribution.objects.create(
            user=self.user,
            fbclid="test-fbclid-value",
            fbc="",  # No fbc stored
        )

        with patch("pages.signals.record_fbc_synthesized") as mock_record_fbc_synthesized:
            context = _build_marketing_context_from_user(self.user)
        click_ids = context.get("click_ids", {})

        # fbc should be synthesized
        self.assertIn("fbc", click_ids)
        self.assertTrue(
            click_ids["fbc"].startswith("fb.1."),
            f"fbc should start with 'fb.1.' but was: {click_ids.get('fbc')}"
        )
        self.assertTrue(
            click_ids["fbc"].endswith(".test-fbclid-value"),
            f"fbc should end with fbclid but was: {click_ids.get('fbc')}"
        )
        fbc_timestamp = click_ids["fbc"].split(".")[2]
        self.assertTrue(
            fbc_timestamp.isdigit() and len(fbc_timestamp) == 13,
            f"fbc timestamp should be a 13-digit millisecond value but was: {fbc_timestamp}"
        )
        # fbclid should also be included
        self.assertEqual(click_ids.get("fbclid"), "test-fbclid-value")
        mock_record_fbc_synthesized.assert_called_once_with(
            source="pages.signals.build_marketing_context_from_user"
        )

    def test_uses_existing_fbc_over_synthesis(self):
        """When fbc already exists, don't synthesize from fbclid."""
        from pages.signals import _build_marketing_context_from_user

        UserAttribution.objects.create(
            user=self.user,
            fbc="fb.1.existing.stored-fbc",
            fbclid="some-fbclid",
        )

        with patch("pages.signals.record_fbc_synthesized") as mock_record_fbc_synthesized:
            context = _build_marketing_context_from_user(self.user)
        click_ids = context.get("click_ids", {})

        # Should use existing fbc
        self.assertEqual(click_ids.get("fbc"), "fb.1.existing.stored-fbc")
        mock_record_fbc_synthesized.assert_not_called()

    def test_includes_fbp_in_context(self):
        """fbp (Browser ID) should be included in click_ids."""
        from pages.signals import _build_marketing_context_from_user

        UserAttribution.objects.create(
            user=self.user,
            fbp="fb.1.1234567890.987654321",
        )

        context = _build_marketing_context_from_user(self.user)
        click_ids = context.get("click_ids", {})

        self.assertEqual(click_ids.get("fbp"), "fb.1.1234567890.987654321")

    def test_includes_user_agent_in_context(self):
        """User agent should be included in context."""
        from pages.signals import _build_marketing_context_from_user

        UserAttribution.objects.create(
            user=self.user,
            last_user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )

        context = _build_marketing_context_from_user(self.user)

        self.assertEqual(
            context.get("user_agent"),
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )

    def test_includes_client_ip_in_context(self):
        """Client IP should be included in context."""
        from pages.signals import _build_marketing_context_from_user

        UserAttribution.objects.create(
            user=self.user,
            last_client_ip="192.168.1.100",
        )

        context = _build_marketing_context_from_user(self.user)

        self.assertEqual(context.get("client_ip"), "192.168.1.100")

    def test_includes_ga_client_id_in_context(self):
        """GA client ID should be included in context for GA MP events."""
        from pages.signals import _build_marketing_context_from_user

        UserAttribution.objects.create(
            user=self.user,
            ga_client_id="GA1.2.111.222",
        )

        context = _build_marketing_context_from_user(self.user)

        self.assertEqual(context.get("ga_client_id"), "GA1.2.111.222")

    def test_returns_minimal_context_when_no_attribution(self):
        """When user has no attribution, return minimal context with consent."""
        from pages.signals import _build_marketing_context_from_user

        # Don't create attribution
        context = _build_marketing_context_from_user(self.user)

        self.assertEqual(context, {"consent": True})


def _build_event_payload(
    *,
    status="active",
    invoice_id="in_123",
    usage_type="licensed",
    quantity=1,
    billing_reason="subscription_update",
    product="prod_123",
    extra_items=None,
    unit_amount=2999,
    currency="usd",
):
    items_data = [
        {
            "plan": {"usage_type": usage_type},
            "price": {
                "product": product,
                "unit_amount": unit_amount,
                "unit_amount_decimal": str(unit_amount) if unit_amount is not None else None,
                "currency": currency,
            },
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


def _build_invoice_payload(
    *,
    invoice_id="in_fail",
    customer_id="cus_fail",
    subscription_id="sub_fail",
    attempt_count=1,
    next_payment_attempt=None,
    livemode=True,
    amount_due=4720,
    amount_paid=0,
    currency="usd",
    billing_reason="subscription_cycle",
    status="open",
    auto_advance=True,
    hosted_invoice_url="https://invoice.example/test",
    invoice_pdf="https://invoice.example/test.pdf",
    price_id="price_fail",
    product_id="prod_fail",
    receipt_number="rcpt-test",
):
    return {
        "object": "invoice",
        "id": invoice_id,
        "number": "INV-FAIL",
        "customer": customer_id,
        "subscription": subscription_id,
        "attempt_count": attempt_count,
        "attempted": True,
        "next_payment_attempt": next_payment_attempt,
        "livemode": livemode,
        "amount_due": amount_due,
        "total": amount_due,
        "amount_paid": amount_paid,
        "currency": currency,
        "billing_reason": billing_reason,
        "collection_method": "charge_automatically",
        "status": status,
        "auto_advance": auto_advance,
        "hosted_invoice_url": hosted_invoice_url,
        "invoice_pdf": invoice_pdf,
        "receipt_number": receipt_number,
        "lines": {
            "data": [
                {
                    "id": "il_fail",
                    "object": "line_item",
                    "price": {"id": price_id, "product": product_id},
                }
            ]
        },
    }


@tag("batch_pages")
class SubscriptionSignalTests(TestCase):
    maxDiff = None

    def setUp(self):
        self.user = User.objects.create_user(username="stripe-user", email="stripe@example.com", password="pw")
        self.billing = UserBilling.objects.get(user=self.user)
        self.billing.billing_cycle_anchor = 1
        self.billing.save(update_fields=["billing_cycle_anchor"])

        self._capi_patcher = patch("pages.signals.capi")
        self.mock_capi = self._capi_patcher.start()
        self.addCleanup(self._capi_patcher.stop)
        self.mock_capi.reset_mock()

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
    @override_settings(CAPI_LTV_MULTIPLE=1.0)
    def test_subscription_anchor_updates_from_stripe(self):
        self.mock_capi.reset_mock()
        payload = _build_event_payload(billing_reason="subscription_create")
        event = _build_djstripe_event(payload, event_type="customer.subscription.created")

        fresh_user = User.objects.get(pk=self.user.pk)
        sub = self._mock_subscription(current_period_day=17, subscriber=fresh_user)
        sub.stripe_data['billing_reason'] = "subscription_create"
        sub.billing_reason = "subscription_create"
        event_id = "sub-evt-123"
        sub.stripe_data['metadata'] = {"gobii_event_id": event_id}

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

        self.mock_capi.assert_not_called()

    @tag("batch_pages")
    @override_settings(CAPI_LTV_MULTIPLE=2.0)
    def test_subscription_capi_value_applies_ltv_multiple(self):
        self.mock_capi.reset_mock()
        payload = _build_event_payload(status="trialing", billing_reason="subscription_create", invoice_id=None)
        event = _build_djstripe_event(payload, event_type="customer.subscription.created")

        fresh_user = User.objects.get(pk=self.user.pk)
        sub = self._mock_subscription(current_period_day=17, subscriber=fresh_user)
        sub.status = "trialing"
        sub.latest_invoice = None
        sub.stripe_data["latest_invoice"] = None
        sub.stripe_data["billing_reason"] = "subscription_create"
        sub.billing_reason = "subscription_create"

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
            patch("pages.signals.TaskCreditService.grant_subscription_credits"), \
            patch("pages.signals.mark_user_billing_with_plan", wraps=real_mark_user_billing_with_plan), \
            patch("pages.signals.Analytics.identify"), \
            patch("pages.signals.Analytics.track_event"):

            handle_subscription_event(event)

        self.mock_capi.assert_called_once()
        props = self.mock_capi.call_args.kwargs["properties"]
        # Base value from payload is 29.99; with 2x multiplier expect ~59.98
        self.assertAlmostEqual(props["predicted_ltv"], 59.98, places=2)
        self.assertEqual(props["value"], 0)
        self.assertEqual(props["currency"], "USD")

    @tag("batch_pages")
    @patch("pages.signals.ensure_single_individual_subscription")
    def test_subscription_created_dedupes_individual_plan(self, mock_ensure):
        payload = _build_event_payload(billing_reason="subscription_create")
        payload_items = payload["items"]["data"]
        payload_items[0]["price"]["id"] = "price_base"
        payload_items[0]["price"]["usage_type"] = "licensed"
        payload_items[0]["price"]["product"] = "prod_plan"
        payload_items.append(
            {
                "plan": {"usage_type": "metered"},
                "price": {"id": "price_meter", "usage_type": "metered", "product": "prod_meter"},
                "quantity": None,
            }
        )
        event = _build_djstripe_event(payload, event_type="customer.subscription.created")

        fresh_user = User.objects.get(pk=self.user.pk)
        sub = self._mock_subscription(subscriber=fresh_user, current_period_day=12)
        sub.stripe_data["billing_reason"] = "subscription_create"
        sub.customer.id = "cus_test"
        sub.stripe_data = payload

        plan_products = {"prod_plan"}

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
            patch("pages.signals.TaskCreditService.grant_subscription_credits"), \
            patch("pages.signals.mark_user_billing_with_plan", wraps=real_mark_user_billing_with_plan), \
            patch("pages.signals.Analytics.identify"), \
            patch("pages.signals.Analytics.track_event"), \
            patch("pages.signals._individual_plan_product_ids", return_value=plan_products):

            handle_subscription_event(event)

        mock_ensure.assert_called_once_with(
            customer_id="cus_test",
            licensed_price_id="price_base",
            metered_price_id="price_meter",
            metadata=payload.get("metadata"),
            idempotency_key=f"sub-webhook-upsert-{payload.get('id', '')}",
            create_if_missing=False,
        )

    @tag("batch_pages")
    def test_subscription_event_includes_client_ip_from_attribution(self):
        self.mock_capi.reset_mock()
        payload = _build_event_payload(status="trialing", billing_reason="subscription_create", invoice_id=None)
        event = _build_djstripe_event(payload, event_type="customer.subscription.created")

        UserAttribution.objects.update_or_create(
            user=self.user,
            defaults={"last_client_ip": "203.0.113.5"},
        )

        fresh_user = User.objects.get(pk=self.user.pk)
        sub = self._mock_subscription(current_period_day=12, subscriber=fresh_user)
        sub.status = "trialing"
        sub.latest_invoice = None
        sub.stripe_data["latest_invoice"] = None
        sub.stripe_data['billing_reason'] = "subscription_create"
        sub.billing_reason = "subscription_create"

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
            patch("pages.signals.TaskCreditService.grant_subscription_credits"), \
            patch("pages.signals.mark_user_billing_with_plan", wraps=real_mark_user_billing_with_plan), \
            patch("pages.signals.Analytics.identify"), \
            patch("pages.signals.Analytics.track_event"):

            handle_subscription_event(event)

        self.mock_capi.assert_called_once()
        context = self.mock_capi.call_args.kwargs["context"]
        self.assertEqual(context.get("client_ip"), "203.0.113.5")

    @tag("batch_pages")
    def test_subscription_create_update_event_does_not_emit_duplicate_marketing(self):
        self.mock_capi.reset_mock()
        payload = _build_event_payload(billing_reason="subscription_create")
        event = _build_djstripe_event(payload, event_type="customer.subscription.updated")

        fresh_user = User.objects.get(pk=self.user.pk)
        sub = self._mock_subscription(current_period_day=12, subscriber=fresh_user)
        sub.stripe_data['billing_reason'] = "subscription_create"
        sub.billing_reason = "subscription_create"

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
            patch("pages.signals.TaskCreditService.grant_subscription_credits"), \
            patch("pages.signals.mark_user_billing_with_plan", wraps=real_mark_user_billing_with_plan), \
            patch("pages.signals.Analytics.identify"), \
            patch("pages.signals.Analytics.track_event") as mock_track_event:

            handle_subscription_event(event)

        mock_track_event.assert_called_once()
        self.mock_capi.assert_not_called()

    @tag("batch_pages")
    def test_subscription_cycle_emits_renewed_event(self):
        self.mock_capi.reset_mock()
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

        self.mock_capi.assert_not_called()

    @tag("batch_pages")
    def test_subscription_update_skips_credit_grant(self):
        self.mock_capi.reset_mock()
        payload = _build_event_payload(billing_reason="subscription_update")
        event = _build_djstripe_event(payload, event_type="customer.subscription.updated")

        fresh_user = User.objects.get(pk=self.user.pk)
        sub = self._mock_subscription(current_period_day=15, subscriber=fresh_user)
        sub.stripe_data["billing_reason"] = "subscription_update"
        sub.billing_reason = "subscription_update"

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
            patch("pages.signals.TaskCreditService.grant_subscription_credits") as mock_grant, \
            patch("pages.signals.mark_user_billing_with_plan", wraps=real_mark_user_billing_with_plan), \
            patch("pages.signals.Analytics.identify"), \
            patch("pages.signals.Analytics.track_event"):

            handle_subscription_event(event)

        mock_grant.assert_not_called()
        self.mock_capi.assert_not_called()

    @tag("batch_pages")
    def test_trialing_subscription_grants_full_credits(self):
        self.mock_capi.reset_mock()
        payload = _build_event_payload(status="trialing", billing_reason="subscription_create", invoice_id=None)
        event = _build_djstripe_event(payload, event_type="customer.subscription.created")

        fresh_user = User.objects.get(pk=self.user.pk)
        sub = self._mock_subscription(current_period_day=1, subscriber=fresh_user)
        sub.status = "trialing"
        sub.latest_invoice = None
        sub.stripe_data["latest_invoice"] = None

        start_dt = timezone.make_aware(datetime(2025, 9, 1, 8, 0, 0), timezone=dt_timezone.utc)
        end_dt = timezone.make_aware(datetime(2025, 9, 8, 8, 0, 0), timezone=dt_timezone.utc)
        sub.stripe_data["current_period_start"] = str(start_dt)
        sub.stripe_data["current_period_end"] = str(end_dt)
        sub.stripe_data["billing_reason"] = "subscription_create"
        sub.billing_reason = "subscription_create"

        plan_payload = {"id": PlanNamesChoices.STARTUP.value, "monthly_task_credits": 300}

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.get_plan_by_product_id", return_value=plan_payload), \
            patch("pages.signals.TaskCreditService.grant_subscription_credits") as mock_grant, \
            patch("pages.signals.mark_user_billing_with_plan", wraps=real_mark_user_billing_with_plan), \
            patch("pages.signals.Analytics.identify") as mock_identify, \
            patch("pages.signals.Analytics.track_event") as mock_track_event:

            handle_subscription_event(event)

        self.assertTrue(mock_grant.called)
        grant_kwargs = mock_grant.call_args.kwargs
        self.assertIsNone(grant_kwargs["credit_override"])
        self.assertTrue(grant_kwargs["invoice_id"].startswith("trial:sub_123"))
        self.assertEqual(grant_kwargs["expiration_date"], end_dt + relativedelta(months=1))

        self.mock_capi.assert_called_once()
        capi_kwargs = self.mock_capi.call_args.kwargs
        self.assertEqual(capi_kwargs["event_name"], "StartTrial")
        props = capi_kwargs["properties"]
        self.assertEqual(props["value"], 0)
        self.assertEqual(props["currency"], "USD")

        events = [call.kwargs.get("event") for call in mock_track_event.call_args_list]
        self.assertIn(AnalyticsEvent.BILLING_TRIAL_STARTED, events)
        identify_args, _identify_kwargs = mock_identify.call_args
        self.assertTrue(identify_args[1].get("is_trial"))

    @tag("batch_pages")
    def test_trial_conversion_topoffs_credits(self):
        self.mock_capi.reset_mock()
        payload = _build_event_payload(status="active", billing_reason="subscription_cycle", invoice_id="in_paid")
        event = _build_djstripe_event(payload, event_type="customer.subscription.updated")

        fresh_user = User.objects.get(pk=self.user.pk)
        sub = self._mock_subscription(current_period_day=8, subscriber=fresh_user)
        sub.status = "active"

        trial_end = timezone.make_aware(datetime(2025, 9, 8, 8, 0, 0), timezone=dt_timezone.utc)
        period_start = trial_end
        period_end = timezone.make_aware(datetime(2025, 10, 8, 8, 0, 0), timezone=dt_timezone.utc)

        sub.stripe_data["trial_end"] = str(trial_end)
        sub.stripe_data["current_period_start"] = str(period_start)
        sub.stripe_data["current_period_end"] = str(period_end)
        sub.stripe_data["latest_invoice"] = "in_paid"
        sub.stripe_data["billing_reason"] = "subscription_cycle"
        sub.billing_reason = "subscription_cycle"

        plan_payload = {"id": PlanNamesChoices.STARTUP.value, "monthly_task_credits": 300}

        TaskCredit = apps.get_model("api", "TaskCredit")
        TaskCredit.objects.create(
            user=fresh_user,
            credits=300,
            credits_used=200,
            expiration_date=period_end,
            stripe_invoice_id="trial:sub_123:2025-09-01",
            granted_date=period_start,
            plan=PlanNamesChoices.STARTUP,
            grant_type=GrantTypeChoices.PLAN,
            additional_task=False,
        )

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.get_plan_by_product_id", return_value=plan_payload), \
            patch("pages.signals.TaskCreditService.grant_subscription_credits") as mock_grant, \
            patch("pages.signals.mark_user_billing_with_plan", wraps=real_mark_user_billing_with_plan), \
            patch("pages.signals.Analytics.identify"), \
            patch("pages.signals.Analytics.track_event"):

            handle_subscription_event(event)

        self.assertTrue(mock_grant.called)
        grant_kwargs = mock_grant.call_args.kwargs
        self.assertEqual(grant_kwargs["credit_override"], Decimal(200))
        self.assertEqual(grant_kwargs["invoice_id"], "in_paid")
        self.assertEqual(grant_kwargs["expiration_date"], period_end)

    @tag("batch_pages")
    def test_missing_user_billing_logs_exception(self):
        self.mock_capi.reset_mock()
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
        self.mock_capi.assert_not_called()

    @tag("batch_pages")
    def test_subscription_cancellation_updates_plan_trait(self):
        self.mock_capi.reset_mock()
        self.billing.subscription = PlanNames.STARTUP
        self.billing.save(update_fields=["subscription"])
        payload = _build_event_payload(status="canceled")
        event = _build_djstripe_event(payload, event_type="customer.subscription.deleted")

        sub = self._mock_subscription(current_period_day=10, subscriber=self.user)
        sub.status = "canceled"
        sub.stripe_data = payload

        with patch("pages.signals.PaymentsHelper.get_stripe_key"), \
            patch("pages.signals.Subscription.sync_from_stripe_data", return_value=sub), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
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
        self.assertFalse(identify_args[1]["is_trial"])
        self.assertEqual(identify_kwargs, {})

        mock_track_event.assert_called_once()
        track_kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(track_kwargs["properties"]["plan"], PlanNames.FREE)

        self.mock_capi.assert_called_once()
        capi_kwargs = self.mock_capi.call_args.kwargs
        self.assertEqual(capi_kwargs["event_name"], "CancelSubscription")
        self.assertIsNone(capi_kwargs["request"])
        props = capi_kwargs["properties"]
        self.assertEqual(props["plan"], PlanNames.STARTUP)
        self.assertEqual(props["subscription_id"], "sub_123")
        self.assertEqual(props["status"], "canceled")
        self.assertEqual(props["churn_stage"], "voluntary")
        self.assertNotIn("currency", props)
        self.assertNotIn("value", props)
        self.assertNotIn("event_id", props)
        self.assertTrue(capi_kwargs["context"].get("consent"))

    @tag("batch_pages")
    def test_dedicated_ip_allocation_from_subscription(self):
        self.mock_capi.reset_mock()
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
        self.mock_capi.assert_not_called()

    @tag("batch_pages")
    def test_dedicated_ip_release_on_quantity_decrease(self):
        self.mock_capi.reset_mock()
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
        self.mock_capi.assert_not_called()

    @tag("batch_pages")
    def test_dedicated_ip_release_on_cancellation(self):
        self.mock_capi.reset_mock()
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
        self.mock_capi.assert_called_once()


@tag("batch_pages")
class SubscriptionSignalOrganizationTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username="org-owner", email="org@example.com", password="pw")
        self.org = Organization.objects.create(name="Org", slug="org", created_by=owner)
        billing = self.org.billing
        billing.stripe_customer_id = "cus_org"
        billing.subscription = PlanNamesChoices.ORG_TEAM.value
        billing.save(update_fields=["stripe_customer_id", "subscription"])
        capi_patcher = patch("pages.signals.capi")
        self.addCleanup(capi_patcher.stop)
        self.mock_capi = capi_patcher.start()
        self.mock_capi.reset_mock()
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
        self.mock_capi.reset_mock()
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
        self.mock_capi.assert_not_called()

    @patch("pages.signals.stripe.SubscriptionItem.create")
    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_create_with_existing_seats_grants_delta(self, mock_sync, mock_plan, mock_grant, mock_item_create):
        self.mock_capi.reset_mock()
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
        self.mock_capi.assert_not_called()

    @patch("pages.signals.stripe.SubscriptionItem.create")
    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_update_grants_difference(self, mock_sync, mock_plan, mock_grant, mock_item_create):
        self.mock_capi.reset_mock()
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
        self.mock_capi.assert_not_called()

    @patch("pages.signals.stripe.SubscriptionItem.create")
    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_update_decrease_no_grant(self, mock_sync, mock_plan, mock_grant, mock_item_create):
        self.mock_capi.reset_mock()
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
        self.mock_capi.assert_not_called()

    @patch("pages.signals.stripe.SubscriptionItem.create")
    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_cycle_renews_with_replace_current(self, mock_sync, mock_plan, mock_grant, mock_item_create):
        self.mock_capi.reset_mock()
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
        self.mock_capi.assert_not_called()

    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_adds_overage_item_when_missing(self, mock_sync, mock_plan, mock_grant):
        self.mock_capi.reset_mock()
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
        self.mock_capi.assert_not_called()

    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_skips_overage_item_when_present(self, mock_sync, mock_plan, mock_grant):
        self.mock_capi.reset_mock()
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
        self.mock_capi.assert_not_called()

    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_detach_pending_skips_overage_create(self, mock_sync, mock_plan, mock_grant):
        self.mock_capi.reset_mock()
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
        self.mock_capi.assert_not_called()

    @patch("pages.signals.TaskCreditService.grant_subscription_credits_for_organization")
    @patch("pages.signals.get_plan_by_product_id")
    @patch("pages.signals.Subscription.sync_from_stripe_data")
    def test_subscription_detach_pending_clears_flag_when_item_present(self, mock_sync, mock_plan, mock_grant):
        self.mock_capi.reset_mock()
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
        self.mock_capi.assert_not_called()


@tag("batch_pages")
class PaymentFailedSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fail-user", email="fail@example.com", password="pw")

    def test_invoice_payment_failed_for_user_tracks_event(self):
        payload = _build_invoice_payload(
            customer_id="cus_user",
            subscription_id="sub_user",
            attempt_count=2,
            next_payment_attempt=None,
            auto_advance=False,
            amount_due=2500,
        )
        event = _build_djstripe_event(payload, event_type="invoice.payment_failed")

        invoice_obj = SimpleNamespace(
            id=payload["id"],
            customer=SimpleNamespace(id="cus_user", subscriber=self.user),
            subscription=SimpleNamespace(id="sub_user"),
            number=payload["number"],
        )

        with patch("pages.signals.stripe_status", return_value=SimpleNamespace(enabled=True)), \
            patch("pages.signals.PaymentsHelper.get_stripe_key", return_value="sk_test"), \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj), \
            patch("pages.signals.Analytics.track_event") as mock_track_event, \
            patch("pages.signals.Analytics.track_event_anonymous") as mock_track_anonymous, \
            patch("pages.signals.get_plan_by_product_id", return_value=None):

            handle_invoice_payment_failed(event)

        mock_track_anonymous.assert_not_called()
        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.user.id)
        self.assertEqual(kwargs["event"], AnalyticsEvent.BILLING_PAYMENT_FAILED)
        props = kwargs["properties"]
        self.assertEqual(props["attempt_number"], 2)
        self.assertTrue(props["final_attempt"])
        self.assertEqual(props["stripe.invoice_id"], payload["id"])
        self.assertEqual(props["stripe.subscription_id"], payload["subscription"])

    def test_invoice_payment_failed_for_org_tracks_creator(self):
        owner = User.objects.create_user(username="org-owner-fail", email="org-fail@example.com", password="pw")
        org = Organization.objects.create(name="Fail Org", slug="fail-org", created_by=owner)
        billing = org.billing
        billing.stripe_customer_id = "cus_org_fail"
        billing.save(update_fields=["stripe_customer_id"])

        payload = _build_invoice_payload(
            customer_id="cus_org_fail",
            subscription_id="sub_org",
            attempt_count=1,
            next_payment_attempt=timezone.now().timestamp() + 3600,
            auto_advance=True,
            product_id="prod_org",
        )
        event = _build_djstripe_event(payload, event_type="invoice.payment_failed")

        invoice_obj = SimpleNamespace(
            id=payload["id"],
            customer=SimpleNamespace(id="cus_org_fail", subscriber=None),
            subscription=None,
            number=payload["number"],
        )

        with patch("pages.signals.stripe_status", return_value=SimpleNamespace(enabled=True)), \
            patch("pages.signals.PaymentsHelper.get_stripe_key", return_value="sk_test"), \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj), \
            patch("pages.signals.Analytics.track_event") as mock_track_event, \
            patch("pages.signals.Analytics.track_event_anonymous") as mock_track_anonymous, \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.ORG_TEAM.value}):

            handle_invoice_payment_failed(event)

        mock_track_anonymous.assert_not_called()
        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["user_id"], owner.id)
        props = kwargs["properties"]
        self.assertFalse(props.get("final_attempt", True))
        self.assertEqual(props["organization_id"], str(org.id))
        self.assertEqual(props["plan"], PlanNamesChoices.ORG_TEAM.value)

    def test_invoice_payment_failed_resolves_user_from_customer_lookup_when_missing_subscriber(self):
        payload = _build_invoice_payload(
            customer_id="cus_lookup_failed",
            subscription_id="sub_lookup_failed",
            attempt_count=1,
            next_payment_attempt=None,
        )
        event = _build_djstripe_event(payload, event_type="invoice.payment_failed")

        invoice_obj = SimpleNamespace(
            id=payload["id"],
            customer=SimpleNamespace(id="cus_lookup_failed", subscriber=None),
            subscription=SimpleNamespace(id="sub_lookup_failed"),
            number=payload["number"],
        )

        with patch("pages.signals.stripe_status", return_value=SimpleNamespace(enabled=True)), \
            patch("pages.signals.PaymentsHelper.get_stripe_key", return_value="sk_test"), \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj), \
            patch("pages.signals._get_customer_with_subscriber", return_value=SimpleNamespace(id="cus_lookup_failed", subscriber=self.user)), \
            patch("pages.signals.Analytics.track_event") as mock_track_event, \
            patch("pages.signals.Analytics.track_event_anonymous") as mock_track_anonymous, \
            patch("pages.signals.get_plan_by_product_id", return_value=None):

            handle_invoice_payment_failed(event)

        mock_track_anonymous.assert_not_called()
        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.user.id)
        self.assertEqual(kwargs["event"], AnalyticsEvent.BILLING_PAYMENT_FAILED)


@tag("batch_pages")
class PaymentSucceededSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="success-user", email="success@example.com", password="pw")

    def test_invoice_payment_succeeded_for_user_tracks_event(self):
        payload = _build_invoice_payload(
            customer_id="cus_user_succeeded",
            subscription_id="sub_user_succeeded",
            attempt_count=1,
            next_payment_attempt=None,
            auto_advance=False,
            amount_due=3000,
            amount_paid=3000,
            status="paid",
            receipt_number="rcpt-123",
        )
        event = _build_djstripe_event(payload, event_type="invoice.payment_succeeded")

        invoice_obj = SimpleNamespace(
            id=payload["id"],
            customer=SimpleNamespace(id="cus_user_succeeded", subscriber=self.user),
            subscription=SimpleNamespace(id="sub_user_succeeded"),
            number=payload["number"],
        )

        with patch("pages.signals.stripe_status", return_value=SimpleNamespace(enabled=True)), \
            patch("pages.signals.PaymentsHelper.get_stripe_key", return_value="sk_test"), \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj), \
            patch("pages.signals.Analytics.track_event") as mock_track_event, \
            patch("pages.signals.Analytics.track_event_anonymous") as mock_track_anonymous, \
            patch("pages.signals.get_plan_by_product_id", return_value=None):

            handle_invoice_payment_succeeded(event)

        mock_track_anonymous.assert_not_called()
        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.user.id)
        self.assertEqual(kwargs["event"], AnalyticsEvent.BILLING_PAYMENT_SUCCEEDED)
        props = kwargs["properties"]
        self.assertEqual(props["attempt_number"], 1)
        self.assertTrue(props["final_attempt"])
        self.assertEqual(props["stripe.invoice_id"], payload["id"])
        self.assertEqual(props["stripe.subscription_id"], payload["subscription"])
        self.assertEqual(props["amount_paid"], 30.0)
        self.assertEqual(props["receipt_number"], "rcpt-123")

    def test_invoice_payment_succeeded_emits_subscribe_capi_for_first_payment(self):
        payload = _build_invoice_payload(
            customer_id="cus_user_succeeded",
            subscription_id="sub_user_succeeded",
            amount_paid=3000,
            status="paid",
            billing_reason="subscription_create",
            product_id="prod_plan",
        )
        payload["lines"]["data"][0]["amount"] = 3000
        payload["lines"]["data"][0]["price"]["unit_amount"] = 3000
        payload["lines"]["data"][0]["price"]["currency"] = "usd"
        event = _build_djstripe_event(payload, event_type="invoice.payment_succeeded")

        invoice_obj = SimpleNamespace(
            id=payload["id"],
            customer=SimpleNamespace(id="cus_user_succeeded", subscriber=self.user),
            subscription=SimpleNamespace(id="sub_user_succeeded", stripe_data={"metadata": {"gobii_event_id": "evt-123"}}),
            number=payload["number"],
        )

        with patch("pages.signals.stripe_status", return_value=SimpleNamespace(enabled=True)), \
            patch("pages.signals.PaymentsHelper.get_stripe_key", return_value="sk_test"), \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj), \
            patch("pages.signals.Analytics.track_event"), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
            patch("pages.signals.capi") as mock_capi:

            handle_invoice_payment_succeeded(event)

        mock_capi.assert_called_once()
        capi_kwargs = mock_capi.call_args.kwargs
        self.assertEqual(capi_kwargs["event_name"], "Subscribe")
        props = capi_kwargs["properties"]
        self.assertEqual(props["plan"], PlanNamesChoices.STARTUP.value)
        self.assertEqual(props["subscription_id"], "sub_user_succeeded")
        self.assertEqual(props["stripe.invoice_id"], payload["id"])
        self.assertEqual(props["transaction_value"], 30.0)
        self.assertEqual(props["currency"], "USD")
        self.assertEqual(props["event_id"], "evt-123")

    def test_invoice_payment_succeeded_emits_ga_only_subscribe_for_standard_renewal(self):
        payload = _build_invoice_payload(
            customer_id="cus_user_succeeded",
            subscription_id="sub_user_succeeded",
            amount_paid=3000,
            status="paid",
            billing_reason="subscription_cycle",
            product_id="prod_plan",
        )
        payload["lines"]["data"][0]["amount"] = 3000
        payload["lines"]["data"][0]["price"]["unit_amount"] = 3000
        payload["lines"]["data"][0]["price"]["currency"] = "usd"
        event = _build_djstripe_event(payload, event_type="invoice.payment_succeeded")

        invoice_obj = SimpleNamespace(
            id=payload["id"],
            customer=SimpleNamespace(id="cus_user_succeeded", subscriber=self.user),
            subscription=SimpleNamespace(id="sub_user_succeeded", stripe_data={"metadata": {"gobii_event_id": "evt-renew"}}),
            number=payload["number"],
        )

        with patch("pages.signals.stripe_status", return_value=SimpleNamespace(enabled=True)), \
            patch("pages.signals.PaymentsHelper.get_stripe_key", return_value="sk_test"), \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj), \
            patch("pages.signals.Analytics.track_event"), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
            patch("pages.signals.capi") as mock_capi:

            handle_invoice_payment_succeeded(event)

        mock_capi.assert_called_once()
        capi_kwargs = mock_capi.call_args.kwargs
        self.assertEqual(capi_kwargs["event_name"], "Subscribe")
        self.assertEqual(capi_kwargs["provider_targets"], ["google_analytics"])
        props = capi_kwargs["properties"]
        self.assertEqual(props["plan"], PlanNamesChoices.STARTUP.value)
        self.assertEqual(props["subscription_id"], "sub_user_succeeded")
        self.assertEqual(props["stripe.invoice_id"], payload["id"])
        self.assertEqual(props["transaction_value"], 30.0)
        self.assertEqual(props["currency"], "USD")
        self.assertEqual(props["event_id"], payload["id"])

    def test_invoice_payment_succeeded_does_not_emit_subscribe_for_trial_start(self):
        trial_end = timezone.make_aware(datetime(2025, 9, 8, 8, 0, 0), timezone=dt_timezone.utc)
        payload = _build_invoice_payload(
            customer_id="cus_user_succeeded",
            subscription_id="sub_user_succeeded",
            amount_paid=0,
            status="paid",
            billing_reason="subscription_create",
            product_id="prod_plan",
        )
        event = _build_djstripe_event(payload, event_type="invoice.payment_succeeded")

        invoice_obj = SimpleNamespace(
            id=payload["id"],
            customer=SimpleNamespace(id="cus_user_succeeded", subscriber=self.user),
            subscription=SimpleNamespace(
                id="sub_user_succeeded",
                stripe_data={
                    "status": "trialing",
                    "trial_end": str(trial_end),
                },
            ),
            number=payload["number"],
        )

        with patch("pages.signals.stripe_status", return_value=SimpleNamespace(enabled=True)), \
            patch("pages.signals.PaymentsHelper.get_stripe_key", return_value="sk_test"), \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj), \
            patch("pages.signals.Analytics.track_event"), \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}), \
            patch("pages.signals.capi") as mock_capi:

            handle_invoice_payment_succeeded(event)

        mock_capi.assert_not_called()

    def test_invoice_payment_succeeded_emits_trial_converted_event(self):
        trial_end = timezone.make_aware(datetime(2025, 9, 8, 8, 0, 0), timezone=dt_timezone.utc)
        payload = _build_invoice_payload(
            customer_id="cus_user_succeeded",
            subscription_id="sub_user_succeeded",
            amount_paid=3000,
            status="paid",
            billing_reason="subscription_cycle",
            product_id="prod_plan",
        )
        payload["lines"]["data"][0]["period"] = {
            "start": int(trial_end.timestamp()),
            "end": int((trial_end + timedelta(days=30)).timestamp()),
        }
        event = _build_djstripe_event(payload, event_type="invoice.payment_succeeded")

        invoice_obj = SimpleNamespace(
            id=payload["id"],
            customer=SimpleNamespace(id="cus_user_succeeded", subscriber=self.user),
            subscription=SimpleNamespace(id="sub_user_succeeded", stripe_data={"trial_end": str(trial_end)}),
            number=payload["number"],
        )

        with patch("pages.signals.stripe_status", return_value=SimpleNamespace(enabled=True)), \
            patch("pages.signals.PaymentsHelper.get_stripe_key", return_value="sk_test"), \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj), \
            patch("pages.signals.Analytics.track_event") as mock_track_event, \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.STARTUP.value}):

            handle_invoice_payment_succeeded(event)

        events = [call.kwargs.get("event") for call in mock_track_event.call_args_list]
        self.assertIn(AnalyticsEvent.BILLING_PAYMENT_SUCCEEDED, events)
        self.assertIn(AnalyticsEvent.BILLING_TRIAL_CONVERTED, events)

    def test_invoice_payment_succeeded_for_org_tracks_creator(self):
        owner = User.objects.create_user(username="org-owner-success", email="org-success@example.com", password="pw")
        org = Organization.objects.create(name="Success Org", slug="success-org", created_by=owner)
        billing = org.billing
        billing.stripe_customer_id = "cus_org_success"
        billing.save(update_fields=["stripe_customer_id"])

        payload = _build_invoice_payload(
            customer_id="cus_org_success",
            subscription_id="sub_org_success",
            attempt_count=1,
            next_payment_attempt=None,
            auto_advance=False,
            amount_due=3500,
            amount_paid=3500,
            status="paid",
            product_id="prod_org",
        )
        event = _build_djstripe_event(payload, event_type="invoice.payment_succeeded")

        invoice_obj = SimpleNamespace(
            id=payload["id"],
            customer=SimpleNamespace(id="cus_org_success", subscriber=None),
            subscription=None,
            number=payload["number"],
        )

        with patch("pages.signals.stripe_status", return_value=SimpleNamespace(enabled=True)), \
            patch("pages.signals.PaymentsHelper.get_stripe_key", return_value="sk_test"), \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj), \
            patch("pages.signals.Analytics.track_event") as mock_track_event, \
            patch("pages.signals.Analytics.track_event_anonymous") as mock_track_anonymous, \
            patch("pages.signals.get_plan_by_product_id", return_value={"id": PlanNamesChoices.ORG_TEAM.value}):

            handle_invoice_payment_succeeded(event)

        mock_track_anonymous.assert_not_called()
        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["user_id"], owner.id)
        props = kwargs["properties"]
        self.assertTrue(props.get("final_attempt"))
        self.assertEqual(props["organization_id"], str(org.id))
        self.assertEqual(props["plan"], PlanNamesChoices.ORG_TEAM.value)
        self.assertEqual(props["amount_paid"], 35.0)

    def test_invoice_payment_succeeded_resolves_user_from_customer_lookup_when_missing_subscriber(self):
        payload = _build_invoice_payload(
            customer_id="cus_lookup_succeeded",
            subscription_id="sub_lookup_succeeded",
            attempt_count=1,
            next_payment_attempt=None,
            amount_paid=3200,
            status="paid",
        )
        event = _build_djstripe_event(payload, event_type="invoice.payment_succeeded")

        invoice_obj = SimpleNamespace(
            id=payload["id"],
            customer=SimpleNamespace(id="cus_lookup_succeeded", subscriber=None),
            subscription=SimpleNamespace(id="sub_lookup_succeeded"),
            number=payload["number"],
        )

        with patch("pages.signals.stripe_status", return_value=SimpleNamespace(enabled=True)), \
            patch("pages.signals.PaymentsHelper.get_stripe_key", return_value="sk_test"), \
            patch("pages.signals.Invoice.sync_from_stripe_data", return_value=invoice_obj), \
            patch("pages.signals._get_customer_with_subscriber", return_value=SimpleNamespace(id="cus_lookup_succeeded", subscriber=self.user)), \
            patch("pages.signals.Analytics.track_event") as mock_track_event, \
            patch("pages.signals.Analytics.track_event_anonymous") as mock_track_anonymous, \
            patch("pages.signals.get_plan_by_product_id", return_value=None):

            handle_invoice_payment_succeeded(event)

        mock_track_anonymous.assert_not_called()
        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.user.id)
        self.assertEqual(kwargs["event"], AnalyticsEvent.BILLING_PAYMENT_SUCCEEDED)
