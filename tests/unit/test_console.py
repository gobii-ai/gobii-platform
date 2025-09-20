from django.test import TestCase, Client, tag
from django.contrib.auth import get_user_model
from django.urls import reverse
from unittest.mock import patch


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

    @patch("console.views.fetch_timeline_window")
    def test_timeline_newer_updates_cursor(self, mock_fetch_window):
        """Ensure newer timeline fetches advance the cursor to avoid duplicates."""
        from api.models import BrowserUseAgent, PersistentAgent
        from console.timeline import TimelineWindow

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Cursor Test Browser Agent",
        )
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Cursor Test Persistent Agent",
            charter="Test",
            browser_use_agent=browser_agent,
        )

        new_cursor = "2024-09-01T12:00:00|message|abc123"
        mock_fetch_window.return_value = TimelineWindow(
            events=[],
            has_more_older=False,
            has_more_newer=False,
            window_oldest_cursor=None,
            window_newest_cursor=new_cursor,
        )

        url = reverse("agent_timeline_window", args=[agent.id])
        response = self.client.get(
            url,
            {
                "direction": "newer",
                "cursor": "2024-08-31T23:00:00|message|old",
                "current_newest": "2024-08-31T23:00:00|message|old",
            },
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(new_cursor, content)
        self.assertNotIn(
            "2024-08-31T23:00:00|message|old",
            content,
            "Timeline newer cursor should move forward to prevent duplicate fetches.",
        )
