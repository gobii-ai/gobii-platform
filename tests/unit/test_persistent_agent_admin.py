from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.urls import reverse

from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentSystemMessage


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

    def _create_agent(self, **overrides):
        """Helper to create additional persistent agents with unique browser agents."""
        browser_agent = BrowserUseAgent.objects.create(
            user=overrides.get("user", self.admin_user),
            name=overrides.pop("browser_name", f"Browser Agent {BrowserUseAgent.objects.count()}"),
        )
        defaults = {
            "user": self.admin_user,
            "name": "Persistent Agent Extra",
            "charter": "Assist with tasks",
            "browser_use_agent": browser_agent,
        }
        defaults.update(overrides)
        return PersistentAgent.objects.create(**defaults)

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
        self.assertContains(response, 'id="id_only_active" value="1" checked')
        self.assertContains(response, 'id="id_only_with_user" value="1" checked')
        self.assertNotContains(response, 'id="id_skip_expired" value="1" checked')

    def test_trigger_processing_skips_inactive_agents_by_default(self):
        inactive_agent = self._create_agent(is_active=False, name="Inactive Agent")
        url = reverse("admin:api_persistentagent_trigger_processing")
        submitted_ids = f"{inactive_agent.id}\n{self.persistent_agent.id}"

        with patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(
                url,
                data={
                    "agent_ids": submitted_ids,
                    "only_active": "on",
                    "only_with_user": "on",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with(str(self.persistent_agent.id))
        messages = list(response.context["messages"])
        self.assertTrue(any("Skipped inactive agent ID(s)" in message.message for message in messages))

    def test_trigger_processing_processes_inactive_agent_when_checkbox_off(self):
        inactive_agent = self._create_agent(is_active=False, name="Inactive Agent")
        url = reverse("admin:api_persistentagent_trigger_processing")

        with patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(
                url,
                data={
                    "agent_ids": str(inactive_agent.id),
                    "only_with_user": "on",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with(str(inactive_agent.id))

    def test_trigger_processing_skips_expired_agents_when_requested(self):
        expired_agent = self._create_agent(
            life_state=PersistentAgent.LifeState.EXPIRED,
            name="Expired Agent",
        )
        url = reverse("admin:api_persistentagent_trigger_processing")

        with patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(
                url,
                data={
                    "agent_ids": str(expired_agent.id),
                    "only_active": "on",
                    "only_with_user": "on",
                    "skip_expired": "on",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        mock_delay.assert_not_called()
        messages = list(response.context["messages"])
        self.assertTrue(any("Skipped expired agent ID(s)" in message.message for message in messages))

    def test_trigger_processing_processes_expired_when_skip_unchecked(self):
        expired_agent = self._create_agent(
            life_state=PersistentAgent.LifeState.EXPIRED,
            name="Expired Agent",
        )
        url = reverse("admin:api_persistentagent_trigger_processing")

        with patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(
                url,
                data={
                    "agent_ids": str(expired_agent.id),
                    "only_active": "on",
                    "only_with_user": "on",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with(str(expired_agent.id))

    def test_trigger_processing_skips_missing_user_when_checkbox_on(self):
        url = reverse("admin:api_persistentagent_trigger_processing")

        class _EmptyQuerySet:
            def values_list(self, *args, **kwargs):
                return []

        class _EmptyManager:
            def filter(self, *args, **kwargs):
                return _EmptyQuerySet()

        class _MissingUserModel:
            objects = _EmptyManager()

        with patch("api.admin.get_user_model", return_value=_MissingUserModel), \
                patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(
                url,
                data={
                    "agent_ids": str(self.persistent_agent.id),
                    "only_active": "on",
                    "only_with_user": "on",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        mock_delay.assert_not_called()
        messages = list(response.context["messages"])
        self.assertTrue(any("Skipped agent ID(s) missing a user" in message.message for message in messages))

    def test_force_proactive_get_renders_form(self):
        url = reverse("admin:api_persistentagent_force_proactive", args=[self.persistent_agent.pk])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Force Proactive Outreach")
        self.assertContains(response, str(self.persistent_agent.id))

    def test_force_proactive_post_triggers_outreach(self):
        url = reverse("admin:api_persistentagent_force_proactive", args=[self.persistent_agent.pk])
        reason = " Need immediate outreach "

        with patch("api.admin.ProactiveActivationService.force_trigger") as mock_force, patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(url, data={"reason": reason}, follow=True)

        self.assertEqual(response.status_code, 200)
        mock_force.assert_called_once_with(
            self.persistent_agent,
            initiated_by=self.admin_user.email,
            reason="Need immediate outreach",
        )
        mock_delay.assert_called_once_with(str(self.persistent_agent.pk))
        messages = list(response.context["messages"])
        self.assertTrue(any("Forced proactive outreach queued" in message.message for message in messages))

    def test_system_message_get_renders_form(self):
        url = reverse("admin:api_persistentagent_system_message", args=[self.persistent_agent.pk])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Issue System Message")
        self.assertContains(response, str(self.persistent_agent.id))

    def test_system_message_post_creates_record_and_triggers_processing(self):
        url = reverse("admin:api_persistentagent_system_message", args=[self.persistent_agent.pk])

        with patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(url, data={"message": "Focus on the quarterly report"}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            PersistentAgentSystemMessage.objects.filter(
                agent=self.persistent_agent,
                body="Focus on the quarterly report",
            ).exists()
        )
        mock_delay.assert_called_once_with(str(self.persistent_agent.pk))
        messages = list(response.context["messages"])
        self.assertTrue(any("System message saved" in message.message for message in messages))

    def test_system_message_broadcast_get_renders_form(self):
        url = reverse("admin:api_persistentagentsystemmessage_broadcast")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Broadcast System Message")
        self.assertContains(response, "Broadcast Message")

    def test_system_message_broadcast_creates_records_without_processing(self):
        extra_agent = self._create_agent(name="Second Agent")
        url = reverse("admin:api_persistentagentsystemmessage_broadcast")

        with patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(
                url,
                data={"message": "Global directive"},
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        mock_delay.assert_not_called()
        for agent in (self.persistent_agent, extra_agent):
            self.assertTrue(
                PersistentAgentSystemMessage.objects.filter(agent=agent, body="Global directive").exists()
            )

        messages = list(response.context["messages"])
        self.assertTrue(any("Broadcast saved for 2 persistent agents" in message.message for message in messages))
