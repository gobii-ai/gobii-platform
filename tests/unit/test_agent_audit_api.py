import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag

from api.models import BrowserUseAgent, PersistentAgent


@tag("batch_console_api")
class StaffAgentAuditAPITests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff = user_model.objects.create_user(
            username="staff-admin",
            email="staff@example.com",
            password="pass123",
            is_staff=True,
        )
        self.nonstaff = user_model.objects.create_user(
            username="regular",
            email="regular@example.com",
            password="pass123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.nonstaff, name="Browser Agent")
        self.agent = PersistentAgent.objects.create(
            user=self.nonstaff,
            name="Audit Target",
            charter="Do things",
            browser_use_agent=self.browser_agent,
        )
        self.client = Client()
        self.client.force_login(self.staff)

    def test_process_events_endpoint_enqueues_task(self):
        with patch("console.api_views.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(f"/console/api/staff/agents/{self.agent.id}/audit/process/")
        self.assertEqual(response.status_code, 202)
        mock_delay.assert_called_once_with(str(self.agent.id))
        payload = response.json()
        self.assertIn("queued", payload)
        self.assertIn("processing_active", payload)

    def test_create_system_message(self):
        payload = {"body": "Priority directive", "is_active": True}
        response = self.client.post(
            f"/console/api/staff/agents/{self.agent.id}/system-messages/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data.get("kind"), "system_message")
        self.assertEqual(data.get("body"), "Priority directive")
        self.assertTrue(data.get("can_edit"))

    def test_system_message_requires_staff(self):
        self.client.force_login(self.nonstaff)
        response = self.client.post(
            f"/console/api/staff/agents/{self.agent.id}/system-messages/",
            data=json.dumps({"body": "nope"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
