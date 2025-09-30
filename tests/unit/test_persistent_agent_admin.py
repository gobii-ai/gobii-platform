from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.urls import reverse

from api.models import BrowserUseAgent, PersistentAgent


@tag("batch_api_persistent_agents")
class PersistentAgentAdminTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin_user = User.objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="testpass123",
        )
        self.client.force_login(self.admin_user)

        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.admin_user,
            name="Browser Agent",
        )

        self.persistent_agent = PersistentAgent.objects.create(
            user=self.admin_user,
            name="Persistent Agent",
            charter="Assist with tasks",
            browser_use_agent=self.browser_agent,
        )

    def test_trigger_processing_queues_valid_ids(self):
        url = reverse("admin:api_persistentagent_trigger_processing")
        invalid_id = "not-a-uuid"
        submitted_ids = f"{self.persistent_agent.id}\n{invalid_id}\n{self.persistent_agent.id}"

        with patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(url, data={"agent_ids": submitted_ids}, follow=True)

        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with(str(self.persistent_agent.id))

        messages = list(response.context["messages"])
        self.assertTrue(any("Queued event processing for 1 persistent agent" in message.message for message in messages))
        self.assertTrue(any("Skipped invalid ID(s)" in message.message for message in messages))

    def test_trigger_processing_page_renders_form(self):
        url = reverse("admin:api_persistentagent_trigger_processing")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Trigger Event Processing")
        self.assertContains(response, "Persistent Agent IDs")
