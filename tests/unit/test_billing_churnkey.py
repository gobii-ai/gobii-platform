import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from billing.churnkey import build_churnkey_cancel_flow_config, get_churnkey_auth_hash


class EmptyProxyQuerySet(list):
    def select_related(self, *args, **kwargs):
        return self

    def prefetch_related(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self


@tag("batch_billing")
class BillingChurnKeyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="billing-churnkey-user",
            email="billing-churnkey-user@example.com",
            password="pw12345",
        )
        self.client = Client()
        self.client.force_login(self.user)

    @override_settings(CHURN_KEY_API_KEY="super-secret")
    def test_get_churnkey_auth_hash_uses_customer_id_hmac(self):
        self.assertEqual(
            get_churnkey_auth_hash("cus_123"),
            "c5a6858761ff851e55565b5459d24e4133fdfa986129e233c5421f0a074a821d",
        )

    @override_settings(CHURN_KEY_API_KEY="")
    def test_build_cancel_flow_config_returns_none_without_api_key(self):
        self.assertIsNone(
            build_churnkey_cancel_flow_config(
                customer_id="cus_123",
                subscription_id="sub_123",
                livemode=False,
            )
        )

    @override_settings(
        STRIPE_ENABLED=True,
        CHURN_KEY_API_KEY="super-secret",
        CHURN_KEY_APP_ID="jeqgxz3uq",
    )
    @patch("console.views.Analytics.track_event")
    @patch("console.views.reconcile_user_plan_from_stripe", return_value={"id": "startup", "name": "Startup"})
    @patch("console.mixins.reconcile_user_plan_from_stripe", return_value={"id": "startup", "name": "Startup"})
    @patch("console.views.get_subscription_base_price", return_value=(Decimal("99"), "USD"))
    @patch("console.views.AddonEntitlementService.get_addon_context_for_owner", return_value={})
    @patch("console.views.DedicatedProxyService.allocated_count", return_value=0)
    @patch("console.views.DedicatedProxyService.allocated_proxies", return_value=EmptyProxyQuerySet())
    @patch("console.views.is_multi_assign_enabled", return_value=False)
    @patch("console.views._resolve_dedicated_ip_pricing", return_value=(Decimal("0"), "USD"))
    @patch("console.views.get_stripe_customer", return_value=SimpleNamespace(id="cus_123", livemode=False))
    @patch("console.views.get_active_subscription")
    def test_billing_page_exposes_churnkey_payload_and_loader(
        self,
        mock_get_active_subscription,
        _mock_get_stripe_customer,
        _mock_resolve_dedicated_ip_pricing,
        _mock_is_multi_assign_enabled,
        _mock_allocated_proxies,
        _mock_allocated_count,
        _mock_addon_context,
        _mock_get_subscription_base_price,
        _mock_console_mixins_plan,
        _mock_console_views_plan,
        _mock_track_event,
    ):
        mock_get_active_subscription.return_value = SimpleNamespace(
            id="sub_123",
            current_period_start=timezone.now(),
            current_period_end=timezone.now(),
            cancel_at=None,
            cancel_at_period_end=False,
            status="active",
            trial_end=None,
            is_status_current=lambda: True,
        )

        response = self.client.get(reverse("billing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "https://assets.churnkey.co/js/app.js?appId=jeqgxz3uq")

        soup = BeautifulSoup(response.content, "html.parser")
        payload_script = soup.find("script", id="billing-props")
        self.assertIsNotNone(payload_script)
        self.assertTrue(payload_script.string)

        payload = json.loads(payload_script.string)
        self.assertEqual(
            payload["churnKey"],
            {
                "enabled": True,
                "appId": "jeqgxz3uq",
                "customerId": "cus_123",
                "subscriptionId": "sub_123",
                "authHash": "c5a6858761ff851e55565b5459d24e4133fdfa986129e233c5421f0a074a821d",
                "mode": "test",
                "provider": "stripe",
            },
        )
