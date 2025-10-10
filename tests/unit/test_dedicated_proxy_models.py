from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, tag

from api.models import DedicatedProxyAllocation, ProxyServer, Organization


@tag("batch_dedicated_proxy_models")
class DedicatedProxyAllocationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="password",
        )
        self.proxy = ProxyServer.objects.create(
            name="Dedicated Proxy",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="1.2.3.4",
            port=8080,
            username="user",
            password="pass",
            static_ip="1.2.3.4",
            is_active=True,
            is_dedicated=True,
        )

    def test_assign_to_user_creates_allocation(self):
        allocation = DedicatedProxyAllocation.objects.assign_to_owner(self.proxy, self.user)
        self.assertEqual(allocation.owner_user, self.user)
        self.assertIsNone(allocation.owner_organization)
        self.assertTrue(self.proxy.is_dedicated_allocated)
        self.assertEqual(
            list(DedicatedProxyAllocation.objects.for_owner(self.user)),
            [allocation],
        )

    def test_assign_requires_dedicated_proxy(self):
        shared_proxy = ProxyServer.objects.create(
            name="Shared Proxy",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="5.6.7.8",
            port=8080,
            username="shared",
            password="shared",
            static_ip="5.6.7.8",
            is_active=True,
            is_dedicated=False,
        )
        with self.assertRaises(ValidationError):
            DedicatedProxyAllocation.objects.assign_to_owner(shared_proxy, self.user)

    def test_assign_requires_single_owner(self):
        org = Organization.objects.create(
            name="Example Org",
            slug="example-org",
            plan="org_team",
            created_by=self.user,
        )
        allocation = DedicatedProxyAllocation(
            proxy=self.proxy,
            owner_user=self.user,
            owner_organization=org,
        )
        with self.assertRaises(ValidationError):
            allocation.full_clean()

    def test_for_owner_with_organization(self):
        org = Organization.objects.create(
            name="Example Org",
            slug="example-org-2",
            plan="org_team",
            created_by=self.user,
        )
        org_proxy = ProxyServer.objects.create(
            name="Dedicated Org Proxy",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="9.9.9.9",
            port=8090,
            username="org",
            password="org",
            static_ip="9.9.9.9",
            is_active=True,
            is_dedicated=True,
        )
        allocation = DedicatedProxyAllocation.objects.assign_to_owner(org_proxy, org)
        self.assertEqual(allocation.owner_organization, org)
        self.assertIsNone(allocation.owner_user)
        org_allocations = DedicatedProxyAllocation.objects.for_owner(org)
        self.assertEqual(list(org_allocations), [allocation])
