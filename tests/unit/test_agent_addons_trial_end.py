from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, tag


@tag("batch_billing")
class AgentAddonsTrialEndTests(TestCase):
    def test_trial_addon_purchase_ends_trial_immediately(self):
        from console import agent_addons

        owner = SimpleNamespace(id=1)

        stripe_stub = SimpleNamespace(
            Subscription=SimpleNamespace(
                retrieve=lambda *args, **kwargs: {
                    "id": "sub_trial",
                    "status": "trialing",
                    "items": {"data": []},
                },
                modify=lambda *args, **kwargs: {
                    "id": "sub_trial",
                    "status": "active",
                    "items": {"data": []},
                },
            ),
            error=SimpleNamespace(StripeError=Exception),
        )

        with patch("console.agent_addons.stripe_status", return_value=SimpleNamespace(enabled=True)), \
             patch("console.agent_addons.stripe", stripe_stub), \
             patch("console.agent_addons._ensure_stripe_ready", return_value=None), \
             patch("console.agent_addons.get_active_subscription", return_value=SimpleNamespace(id="sub_trial")), \
             patch(
                 "console.agent_addons.AddonEntitlementService.get_price_options",
                 return_value=[SimpleNamespace(price_id="price_task_pack")],
             ), \
             patch(
                 "console.agent_addons.BillingService.get_current_billing_period_for_owner",
                 return_value=(date(2026, 2, 1), date(2026, 3, 1)),
             ), \
             patch("console.agent_addons.AddonEntitlementService.sync_subscription_entitlements", return_value=None):
            # Swap in a mock for Subscription.modify so we can assert on kwargs.
            with patch.object(stripe_stub.Subscription, "modify", wraps=stripe_stub.Subscription.modify) as mock_modify:
                success, error, status = agent_addons.update_task_pack_quantities(
                    owner=owner,
                    owner_type="user",
                    plan_id="startup",
                    quantities={"price_task_pack": 1},
                )

        self.assertTrue(success)
        self.assertIsNone(error)
        self.assertEqual(status, 200)
        _, kwargs = mock_modify.call_args
        self.assertEqual(kwargs.get("trial_end"), "now")
