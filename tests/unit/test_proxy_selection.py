"""
Tests for HTTP proxy selection functionality.
"""
import uuid
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, tag, override_settings
from django.conf import settings
from django.utils import timezone
from django.contrib.auth import get_user_model

from api.models import ProxyServer, ProxyHealthCheckResult, ProxyHealthCheckSpec, BrowserUseAgent, BrowserUseAgentTask
from api.proxy_selection import (
    proxy_has_recent_health_pass,
    select_proxy,
    select_proxy_for_persistent_agent,
    select_proxy_for_browser_task
)

User = get_user_model()


@tag("batch_proxy_selection")
class ProxySelectionTests(TestCase):
    """Test proxy selection functionality."""
    
    def setUp(self):
        """Set up test data."""
        # Create test proxies
        self.healthy_proxy = ProxyServer.objects.create(
            name="Healthy Proxy",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="healthy.proxy.com",
            port=8080,
            username="user1",
            password="pass1",
            is_active=True
        )
        
        self.unhealthy_proxy = ProxyServer.objects.create(
            name="Unhealthy Proxy",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="unhealthy.proxy.com",
            port=8080,
            username="user2",
            password="pass2",
            is_active=True
        )
        
        self.inactive_proxy = ProxyServer.objects.create(
            name="Inactive Proxy",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="inactive.proxy.com",
            port=8080,
            is_active=False
        )
        
        # Create health check spec
        self.health_check_spec = ProxyHealthCheckSpec.objects.create(
            name="Basic Health Check",
            prompt="Visit google.com and check if it loads"
        )
        
        # Create recent successful health check for healthy proxy
        ProxyHealthCheckResult.objects.create(
            proxy_server=self.healthy_proxy,
            health_check_spec=self.health_check_spec,
            status=ProxyHealthCheckResult.Status.PASSED,
            checked_at=timezone.now() - timedelta(days=1)
        )
        
        # Create old successful health check for unhealthy proxy
        ProxyHealthCheckResult.objects.create(
            proxy_server=self.unhealthy_proxy,
            health_check_spec=self.health_check_spec,
            status=ProxyHealthCheckResult.Status.PASSED,
            checked_at=timezone.now() - timedelta(days=60)
        )
        
        # Create recent failed health check for unhealthy proxy
        ProxyHealthCheckResult.objects.create(
            proxy_server=self.unhealthy_proxy,
            health_check_spec=self.health_check_spec,
            status=ProxyHealthCheckResult.Status.FAILED,
            checked_at=timezone.now() - timedelta(hours=1)
        )

    @tag("batch_proxy_selection")
    def test_proxy_has_recent_health_pass_default_days(self):
        """Test proxy health check with default 45-day window."""
        # Healthy proxy should pass
        self.assertTrue(proxy_has_recent_health_pass(self.healthy_proxy))
        
        # Unhealthy proxy should fail (old success, recent failure)
        self.assertFalse(proxy_has_recent_health_pass(self.unhealthy_proxy))
        
        # Inactive proxy should fail (no health checks)
        self.assertFalse(proxy_has_recent_health_pass(self.inactive_proxy))
    
    def test_proxy_has_recent_health_pass_custom_days(self):
        """Test proxy health check with custom day window."""
        # With 30-day window, healthy proxy should still pass
        self.assertTrue(proxy_has_recent_health_pass(self.healthy_proxy, health_check_days=30))
        
        # With 30-day window, unhealthy proxy should still fail
        self.assertFalse(proxy_has_recent_health_pass(self.unhealthy_proxy, health_check_days=30))
        
        # With 90-day window, unhealthy proxy should now pass (includes old success)
        self.assertTrue(proxy_has_recent_health_pass(self.unhealthy_proxy, health_check_days=90))

    @tag("batch_proxy_selection")
    def test_select_proxy_with_override(self):
        """Test proxy selection with override proxy."""
        result = select_proxy(override_proxy=self.unhealthy_proxy)
        self.assertEqual(result, self.unhealthy_proxy)
    
    @tag("batch_proxy_selection")
    def test_select_proxy_with_healthy_preferred(self):
        """Test proxy selection with healthy preferred proxy."""
        result = select_proxy(preferred_proxy=self.healthy_proxy)
        self.assertEqual(result, self.healthy_proxy)
    
    @patch('api.models.BrowserUseAgent.select_random_proxy')
    def test_select_proxy_with_unhealthy_preferred_has_alternative(self, mock_select_random):
        """Test proxy selection with unhealthy preferred proxy and healthy alternative available."""
        mock_select_random.return_value = self.healthy_proxy
        
        result = select_proxy(preferred_proxy=self.unhealthy_proxy)
        self.assertEqual(result, self.healthy_proxy)
        mock_select_random.assert_called_once()
    
    @patch('api.models.BrowserUseAgent.select_random_proxy')
    def test_select_proxy_with_unhealthy_preferred_no_alternative(self, mock_select_random):
        """Test proxy selection with unhealthy preferred proxy and no healthy alternative."""
        mock_select_random.return_value = None
        
        result = select_proxy(preferred_proxy=self.unhealthy_proxy)
        self.assertEqual(result, self.unhealthy_proxy)  # Falls back to preferred
        mock_select_random.assert_called_once()
    
    @patch('api.models.BrowserUseAgent.select_random_proxy')
    def test_select_proxy_random_selection(self, mock_select_random):
        """Test proxy selection with no preferred proxy."""
        mock_select_random.return_value = self.healthy_proxy
        
        result = select_proxy()
        self.assertEqual(result, self.healthy_proxy)
        mock_select_random.assert_called_once()
    
    @patch('api.models.BrowserUseAgent.select_random_proxy')
    @patch.object(settings, 'DEBUG', True)
    @tag("batch_proxy_selection")
    def test_select_proxy_no_proxy_debug_mode(self, mock_select_random):
        """Test proxy selection returns None in debug mode when no proxy available."""
        mock_select_random.return_value = None
        
        result = select_proxy(allow_no_proxy_in_debug=True)
        self.assertIsNone(result)
    
    @patch('api.models.BrowserUseAgent.select_random_proxy')
    @override_settings(GOBII_PROPRIETARY_MODE=True, DEBUG=False)
    @tag("batch_proxy_selection")
    def test_select_proxy_no_proxy_production_mode(
        self,
        mock_select_random,
    ):
        """Test proxy selection raises error in production mode when no proxy available."""
        mock_select_random.return_value = None
        
        with self.assertRaises(RuntimeError) as context:
            select_proxy(allow_no_proxy_in_debug=False)
        
        self.assertIn("No proxy available", str(context.exception))
        self.assertIn("proprietary mode", str(context.exception))

    @patch('api.models.BrowserUseAgent.select_random_proxy')
    @override_settings(GOBII_PROPRIETARY_MODE=False, DEBUG=False)
    def test_select_proxy_no_proxy_community_mode(
        self,
        mock_select_random,
    ):
        """Test proxy selection returns None in community mode without proxies."""
        mock_select_random.return_value = None

        result = select_proxy(allow_no_proxy_in_debug=False)

        self.assertIsNone(result)
    
    @patch('api.models.BrowserUseAgent.select_random_proxy')
    @override_settings(DEBUG=True, GOBII_PROPRIETARY_MODE=True)
    def test_select_proxy_no_proxy_debug_mode_disabled(self, mock_select_random):
        """Test proxy selection raises error even in debug mode when allow_no_proxy_in_debug=False."""
        mock_select_random.return_value = None
        
        with self.assertRaises(RuntimeError):
            select_proxy(allow_no_proxy_in_debug=False)
    
    @patch('api.models.BrowserUseAgent.select_random_proxy')
    def test_select_proxy_custom_health_check_days(self, mock_select_random):
        """Test proxy selection with custom health check days."""
        # Mock that no alternative proxy is available
        mock_select_random.return_value = None
        
        # With 30 days, unhealthy proxy should fail health check
        result = select_proxy(
            preferred_proxy=self.unhealthy_proxy,
            health_check_days=30
        )
        # Should fall back to preferred since no alternative available
        self.assertEqual(result, self.unhealthy_proxy)
        
        # With 90 days, unhealthy proxy should pass health check
        result = select_proxy(
            preferred_proxy=self.unhealthy_proxy,
            health_check_days=90
        )
        self.assertEqual(result, self.unhealthy_proxy)

    def test_select_proxy_context_logging(self):
        """Test that context_id is used for logging (no assertions, just coverage)."""
        # This test ensures the context_id parameter works without errors
        result = select_proxy(
            preferred_proxy=self.healthy_proxy,
            context_id="test-context-123"
        )
        self.assertEqual(result, self.healthy_proxy)


@tag("batch_proxy_selection")
class PersistentAgentProxySelectionTests(TestCase):
    """Test proxy selection for persistent agents."""
    
    def setUp(self):
        """Set up test data."""
        self.proxy = ProxyServer.objects.create(
            name="Test Proxy",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="test.proxy.com",
            port=8080,
            is_active=True
        )
        
        # Mock persistent agent
        self.mock_persistent_agent = MagicMock()
        self.mock_persistent_agent.id = "agent-123"
        self.mock_persistent_agent.preferred_proxy = self.proxy
    
    @patch('api.proxy_selection.select_proxy')
    def test_select_proxy_for_persistent_agent_with_preferred(self, mock_select_proxy):
        """Test proxy selection for persistent agent with preferred proxy."""
        mock_select_proxy.return_value = self.proxy
        
        result = select_proxy_for_persistent_agent(self.mock_persistent_agent)
        
        mock_select_proxy.assert_called_once_with(
            preferred_proxy=self.proxy,
            override_proxy=None,
            context_id="persistent_agent_agent-123"
        )
        self.assertEqual(result, self.proxy)
    
    @patch('api.proxy_selection.select_proxy')
    def test_select_proxy_for_persistent_agent_no_preferred(self, mock_select_proxy):
        """Test proxy selection for persistent agent without preferred proxy."""
        self.mock_persistent_agent.preferred_proxy = None
        mock_select_proxy.return_value = self.proxy
        
        result = select_proxy_for_persistent_agent(self.mock_persistent_agent)
        
        mock_select_proxy.assert_called_once_with(
            preferred_proxy=None,
            override_proxy=None,
            context_id="persistent_agent_agent-123"
        )
        self.assertEqual(result, self.proxy)
    
    @patch('api.proxy_selection.select_proxy')
    def test_select_proxy_for_persistent_agent_with_override(self, mock_select_proxy):
        """Test proxy selection for persistent agent with override proxy."""
        override_proxy = ProxyServer.objects.create(
            name="Override Proxy",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="override.proxy.com",
            port=8080,
            is_active=True
        )
        mock_select_proxy.return_value = override_proxy
        
        result = select_proxy_for_persistent_agent(
            self.mock_persistent_agent,
            override_proxy=override_proxy,
            health_check_days=30
        )
        
        mock_select_proxy.assert_called_once_with(
            preferred_proxy=self.proxy,
            override_proxy=override_proxy,
            context_id="persistent_agent_agent-123",
            health_check_days=30
        )
        self.assertEqual(result, override_proxy)


@tag("batch_proxy_selection")
class BrowserTaskProxySelectionTests(TestCase):
    """Test proxy selection for browser tasks."""
    
    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123"
        )
        
        self.proxy = ProxyServer.objects.create(
            name="Test Proxy",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="test.proxy.com",
            port=8080,
            is_active=True
        )
        
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Test Agent",
            preferred_proxy=self.proxy
        )
        
        self.task = BrowserUseAgentTask.objects.create(
            user=self.user,
            agent=self.browser_agent,
            prompt="Test task"
        )
    
    @patch('api.proxy_selection.select_proxy')
    def test_select_proxy_for_browser_task_with_agent(self, mock_select_proxy):
        """Test proxy selection for browser task with agent."""
        mock_select_proxy.return_value = self.proxy
        
        result = select_proxy_for_browser_task(self.task)
        
        mock_select_proxy.assert_called_once_with(
            preferred_proxy=self.proxy,
            override_proxy=None,
            context_id=f"task_{self.task.id}"
        )
        self.assertEqual(result, self.proxy)
    
    @patch('api.proxy_selection.select_proxy')
    def test_select_proxy_for_browser_task_no_agent(self, mock_select_proxy):
        """Test proxy selection for browser task without agent."""
        self.task.agent = None
        self.task.save()
        mock_select_proxy.return_value = self.proxy
        
        result = select_proxy_for_browser_task(self.task)
        
        mock_select_proxy.assert_called_once_with(
            preferred_proxy=None,
            override_proxy=None,
            context_id=f"task_{self.task.id}"
        )
        self.assertEqual(result, self.proxy)
    
    @patch('api.proxy_selection.select_proxy')
    def test_select_proxy_for_browser_task_agent_no_preferred_proxy(self, mock_select_proxy):
        """Test proxy selection for browser task with agent but no preferred proxy."""
        self.browser_agent.preferred_proxy = None
        self.browser_agent.save()
        mock_select_proxy.return_value = self.proxy
        
        result = select_proxy_for_browser_task(self.task)
        
        mock_select_proxy.assert_called_once_with(
            preferred_proxy=None,
            override_proxy=None,
            context_id=f"task_{self.task.id}"
        )
        self.assertEqual(result, self.proxy)
    
    @patch('api.proxy_selection.select_proxy')
    def test_select_proxy_for_browser_task_with_override_and_kwargs(self, mock_select_proxy):
        """Test proxy selection for browser task with override and additional kwargs."""
        override_proxy = ProxyServer.objects.create(
            name="Override Proxy",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="override.proxy.com",
            port=8080,
            is_active=True
        )
        mock_select_proxy.return_value = override_proxy
        
        result = select_proxy_for_browser_task(
            self.task,
            override_proxy=override_proxy,
            health_check_days=7,
            allow_no_proxy_in_debug=True
        )
        
        mock_select_proxy.assert_called_once_with(
            preferred_proxy=self.proxy,
            override_proxy=override_proxy,
            context_id=f"task_{self.task.id}",
            health_check_days=7,
            allow_no_proxy_in_debug=True
        )
        self.assertEqual(result, override_proxy)


@tag("batch_proxy_selection")
class ProxySelectionIntegrationTests(TestCase):
    """Integration tests for proxy selection without mocking."""
    
    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="integrationuser",
            email="integration@example.com",
            password="testpass123"
        )
        
        # Create multiple proxies with different health states
        self.healthy_proxy1 = ProxyServer.objects.create(
            name="Healthy Proxy 1",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="healthy1.proxy.com",
            port=8080,
            static_ip="192.168.1.1",
            is_active=True
        )
        
        self.healthy_proxy2 = ProxyServer.objects.create(
            name="Healthy Proxy 2",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="healthy2.proxy.com",
            port=8080,
            static_ip="192.168.1.2",
            is_active=True
        )
        
        # Create health check spec
        self.health_check_spec = ProxyHealthCheckSpec.objects.create(
            name="Integration Health Check",
            prompt="Test health check"
        )
        
        # Add recent health checks
        for proxy in [self.healthy_proxy1, self.healthy_proxy2]:
            ProxyHealthCheckResult.objects.create(
                proxy_server=proxy,
                health_check_spec=self.health_check_spec,
                status=ProxyHealthCheckResult.Status.PASSED,
                checked_at=timezone.now() - timedelta(hours=1)
            )
    
    def test_end_to_end_proxy_selection_flow(self):
        """Test end-to-end proxy selection without mocks."""
        # Create browser agent with preferred proxy
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Integration Test Agent",
            preferred_proxy=self.healthy_proxy1
        )
        
        # Create task
        task = BrowserUseAgentTask.objects.create(
            user=self.user,
            agent=browser_agent,
            prompt="Integration test task"
        )
        
        # Test proxy selection
        result = select_proxy_for_browser_task(task)
        
        # Should get the preferred proxy since it's healthy
        self.assertEqual(result, self.healthy_proxy1)
        
        # Test with override
        result_with_override = select_proxy_for_browser_task(
            task,
            override_proxy=self.healthy_proxy2
        )
        
        # Should get the override proxy
        self.assertEqual(result_with_override, self.healthy_proxy2)
    
    def test_proxy_selection_priority_order(self):
        """Test that proxy selection follows the correct priority order."""
        # Test override priority (highest)
        result = select_proxy(
            preferred_proxy=self.healthy_proxy1,
            override_proxy=self.healthy_proxy2
        )
        self.assertEqual(result, self.healthy_proxy2)
        
        # Test preferred proxy priority
        result = select_proxy(preferred_proxy=self.healthy_proxy1)
        self.assertEqual(result, self.healthy_proxy1) 

    def test_select_random_proxy_skips_dedicated_inventory(self):
        dedicated = ProxyServer.objects.create(
            name="Dedicated Only",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="dedicated.proxy.com",
            port=8081,
            static_ip="192.168.2.2",
            is_active=True,
            is_dedicated=True,
        )
        ProxyHealthCheckResult.objects.create(
            proxy_server=dedicated,
            health_check_spec=self.health_check_spec,
            status=ProxyHealthCheckResult.Status.PASSED,
            checked_at=timezone.now() - timedelta(hours=1)
        )

        selected = BrowserUseAgent.select_random_proxy()
        self.assertIn(selected, {self.healthy_proxy1, self.healthy_proxy2})
        self.assertNotEqual(selected, dedicated)

    def test_select_random_proxy_returns_none_when_only_dedicated(self):
        ProxyServer.objects.exclude(is_dedicated=True).delete()
        dedicated = ProxyServer.objects.create(
            name="Dedicated Pool",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="dedicated.pool.proxy.com",
            port=8082,
            static_ip="192.168.3.3",
            is_active=True,
            is_dedicated=True,
        )
        ProxyHealthCheckResult.objects.create(
            proxy_server=dedicated,
            health_check_spec=self.health_check_spec,
            status=ProxyHealthCheckResult.Status.PASSED,
            checked_at=timezone.now() - timedelta(hours=1)
        )

        self.assertIsNone(BrowserUseAgent.select_random_proxy())
