"""
Tests for Decodo IP block sync functionality.
"""
import uuid
from unittest.mock import patch, MagicMock

from django.test import TestCase, RequestFactory, tag
from django.contrib.auth import get_user_model
from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.urls import reverse
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.messages.storage.fallback import FallbackStorage

from api.models import DecodoCredential, DecodoIPBlock, DecodoIP
from api.admin import DecodoIPBlockAdmin
from api.tasks import sync_ip_block, _fetch_decodo_ip_data, _update_or_create_ip_record

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
        
        # Test updating the same record
        ip_data["isp"]["isp"] = "Updated ISP"
        was_created = _update_or_create_ip_record(self.ip_block, ip_data, 10001)
        self.assertFalse(was_created)
        
        ip_record.refresh_from_db()
        self.assertEqual(ip_record.isp_name, "Updated ISP")
        
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
