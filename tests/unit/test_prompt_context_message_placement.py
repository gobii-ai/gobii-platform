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

    def test_sqlite_guidance_only_in_system_message(self):
        sqlite_guidance = prompt_context._get_sqlite_guidance()

        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ):
            context, _, _ = build_prompt_context(self.agent)

        system_message = next(message for message in context if message["role"] == "system")
        user_message = next(message for message in context if message["role"] == "user")

        self.assertEqual(system_message["content"].count(sqlite_guidance), 1)
        self.assertNotIn(sqlite_guidance, user_message["content"])
        all_contents = "\n".join(message["content"] for message in context)
        self.assertEqual(all_contents.count(sqlite_guidance), 1)
        self.assertIn("<sqlite_guidance>", system_message["content"])
        self.assertIn("</sqlite_guidance>", system_message["content"])
        self.assertIn("keyed entities/events/relations", sqlite_guidance)
        self.assertIn("multi-fetch finite sets as keyed", sqlite_guidance)
        self.assertIn("with fields/status/source", sqlite_guidance)
        self.assertIn("query gaps before reporting", sqlite_guidance)
        self.assertIn("Only sourced blockers are unresolved", sqlite_guidance)
        self.assertIn("return only needed rows to context", sqlite_guidance)
        self.assertIn(
            "one shaped INSERT ... SELECT/json_each filtered by IN/tool_name",
            sqlite_guidance,
        )
        self.assertIn("extract fields in SQL, not literals", sqlite_guidance)
        self.assertIn("Never filter one result_id at a time, make a table per result", sqlite_guidance)
        self.assertIn("Keep chat/outreach light. Owner reports on 4+ peers", system_message["content"])
        self.assertIn(
            "need resolved/total and one table with requested fields and available source links",
            system_message["content"],
        )
        self.assertIn("Link names only with provided item/detail links", system_message["content"])
        self.assertIn("resolve/source each requested field", system_message["content"])
        self.assertIn("grouped discovery isn't coverage", system_message["content"])
        self.assertIn("separate sourced unavailability from research gaps", system_message["content"])
        self.assertIn("asks except finite sets", system_message["content"])
        self.assertIn("deep/exhaustive research and finite-set coverage", system_message["content"])
        self.assertIn("batch gaps, follow up misses, and reconcile coverage", system_message["content"])
        self.assertIn("never repeat a successful URL/query", system_message["content"])
