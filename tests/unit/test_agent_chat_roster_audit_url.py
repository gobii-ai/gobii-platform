from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.urls import reverse

from api.models import BrowserUseAgent, PersistentAgent


class AgentChatRosterAuditUrlTests(TestCase):
    @tag("batch_agent_chat")
    def test_roster_includes_audit_url_for_superuser_non_staff(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="superuser-non-staff",
            email="superuser-non-staff@example.com",
            password="password123",
        )
        user.is_superuser = True
        user.is_staff = False
        user.save(update_fields=["is_superuser", "is_staff"])

        browser_agent = BrowserUseAgent.objects.create(user=user, name="Browser Agent")
        agent = PersistentAgent.objects.create(
            user=user,
            name="Roster Tester",
            charter="Do useful things",
            browser_use_agent=browser_agent,
        )

        client = Client()
        client.force_login(user)

        response = client.get(reverse("console_agent_roster"))
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        agents = payload.get("agents", [])
        agent_entry = next((entry for entry in agents if entry.get("id") == str(agent.id)), None)
        self.assertIsNotNone(agent_entry)

        expected_audit_url = reverse("console-agent-audit", kwargs={"agent_id": agent.id})
        self.assertEqual(agent_entry.get("audit_url"), expected_audit_url)

