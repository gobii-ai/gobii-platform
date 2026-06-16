from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from unittest.mock import patch

from api.agent.core import prompt_context
from api.agent.core.prompt_context import build_prompt_context
from api.models import BrowserUseAgent, PersistentAgent

User = get_user_model()


@tag("batch_promptree")
class PromptContextSqlitePlacementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="prompt_sqlite@example.com",
            email="prompt_sqlite@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="PromptSQLiteBA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="PromptSQLiteAgent",
            charter="Test sqlite guidance placement",
            browser_use_agent=self.browser_agent,
        )

    def test_sqlite_examples_only_in_system_message(self):
        sqlite_examples = prompt_context._get_sqlite_examples()

        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ):
            context, _, _ = build_prompt_context(self.agent)

        system_message = next(message for message in context if message["role"] == "system")
        user_message = next(message for message in context if message["role"] == "user")

        self.assertEqual(system_message["content"].count(sqlite_examples), 1)
        self.assertNotIn(sqlite_examples, user_message["content"])
        all_contents = "\n".join(message["content"] for message in context)
        self.assertEqual(all_contents.count(sqlite_examples), 1)
        self.assertIn("<sqlite_examples>", system_message["content"])
        self.assertIn("</sqlite_examples>", system_message["content"])
