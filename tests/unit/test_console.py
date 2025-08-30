from django.test import TestCase, Client, tag
from django.contrib.auth import get_user_model
from django.urls import reverse
from waffle.testutils import override_flag
from constants.feature_flags import PERSISTENT_AGENTS


@tag("batch_console_agents")
class ConsoleViewsTest(TestCase):
    def setUp(self):
        """Set up test user and client."""
        User = get_user_model()
        self.user = User.objects.create_user(
            username='test@example.com',
            email='test@example.com',
            password='testpass123'
        )
        self.client = Client()
        self.client.login(email='test@example.com', password='testpass123')

    @override_flag(PERSISTENT_AGENTS, active=True)
    @tag("batch_console_agents")
    def test_delete_persistent_agent_also_deletes_browser_agent(self):
        """Test that deleting a persistent agent also deletes its browser agent."""
        from api.models import PersistentAgent, BrowserUseAgent
        
        # Create a browser use agent
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name='Test Browser Agent'
        )
        
        # Create a persistent agent linked to the browser agent
        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name='Test Persistent Agent',
            charter='Test charter',
            browser_use_agent=browser_agent
        )
        
        # Store IDs for verification after deletion
        browser_agent_id = browser_agent.id
        persistent_agent_id = persistent_agent.id
        
        # Verify both agents exist before deletion
        self.assertTrue(BrowserUseAgent.objects.filter(id=browser_agent_id).exists())
        self.assertTrue(PersistentAgent.objects.filter(id=persistent_agent_id).exists())
        
        # Delete the persistent agent via the console view
        url = reverse('agent_delete', kwargs={'pk': persistent_agent_id})
        response = self.client.delete(url)
        
        # Verify the response is successful
        self.assertEqual(response.status_code, 200)
        
        # Verify both the persistent agent and browser agent are deleted
        self.assertFalse(PersistentAgent.objects.filter(id=persistent_agent_id).exists())
        self.assertFalse(BrowserUseAgent.objects.filter(id=browser_agent_id).exists())
