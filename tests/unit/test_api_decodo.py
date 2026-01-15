"""
Tests for Decodo IP block sync functionality.
"""
import uuid
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.core import mail
from django.test import TestCase, RequestFactory, tag, override_settings
from django.contrib.auth import get_user_model
from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.urls import reverse
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.messages.storage.fallback import FallbackStorage
from django.utils import timezone

from api.models import (
    DecodoLowInventoryAlert,
    DedicatedProxyAllocation,
    DecodoCredential,
    DecodoIPBlock,
    DecodoIP,
    ProxyServer,
)
from api.admin import DecodoIPBlockAdmin
from api.services.decodo_inventory import maybe_send_decodo_low_inventory_alert
from api.tasks import (
    sync_ip_block,
    _fetch_decodo_ip_data,
    _update_or_create_ip_record,
    _update_or_create_proxy_record,
    decodo_low_inventory_reminder,
)

User = get_user_model()


@tag("batch_api_decodo")
class DecodoSyncTaskTests(TestCase):
    """Test Decodo IP block sync tasks."""
    
    def setUp(self):
        """Set up test data."""
        self.credential = DecodoCredential.objects.create(
            username="test_user",
            password="test_pass"
        )
        self.ip_block = DecodoIPBlock.objects.create(
            credential=self.credential,
            block_size=2,
            endpoint="test.decodo.com",
            start_port=10001
        )
        
    def test_fetch_decodo_ip_data_success(self):
        """Test successful API call to Decodo."""
        mock_response_data = {
            "proxy": {"ip": "192.168.1.1"},
            "isp": {
                "isp": "Test ISP",
                "asn": 12345,
                "domain": "test.isp",
                "organization": "Test Organization"
            },
            "city": {
                "name": "Test City",
                "code": "TC",
                "state": "Test State",
                "time_zone": "UTC",
                "zip_code": "12345",
                "latitude": 40.7128,
                "longitude": -74.0060
            },
            "country": {
                "code": "US",
                "name": "United States",
                "continent": "North America"
            }
        }
        
        with patch('api.tasks.proxy_tasks.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_response_data
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response
            
            result = _fetch_decodo_ip_data(
                username="test_user",
                password="test_pass",
                endpoint="test.decodo.com",
                port=10001
            )
            
            self.assertEqual(result, mock_response_data)
            mock_get.assert_called_once()
            
    def test_fetch_decodo_ip_data_failure(self):
        """Test API call failure."""
        with patch('api.tasks.proxy_tasks.requests.get') as mock_get:
            mock_get.side_effect = Exception("Network error")
            
            result = _fetch_decodo_ip_data(
                username="test_user",
                password="test_pass", 
                endpoint="test.decodo.com",
                port=10001
            )
            
            self.assertIsNone(result)
            
    def test_update_or_create_ip_record(self):
        """Test creating/updating IP records."""
        ip_data = {
            "proxy": {"ip": "192.168.1.1"},
            "isp": {
                "isp": "Test ISP",
                "asn": 12345,
                "domain": "test.isp",
                "organization": "Test Organization"
            },
            "city": {
                "name": "Test City",
                "code": "TC",
                "state": "Test State",
                "time_zone": "UTC",
                "zip_code": "12345",
                "latitude": 40.7128,
                "longitude": -74.0060
            },
            "country": {
                "code": "US",
                "name": "United States",
                "continent": "North America"
            }
        }
        
        # Test creating a new record
        was_created = _update_or_create_ip_record(self.ip_block, ip_data, 10001)
        self.assertTrue(was_created)
        
        ip_record = DecodoIP.objects.get(ip_address="192.168.1.1")
        self.assertEqual(ip_record.ip_block, self.ip_block)
        self.assertEqual(ip_record.isp_name, "Test ISP")
        self.assertEqual(ip_record.isp_asn, 12345)
        self.assertEqual(ip_record.city_name, "Test City")
        self.assertEqual(ip_record.country_code, "US")
        proxy = ProxyServer.objects.get(decodo_ip=ip_record)
        self.assertTrue(proxy.is_dedicated)
        
        # Test updating the same record
        ip_data["isp"]["isp"] = "Updated ISP"
        was_created = _update_or_create_ip_record(self.ip_block, ip_data, 10001)
        self.assertFalse(was_created)
        
        ip_record.refresh_from_db()
        self.assertEqual(ip_record.isp_name, "Updated ISP")
        proxy.refresh_from_db()
        self.assertTrue(proxy.is_dedicated)
        
    @patch('api.tasks.proxy_tasks._fetch_decodo_ip_data')
    @patch('api.tasks.proxy_tasks._update_or_create_ip_record')
    def test_sync_ip_block_task(self, mock_update_record, mock_fetch_data):
        """Test the main sync task."""
        mock_fetch_data.return_value = {"proxy": {"ip": "192.168.1.1"}}
        mock_update_record.return_value = True
        
        # Run the sync task
        sync_ip_block(str(self.ip_block.id))
        
        # Verify it was called for each IP in the block
        self.assertEqual(mock_fetch_data.call_count, self.ip_block.block_size)
        self.assertEqual(mock_update_record.call_count, self.ip_block.block_size)
        
        # Check the calls were made with correct ports
        expected_calls = [
            ((), {'username': 'test_user', 'password': 'test_pass', 
                  'endpoint': 'test.decodo.com', 'port': 10001}),
            ((), {'username': 'test_user', 'password': 'test_pass',
                  'endpoint': 'test.decodo.com', 'port': 10002})
        ]
        actual_calls = [call for call in mock_fetch_data.call_args_list]
        
        for i, expected_call in enumerate(expected_calls):
            self.assertEqual(actual_calls[i][1], expected_call[1])

    @patch('api.tasks.proxy_tasks._fetch_decodo_ip_data')
    @patch('api.tasks.proxy_tasks._update_or_create_ip_record')
    def test_sync_ip_block_skips_auto_deactivated_proxy(self, mock_update_record, mock_fetch_data):
        self.ip_block.block_size = 1
        self.ip_block.save(update_fields=["block_size"])
        ProxyServer.objects.create(
            name="Deactivated Decodo Proxy",
            proxy_type=ProxyServer.ProxyType.HTTPS,
            host=self.ip_block.endpoint,
            port=self.ip_block.start_port,
            username=self.credential.username,
            password=self.credential.password,
            is_active=False,
            is_dedicated=True,
            auto_deactivated_at=timezone.now(),
            deactivation_reason="repeated_health_check_failures",
        )

        sync_ip_block(str(self.ip_block.id))

        mock_fetch_data.assert_not_called()
        mock_update_record.assert_not_called()

    @override_settings(PROXY_CONSECUTIVE_FAILURE_THRESHOLD=1)
    def test_auto_deactivation_detaches_decodo_ip(self):
        decodo_ip = DecodoIP.objects.create(
            ip_block=self.ip_block,
            ip_address="192.168.1.50",
            port=10001,
        )
        proxy = ProxyServer.objects.create(
            name="Decodo Proxy",
            proxy_type=ProxyServer.ProxyType.HTTPS,
            host=self.ip_block.endpoint,
            port=decodo_ip.port,
            username=self.credential.username,
            password=self.credential.password,
            static_ip=decodo_ip.ip_address,
            is_active=True,
            is_dedicated=True,
            decodo_ip=decodo_ip,
        )

        deactivated = proxy.record_health_check(False)

        self.assertTrue(deactivated)
        self.assertFalse(DecodoIP.objects.filter(id=decodo_ip.id).exists())
        proxy.refresh_from_db()
        self.assertIsNone(proxy.decodo_ip_id)

    @override_settings(PROXY_CONSECUTIVE_FAILURE_THRESHOLD=1)
    def test_auto_deactivation_skips_dedicated_allocation(self):
        user = User.objects.create_user(
            username="proxy-owner",
            email="proxy-owner@example.com",
            password="password",
        )
        decodo_ip = DecodoIP.objects.create(
            ip_block=self.ip_block,
            ip_address="192.168.1.55",
            port=10002,
        )
        proxy = ProxyServer.objects.create(
            name="Assigned Decodo Proxy",
            proxy_type=ProxyServer.ProxyType.HTTPS,
            host=self.ip_block.endpoint,
            port=decodo_ip.port,
            username=self.credential.username,
            password=self.credential.password,
            static_ip=decodo_ip.ip_address,
            is_active=True,
            is_dedicated=True,
            decodo_ip=decodo_ip,
        )
        DedicatedProxyAllocation.objects.assign_to_owner(proxy, user)

        deactivated = proxy.record_health_check(False)

        self.assertFalse(deactivated)
        proxy.refresh_from_db()
        self.assertTrue(proxy.is_active)
        self.assertIsNone(proxy.auto_deactivated_at)
        self.assertEqual(proxy.deactivation_reason, "")
        self.assertEqual(proxy.decodo_ip_id, decodo_ip.id)
        self.assertTrue(DecodoIP.objects.filter(id=decodo_ip.id).exists())
        self.assertEqual(proxy.consecutive_health_failures, 1)

    @override_settings(
        DECODO_LOW_INVENTORY_THRESHOLD=2,
        DECODO_LOW_INVENTORY_EMAIL="ops@example.com",
    )
    def test_low_inventory_alert_sends_once_per_day(self):
        owner = User.objects.create_user(
            username="dedicated-owner",
            email="dedicated-owner@example.com",
            password="password",
        )
        decodo_ip = DecodoIP.objects.create(
            ip_block=self.ip_block,
            ip_address="192.168.1.70",
            port=10003,
        )
        ProxyServer.objects.create(
            name="Active Decodo Proxy",
            proxy_type=ProxyServer.ProxyType.HTTPS,
            host=self.ip_block.endpoint,
            port=decodo_ip.port,
            username=self.credential.username,
            password=self.credential.password,
            static_ip=decodo_ip.ip_address,
            is_active=True,
            is_dedicated=True,
            decodo_ip=decodo_ip,
        )
        dedicated_ip = DecodoIP.objects.create(
            ip_block=self.ip_block,
            ip_address="192.168.1.72",
            port=10005,
        )
        dedicated_proxy = ProxyServer.objects.create(
            name="Dedicated Decodo Proxy",
            proxy_type=ProxyServer.ProxyType.HTTPS,
            host=self.ip_block.endpoint,
            port=dedicated_ip.port,
            username=self.credential.username,
            password=self.credential.password,
            static_ip=dedicated_ip.ip_address,
            is_active=True,
            is_dedicated=True,
            decodo_ip=dedicated_ip,
        )
        DedicatedProxyAllocation.objects.assign_to_owner(dedicated_proxy, owner)

        sent = maybe_send_decodo_low_inventory_alert(reason="test")

        self.assertTrue(sent)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Active shared proxies: 1", mail.outbox[0].body)
        self.assertIn("Active dedicated allocations: 1", mail.outbox[0].body)
        self.assertIn("Active Decodo proxies (total): 2", mail.outbox[0].body)
        self.assertEqual(DecodoLowInventoryAlert.objects.count(), 1)

        sent = maybe_send_decodo_low_inventory_alert(reason="test")

        self.assertFalse(sent)
        self.assertEqual(len(mail.outbox), 1)

        next_day = timezone.localdate() + timedelta(days=1)
        with patch("api.services.decodo_inventory.timezone.localdate", return_value=next_day):
            sent = maybe_send_decodo_low_inventory_alert(reason="test")

        self.assertTrue(sent)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(DecodoLowInventoryAlert.objects.count(), 2)

    @override_settings(
        DECODO_LOW_INVENTORY_THRESHOLD=1,
        DECODO_LOW_INVENTORY_EMAIL="ops@example.com",
    )
    def test_low_inventory_alert_skips_when_above_threshold(self):
        decodo_ip = DecodoIP.objects.create(
            ip_block=self.ip_block,
            ip_address="192.168.1.71",
            port=10004,
        )
        ProxyServer.objects.create(
            name="Active Decodo Proxy",
            proxy_type=ProxyServer.ProxyType.HTTPS,
            host=self.ip_block.endpoint,
            port=decodo_ip.port,
            username=self.credential.username,
            password=self.credential.password,
            static_ip=decodo_ip.ip_address,
            is_active=True,
            is_dedicated=True,
            decodo_ip=decodo_ip,
        )

        sent = maybe_send_decodo_low_inventory_alert(reason="test")

        self.assertFalse(sent)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(DecodoLowInventoryAlert.objects.count(), 0)

    def test_proxy_record_reuses_existing_proxy(self):
        decodo_ip = DecodoIP.objects.create(
            ip_block=self.ip_block,
            ip_address="192.168.1.60",
            port=10002,
        )
        proxy = ProxyServer.objects.create(
            name="Existing Proxy",
            proxy_type=ProxyServer.ProxyType.HTTPS,
            host=self.ip_block.endpoint,
            port=decodo_ip.port,
            username="old_user",
            password="old_pass",
            is_active=False,
            is_dedicated=False,
        )

        created = _update_or_create_proxy_record(decodo_ip, self.ip_block)

        self.assertFalse(created)
        proxy.refresh_from_db()
        self.assertEqual(proxy.decodo_ip_id, decodo_ip.id)
        self.assertTrue(proxy.is_dedicated)

    @override_settings(GOBII_RELEASE_ENV="staging")
    @patch("api.tasks.proxy_tasks.maybe_send_decodo_low_inventory_alert")
    @patch("api.tasks.proxy_tasks.logger")
    def test_inventory_reminder_skips_outside_prod(self, mock_logger, mock_alert):
        decodo_low_inventory_reminder(None)
        mock_alert.assert_not_called()
        mock_logger.info.assert_called_once_with(
            "Decodo inventory reminder skipped; task runs only in production (env=%s)",
            "staging",
        )


@tag("batch_api_decodo")
class DecodoAdminTests(TestCase):
    """Test Decodo admin interface."""
    
    def setUp(self):
        """Set up test data."""
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.admin = DecodoIPBlockAdmin(DecodoIPBlock, self.site)
        
        # Create a superuser
        self.superuser = User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='admin123'
        )
        
        self.credential = DecodoCredential.objects.create(
            username="test_user",
            password="test_pass"
        )
        self.ip_block = DecodoIPBlock.objects.create(
            credential=self.credential,
            block_size=2,
            endpoint="test.decodo.com", 
            start_port=10001
        )
        
    def test_sync_now_button_display(self):
        """Test that the sync button is displayed correctly."""
        button_html = self.admin.sync_now(self.ip_block)
        self.assertIn('Sync&nbsp;Now', button_html)
        self.assertIn(f'/admin/api/decodoipblock/{self.ip_block.pk}/sync/', button_html)
        
    @patch('api.admin.sync_ip_block.delay')
    def test_sync_view_success(self, mock_delay):
        """Test successful sync via admin button."""
        request = self.factory.post(f'/admin/api/decodoipblock/{self.ip_block.pk}/sync/')
        request.user = self.superuser
        
        # Django admin requires a session and a message storage backend
        session_middleware = SessionMiddleware(lambda r: None)
        session_middleware.process_request(request)
        request.session.save()
        setattr(request, '_messages', FallbackStorage(request))
        
        response = self.admin.sync_view(request, str(self.ip_block.pk))
        
        # Check that task was queued
        mock_delay.assert_called_once_with(str(self.ip_block.pk))
        
        # Check redirect
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/admin/api/decodoipblock/{self.ip_block.pk}/change/', response.url)
        
    def test_sync_view_not_found(self):
        """Test sync view with non-existent IP block."""
        fake_id = uuid.uuid4()
        request = self.factory.post(f'/admin/api/decodoipblock/{fake_id}/sync/')
        request.user = self.superuser
        # Django admin requires a session and a message storage backend
        session_middleware = SessionMiddleware(lambda r: None)
        session_middleware.process_request(request)
        request.session.save()
        setattr(request, '_messages', FallbackStorage(request))
        
        response = self.admin.sync_view(request, str(fake_id))
        
        # Should still redirect
        self.assertEqual(response.status_code, 302)
