import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from api.models import (
    BrowserUseAgent,
    DedicatedProxyAllocation,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    ProxyServer,
)


def create_persistent_agent(user, name: str, *, organization: Organization | None = None) -> PersistentAgent:
    """Create a PersistentAgent (and backing BrowserUseAgent) for tests."""
    browser_agent = BrowserUseAgent(user=user, name=name)
    if organization is not None:
        browser_agent._agent_creation_organization = organization
    browser_agent.save()
    if hasattr(browser_agent, "_agent_creation_organization"):
        delattr(browser_agent, "_agent_creation_organization")

    persistent_agent = PersistentAgent(
        user=user,
        organization=organization,
        name=name,
        charter="",
        browser_use_agent=browser_agent,
    )
    persistent_agent.full_clean()
    persistent_agent.save()
    return persistent_agent


@tag("batch_billing")
class ConsoleBillingUpdateApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="billing-owner",
            email="billing-owner@example.com",
            password="pw12345",
        )
        self.org = Organization.objects.create(
            name="Billing Org",
            slug="billing-org",
            created_by=self.user,
        )
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self.client.force_login(self.user)

        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(self.org.id)
        session["context_name"] = self.org.name
        session.save()

        self.url = reverse("console_billing_update")

    @patch("console.billing_update_service.AddonEntitlementService.sync_subscription_entitlements")
    @patch(
        "console.billing_update_service.BillingService.get_current_billing_period_for_owner",
        return_value=(date(2026, 2, 1), date(2026, 3, 1)),
    )
    @patch(
        "console.billing_update_service.AddonEntitlementService.get_price_options",
        return_value=[SimpleNamespace(price_id="price_task_pack")],
    )
    @patch("console.billing_update_service._get_owner_plan_id", return_value="startup")
    @patch("console.billing_update_service.get_active_subscription", return_value=SimpleNamespace(id="sub_trial"))
    @patch("console.billing_update_service._assign_stripe_api_key", return_value=None)
    @patch("console.billing_update_service._sync_subscription_after_direct_update")
    @patch("console.billing_update_service.stripe.Subscription.modify")
    @patch("console.billing_update_service.stripe.Subscription.retrieve")
    @patch("console.billing_update_service.stripe_status")
    def test_trial_addon_purchase_ends_trial_immediately(
        self,
        mock_stripe_status,
        mock_retrieve,
        mock_modify,
        mock_sync_subscription,
        mock_assign_key,
        mock_get_active_subscription,
        mock_get_plan_id,
        mock_get_price_options,
        mock_period,
        mock_sync_entitlements,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)

        session = self.client.session
        session["context_type"] = "personal"
        session["context_id"] = str(self.user.id)
        session["context_name"] = self.user.get_full_name() or self.user.email
        session.save()

        mock_retrieve.return_value = {
            "id": "sub_trial",
            "status": "trialing",
            "items": {"data": []},
        }
        mock_modify.return_value = {
            "id": "sub_trial",
            "status": "active",
            "items": {"data": []},
            "latest_invoice": None,
        }

        resp = self.client.post(
            self.url,
            data=json.dumps({
                "ownerType": "user",
                "addonQuantities": {"price_task_pack": 1},
            }),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("ok"))

        _, kwargs = mock_modify.call_args
        self.assertEqual(kwargs.get("trial_end"), "now")
        mock_sync_subscription.assert_called_once_with(mock_modify.return_value)

    @patch("console.billing_update_service.AddonEntitlementService.get_price_options", return_value=[SimpleNamespace(price_id="price_task_pack")])
    @patch("console.billing_update_service._get_owner_plan_id", return_value="startup")
    @patch("console.billing_update_service.get_active_subscription", return_value=None)
    @patch("console.billing_update_service.stripe_status")
    def test_no_active_subscription_returns_support_detail(
        self,
        mock_stripe_status,
        _mock_get_active_subscription,
        _mock_get_plan_id,
        _mock_get_price_options,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)

        session = self.client.session
        session["context_type"] = "personal"
        session["context_id"] = str(self.user.id)
        session["context_name"] = self.user.get_full_name() or self.user.email
        session.save()

        resp = self.client.post(
            self.url,
            data=json.dumps({
                "ownerType": "user",
                "addonQuantities": {"price_task_pack": 1},
            }),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 400)
        payload = resp.json()
        self.assertEqual(payload.get("error"), "no_active_subscription")
        self.assertIn("support@gobii.ai", payload.get("detail", ""))

    @patch("console.billing_update_service.stripe_status")
    def test_org_addons_rejected_without_seats(self, mock_stripe_status):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)

        # Ensure we start with no seats.
        self.org.billing.purchased_seats = 0
        self.org.billing.save(update_fields=["purchased_seats"])

        resp = self.client.post(
            self.url,
            data=json.dumps({
                "ownerType": "organization",
                "organizationId": str(self.org.id),
                "addonQuantities": {"price_task_pack": 1},
            }),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 400)
        payload = resp.json()
        self.assertEqual(payload.get("error"), "seats_required")

    @patch("console.billing_update_service._assign_stripe_api_key", return_value=None)
    @patch("console.billing_update_service.stripe.Subscription.retrieve")
    @patch("console.billing_update_service._sync_subscription_after_direct_update")
    @patch(
        "console.billing_update_service.ensure_single_individual_subscription",
        return_value=({"id": "sub_plan_change"}, "updated"),
    )
    @patch(
        "console.billing_update_service.get_or_create_stripe_customer",
        return_value=SimpleNamespace(id="cus_plan_change"),
    )
    @patch("console.billing_update_service.stripe_status")
    def test_plan_change_syncs_subscription_immediately(
        self,
        mock_stripe_status,
        _mock_get_customer,
        _mock_ensure_single_subscription,
        mock_sync_subscription,
        mock_retrieve_subscription,
        _mock_assign_key,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)
        mock_retrieve_subscription.return_value = {
            "id": "sub_plan_change",
            "latest_invoice": None,
        }

        session = self.client.session
        session["context_type"] = "personal"
        session["context_id"] = str(self.user.id)
        session["context_name"] = self.user.get_full_name() or self.user.email
        session.save()

        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "ownerType": "user",
                    "planTarget": "startup",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload.get("ok"))
        mock_retrieve_subscription.assert_called_once()
        mock_sync_subscription.assert_called_once_with(mock_retrieve_subscription.return_value)

    @patch("console.billing_update_service._assign_stripe_api_key", return_value=None)
    @patch("console.billing_update_service.stripe.Subscription.retrieve")
    @patch(
        "console.billing_update_service.ensure_single_individual_subscription",
        return_value=({"id": "sub_plan_change"}, "updated"),
    )
    @patch(
        "console.billing_update_service.get_or_create_stripe_customer",
        return_value=SimpleNamespace(id="cus_plan_change"),
    )
    @patch("util.subscription_helper.Subscription.sync_from_stripe_data", side_effect=RuntimeError("sync failure"))
    @patch("console.billing_update_service.stripe_status")
    def test_plan_change_sync_failures_are_best_effort(
        self,
        mock_stripe_status,
        _mock_subscription_sync,
        _mock_get_customer,
        _mock_ensure_single_subscription,
        mock_retrieve_subscription,
        _mock_assign_key,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)
        mock_retrieve_subscription.return_value = {
            "id": "sub_plan_change",
            "latest_invoice": None,
        }

        session = self.client.session
        session["context_type"] = "personal"
        session["context_id"] = str(self.user.id)
        session["context_name"] = self.user.get_full_name() or self.user.email
        session.save()

        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "ownerType": "user",
                    "planTarget": "startup",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("ok"))

    @patch("console.billing_update_service._update_stripe_dedicated_ip_quantity")
    @patch("console.billing_update_service.stripe_status")
    @patch("console.billing_update_service._get_owner_plan_id", return_value="org_team")
    def test_dedicated_ip_removal_auto_unassigns_and_is_scoped(
        self,
        mock_get_plan_id,
        mock_stripe_status,
        mock_update_dedicated_qty,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)
        mock_update_dedicated_qty.return_value = None

        # Give the org seats so dedicated IP changes are allowed past the seat gate.
        self.org.billing.purchased_seats = 1
        self.org.billing.save(update_fields=["purchased_seats"])

        proxy = ProxyServer.objects.create(
            name="Dedicated 1",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="10.0.0.1",
            port=8080,
            is_active=True,
            is_dedicated=True,
        )
        DedicatedProxyAllocation.objects.assign_to_owner(proxy, self.org)

        org_agent = create_persistent_agent(self.user, "Org Agent", organization=self.org)
        org_agent.browser_use_agent.preferred_proxy = proxy
        org_agent.browser_use_agent.save(update_fields=["preferred_proxy"])

        # Another org assigns the same proxy id. This should not leak in error payloads.
        other_user = get_user_model().objects.create_user(
            username="other-user",
            email="other-user@example.com",
            password="pw12345",
        )
        other_org = Organization.objects.create(
            name="Other Org",
            slug="other-org",
            created_by=other_user,
        )
        other_org.billing.purchased_seats = 1
        other_org.billing.save(update_fields=["purchased_seats"])
        other_agent = create_persistent_agent(other_user, "Other Org Agent", organization=other_org)
        other_agent.browser_use_agent.preferred_proxy = proxy
        other_agent.browser_use_agent.save(update_fields=["preferred_proxy"])

        resp = self.client.post(
            self.url,
            data=json.dumps({
                "ownerType": "organization",
                "organizationId": str(self.org.id),
                "dedicatedIps": {
                    "addQuantity": 0,
                    "removeProxyIds": [str(proxy.id)],
                    "unassignProxyIds": [],
                },
            }),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload.get("ok"))

        org_agent.browser_use_agent.refresh_from_db()
        self.assertIsNone(org_agent.browser_use_agent.preferred_proxy_id)

        other_agent.browser_use_agent.refresh_from_db()
        self.assertEqual(other_agent.browser_use_agent.preferred_proxy_id, proxy.id)

    @patch("console.billing_update_service._update_stripe_dedicated_ip_quantity")
    @patch("console.billing_update_service.stripe_status")
    @patch("console.billing_update_service._get_owner_plan_id", return_value="org_team")
    def test_dedicated_ip_removal_only_clears_owner_agents(
        self,
        mock_get_plan_id,
        mock_stripe_status,
        mock_update_dedicated_qty,
    ):
        mock_stripe_status.return_value = SimpleNamespace(enabled=True)
        mock_update_dedicated_qty.return_value = None

        self.org.billing.purchased_seats = 1
        self.org.billing.save(update_fields=["purchased_seats"])

        proxy = ProxyServer.objects.create(
            name="Dedicated 2",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="10.0.0.2",
            port=8080,
            is_active=True,
            is_dedicated=True,
        )
        DedicatedProxyAllocation.objects.assign_to_owner(proxy, self.org)

        org_agent = create_persistent_agent(self.user, "Org Agent 2", organization=self.org)
        org_agent.browser_use_agent.preferred_proxy = proxy
        org_agent.browser_use_agent.save(update_fields=["preferred_proxy"])

        other_user = get_user_model().objects.create_user(
            username="other-user-2",
            email="other-user-2@example.com",
            password="pw12345",
        )
        other_org = Organization.objects.create(
            name="Other Org 2",
            slug="other-org-2",
            created_by=other_user,
        )
        other_org.billing.purchased_seats = 1
        other_org.billing.save(update_fields=["purchased_seats"])
        other_agent = create_persistent_agent(other_user, "Other Org Agent 2", organization=other_org)
        other_agent.browser_use_agent.preferred_proxy = proxy
        other_agent.browser_use_agent.save(update_fields=["preferred_proxy"])

        resp = self.client.post(
            self.url,
            data=json.dumps({
                "ownerType": "organization",
                "organizationId": str(self.org.id),
                "dedicatedIps": {
                    "addQuantity": 0,
                    "removeProxyIds": [str(proxy.id)],
                },
            }),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload.get("ok"))

        org_agent.browser_use_agent.refresh_from_db()
        self.assertIsNone(org_agent.browser_use_agent.preferred_proxy_id)

        other_agent.browser_use_agent.refresh_from_db()
        self.assertEqual(other_agent.browser_use_agent.preferred_proxy_id, proxy.id)
