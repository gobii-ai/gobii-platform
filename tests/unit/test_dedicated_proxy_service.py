from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.models import DedicatedProxyAllocation, ProxyServer
from api.services.dedicated_proxy_service import (
    DedicatedProxyService,
    DedicatedProxyUnavailableError,
    is_multi_assign_enabled,
)


@tag("batch_dedicated_proxy_service")
class DedicatedProxyServiceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="dedicated-owner",
            email="dedicated-owner@example.com",
            password="password",
        )
        self.proxy_one = ProxyServer.objects.create(
            name="Dedicated Proxy 1",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="10.0.0.1",
            port=8000,
            username="user",
            password="pass",
            static_ip="10.0.0.1",
            is_active=True,
            is_dedicated=True,
        )
        self.proxy_two = ProxyServer.objects.create(
            name="Dedicated Proxy 2",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="10.0.0.2",
            port=8000,
            username="user",
            password="pass",
            static_ip="10.0.0.2",
            is_active=True,
            is_dedicated=True,
        )

    def test_allocate_proxy_success(self):
        proxy = DedicatedProxyService.allocate_proxy(self.user)
        self.assertTrue(proxy.is_dedicated_allocated)
        self.assertEqual(proxy, self.proxy_one)
        allocation = DedicatedProxyAllocation.objects.get(proxy=proxy)
        self.assertEqual(allocation.owner_user, self.user)

    def test_allocate_proxy_unavailable(self):
        DedicatedProxyService.allocate_proxy(self.user)
        DedicatedProxyService.allocate_proxy(self.user)
        with self.assertRaises(DedicatedProxyUnavailableError):
            DedicatedProxyService.allocate_proxy(self.user)

    def test_release_proxy_returns_to_pool(self):
        proxy = DedicatedProxyService.allocate_proxy(self.user)
        released = DedicatedProxyService.release_proxy(proxy)
        self.assertTrue(released)
        proxy.refresh_from_db()
        self.assertFalse(proxy.is_dedicated_allocated)
        self.assertEqual(DedicatedProxyService.available_count(), 2)

    def test_release_for_owner(self):
        DedicatedProxyService.allocate_proxy(self.user)
        DedicatedProxyService.allocate_proxy(self.user)
        released = DedicatedProxyService.release_for_owner(self.user)
        self.assertEqual(released, 2)
        self.assertEqual(DedicatedProxyService.available_count(), 2)

    def test_is_multi_assign_enabled_reads_setting(self):
        self.assertTrue(is_multi_assign_enabled())
        with self.settings(DEDICATED_IP_ALLOW_MULTI_ASSIGN=False):
            self.assertFalse(is_multi_assign_enabled())
