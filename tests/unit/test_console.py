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

    @tag("batch_console_agents")
    def test_delete_persistent_agent_handles_missing_browser_agent(self):
        """Deletion should succeed even if the BrowserUseAgent record is missing."""
        from api.models import PersistentAgent, BrowserUseAgent

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name='Missing Browser Agent'
        )
        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name='Agent With Missing Browser',
            charter='Charter',
            browser_use_agent=browser_agent
        )

        empty_qs = BrowserUseAgent.objects.none()
        url = reverse('agent_delete', kwargs={'pk': persistent_agent.id})

        with patch.object(BrowserUseAgent.objects, 'filter', return_value=empty_qs):
            response = self.client.delete(url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersistentAgent.objects.filter(id=persistent_agent.id).exists())

    @patch("console.views.AgentService.has_agents_available", return_value=True)
    @tag("batch_console_agents")
    def test_org_agent_creation_blocked_without_seat(self, _mock_agents_available):
        """Org-owned agent creation should surface a validation error when no seats exist."""
        from api.models import Organization, OrganizationMembership, PersistentAgent

        org = Organization.objects.create(
            name="Seatless Inc",
            slug="seatless-inc",
            created_by=self.user,
        )
        OrganizationMembership.objects.create(
            org=org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        session = self.client.session
        session["agent_charter"] = "Help with tasks"
        session["context_type"] = "organization"
        session["context_id"] = str(org.id)
        session["context_name"] = org.name
        session.save()

        response = self.client.post(
            reverse("agent_create_contact"),
            data={
                "preferred_contact_method": "email",
                "contact_endpoint_email": "owner@example.com",
                "email_enabled": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.redirect_chain)
        form = response.context.get("form")
        self.assertIsNotNone(form)
        non_field_errors = form.non_field_errors()
        self.assertTrue(any("Purchase organization seats" in err for err in non_field_errors))
        context_data = response.context
        if hasattr(context_data, 'get'):
            messages_iter = context_data.get('messages')
            self.assertIsNotNone(messages_iter)
        else:
            messages_iter = None
            for ctx in context_data:
                if 'messages' in ctx:
                    messages_iter = ctx['messages']
            self.assertIsNotNone(messages_iter)
        django_messages = list(messages_iter)
        self.assertTrue(any("Purchase organization seats" in msg.message for msg in django_messages))
        self.assertEqual(PersistentAgent.objects.filter(organization=org).count(), 0)
