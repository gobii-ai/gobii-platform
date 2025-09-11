from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.contrib.auth import get_user_model
from api.models import PersistentAgent, BrowserUseAgent
import uuid


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
@tag("batch_contact_requests")
class AgentContactRequestsFriendlyErrorsTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(email="owner@example.com", password="pw", username="owner")
        self.other = User.objects.create_user(email="other@example.com", password="pw", username="other")

        self.browser = BrowserUseAgent.objects.create(user=self.owner, name="BA")
        self.agent = PersistentAgent.objects.create(user=self.owner, name="Test Agent", charter="c", browser_use_agent=self.browser)

    def test_wrong_account_shows_friendly_page(self):
        self.client.force_login(self.other)
        url = reverse("agent_contact_requests", kwargs={"pk": self.agent.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("not associated with", resp.content.decode().lower())

    def test_invalid_agent_shows_friendly_page(self):
        self.client.force_login(self.owner)
        bad_id = uuid.uuid4()
        url = reverse("agent_contact_requests", kwargs={"pk": bad_id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("invalid", resp.content.decode().lower())
