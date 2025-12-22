from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, tag

from constants.plans import PlanNames
from pages.views import _emit_checkout_initiated_event


@tag("batch_marketing_events")
class PricingCheckoutCapiEventTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_emit_checkout_event_includes_plan_metadata(self):
        request = self.factory.get("/pricing/")
        user = SimpleNamespace(id="user-1", email="test@example.com", phone="+15555550123")

        with patch("pages.views.capi") as mock_capi:
            _emit_checkout_initiated_event(
                request=request,
                user=user,
                plan_code=PlanNames.STARTUP,
                plan_label="Pro",
                value=50,
                currency="usd",
                event_id="evt-123",
            )

        mock_capi.assert_called_once_with(
            user=user,
            event_name="InitiateCheckout",
            properties={
                "plan": PlanNames.STARTUP,
                "plan_label": "Pro",
                "event_id": "evt-123",
                "value": 50,
                "currency": "USD",
            },
            request=request,
        )

    def test_emit_checkout_event_skips_empty_currency_and_value(self):
        request = self.factory.get("/pricing/")
        user = SimpleNamespace(id="user-2")

        with patch("pages.views.capi") as mock_capi:
            _emit_checkout_initiated_event(
                request=request,
                user=user,
                plan_code=PlanNames.SCALE,
                plan_label="Scale",
                value=None,
                currency=None,
                event_id="evt-456",
            )

        mock_capi.assert_called_once()
        properties = mock_capi.call_args.kwargs["properties"]
        self.assertEqual(
            properties,
            {
                "plan": PlanNames.SCALE,
                "plan_label": "Scale",
                "event_id": "evt-456",
                "currency": "USD",
            },
        )

    def test_emit_checkout_event_supports_custom_event_name(self):
        request = self.factory.get("/pricing/")
        user = SimpleNamespace(id="user-3")

        with patch("pages.views.capi") as mock_capi:
            _emit_checkout_initiated_event(
                request=request,
                user=user,
                plan_code=PlanNames.STARTUP,
                plan_label="Pro",
                value=10,
                currency="eur",
                event_id="evt-789",
                event_name="AddPaymentInfo",
            )

        mock_capi.assert_called_once_with(
            user=user,
            event_name="AddPaymentInfo",
            properties={
                "plan": PlanNames.STARTUP,
                "plan_label": "Pro",
                "event_id": "evt-789",
                "value": 10,
                "currency": "EUR",
            },
            request=request,
        )

    def test_emit_checkout_event_includes_post_checkout_redirect_flag(self):
        request = self.factory.get("/pricing/")
        user = SimpleNamespace(id="user-4")

        with patch("pages.views.capi") as mock_capi:
            _emit_checkout_initiated_event(
                request=request,
                user=user,
                plan_code=PlanNames.STARTUP,
                plan_label="Pro",
                value=20,
                currency="usd",
                event_id="evt-101",
                post_checkout_redirect_used=True,
            )

        mock_capi.assert_called_once_with(
            user=user,
            event_name="InitiateCheckout",
            properties={
                "plan": PlanNames.STARTUP,
                "plan_label": "Pro",
                "event_id": "evt-101",
                "value": 20,
                "post_checkout_redirect_used": True,
                "currency": "USD",
            },
            request=request,
        )
