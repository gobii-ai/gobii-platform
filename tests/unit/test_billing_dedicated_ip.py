from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from api.models import Organization, OrganizationMembership
from constants.plans import PlanNamesChoices


User = get_user_model()


@tag("batch_dedicated_proxy_service")
@override_settings(SEGMENT_WRITE_KEY="", SEGMENT_WEB_WRITE_KEY="")
class DedicatedIpBillingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="billing-user", email="billing@example.com", password="pw")
        assert self.client.login(username="billing-user", password="pw")

    def _common_patches(self, *, allocated_count=0):
        return [
            patch("console.views._assign_stripe_api_key"),
            patch("console.views.get_or_create_stripe_customer", return_value=SimpleNamespace(id="cus_123")),
            patch("console.views.get_active_subscription", return_value=SimpleNamespace(id="sub_123")),
            patch("console.views.get_stripe_settings", return_value=SimpleNamespace(
                startup_dedicated_ip_price_id="price_dedicated",
                org_team_dedicated_ip_price_id="price_org_dedicated",
            )),
            patch("console.views.stripe.Subscription.retrieve", return_value={"items": {"data": []}}),
            patch("console.views.stripe.SubscriptionItem.create"),
            patch("console.views.stripe.SubscriptionItem.modify"),
            patch("console.views.stripe.SubscriptionItem.delete"),
            patch("console.views.DedicatedProxyService.allocate_proxy"),
            patch("console.views.DedicatedProxyService.release_for_owner"),
            patch("console.views.DedicatedProxyService.release_specific", return_value=True),
            patch("console.views.DedicatedProxyService.allocated_count", return_value=allocated_count),
            patch("console.views.get_user_plan", return_value={"id": PlanNamesChoices.STARTUP.value}),
        ]

    def test_add_dedicated_ips_success(self):
        url = reverse("add_dedicated_ip_quantity")
        with ExitStack() as stack:
            mock_status = stack.enter_context(patch("console.views.stripe_status"))
            mock_status.return_value.enabled = True
            for patcher in self._common_patches(allocated_count=0):
                stack.enter_context(patcher)
            resp = self.client.post(url, {"quantity": 2})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("billing"))
        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any("Dedicated IP quantity updated" in msg for msg in messages))

    def test_add_dedicated_ips_org_success(self):
        org = Organization.objects.create(name="Org", slug="org", created_by=self.user)
        org_billing = org.billing
        org_billing.subscription = PlanNamesChoices.ORG_TEAM.value
        org_billing.save(update_fields=["subscription"])
        OrganizationMembership.objects.create(
            org=org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(org.id)
        session["context_name"] = org.name
        session.save()

        url = reverse("add_dedicated_ip_quantity")
        with ExitStack() as stack:
            mock_status = stack.enter_context(patch("console.views.stripe_status"))
            mock_status.return_value.enabled = True
            for patcher in self._common_patches(allocated_count=0):
                stack.enter_context(patcher)
            resp = self.client.post(url, {"quantity": 1})

        self.assertEqual(resp.status_code, 302)
        self.assertIn("org_id", resp.url)
        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any("Dedicated IP quantity updated" in msg for msg in messages))

    def test_remove_dedicated_ip(self):
        url = reverse("remove_dedicated_ip")
        with ExitStack() as stack:
            mock_status = stack.enter_context(patch("console.views.stripe_status"))
            mock_status.return_value.enabled = True
            for patcher in self._common_patches(allocated_count=2):
                stack.enter_context(patcher)
            resp = self.client.post(url, {"proxy_id": "proxy-123"})

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("billing"))
        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any("Dedicated IP removed" in msg for msg in messages))

    def test_remove_all_dedicated_ips(self):
        url = reverse("remove_all_dedicated_ip")
        with ExitStack() as stack:
            mock_status = stack.enter_context(patch("console.views.stripe_status"))
            mock_status.return_value.enabled = True
            for patcher in self._common_patches(allocated_count=3):
                stack.enter_context(patcher)
            resp = self.client.post(url)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("billing"))
        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any("All dedicated IPs removed" in msg for msg in messages))
