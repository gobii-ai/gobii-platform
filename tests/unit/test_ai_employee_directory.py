from unittest import mock

from django.conf import settings
from django.test import Client, TestCase, tag
from django.urls import reverse

from agents.services import AIEmployeeTemplateService
from api.models import PersistentAgentTemplate


class AIEmployeeDirectoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.template = PersistentAgentTemplate.objects.create(
            code="test-ai-employee",
            display_name="Test AI Analyst",
            tagline="Keeps tabs on our test fixtures",
            description="Ensures the automated directory behaves as expected during unit tests.",
            charter="Monitor test events and summarize findings for the dev team.",
            base_schedule="0 10 * * *",
            schedule_jitter_minutes=5,
            event_triggers=[{"type": "webhook", "name": "test-suite", "description": "Triggered when CI kicks off."}],
            default_tools=["mcp_test_tool"],
            recommended_contact_channel="email",
            category="Testing",
        )

    def setUp(self):
        self.client = Client()

    @tag("batch_directory")
    def test_directory_lists_seeded_templates(self):
        response = self.client.get(reverse('pages:ai_employee_directory'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.template.display_name)
        self.assertContains(response, "At 10:00 AM")

    @tag("batch_directory")
    def test_hire_view_sets_session_for_anonymous_user(self):
        response = self.client.post(
            reverse('pages:ai_employee_hire', args=[self.template.code]),
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(settings.LOGIN_URL))
        session = self.client.session
        self.assertEqual(session['agent_charter'], self.template.charter)
        self.assertEqual(session[AIEmployeeTemplateService.TEMPLATE_SESSION_KEY], self.template.code)

    @tag("batch_schedule")
    def test_schedule_jitter_applies_offset(self):
        with mock.patch('agents.services.random.randint', return_value=7):
            jittered = AIEmployeeTemplateService.compute_schedule_with_jitter("0 10 * * *", 10)
        self.assertEqual(jittered, "7 10 * * *")

    @tag("batch_schedule")
    def test_schedule_jitter_no_change_when_disabled(self):
        unchanged = AIEmployeeTemplateService.compute_schedule_with_jitter("15 9 * * MON-FRI", 0)
        self.assertEqual(unchanged, "15 9 * * MON-FRI")

    @tag("batch_directory")
    def test_detail_view_shows_human_schedule_description(self):
        response = self.client.get(reverse('pages:ai_employee_detail', args=[self.template.code]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "At 10:00 AM")
