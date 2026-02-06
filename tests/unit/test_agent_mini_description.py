from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from unittest.mock import patch

from api.agent.short_description import (
    build_mini_description,
    compute_charter_hash,
    maybe_schedule_mini_description,
)
from api.agent.tasks.mini_description import generate_agent_mini_description_task
from api.models import BrowserUseAgent, PersistentAgent


@tag("batch_agent_mini_description")
class AgentMiniDescriptionTests(TestCase):
    def setUp(self) -> None:
        User = get_user_model()
        self.user = User.objects.create_user(
            username="owner",
            email="user@example.com",
            password="testpass",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Browser Agent")

    def _create_agent(self, charter: str = "Help with operations") -> PersistentAgent:
        return PersistentAgent.objects.create(
            user=self.user,
            name="Test Persistent Agent",
            charter=charter,
            browser_use_agent=self.browser_agent,
        )

    def test_maybe_schedule_mini_description_skips_without_charter(self) -> None:
        agent = self._create_agent(charter="  ")
        with patch("api.agent.tasks.mini_description.generate_agent_mini_description_task.delay") as mocked_delay:
            scheduled = maybe_schedule_mini_description(agent)
        self.assertFalse(scheduled)
        self.assertFalse(mocked_delay.called)
        agent.refresh_from_db()
        self.assertEqual(agent.mini_description_requested_hash, "")

    def test_maybe_schedule_mini_description_enqueues_when_missing(self) -> None:
        agent = self._create_agent()
        with patch("api.agent.tasks.mini_description.generate_agent_mini_description_task.delay") as mocked_delay:
            scheduled = maybe_schedule_mini_description(agent)
        self.assertTrue(scheduled)
        agent.refresh_from_db()
        expected_hash = compute_charter_hash(agent.charter)
        self.assertEqual(agent.mini_description_requested_hash, expected_hash)
        mocked_delay.assert_called_once_with(str(agent.id), expected_hash, None)

    def test_generate_mini_description_updates_fields(self) -> None:
        agent = self._create_agent()
        charter_hash = compute_charter_hash(agent.charter)
        agent.mini_description_requested_hash = charter_hash
        agent.save(update_fields=["mini_description_requested_hash"])

        with patch("api.agent.tasks.mini_description._generate_via_llm", return_value="Sales leads generator"):
            generate_agent_mini_description_task.run(str(agent.id), charter_hash)

        agent.refresh_from_db()
        self.assertEqual(agent.mini_description, "Sales leads generator")
        self.assertEqual(agent.mini_description_charter_hash, charter_hash)
        self.assertEqual(agent.mini_description_requested_hash, "")

    def test_generate_mini_description_skips_when_charter_changed(self) -> None:
        agent = self._create_agent()
        old_hash = compute_charter_hash(agent.charter)
        agent.mini_description_requested_hash = old_hash
        agent.save(update_fields=["mini_description_requested_hash"])

        agent.charter = "New responsibilities"
        agent.save(update_fields=["charter"])

        with patch("api.agent.tasks.mini_description._generate_via_llm", return_value="Updated summary"):
            generate_agent_mini_description_task.run(str(agent.id), old_hash)

        agent.refresh_from_db()
        self.assertEqual(agent.mini_description, "")
        self.assertEqual(agent.mini_description_charter_hash, "")
        self.assertEqual(agent.mini_description_requested_hash, "")

    def test_build_mini_description_uses_mini_when_available(self) -> None:
        agent = self._create_agent()
        agent.mini_description = "Helpful research assistant"
        agent.save(update_fields=["mini_description"])

        mini, source = build_mini_description(agent)

        self.assertEqual(mini, "Helpful research assistant")
        self.assertEqual(source, "mini")

    def test_build_mini_description_uses_placeholder_when_only_charter(self) -> None:
        charter = "Assist leadership with quarterly planning and cross-functional coordination"
        agent = self._create_agent(charter=charter)

        mini, source = build_mini_description(agent)

        self.assertEqual(mini, "Agent")
        self.assertEqual(source, "placeholder")
