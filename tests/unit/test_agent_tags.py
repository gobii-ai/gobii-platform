from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from unittest.mock import patch

from api.agent.short_description import compute_charter_hash
from api.agent.tags import maybe_schedule_agent_tags
from api.agent.tasks.agent_tags import (
    _extract_tags,
    _generate_via_llm,
    generate_agent_tags_task,
)
from api.models import BrowserUseAgent, PersistentAgent


@tag("batch_agent_tags")
class AgentTagGenerationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="agent-owner",
            email="owner@example.com",
            password="pass",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Browser Agent")

    def _create_agent(self, charter="Assist with operations") -> PersistentAgent:
        return PersistentAgent.objects.create(
            user=self.user,
            name="Persistent Agent",
            charter=charter,
            browser_use_agent=self.browser_agent,
        )

    def test_maybe_schedule_agent_tags_skips_when_charter_missing(self):
        agent = self._create_agent(charter="  ")
        with patch("api.agent.tasks.agent_tags.generate_agent_tags_task.delay") as mocked_delay:
            scheduled = maybe_schedule_agent_tags(agent)
        self.assertFalse(scheduled)
        mocked_delay.assert_not_called()
        agent.refresh_from_db()
        self.assertEqual(agent.tags_requested_hash, "")

    def test_maybe_schedule_agent_tags_enqueues_task(self):
        agent = self._create_agent()
        with patch("api.agent.tasks.agent_tags.generate_agent_tags_task.delay") as mocked_delay:
            scheduled = maybe_schedule_agent_tags(agent)
        self.assertTrue(scheduled)
        agent.refresh_from_db()
        expected_hash = compute_charter_hash(agent.charter)
        self.assertEqual(agent.tags_requested_hash, expected_hash)
        mocked_delay.assert_called_once_with(str(agent.id), expected_hash, None)

    def test_generate_agent_tags_updates_fields(self):
        agent = self._create_agent()
        charter_hash = compute_charter_hash(agent.charter)
        agent.tags_requested_hash = charter_hash
        agent.save(update_fields=["tags_requested_hash"])

        with patch("api.agent.tasks.agent_tags._generate_via_llm", return_value=["Customer Support", "Operations"]):
            generate_agent_tags_task.run(str(agent.id), charter_hash)

        agent.refresh_from_db()
        self.assertEqual(agent.tags, ["Customer Support", "Operations"])
        self.assertEqual(agent.tags_charter_hash, charter_hash)
        self.assertEqual(agent.tags_requested_hash, "")

    def test_generate_agent_tags_skips_when_charter_changes(self):
        agent = self._create_agent()
        original_hash = compute_charter_hash(agent.charter)
        agent.tags_requested_hash = original_hash
        agent.save(update_fields=["tags_requested_hash"])

        agent.charter = "Handle marketing analytics"
        agent.save(update_fields=["charter"])

        with patch("api.agent.tasks.agent_tags._generate_via_llm", return_value=["Marketing", "Analytics"]):
            generate_agent_tags_task.run(str(agent.id), original_hash)

        agent.refresh_from_db()
        self.assertEqual(agent.tags, [])
        self.assertEqual(agent.tags_charter_hash, "")
        self.assertEqual(agent.tags_requested_hash, "")

    def test_extract_tags_handles_code_block_json(self):
        content = """```json
["Personal Assistant", "Task Automation", "Communication"]
```"""
        tags = _extract_tags(content)
        self.assertEqual(tags, ["Personal Assistant", "Task Automation", "Communication"])

    def test_existing_tags_are_normalized_without_rescheduling(self):
        agent = self._create_agent()
        agent.tags = [
            '```json ["personal assistant"',
            '"task automation"',
            '"communication"',
            '"research"',
            '"scheduling"] ```',
        ]
        charter_hash = compute_charter_hash(agent.charter)
        agent.tags_charter_hash = charter_hash
        agent.save(update_fields=["tags", "tags_charter_hash"])

        with patch("api.agent.tasks.agent_tags.generate_agent_tags_task.delay") as mocked_delay:
            scheduled = maybe_schedule_agent_tags(agent)

        self.assertFalse(scheduled)
        mocked_delay.assert_not_called()
        agent.refresh_from_db()
        self.assertEqual(
            agent.tags,
            ["personal assistant", "task automation", "communication"],
        )

    def test_generate_via_llm_uses_agent_for_llm_config(self):
        agent = self._create_agent()
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='["Ops","Support","Insights"]'))]
        )

        with patch("api.agent.tasks.agent_tags.get_summarization_llm_config", return_value=("provider", "model", {})) as mocked_config, patch(
            "api.agent.tasks.agent_tags.run_completion", return_value=response
        ):
            tags = _generate_via_llm(agent, agent.charter)

        self.assertEqual(tags, ["Ops", "Support", "Insights"])
        mocked_config.assert_called_once()
        _, kwargs = mocked_config.call_args
        self.assertIs(kwargs.get("agent"), agent)
